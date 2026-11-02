"""
Running a map's external tool calls: what the World Editor does with
JassHelper's `//! external` commands and `//! externalblock` blocks after it
has compiled the script.

JassHelper writes each block's `//! i` lines to a temporary file (with the
block's extension; `$FILENAME$` in the arguments stands for it) and starts the
tool with `map lookuppaths arguments`, one process per external, in source
order, stopping at the first one that fails. `ExternalRunner` does the same
without starting anything: the tools are the Python implementations in
`tools`, the Lua runs in `wc3files.scripts.lua_vm`, and every file a script
touches lives in a sandbox.

The sandbox is a workspace directory laid out like a JassNewGenPack install:

     <workspace>/                     = C:\\Program Files\\Warcraft III
     <workspace>/jassnewgenpack/      the editor's folder, the tools' working
                                      directory (logs\\grimex.txt is here)
     <workspace>/jassnewgenpack/jass/      JassHelper's lookup folder: vJass a
                                      script generates for the map to import
     <workspace>/jassnewgenpack/grimext/   where the tools are

`package.path` names that grimext folder, as the real tools' Lua does, so
scripts that find their files from it (the LUA_FILE_HEADER family) work
unchanged. Files they write persist in the workspace between blocks, which is
how one block passes a library to the next. Nothing outside the workspace and
the lookup folders can be read, nothing outside the workspace written, and no
program is ever started.
"""

import os
import shutil
import tempfile

from wc3files.scripts.lua_vm import LuaVM, LuaError, run_with_stack, to_text
from wc3files.scripts.lua_stdlib import LuaHost, LuaExit
from .tools import TOOLS, UNSUPPORTED_TOOLS, ToolContext, ToolError, split_arguments


DEFAULT_INSTALL = 'C:\\Program Files\\Warcraft III'

# The tools are Windows programs: text-mode files use CRLF line breaks.
WINDOWS_NEWLINE = '\r\n'


class ExternalError(Exception):
     """An external tool call that failed: a Lua error in a block, a refused
     command line, an unknown tool.

     location
          Where it happened in the source (`file:line`): the `//! i` line for
          a Lua error, the `//! external` line otherwise.
     tool
          The tool name.
     traceback
          For a Lua error, the (position, function) pairs it went through.
     """

     def __init__(self, message, location=None, tool=None, traceback=None):
          super().__init__(message)
          self.message = message
          self.location = location
          self.tool = tool
          self.traceback = traceback or []

     def __str__(self):
          if self.location and not self.message.startswith(str(self.location)):
               return f'{self.location}: {self.message}'
          return self.message


class ExternalsResult:
     """What running the externals did.

     files
          All the map's files afterwards (`{archive path: bytes}`).
     changed
          The archive paths the tools wrote.
     errors
          ExternalError for each external that failed.
     log
          The tools' log lines (what logs\\grimex.txt would hold).
     output
          What the scripts printed.
     denied
          `(operation, path or command)` for each file or command access the
          sandbox refused.
     ran
          `(External, 'ok' | 'error' | 'skipped')` for each external.
     """

     def __init__(self, files):
          self.files = files
          self.changed = []
          self.errors = []
          self.log = []
          self.output = ''
          self.denied = []
          self.ran = []

     @property
     def ok(self):
          return not self.errors

     def changed_files(self):
          return {name: self.files[name] for name in self.changed if name in self.files}

     def __repr__(self):
          return (f'ExternalsResult({len(self.ran)} externals, {len(self.errors)} errors, '
                  f'{len(self.changed)} files changed)')


class ExternalRunner:
     """Runs `//! external` commands and external blocks against a map.

     map_files
          The map's files (`{archive path: bytes}`), e.g. from
          `W3xFile.files`; an empty map when None. A copy is changed.
     workspace
          The sandbox directory (see the module docstring). A temporary one is
          made when None, and removed by `close()`.
     lookup_folders
          Folders where command lines and scripts look for files, after the
          workspace (JassHelper's `[lookupfolders]`). Read only.
     install_path
          The absolute path the workspace stands for.
     stop_on_error
          Stop at the first external that fails, as the World Editor does.
          False runs them all, to report every error at once.
     max_steps
          The Lua step budget per external.
     """

     def __init__(self, map_files=None, workspace=None, lookup_folders=(),
                  install_path=DEFAULT_INSTALL, stop_on_error=True,
                  max_steps=200_000_000, environ=None):
          self._temporary = None
          if workspace is None:
               self._temporary = tempfile.mkdtemp(prefix='wc3files_grimex_')
               workspace = self._temporary
          self.workspace = os.path.abspath(workspace)
          self.editor_dir = os.path.join(self.workspace, 'jassnewgenpack')
          self.grimext_dir = os.path.join(self.editor_dir, 'grimext')
          # The editor folder's own subfolders: logs (the tools' log), jass
          # (JassHelper's lookup folder, where scripts put generated vJass
          # for the map to import) and grimext (the tools).
          self.jass_dir = os.path.join(self.editor_dir, 'jass')
          for folder in ('logs', 'jass', 'grimext'):
               os.makedirs(os.path.join(self.editor_dir, folder), exist_ok=True)
          self.lookup_folders = [os.path.abspath(p) for p in lookup_folders]
          self.install_path = install_path.rstrip('\\/')
          self.stop_on_error = stop_on_error
          self.max_steps = max_steps
          self.environ = dict(environ or {})
          self.files = dict(map_files or {})
          self.ctx = None

     def close(self):
          if self._temporary is not None:
               shutil.rmtree(self._temporary, ignore_errors=True)
               self._temporary = None

     def __enter__(self):
          return self

     def __exit__(self, *exc):
          self.close()

     # -- paths -----------------------------------------------------------------

     def _host(self, result):
          host = LuaHost(root=self.workspace, writable=True, cwd=self.editor_dir,
                         mounts={self.install_path: self.workspace},
                         read_only=self.lookup_folders, environ=self.environ,
                         output=lambda text: _append_output(result, text),
                         text_newline=WINDOWS_NEWLINE)
          return host

     def find_file(self, path, host=None):
          """Where a path from a command line or a script is on disk."""
          host = host or self._host(ExternalsResult({}))
          text = to_text(path)
          candidates = []
          real = host.real_path(path)
          if real is not None:
               candidates.append(real)
          if not os.path.isabs(text) and ':' not in text:
               for folder in [self.editor_dir, self.workspace] + self.lookup_folders:
                    candidates.append(os.path.normpath(os.path.join(folder, *_parts(text))))
          for candidate in candidates:
               try:
                    allowed = host._inside(candidate, [self.workspace] + self.lookup_folders)
               except Exception:
                    allowed = False
               if allowed and os.path.exists(candidate):
                    return candidate
          return None

     def _write_file(self, host, path, data):
          real = host.check(path, write=True)
          os.makedirs(os.path.dirname(real) or '.', exist_ok=True)
          with open(real, 'wb') as fh:
               fh.write(data)

     # -- running ---------------------------------------------------------------

     def run(self, externals):
          """Run the externals (the `externals` of a vJass CompileResult, or
          `External` objects) in order. Returns an ExternalsResult."""
          result = ExternalsResult(self.files)
          stopped = False
          for external in externals:
               if stopped:
                    result.ran.append((external, 'skipped'))
                    continue
               error = self._run_one(external, result)
               if error is None:
                    result.ran.append((external, 'ok'))
               else:
                    result.errors.append(error)
                    result.ran.append((external, 'error'))
                    if self.stop_on_error:
                         stopped = True
          result.changed = sorted(set(result.changed))
          return result

     def run_script(self, source, tool='ObjectMerger', chunkname='script.lua',
                    positions=None):
          """Run Lua source with a tool's functions, as `tool script.lua`
          would. Returns an ExternalsResult."""
          from wc3files.vjass.preprocessor import External
          from wc3files.vjass.errors import Location
          lines = source.replace('\r\n', '\n').split('\n')
          external = External(tool, '$FILENAME$', Location(chunkname, 1), lines=lines,
                              positions=positions or [f'{chunkname}:{n}'
                                                      for n in range(1, len(lines) + 1)],
                              extension='lua')
          return self.run([external])

     def _run_one(self, external, result):
          tool_name = external.tool
          tool_class = TOOLS.get(tool_name.lower())
          if tool_class is None:
               if any(tool_name.lower() == name.lower() for name in UNSUPPORTED_TOOLS):
                    message = f'the {tool_name} tool is not supported'
               else:
                    message = f'External not found in config file: "{tool_name}"'
               return ExternalError(message, external.location, tool_name)
          host = self._host(result)
          ctx = ToolContext(self.files, lambda path: self.find_file(path, host), host,
                            write_file=lambda path, data: self._write_file(host, path, data))
          self.ctx = ctx
          tool = tool_class(ctx)
          # Each external is a separate run of the tool: on failure the map is
          # left as it was before it.
          before = dict(self.files)
          failure = None
          try:
               arguments = split_arguments(external.arguments or '')
               if external.is_block:
                    if (external.extension or '').lower() != 'lua':
                         raise ToolError(f'blocks with extension={external.extension} are not '
                                         f'supported; only Lua blocks can be run')
                    if '$FILENAME$' not in arguments:
                         raise ToolError('the block is never passed to the tool: its arguments '
                                         'do not name $FILENAME$')
                    self._run_lua(tool, external.text, f'{external.location}',
                                  [str(p) for p in external.positions], host)
                    others = [a for a in arguments if a != '$FILENAME$']
                    if others:
                         tool.run_command(others, lambda t, p: self._run_lua_file(t, p, host))
               else:
                    tool.run_command(arguments, lambda t, p: self._run_lua_file(t, p, host))
               tool.finish()
          except LuaError as error:
               failure = ExternalError(str(error), None, tool_name,
                                       traceback=list(error.traceback))
          except ToolError as error:
               failure = ExternalError(str(error), external.location, tool_name)
          except LuaExit as exit_:
               tool.finish()
               if exit_.code:
                    failure = ExternalError(f'the script exited with code {exit_.code}',
                                            external.location, tool_name)
          finally:
               result.log.extend(ctx.log)
               result.denied.extend(host.denied)
          if failure is not None:
               self.files.clear()
               self.files.update(before)
               return failure
          for name, data in self.files.items():
               if before.get(name) is not data:
                    result.changed.append(name)
          return None

     def _run_lua(self, tool, source, chunkname, positions, host):
          vm = LuaVM(version='5.1', host=host, max_steps=self.max_steps)
          exe_dir = self.install_path + '\\jassnewgenpack\\grimext'
          vm.get_global('package').set(
               'path', f'.\\?.lua;{exe_dir}\\lua\\?.lua;{exe_dir}\\lua\\?\\init.lua;'
                       f'{exe_dir}\\?.lua;{exe_dir}\\?\\init.lua')
          vm.get_global('package').set('cpath', f'.\\?.dll;{exe_dir}\\?.dll')
          tool.install(vm)
          run_with_stack(vm.execute, source, chunkname, positions)

     def _run_lua_file(self, tool, path, host):
          real = self.find_file(path, host)
          if real is None:
               raise ToolError(f"{path} doesn't exist")
          with open(real, 'rb') as fh:
               text = fh.read().decode('utf-8', 'replace')
          self._run_lua(tool, text, path, None, host)


def _append_output(result, text):
     result.output += text


def _parts(path):
     return [p for p in path.replace('\\', '/').split('/') if p]

