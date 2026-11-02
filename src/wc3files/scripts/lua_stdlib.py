"""
The Lua standard library for `lua_vm`, and the host it runs against.

Every library of Lua 5.1 is here (base, `string`, `table`, `math`, `os`,
`io`, `coroutine`, `package`), with Lua 5.3's additions (`utf8`,
`table.pack`/`unpack`, `math.type`, integer division, ...). The 5.1-only
functions (`unpack`, `loadstring`, `table.getn`, `math.mod`, `string.gfind`,
...) are installed in 5.1 mode, which is what Warcraft III tooling uses.

Everything that leaves the interpreter goes through a `LuaHost`: files, the
current directory, environment variables, commands (`os.execute`,
`io.popen`), and `print` output. The default host allows no file access and
runs no command. `LuaHost(root=...)` gives a script one directory tree, with
optional mount points that make that tree appear at the absolute paths the
script expects (JassNewGenPack-era scripts build paths such as
`C:\\Program Files\\Warcraft III\\jassnewgenpack\\grimext\\...`). The
commands such scripts use to manage that tree (`mkdir`, `if not exist ...`,
`del`, `rmdir`, `dir /b`, `copy`, `move`) are interpreted inside the sandbox;
anything else is refused and recorded in `host.denied`. A real shell is never
started.
"""

import math
import os
import random as _random
import re
import time as _time

from .lua_vm import (
     LuaError, LuaTable, LuaFunction, PyFunction, LuaUserdata, LightUserdata, Coroutine,
     type_name, truthy, is_number, str_to_number, number_to_str,
     to_text, to_bytes_text, _INT_MIN, _INT_MAX, _wrap, set_weak_mode,
)
from . import lua_patterns
from .lua_patterns import PatternError


class LuaExit(Exception):
     """`os.exit()` was called."""

     def __init__(self, code):
          super().__init__(code)
          self.code = code


# --- host ---------------------------------------------------------------------

class LuaHost:
     """What a Lua script can reach outside the interpreter.

     root
          The directory the script may read (and, with `writable`, write).
          None allows no file access at all.
     writable
          Allow creating, changing and deleting files under `root`.
     cwd
          The directory relative paths start from; `root` by default.
     mounts
          `{virtual_path: real_dir}`: absolute paths the script uses that
          stand for directories inside `root` (or for other readable
          directories). Matching is case-insensitive and ignores the slash
          direction, as on Windows.
     read_only
          Extra directories the script may read but never write (lookup
          folders, a bundled library).
     environ
          What `os.getenv` sees. Nothing by default.
     output
          A callable receiving `print`/`io.write` text; by default it is
          collected in `self.output_lines`.
     allow_commands
          Let `os.execute`/`io.popen` interpret the built-in directory
          commands. On by default; nothing else ever runs.
     text_newline
          What a line break is in a file opened in text mode (no `b` in the
          mode): '\r\n' gives Windows' C library behaviour, where reading
          turns CRLF into LF and writing turns LF into CRLF; '\n' leaves the
          bytes alone, as on POSIX. The default follows the platform.
     """

     def __init__(self, root=None, writable=False, cwd=None, mounts=None, read_only=(),
                  environ=None, output=None, allow_commands=True, text_newline=None):
          self.text_newline = text_newline or ('\r\n' if os.name == 'nt' else '\n')
          self.root = os.path.abspath(root) if root else None
          self.writable = writable
          self.cwd = os.path.abspath(cwd) if cwd else self.root
          self.mounts = []
          for virtual, real in (mounts or {}).items():
               self.mounts.append((_norm_virtual(virtual), os.path.abspath(real)))
          self.mounts.sort(key=lambda m: -len(m[0]))
          self.read_only = [os.path.abspath(p) for p in read_only]
          self.environ = dict(environ or {})
          self._output = output
          self.output_lines = []
          self.allow_commands = allow_commands
          self.denied = []
          self.written = []
          self.removed = []

     # -- output --------------------------------------------------------------

     def write_output(self, text):
          if self._output is not None:
               self._output(text)
          else:
               self.output_lines.append(text)

     @property
     def output(self):
          return ''.join(self.output_lines)

     # -- paths ----------------------------------------------------------------

     def real_path(self, path):
          """Map a path as the script spells it to a real path, or None."""
          if not isinstance(path, str) or not path:
               return None
          text = to_text(path)
          virtual = _norm_virtual(text)
          for prefix, real in self.mounts:
               if virtual == prefix or virtual.startswith(prefix + '/'):
                    rest = text[len(prefix):].lstrip('/\\')
                    return os.path.normpath(os.path.join(real, *_split(rest)))
          if _is_absolute(text):
               if os.name == 'nt' or not re.match(r'^[A-Za-z]:', text):
                    return os.path.normpath(text)
               return None
          base = self.cwd
          if base is None:
               return None
          return os.path.normpath(os.path.join(base, *_split(text)))

     def _inside(self, real, roots):
          real_cmp = os.path.normcase(os.path.abspath(real))
          for root in roots:
               if root is None:
                    continue
               root_cmp = os.path.normcase(root)
               if real_cmp == root_cmp or real_cmp.startswith(root_cmp.rstrip('\\/') + os.sep):
                    return True
          return False

     def check(self, path, write=False):
          """The real path the script may use, or raise PermissionError."""
          real = self.real_path(path)
          if real is None:
               raise PermissionError(f'{to_text(path)}: Permission denied')
          roots = [self.root] + [real_dir for _, real_dir in self.mounts]
          if write:
               if not self.writable or not self._inside(real, roots):
                    self.denied.append(('write', to_text(path)))
                    raise PermissionError(f'{to_text(path)}: Permission denied')
          elif not self._inside(real, roots + self.read_only):
               self.denied.append(('read', to_text(path)))
               raise PermissionError(f'{to_text(path)}: Permission denied')
          return real

     # -- file operations (raise OSError) ---------------------------------------------

     def open(self, path, mode):
          write = any(c in mode for c in 'wa+')
          real = self.check(path, write=write)
          binary = mode.replace('b', '')
          if binary not in ('r', 'w', 'a', 'r+', 'w+', 'a+'):
               raise ValueError(f"invalid mode '{mode}'")
          handle = open(real, binary + 'b')
          if write:
               self.written.append(real)
          return handle

     def remove(self, path):
          real = self.check(path, write=True)
          if os.path.isdir(real):
               os.rmdir(real)
          else:
               os.remove(real)
          self.removed.append(real)

     def rename(self, old, new):
          src = self.check(old, write=True)
          dst = self.check(new, write=True)
          os.replace(src, dst)

     def exists(self, path):
          try:
               return os.path.exists(self.check(path))
          except PermissionError:
               return False

     def is_dir(self, path):
          try:
               return os.path.isdir(self.check(path))
          except PermissionError:
               return False

     def listdir(self, path):
          return sorted(os.listdir(self.check(path)))

     def makedirs(self, path):
          os.makedirs(self.check(path, write=True), exist_ok=True)

     def tmpname(self):
          if self.root is None or not self.writable:
               raise PermissionError('no writable directory for a temporary file')
          for i in range(1, 100000):
               name = os.path.join(self.root, f'lua_tmp_{i}')
               if not os.path.exists(name):
                    # Created at once, as mkstemp does, so the name is taken.
                    open(name, 'wb').close()
                    self.written.append(name)
                    return name
          raise OSError('no temporary name available')

     def getenv(self, name):
          return self.environ.get(name)

     # -- commands ------------------------------------------------------------------

     def execute(self, command):
          """Interpret a command (os.execute). Returns (exit code, output)."""
          if not self.allow_commands:
               self.denied.append(('execute', to_text(command)))
               return 1, ''
          return _Shell(self).run(to_text(command))


def _norm_virtual(path):
     return path.replace('\\', '/').rstrip('/').lower()


def _split(path):
     return [p for p in re.split(r'[\\/]+', path) if p]


def _is_absolute(path):
     return bool(re.match(r'^([A-Za-z]:[\\/]|[\\/])', path))


class _Shell:
     """The few cmd.exe commands map-tool scripts use to manage their own
     files, run against the sandbox. Anything else is refused."""

     def __init__(self, host):
          self.host = host
          self.out = []

     def run(self, command):
          command = command.strip()
          if not command:
               return 0, ''
          code = 0
          for part in _split_commands(command):
               code = self.one(part)
          return code, ''.join(self.out)

     def one(self, command):
          words = _shell_words(command)
          if not words:
               return 0
          head = words[0].lower()
          try:
               if head == 'if':
                    return self.if_(words[1:], command)
               if head in ('mkdir', 'md'):
                    for path in words[1:]:
                         self.host.makedirs(path)
                    return 0
               if head in ('del', 'erase'):
                    for path in [w for w in words[1:] if not w.startswith('/')]:
                         real = self.host.check(path, write=True)
                         if os.path.isfile(real):
                              self.host.remove(path)
                    return 0
               if head in ('rmdir', 'rd'):
                    recursive = any(w.lower() == '/s' for w in words[1:])
                    for path in [w for w in words[1:] if not w.startswith('/')]:
                         real = self.host.check(path, write=True)
                         if recursive:
                              import shutil
                              shutil.rmtree(real)
                         else:
                              os.rmdir(real)
                    return 0
               if head == 'dir':
                    paths = [w for w in words[1:] if not w.startswith('/')]
                    path = paths[0] if paths else '.'
                    for name in self.host.listdir(path):
                         self.out.append(name + '\n')
                    return 0
               if head in ('copy', 'move'):
                    paths = [w for w in words[1:] if not w.startswith('/')]
                    if len(paths) != 2:
                         return 1
                    src = self.host.check(paths[0])
                    dst = self.host.check(paths[1], write=True)
                    if os.path.isdir(dst):
                         dst = os.path.join(dst, os.path.basename(src))
                    import shutil
                    if head == 'copy':
                         shutil.copyfile(src, dst)
                    else:
                         self.host.check(paths[0], write=True)
                         shutil.move(src, dst)
                    return 0
               if head == 'echo':
                    self.out.append(command[4:].strip() + '\n')
                    return 0
               if head == 'cd' and len(words) == 2:
                    real = self.host.check(words[1])
                    if os.path.isdir(real):
                         self.host.cwd = real
                         return 0
                    return 1
          except (OSError, PermissionError):
               return 1
          self.host.denied.append(('execute', command))
          return 1

     def if_(self, words, command):
          negate = False
          if words and words[0].lower() == 'not':
               negate = True
               words = words[1:]
          if len(words) < 3 or words[0].lower() != 'exist':
               self.host.denied.append(('execute', command))
               return 1
          exists = self.host.exists(words[1])
          if exists != negate:
               rest = ' '.join(_quote(w) for w in words[2:])
               return self.one(rest)
          return 0


def _split_commands(command):
     parts = []
     current = []
     quoted = False
     i = 0
     while i < len(command):
          c = command[i]
          if c == '"':
               quoted = not quoted
          if not quoted and c == '&':
               parts.append(''.join(current))
               current = []
               i += 2 if command[i:i + 2] == '&&' else 1
               continue
          current.append(c)
          i += 1
     parts.append(''.join(current))
     return [p.strip() for p in parts if p.strip()]


def _shell_words(command):
     words = []
     current = []
     quoted = False
     started = False
     for c in command:
          if c == '"':
               quoted = not quoted
               started = True
               continue
          if c in ' \t' and not quoted:
               if started:
                    words.append(''.join(current))
                    current = []
                    started = False
               continue
          current.append(c)
          started = True
     if started:
          words.append(''.join(current))
     return words


def _quote(word):
     return f'"{word}"' if ' ' in word else word


# --- argument helpers -------------------------------------------------------------

def _arg(args, i):
     return args[i] if i < len(args) else None


def _bad(i, fname, message):
     return LuaError(f"bad argument #{i + 1} to '{fname}' ({message})")


def _typeerror(args, i, fname, expected):
     got = type_name(args[i]) if i < len(args) else 'no value'
     if i < len(args) and type(args[i]) is LightUserdata:
          got = 'light userdata'
     elif i < len(args):
          mt = getattr(args[i], 'metatable', None)
          name = mt.get('__name') if isinstance(mt, LuaTable) else None
          if type(name) is str:
               got = name                     # 5.3: a metatable's __name
     return _bad(i, fname, f'{expected} expected, got {got}')


class _Lib:
     def __init__(self, vm, host):
          self.vm = vm
          self.host = host
          self.version = vm.version

     def c_call(self):
          """A C-call boundary: Lua code called from inside cannot yield
          (Lua's library functions without a continuation)."""
          import contextlib
          co = self.vm.current_coroutine

          @contextlib.contextmanager
          def boundary():
               if co is not None:
                    co.c_calls = getattr(co, 'c_calls', 0) + 1
               try:
                    yield
               finally:
                    if co is not None:
                         co.c_calls -= 1
          return boundary()

     # -- checks ---------------------------------------------------------------

     def check_any(self, args, i, fname):
          if i >= len(args):
               raise _bad(i, fname, 'value expected')
          return args[i]

     def check_table(self, args, i, fname):
          v = _arg(args, i)
          if type(v) is not LuaTable:
               raise _typeerror(args, i, fname, 'table')
          return v

     def check_str(self, args, i, fname):
          v = _arg(args, i)
          t = type(v)
          if t is str:
               return v
          if t is int or t is float:
               return number_to_str(v, self.version)
          raise _typeerror(args, i, fname, 'string')

     def opt_str(self, args, i, fname, default):
          if _arg(args, i) is None:
               return default
          return self.check_str(args, i, fname)

     def check_num(self, args, i, fname):
          v = _arg(args, i)
          t = type(v)
          if t is int or t is float:
               return v
          if t is str:
               n = str_to_number(v)
               if n is not None:
                    return n
          raise _typeerror(args, i, fname, 'number')

     def opt_num(self, args, i, fname, default):
          if _arg(args, i) is None:
               return default
          return self.check_num(args, i, fname)

     def check_int(self, args, i, fname):
          v = self.check_num(args, i, fname)
          if type(v) is int:
               return v
          if v != v or v in (math.inf, -math.inf):
               if self.version == '5.1':
                    return 0
               raise _bad(i, fname, 'number has no integer representation')
          if self.version == '5.1':
               return int(v)
          if v.is_integer() and -9223372036854775808.0 <= v < 9223372036854775808.0:
               return int(v)
          raise _bad(i, fname, 'number has no integer representation')

     def opt_int(self, args, i, fname, default):
          if _arg(args, i) is None:
               return default
          return self.check_int(args, i, fname)

     def check_fn(self, args, i, fname):
          v = _arg(args, i)
          if type(v) not in (LuaFunction, PyFunction):
               raise _typeerror(args, i, fname, 'function')
          return v

     def int_result(self, v):
          """A float with an integral value as an int (math.floor & co)."""
          if type(v) is float and v.is_integer() and _INT_MIN <= v <= _INT_MAX:
               return int(v)
          return v


def install(vm, host=None):
     """Install the standard library into `vm.globals`."""
     host = host or LuaHost()
     vm.host = host
     lib = _Lib(vm, host)
     g = vm.globals
     g.set('_G', g)
     g.set('_VERSION', f'Lua {vm.version}')
     _install_base(vm, lib, g)
     string = _install_string(vm, lib)
     g.set('string', string)
     meta = LuaTable()
     meta.set('__index', string)
     vm.string_meta = meta
     g.set('table', _install_table(vm, lib))
     g.set('math', _install_math(vm, lib))
     g.set('os', _install_os(vm, lib))
     g.set('io', _install_io(vm, lib))
     g.set('coroutine', _install_coroutine(vm, lib))
     g.set('debug', _install_debug(vm, lib))
     if vm.version != '5.1':
          g.set('utf8', _install_utf8(vm, lib))
     _install_package(vm, lib, g)          # after every library, for package.loaded
     return host


def _fn(table, name, fn):
     table.set(name, PyFunction(fn, name))


# --- base ---------------------------------------------------------------------------

def _install_base(vm, lib, g):
     def print_(args):
          # Lua's print calls whatever the global 'tostring' is now
          tostring = vm.globals.hash.get('tostring')
          if tostring is builtin_tostring[0]:
               texts = [vm.tostring(a) for a in args]
          else:
               texts = []
               for a in args:
                    r = vm.call(tostring, [a], "global 'tostring'")
                    s = r[0] if r else None
                    if type(s) is not str:
                         if type(s) in (int, float):
                              s = vm.tostring(s)
                         else:
                              raise LuaError("'tostring' must return a string to 'print'")
                    texts.append(s)
          vm.host.write_output('\t'.join(texts) + '\n')

     builtin_tostring = [None]

     def type_(args):
          lib.check_any(args, 0, 'type')
          return [type_name(args[0])]

     def tostring(args):
          lib.check_any(args, 0, 'tostring')
          return [vm.tostring(args[0])]

     def tonumber(args):
          v = _arg(args, 0)
          base = _arg(args, 1)
          if base is None:
               if is_number(v):
                    return [v]
               if type(v) is str:
                    return [str_to_number(v)]
               lib.check_any(args, 0, 'tonumber')
               return [None]
          base = lib.check_int(args, 1, 'tonumber')
          if not 2 <= base <= 36:
               raise _bad(1, 'tonumber', 'base out of range')
          text = lib.check_str(args, 0, 'tonumber').strip(' \t\n\r\f\v').lower()
          if text.startswith('+') and vm.version != '5.1':
               text = text[1:]
               negative = False
          else:
               negative = text.startswith('-')
          if negative:
               text = text[1:]
          if not text:
               return [None]
          value = 0
          for c in text:
               digit = int(c, 36) if c.isalnum() else 99
               if digit >= base:
                    return [None]
               value = value * base + digit
          value = -value if negative else value
          return [_wrap(value) if vm.version != '5.1' else value]

     raw_ipairs = vm.version == '5.1'

     def ipairs_iterate(a):
          i = a[1] + 1
          obj = a[0]
          if raw_ipairs and type(obj) is LuaTable:
               v = obj.hash.get(i)
          else:
               v = vm.index(obj, i)
          return [None] if v is None else [i, v]

     # one iterator function for every call, as Lua returns (ipairs{} == ipairs{})
     ipairs_iterator = PyFunction(ipairs_iterate, 'ipairs_iterator')

     def ipairs(args):
          t = lib.check_any(args, 0, 'ipairs')
          return [ipairs_iterator, t, 0]

     def pairs(args):
          t = lib.check_any(args, 0, 'pairs')
          if vm.version != '5.1':
               handler = vm._metamethod(t, '__pairs')
               if handler is not None:
                    return vm.call(handler, [t])[:3]
          if type(t) is not LuaTable:
               raise _typeerror(args, 0, 'pairs', 'table')
          return [vm.next_function, t, None]

     def select(args):
          n = _arg(args, 0)
          if n == '#':
               return [len(args) - 1]
          i = lib.check_int(args, 0, 'select')
          if i < 0:
               i = len(args) + i
               if i < 1:
                    raise _bad(0, 'select', 'index out of range')
               return args[i:]
          if i == 0:
               raise _bad(0, 'select', 'index out of range')
          return args[i:]

     def rawget(args):
          t = lib.check_table(args, 0, 'rawget')
          return [t.get(_arg(args, 1))]

     def rawset(args):
          t = lib.check_table(args, 0, 'rawset')
          t.set(_arg(args, 1), _arg(args, 2))
          return [t]

     def rawequal(args):
          a = _arg(args, 0)
          b = _arg(args, 1)
          if a is b:
               return [type(a) is not float or a == a]
          if type(a) is bool or type(b) is bool:
               return [False]
          if (is_number(a) and is_number(b)) or (type(a) is str and type(b) is str):
               return [a == b]
          return [False]

     def rawlen(args):
          v = _arg(args, 0)
          if type(v) is LuaTable:
               return [v.length()]
          if type(v) is str:
               return [len(v)]
          raise _bad(0, 'rawlen', 'table or string expected')

     def setmetatable(args):
          t = lib.check_table(args, 0, 'setmetatable')
          mt = _arg(args, 1)
          if mt is not None and type(mt) is not LuaTable:
               raise _bad(1, 'setmetatable', 'nil or table expected')
          if t.metatable is not None and t.metatable.get('__metatable') is not None:
               raise LuaError('cannot change a protected metatable')
          t.metatable = mt
          set_weak_mode(t, mt.get('__mode') if mt is not None else None)
          return [t]

     def getmetatable(args):
          mt = vm.getmetatable(_arg(args, 0))
          if mt is None:
               return [None]
          protected = mt.get('__metatable')
          return [protected if protected is not None else mt]

     def assert_(args):
          if not args or not truthy(args[0]):
               if len(args) > 1:
                    message = args[1]
                    if vm.version == '5.1' and type(message) is str:
                         raise LuaError(message)
                    raise LuaError(message, level=0)
               raise LuaError('assertion failed!')
          return args

     def error(args):
          value = _arg(args, 0)
          level = lib.opt_int(args, 1, 'error', 1)
          raise LuaError(value, level=level if type(value) is str and level > 0 else 0)

     def pcall(args):
          fn = lib.check_any(args, 0, 'pcall')
          ok, result = vm.pcall(fn, args[1:])
          if ok:
               return [True] + result
          return [False, result.value]

     def xpcall(args):
          fn = _arg(args, 0)
          handler = _arg(args, 1)
          extra = args[2:] if vm.version != '5.1' else []
          ok, result = vm.pcall(fn, extra)
          if ok:
               return [True] + result
          saved = vm.stack
          if result.stack is not None:
               vm.stack = result.stack
          try:
               ok, handled = vm.pcall(handler, [result.value])
          finally:
               vm.stack = saved
          if not ok:
               return [False, 'error in error handling']
          return [False] + handled[:1]

     def next_(args):
          return vm._next(args)

     def unpack(args):
          t = lib.check_table(args, 0, 'unpack')
          i = lib.opt_int(args, 1, 'unpack', 1)
          j = lib.opt_int(args, 2, 'unpack', None)
          if j is None:
               j = vm.length(t)
          if j - i >= 8000000:
               raise LuaError('too many results to unpack')
          return [vm.index(t, k) if t.metatable else t.hash.get(k) for k in range(i, j + 1)]

     gc_options = ({'stop', 'restart', 'collect', 'count', 'step', 'setpause',
                    'setstepmul'} if vm.version == '5.1' else
                   {'stop', 'restart', 'collect', 'count', 'step', 'setpause',
                    'setstepmul', 'isrunning', 'generational', 'incremental'})

     gc_state = {'running': True}

     def collectgarbage(args):
          option = lib.opt_str(args, 0, 'collectgarbage', 'collect')
          if option not in gc_options:
               raise _bad(0, 'collectgarbage', f"invalid option '{to_text(option)}'")
          if option == 'collect':
               import gc
               gc.collect()
          if option == 'stop':
               gc_state['running'] = False
          elif option == 'restart':
               gc_state['running'] = True
          if option == 'count':
               return [0.0, 0] if vm.version == '5.1' else [0.0]
          if option == 'isrunning':
               return [gc_state['running']]
          if option == 'step':
               import gc
               gc.collect()
               return [True]           # a step always finishes a full cycle here
          return [0]

     def load(args):
          chunk = _arg(args, 0)
          name = lib.opt_str(args, 1, 'load', None)
          if type(chunk) is str:
               text = chunk
               name = name if name is not None else chunk
          else:
               fn = lib.check_fn(args, 0, 'load')
               pieces = []
               while True:
                    try:
                         with lib.c_call():
                              r = vm.call(fn, [])
                    except LuaError as error:
                         if vm.version == '5.1':
                              raise
                         return [None, error.value]
                    piece = r[0] if r else None
                    if piece is None or piece == '':
                         break
                    if type(piece) is not str:
                         return [None, 'reader function must return a string']
                    pieces.append(piece)
               text = ''.join(pieces)
               name = name if name is not None else '=(load)'
          if vm.version == '5.1':
               return _load_text(vm, text, name)
          # 5.2+: load(chunk, name, mode, env); an env given, even nil,
          # becomes the chunk's _ENV
          mode = lib.opt_str(args, 2, 'load', 'bt')
          has_env = len(args) > 3
          return _load_text(vm, text, name, mode, args[3] if has_env else None, has_env)

     def loadstring(args):
          text = lib.check_str(args, 0, 'loadstring')
          name = lib.opt_str(args, 1, 'loadstring', None)
          return _load_text(vm, text, name if name is not None else text)

     def loadfile(args):
          path = lib.opt_str(args, 0, 'loadfile', None)
          if path is None:
               return [None, 'reading from stdin is not supported']
          result = _load_file(vm, path)
          if vm.version != '5.1' and len(args) > 2 and result[0] is not None:
               result[0].upvals[0].v = args[2]          # loadfile(path, mode, env)
          return result

     def dofile(args):
          path = lib.opt_str(args, 0, 'dofile', None)
          if path is None:
               raise LuaError('reading from stdin is not supported')
          r = _load_file(vm, path)
          if r[0] is None:
               raise LuaError(r[1])
          return vm.call(r[0], [])

     def env_target(args, fname):
          f = _arg(args, 0)
          if type(f) in (LuaFunction, PyFunction):
               return f
          level = 1 if f is None else lib.check_int(args, 0, fname)
          if level < 0:
               raise _bad(0, fname, 'level must be non-negative')
          if level == 0:
               return 0
          if level > len(vm.stack):
               raise _bad(0, fname, 'invalid level')
          return vm.stack[-level].fn

     def getfenv(args):
          target = env_target(args, 'getfenv')
          if target == 0 or type(target) is PyFunction:
               return [vm.globals]
          return [target.env]

     def setfenv(args):
          target = env_target(args, 'setfenv')
          t = lib.check_table(args, 1, 'setfenv')
          if target == 0:
               vm.globals = t
               return []
          if type(target) is not LuaFunction:
               raise LuaError("'setfenv' cannot change environment of given object")
          target.env = t
          return [target]

     for name, fn in [
          ('print', print_), ('type', type_), ('tostring', tostring),
          ('tonumber', tonumber), ('ipairs', ipairs), ('pairs', pairs),
          ('select', select), ('rawget', rawget), ('rawset', rawset),
          ('rawequal', rawequal), ('setmetatable', setmetatable),
          ('getmetatable', getmetatable), ('assert', assert_), ('error', error),
          ('pcall', pcall), ('xpcall', xpcall), ('collectgarbage', collectgarbage),
          ('load', load), ('loadfile', loadfile), ('dofile', dofile),
     ]:
          _fn(g, name, fn)
     g.set('next', vm.next_function)
     builtin_tostring[0] = g.get('tostring')

     def newproxy(args):
          proxy = LuaUserdata()
          proto = _arg(args, 0)
          if proto is True:
               proxy.metatable = LuaTable()
          elif isinstance(proto, LuaUserdata):
               proxy.metatable = proto.metatable
          elif proto not in (None, False):
               raise _bad(0, 'newproxy', 'boolean or proxy expected')
          return [proxy]

     if vm.version == '5.1':
          _fn(g, 'newproxy', newproxy)
          _fn(g, 'unpack', unpack)
          _fn(g, 'loadstring', loadstring)
          _fn(g, 'getfenv', getfenv)
          _fn(g, 'setfenv', setfenv)
          _fn(g, 'gcinfo', lambda args: [0])
     else:
          _fn(g, 'rawlen', rawlen)
     lib.unpack = unpack


def chunk_id(source):
     """The short name Lua shows for a chunk in messages (luaO_chunkid)."""
     if source.startswith('='):
          return source[1:60]
     if source.startswith('@'):
          name = source[1:]
          if len(name) > 59:
               name = '...' + name[-56:]
          return name
     length = len(re.split(r'[\n\r]', source, maxsplit=1)[0])
     limit = 60 - 16
     if length > limit:
          length = limit
     if length < len(source):
          return f'[string "{source[:length]}..."]'
     return f'[string "{source}"]'


def _load_text(vm, text, name, mode='bt', env=None, has_env=False):
     m = re.match(r'^\x1bLuaW(\d+)$', text)
     binary = text.startswith('\x1b')
     if binary and 'b' not in mode:
          return [None, f"attempt to load a binary chunk (mode is '{mode}')"]
     if not binary and 't' not in mode:
          return [None, f"attempt to load a text chunk (mode is '{mode}')"]
     if m and int(m.group(1)) < len(vm.dumps):
          from .lua_vm import _Cell
          proto = vm.dumps[int(m.group(1))]
          cells = [_Cell() for _ in range(max(len(proto.upvals), len(proto.upnames)))]
          if cells and vm.version != '5.1':
               # as Lua 5.2+ does, the first upvalue gets the environment
               cells[0].v = env if has_env else vm.globals
          return [LuaFunction(proto, cells, vm)]
     try:
          fn = vm.compile(text, chunk_id(name), binary=True, source_name=name,
                          skip_comment_line=False)
          if has_env and fn.upvals:
               fn.upvals[0].v = env
          return [fn]
     except LuaError as error:
          return [None, error.value]


def _load_file(vm, path):
     try:
          real = vm.host.check(path)
          with open(real, 'rb') as handle:
               text = handle.read().decode('latin-1')
     except PermissionError:
          return [None, f'cannot open {path}: Permission denied']
     except OSError as error:
          return [None, f'cannot open {path}: {error.strerror or "No such file or directory"}']
     try:
          return [vm.compile(text, to_text(path), binary=True)]
     except LuaError as error:
          return [None, error.value]


# --- string -------------------------------------------------------------------------

def _posrelat(pos, length):
     if pos >= 0:
          return pos
     if -pos > length:
          return 0
     return length + pos + 1


def _install_string(vm, lib):
     string = LuaTable()

     def wrap_pattern(fn):
          def run(args):
               try:
                    return fn(args)
               except PatternError as error:
                    raise LuaError(str(error))
          return run

     def len_(args):
          return [len(lib.check_str(args, 0, 'len'))]

     def sub(args):
          s = lib.check_str(args, 0, 'sub')
          n = len(s)
          i = _posrelat(lib.opt_int(args, 1, 'sub', 1), n)
          j = _posrelat(lib.opt_int(args, 2, 'sub', -1), n)
          if i < 1:
               i = 1
          if j > n:
               j = n
          return [s[i - 1:j] if i <= j else '']

     def upper(args):
          s = lib.check_str(args, 0, 'upper')
          return [''.join(chr(ord(c) - 32) if 'a' <= c <= 'z' else c for c in s)]

     def lower(args):
          s = lib.check_str(args, 0, 'lower')
          return [''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in s)]

     def rep(args):
          s = lib.check_str(args, 0, 'rep')
          n = lib.check_int(args, 1, 'rep')
          sep = lib.opt_str(args, 2, 'rep', '')
          if n <= 0:
               return ['']
          if (len(s) + len(sep)) * n > 100_000_000:
               raise LuaError('resulting string too large')
          return [sep.join([s] * n) if sep else s * n]

     def reverse(args):
          return [lib.check_str(args, 0, 'reverse')[::-1]]

     def byte(args):
          s = lib.check_str(args, 0, 'byte')
          n = len(s)
          i = _posrelat(lib.opt_int(args, 1, 'byte', 1), n)
          j = _posrelat(lib.opt_int(args, 2, 'byte', i), n)
          if i < 1:
               i = 1
          if j > n:
               j = n
          return [ord(c) for c in s[i - 1:j]] if i <= j else []

     def char(args):
          out = []
          for k in range(len(args)):
               c = lib.check_int(args, k, 'char')
               if not 0 <= c <= 255:
                    raise _bad(k, 'char', 'value out of range' if vm.version != '5.1'
                               else 'invalid value')
               out.append(chr(c))
          return [''.join(out)]

     def find_impl(args, fname, find_mode):
          s = lib.check_str(args, 0, fname)
          p = lib.check_str(args, 1, fname)
          n = len(s)
          init = _posrelat(lib.opt_int(args, 2, fname, 1), n)
          if init < 1:
               init = 1
          plain = truthy(_arg(args, 3)) if find_mode else False
          r = lua_patterns.find(s, p, init - 1, plain, find_mode)
          return [None] if r is None else r

     def find(args):
          return find_impl(args, 'find', True)

     def match(args):
          return find_impl(args, 'match', False)

     def gmatch(args):
          s = lib.check_str(args, 0, 'gmatch')
          p = lib.check_str(args, 1, 'gmatch')
          iterator = lua_patterns.gmatch(s, p, vm.version != '5.1')

          def step(a):
               try:
                    return next(iterator)
               except StopIteration:
                    return [None]
               except PatternError as error:
                    raise LuaError(str(error))
          return [PyFunction(step, 'gmatch_iterator', nups=3)]

     def gsub(args):
          s = lib.check_str(args, 0, 'gsub')
          p = lib.check_str(args, 1, 'gsub')
          repl = _arg(args, 2)
          max_n = lib.opt_int(args, 3, 'gsub', None)
          t = type(repl)
          if t is int or t is float:
               repl = number_to_str(repl, vm.version)
               t = str
          if t is str:
               replacement = repl
          elif t is LuaTable or t in (LuaFunction, PyFunction):
               def replacement(captures):
                    if t is LuaTable:
                         value = vm.index(repl, captures[0])
                    else:
                         r = vm.call(repl, captures)
                         value = r[0] if r else None
                    if value is None or value is False:
                         return None
                    if type(value) is str:
                         return value
                    if is_number(value):
                         return number_to_str(value, vm.version)
                    raise LuaError(f'invalid replacement value (a {type_name(value)})')
          else:
               raise _typeerror(args, 2, 'gsub', 'string/function/table')
          with lib.c_call():
               text, count = lua_patterns.gsub(s, p, replacement, max_n, vm.version != '5.1')
          return [text, count]

     def format_(args):
          return [_format(vm, lib, args)]

     def dump(args):
          fn = lib.check_fn(args, 0, 'dump')
          if type(fn) is not LuaFunction:
               raise LuaError('unable to dump given function')
          # There is no bytecode to write: the "dump" is a key into a table
          # of compiled functions, which load() recognizes.
          key = len(vm.dumps)
          vm.dumps.append(fn.proto)
          return [f'\x1bLuaW{key}']

     for name, fn in [
          ('len', len_), ('sub', sub), ('upper', upper), ('lower', lower),
          ('rep', rep), ('reverse', reverse), ('byte', byte), ('char', char),
          ('find', wrap_pattern(find)), ('match', wrap_pattern(match)),
          ('gmatch', wrap_pattern(gmatch)), ('gsub', wrap_pattern(gsub)),
          ('format', format_), ('dump', dump),
     ]:
          _fn(string, name, fn)
     if vm.version == '5.1':
          string.set('gfind', string.get('gmatch'))
     else:
          from . import lua_strpack
          for name in ('pack', 'unpack', 'packsize'):
               _fn(string, name, (lambda f: lambda args: f(lib, args))(getattr(lua_strpack, name)))
     return string


_FORMAT_SPEC = re.compile(r'([-+ #0]*)(\d*)(?:\.(\d*))?([a-zA-Z%])?')


def _format(vm, lib, args):
     fmt = lib.check_str(args, 0, 'format')
     out = []
     i = 0
     n = len(fmt)
     argi = 1
     while i < n:
          c = fmt[i]
          if c != '%':
               j = fmt.find('%', i)
               if j < 0:
                    j = n
               out.append(fmt[i:j])
               i = j
               continue
          i += 1
          if i < n and fmt[i] == '%':
               out.append('%')
               i += 1
               continue
          m = _FORMAT_SPEC.match(fmt, i)
          flags, width, precision, conv = m.group(1), m.group(2), m.group(3), m.group(4)
          i = m.end()
          if len(flags) >= 6:
               raise LuaError('invalid format (repeated flags)')
          if len(width) > 2 or (precision is not None and len(precision) > 2):
               raise LuaError("invalid format (width or precision too long)")
          if conv is None:
               raise LuaError(f"invalid conversion '%{fmt[m.start():i + 1]}' to 'format'")
          spec = '%' + flags + width + ('.' + precision if precision is not None else '')
          if argi >= len(args):
               raise _bad(argi, 'format', 'no value')
          index = argi
          argi += 1
          if conv in 'di':
               value = lib.check_int(args, index, 'format')
               out.append((spec + 'd') % value)
          elif conv == 'u':
               value = lib.check_int(args, index, 'format')
               out.append((spec + 'd') % (value & 0xFFFFFFFFFFFFFFFF if value < 0 else value))
          elif conv == 'c':
               out.append(chr(lib.check_int(args, index, 'format') & 0xFF))
          elif conv in 'xXo':
               value = lib.check_int(args, index, 'format')
               if value < 0:
                    value &= 0xFFFFFFFFFFFFFFFF
               out.append((spec + conv) % value)
          elif conv in 'eEfFgG':
               value = float(lib.check_num(args, index, 'format'))
               if value != value or value in (math.inf, -math.inf):
                    text = ('nan' if value != value else 'inf') if conv.islower() \
                         else ('NAN' if value != value else 'INF')
                    if value == -math.inf:
                         text = '-' + text
                    elif '+' in flags:
                         text = '+' + text
                    out.append(('%' + flags.replace('0', '') + width + 's') % text)
               else:
                    out.append((spec + conv) % value)
          elif conv in 'aA':
               value = float(lib.check_num(args, index, 'format'))
               out.append(_format_hexfloat(value, flags, width, precision, conv == 'A'))
          elif conv == 'q':
               out.append(_quoted(vm, args[index]))
          elif conv == 's':
               s = vm.tostring(args[index])
               if precision is None and not width:
                    out.append(s)
               else:
                    if '\0' in s and vm.version != '5.1':
                         raise _bad(index, 'format', 'string contains zeros')
                    out.append((spec + 's') % s)
          else:
               raise LuaError(f"invalid option '%{conv}' to 'format'")
     return ''.join(out)


def _format_hexfloat(value, flags, width, precision, upper):
     """C's %a: 0x1.8p+3, with printf's flags, width and precision."""
     if value != value or value in (math.inf, -math.inf):
          body = 'nan' if value != value else 'inf'
          sign = '-' if math.copysign(1, value) < 0 else ('+' if '+' in flags else
                                                          (' ' if ' ' in flags else ''))
          text = sign + body
          zero = False
     else:
          sign = '-' if math.copysign(1, value) < 0 else ('+' if '+' in flags else
                                                          (' ' if ' ' in flags else ''))
          value = abs(value)
          if value == 0:
               lead, mantissa, exponent = 0, 0, 0
          else:
               hexed = value.hex()                     # 0x1.8000000000000p+3
               head, exp_text = hexed[2:].split('p')
               lead_text, frac_text = head.split('.')
               lead = int(lead_text)
               mantissa = int(frac_text, 16)           # 13 hex digits, 52 bits
               exponent = int(exp_text)
          if precision is None:
               digits = f'{mantissa:013x}'.rstrip('0') if mantissa else ''
          else:
               keep = int(precision or 0)
               if keep < 13:
                    shift = 4 * (13 - keep)
                    q, r = divmod(mantissa, 1 << shift)
                    half = 1 << (shift - 1)
                    if r > half or (r == half and q & 1):
                         q += 1
                    if q >> (4 * keep):                 # carried into the lead digit
                         q -= 1 << (4 * keep)
                         lead += 1
                    digits = f'{q:0{keep}x}' if keep else ''
               else:
                    digits = f'{mantissa:013x}' + '0' * (keep - 13)
          point = '.' if digits or '#' in flags else ''
          text = f'{sign}0x{lead}{point}{digits}p{"-" if exponent < 0 else "+"}{abs(exponent)}'
          zero = '0' in flags and '-' not in flags
     size = int(width) if width else 0
     if len(text) < size:
          if '-' in flags:
               text = text + ' ' * (size - len(text))
          elif zero:
               prefix = len(sign) + 2
               text = text[:prefix] + '0' * (size - len(text)) + text[prefix:]
          else:
               text = ' ' * (size - len(text)) + text
     return text.upper() if upper else text


def _quoted(vm, value):
     if type(value) is not str:
          if vm.version != '5.1' and type(value) is int:
               # mininteger has no decimal literal form; Lua 5.3 writes it in hex
               return '0x8000000000000000' if value == -(1 << 63) else str(value)
          if vm.version != '5.1' and type(value) is float:
               if value == math.inf:
                    return '1e9999'
               if value == -math.inf:
                    return '-1e9999'
               if value != value:
                    return '(0/0)'
               if value.is_integer() and abs(value) < 1e16:
                    return vm.tostring(value)       # '%q' keeps it a float: 3.0
               return value.hex()
          if is_number(value) or value is None or type(value) is bool:
               return vm.tostring(value)
          raise LuaError("bad argument to 'format' (value has no literal form)")
     out = ['"']
     for i, c in enumerate(value):
          if c in '"\\':
               out.append('\\' + c)
          elif c == '\n':
               out.append('\\\n')
          elif c == '\r':
               out.append('\\r')
          elif c == '\0':
               nxt = value[i + 1] if i + 1 < len(value) else ''
               out.append('\\000' if nxt.isdigit() or vm.version == '5.1' else '\\0')
          elif ord(c) < 32 or ord(c) == 127:
               if vm.version == '5.1':
                    out.append(c)
               else:
                    nxt = value[i + 1] if i + 1 < len(value) else ''
                    out.append(f'\\{ord(c):03d}' if nxt.isdigit() else f'\\{ord(c)}')
          else:
               out.append(c)
     out.append('"')
     return ''.join(out)


# --- table --------------------------------------------------------------------------

def _install_table(vm, lib):
     def table_length(t):
          n = vm.length(t)
          if type(n) is int:
               return n
          if vm.version != '5.1':
               if type(n) is str:
                    n = str_to_number(n)
               if type(n) is float and n.is_integer():
                    return int(n)
               if type(n) is not int:
                    raise LuaError('object length is not an integer')
          return n

     table = LuaTable()

     def get(t, i):
          return t.hash.get(i) if t.metatable is None else vm.index(t, i)

     def put(t, i, v):
          if t.metatable is None:
               t.set(i, v)
          else:
               vm.setindex(t, i, v)

     def insert(args):
          t = lib.check_table(args, 0, 'insert')
          n = table_length(t)
          if len(args) == 2:
               put(t, n + 1, args[1])
               return []
          if len(args) != 3:
               raise LuaError("wrong number of arguments to 'insert'")
          pos = lib.check_int(args, 1, 'insert')
          if vm.version != '5.1' and not 1 <= pos <= n + 1:
               raise _bad(1, 'insert', 'position out of bounds')
          for k in range(n, pos - 1, -1):
               put(t, k + 1, get(t, k))
          put(t, pos, args[2])
          return []

     def remove(args):
          t = lib.check_table(args, 0, 'remove')
          n = table_length(t)
          if len(args) < 2 or args[1] is None and vm.version == '5.1':
               if n == 0:
                    if vm.version == '5.1':
                         return []
                    value = get(t, 0)
                    put(t, 0, None)
                    return [value]
               value = get(t, n)
               put(t, n, None)
               return [value]
          pos = lib.check_int(args, 1, 'remove')
          if n == 0 and pos in (0, n):
               value = get(t, pos)
               put(t, pos, None)
               return [value]
          if vm.version == '5.1' and not 1 <= pos <= n:
               return []
          if vm.version != '5.1' and n + 1 != pos and not 1 <= pos <= n + 1:
               raise _bad(1, 'remove', 'position out of bounds')
          value = get(t, pos)
          for k in range(pos, n):
               put(t, k, get(t, k + 1))
          if pos <= n:
               put(t, n, None)
          return [value]

     def concat(args):
          t = lib.check_table(args, 0, 'concat')
          sep = lib.opt_str(args, 1, 'concat', '')
          i = lib.opt_int(args, 2, 'concat', 1)
          j = lib.opt_int(args, 3, 'concat', None)
          if j is None:
               j = table_length(t)
          parts = []
          for k in range(i, j + 1):
               v = get(t, k)
               if type(v) is str:
                    parts.append(v)
               elif is_number(v):
                    parts.append(number_to_str(v, vm.version))
               else:
                    raise LuaError(f"invalid value (at index {k}) in table for 'concat'")
          return [sep.join(parts)]

     def sort(args):
          import functools
          t = lib.check_table(args, 0, 'sort')
          comp = _arg(args, 1)
          if comp is not None and type(comp) not in (LuaFunction, PyFunction):
               raise _typeerror(args, 1, 'sort', 'function')
          n = table_length(t)
          if n > 1 and n >= 0x7FFFFFFF and vm.version != '5.1':
               raise _bad(0, 'sort', 'array too big')
          values = [get(t, k) for k in range(1, n + 1)]
          if comp is None:
               def less(a, b):
                    return vm.lt(a, b)
          else:
               def less(a, b):
                    r = vm.call(comp, [a, b])
                    return truthy(r[0] if r else None)

          def cmp(a, b):
               if less(a, b):
                    return -1
               if less(b, a):
                    return 1
               return 0
          with lib.c_call():
               values.sort(key=functools.cmp_to_key(cmp))
          for k, v in enumerate(values, 1):
               put(t, k, v)
          return []

     def pack(args):
          t = LuaTable.from_list(list(args))
          t.set('n', len(args))
          return [t]

     def getn(args):
          return [vm.length(lib.check_table(args, 0, 'getn'))]

     def maxn(args):
          t = lib.check_table(args, 0, 'maxn')
          best = 0
          for k in t.hash:
               if is_number(k) and k > best:
                    best = k
          return [best]

     def foreach(args):
          t = lib.check_table(args, 0, 'foreach')
          fn = lib.check_fn(args, 1, 'foreach')
          for k, v in t.items():
               r = vm.call(fn, [k, v])
               if r and r[0] is not None:
                    return r[:1]
          return []

     def foreachi(args):
          t = lib.check_table(args, 0, 'foreachi')
          fn = lib.check_fn(args, 1, 'foreachi')
          for k in range(1, table_length(t) + 1):
               r = vm.call(fn, [k, get(t, k)])
               if r and r[0] is not None:
                    return r[:1]
          return []

     def setn(args):
          raise LuaError("'setn' is obsolete")

     def move(args):
          a1 = lib.check_table(args, 0, 'move')
          f = lib.check_int(args, 1, 'move')
          e = lib.check_int(args, 2, 'move')
          t = lib.check_int(args, 3, 'move')
          a2 = _arg(args, 4) if _arg(args, 4) is not None else a1
          if e >= f:
               # Lua 5.3's tmove checks
               if not (f > 0 or e < _INT_MAX + f):
                    raise _bad(2, 'move', 'too many elements to move')
               n = e - f + 1
               if t > _INT_MAX - n + 1:
                    raise _bad(3, 'move', 'destination wrap around')
               if t > e or t <= f or a1 is not a2:
                    for k in range(0, n):
                         vm.tick()
                         put(a2, t + k, get(a1, f + k))
               else:
                    for k in range(n - 1, -1, -1):
                         vm.tick()
                         put(a2, t + k, get(a1, f + k))
          return [a2]

     for name, fn in [('insert', insert), ('remove', remove), ('concat', concat),
                      ('sort', sort)]:
          _fn(table, name, fn)
     if vm.version == '5.1':
          for name, fn in [('getn', getn), ('maxn', maxn), ('foreach', foreach),
                           ('foreachi', foreachi), ('setn', setn)]:
               _fn(table, name, fn)
     else:
          _fn(table, 'pack', pack)
          _fn(table, 'unpack', lib.unpack)
          _fn(table, 'move', move)
     return table


# --- math ----------------------------------------------------------------------------

def _install_math(vm, lib):
     m = LuaTable()
     rng = _random.Random(0)
     v51 = vm.version == '5.1'

     def unary(name, fn, integral=False):
          def run(args):
               x = lib.check_num(args, 0, name)
               try:
                    r = fn(x)
               except (ValueError, OverflowError):
                    r = math.nan
               return [lib.int_result(r) if integral else float(r)]
          _fn(m, name, run)

     def floor(args):
          x = lib.check_num(args, 0, 'floor')
          if type(x) is int:
               return [x]
          if x != x or x in (math.inf, -math.inf):
               return [x]
          r = math.floor(x)
          return [r if _INT_MIN <= r <= _INT_MAX else float(r)]

     def ceil(args):
          x = lib.check_num(args, 0, 'ceil')
          if type(x) is int:
               return [x]
          if x != x or x in (math.inf, -math.inf):
               return [x]
          r = math.ceil(x)
          return [r if _INT_MIN <= r <= _INT_MAX else float(r)]

     def abs_(args):
          x = lib.check_num(args, 0, 'abs')
          if type(x) is int:
               return [_wrap(abs(x))]
          return [abs(x)]

     def fmod(args):
          a = lib.check_num(args, 0, 'fmod')
          b = lib.check_num(args, 1, 'fmod')
          if type(a) is int and type(b) is int and not v51:
               if b == 0:
                    raise _bad(1, 'fmod', 'zero')
               if b == -1:
                    return [0]
               r = abs(a) % abs(b)
               return [-r if a < 0 else r]
          try:
               return [math.fmod(float(a), float(b))]
          except ValueError:
               return [math.nan]

     def modf(args):
          if not v51 and type(_arg(args, 0)) is int:
               return [args[0], 0.0]           # an integer is its own integer part
          x = float(lib.check_num(args, 0, 'modf'))
          if x in (math.inf, -math.inf):
               return [x, 0.0]
          frac, whole = math.modf(x)
          if not v51 and whole.is_integer():
               return [float(whole), frac]
          return [whole, frac]

     def sqrt(args):
          x = float(lib.check_num(args, 0, 'sqrt'))
          return [math.sqrt(x) if x >= 0 else math.nan]

     def log(args):
          x = float(lib.check_num(args, 0, 'log'))
          base = _arg(args, 1)
          try:
               if base is None or v51:
                    return [math.log(x) if x > 0 else (-math.inf if x == 0 else math.nan)]
               b = float(lib.check_num(args, 1, 'log'))
               if b == 2:
                    return [math.log2(x)]
               if b == 10:
                    return [math.log10(x)]
               return [math.log(x) / math.log(b)]
          except (ValueError, ZeroDivisionError):
               return [-math.inf if x == 0 else math.nan]

     def log10(args):
          x = float(lib.check_num(args, 0, 'log10'))
          if x <= 0:
               return [-math.inf if x == 0 else math.nan]
          return [math.log10(x)]

     def exp(args):
          try:
               return [math.exp(lib.check_num(args, 0, 'exp'))]
          except OverflowError:
               return [math.inf]

     def pow_(args):
          from .lua_vm import _pow
          return [_pow(lib.check_num(args, 0, 'pow'), lib.check_num(args, 1, 'pow'))]

     def atan(args):
          y = float(lib.check_num(args, 0, 'atan'))
          x = _arg(args, 1)
          if x is None or v51:
               return [math.atan(y)]
          return [math.atan2(y, float(lib.check_num(args, 1, 'atan')))]

     def atan2(args):
          return [math.atan2(float(lib.check_num(args, 0, 'atan2')),
                             float(lib.check_num(args, 1, 'atan2')))]

     def minmax(name, pick):
          def run(args):
               if not args and not v51:
                    raise _bad(0, name, 'value expected')
               best = lib.check_num(args, 0, name)
               for k in range(1, len(args)):
                    v = lib.check_num(args, k, name)
                    if pick(v, best):
                         best = v
               return [best]
          _fn(m, name, run)

     def random(args):
          if not args:
               return [rng.random()]
          if len(args) > 2:
               raise LuaError("wrong number of arguments to 'random'")
          low = lib.check_int(args, 0, 'random')
          if len(args) == 1:
               high = low
               low = 1
          else:
               high = lib.check_int(args, 1, 'random')
          if low > high:
               raise _bad(len(args) - 1, 'random', 'interval is empty')
          if not v51 and not (low >= 0 or high <= _INT_MAX + low):
               raise _bad(0, 'random', 'interval too large')
          return [rng.randint(low, high)]

     def randomseed(args):
          rng.seed(lib.opt_num(args, 0, 'randomseed', 0))
          return []

     def frexp(args):
          mantissa, exponent = math.frexp(float(lib.check_num(args, 0, 'frexp')))
          return [mantissa, exponent]

     def ldexp(args):
          return [math.ldexp(float(lib.check_num(args, 0, 'ldexp')),
                             lib.check_int(args, 1, 'ldexp'))]

     def tointeger(args):
          x = _arg(args, 0)
          if type(x) is int:
               return [x]
          if type(x) is float and x.is_integer() and _INT_MIN <= x <= _INT_MAX:
               return [int(x)]
          if type(x) is str and not v51:
               n = str_to_number(x)
               if type(n) is float and n.is_integer() and _INT_MIN <= n <= _INT_MAX:
                    n = int(n)            # 5.3 converts "34.0" too
               if type(n) is int:
                    return [n]
          return [None]

     def mtype(args):
          lib.check_any(args, 0, 'type')
          x = args[0]
          if type(x) is int:
               return ['integer']
          if type(x) is float:
               return ['float']
          return [None]

     def ult(args):
          a = lib.check_int(args, 0, 'ult') & 0xFFFFFFFFFFFFFFFF
          b = lib.check_int(args, 1, 'ult') & 0xFFFFFFFFFFFFFFFF
          return [a < b]

     _fn(m, 'floor', floor)
     _fn(m, 'ceil', ceil)
     _fn(m, 'abs', abs_)
     _fn(m, 'fmod', fmod)
     _fn(m, 'modf', modf)
     _fn(m, 'sqrt', sqrt)
     _fn(m, 'log', log)
     _fn(m, 'exp', exp)
     _fn(m, 'atan', atan)
     _fn(m, 'random', random)
     _fn(m, 'randomseed', randomseed)
     unary('sin', math.sin)
     unary('cos', math.cos)
     unary('tan', math.tan)
     unary('asin', math.asin)
     unary('acos', math.acos)
     unary('deg', math.degrees)
     unary('rad', math.radians)
     minmax('max', lambda v, best: v > best)
     minmax('min', lambda v, best: v < best)
     m.set('pi', math.pi)
     m.set('huge', math.inf)
     if v51:
          _fn(m, 'log10', log10)
          _fn(m, 'pow', pow_)
          _fn(m, 'atan2', atan2)
          _fn(m, 'frexp', frexp)
          _fn(m, 'ldexp', ldexp)
          _fn(m, 'mod', fmod)
          unary('cosh', math.cosh)
          unary('sinh', math.sinh)
          unary('tanh', math.tanh)
     else:
          _fn(m, 'tointeger', tointeger)
          _fn(m, 'type', mtype)
          _fn(m, 'ult', ult)
          m.set('maxinteger', _INT_MAX)
          m.set('mininteger', _INT_MIN)
     return m


# --- os ---------------------------------------------------------------------------------

def _install_os(vm, lib):
     o = LuaTable()
     clock_start = _time.process_time()

     def time_(args):
          t = _arg(args, 0)
          if t is None:
               return [int(_time.time())]
          if type(t) is not LuaTable:
               raise _typeerror(args, 0, 'time', 'table')

          def field(name, default=None):
               v = t.get(name)
               if v is None:
                    if default is None:
                         raise LuaError(f"field '{name}' missing in date table")
                    return default
               return int(v)
          isdst = t.get('isdst')
          values = (field('year'), field('month'), field('day'), field('hour', 12),
                    field('min', 0), field('sec', 0), 0, 0,
                    -1 if isdst is None else (1 if truthy(isdst) else 0))
          try:
               return [int(_time.mktime(values))]
          except (OverflowError, ValueError):
               return [None]

     def clock(args):
          return [_time.process_time() - clock_start]

     def date(args):
          fmt = lib.opt_str(args, 0, 'date', '%c')
          when = lib.opt_num(args, 1, 'date', None)
          when = _time.time() if when is None else when
          utc = fmt.startswith('!')
          if utc:
               fmt = fmt[1:]
          st = _time.gmtime(when) if utc else _time.localtime(when)
          if fmt.startswith('*t'):
               t = LuaTable()
               for name, value in [('year', st.tm_year), ('month', st.tm_mon),
                                   ('day', st.tm_mday), ('hour', st.tm_hour),
                                   ('min', st.tm_min), ('sec', st.tm_sec),
                                   ('wday', (st.tm_wday + 1) % 7 + 1),
                                   ('yday', st.tm_yday)]:
                    t.set(name, value)
               t.set('isdst', bool(st.tm_isdst > 0))
               return [t]
          try:
               return [_time.strftime(fmt, st)]
          except ValueError:
               raise LuaError("bad argument #1 to 'date' (invalid conversion specifier)")

     def difftime(args):
          return [float(lib.check_num(args, 0, 'difftime')
                        - lib.opt_num(args, 1, 'difftime', 0))]

     def getenv(args):
          value = vm.host.getenv(to_text(lib.check_str(args, 0, 'getenv')))
          return [None if value is None else to_bytes_text(value)]

     def remove(args):
          path = lib.check_str(args, 0, 'remove')
          try:
               vm.host.remove(path)
               return [True]
          except PermissionError as error:
               return [None, str(error), 13]
          except OSError as error:
               return [None, f'{to_text(path)}: {error.strerror or error}', error.errno or 2]

     def rename(args):
          old = lib.check_str(args, 0, 'rename')
          new = lib.check_str(args, 1, 'rename')
          try:
               vm.host.rename(old, new)
               return [True]
          except PermissionError as error:
               return [None, str(error), 13]
          except OSError as error:
               return [None, f'{to_text(old)}: {error.strerror or error}', error.errno or 2]

     def tmpname(args):
          try:
               return [to_bytes_text(vm.host.tmpname())]
          except OSError:
               raise LuaError('unable to generate a unique filename')

     def execute(args):
          command = _arg(args, 0)
          if command is None:
               return [vm.host.allow_commands] if vm.version != '5.1' else \
                    [1 if vm.host.allow_commands else 0]
          code, output = vm.host.execute(lib.check_str(args, 0, 'execute'))
          if output:
               vm.host.write_output(output)
          if vm.version == '5.1':
               return [code]
          return [True if code == 0 else None, 'exit', code]

     def exit_(args):
          code = _arg(args, 0)
          if code is True or code is None:
               code = 0
          elif code is False:
               code = 1
          raise LuaExit(int(code))

     def setlocale(args):
          locale = _arg(args, 0)
          return ['C' if locale in (None, '', 'C') else None]

     for name, fn in [('time', time_), ('clock', clock), ('date', date),
                      ('difftime', difftime), ('getenv', getenv), ('remove', remove),
                      ('rename', rename), ('tmpname', tmpname), ('execute', execute),
                      ('exit', exit_), ('setlocale', setlocale)]:
          _fn(o, name, fn)
     return o


# --- io ------------------------------------------------------------------------------

class LuaFile(LuaUserdata):
     """A Lua file handle over a Python binary file object (or an in-memory
     buffer, for io.popen output and the standard streams)."""

     def __init__(self, handle, name, writer=None, crlf=False):
          self.handle = handle
          self.name = name
          self.closed = False
          self.writer = writer
          self.crlf = crlf


def _install_io(vm, lib):
     import io as pyio
     io = LuaTable()
     methods = LuaTable()
     meta = LuaTable()
     meta.set('__index', methods)
     meta.set('__name', 'FILE*')

     def tostring(args):
          f = args[0]
          return ['file (closed)' if f.closed else f'file (0x{id(f) & 0xffffffff:08x})']
     _fn(meta, '__tostring', tostring)

     def gc_(args):
          f = check_file(args, 0, '__gc') if args else None
          if f is None:
               raise _typeerror(args, 0, '?', 'FILE*')
          return []
     _fn(meta, '__gc', gc_)

     def new_file(handle, name, writer=None):
          f = LuaFile(handle, name, writer)
          f.metatable = meta
          return f

     stdout = new_file(None, 'stdout', writer=vm.host.write_output)
     stderr = new_file(None, 'stderr', writer=vm.host.write_output)
     stdin = new_file(pyio.BytesIO(b''), 'stdin')
     state = {'output': stdout, 'input': stdin}

     def check_file(args, i, fname):
          f = _arg(args, i)
          if not isinstance(f, LuaFile):
               raise _typeerror(args, i, fname, 'FILE*')
          if f.closed:
               raise LuaError('attempt to use a closed file')
          return f

     def open_(args):
          path = lib.check_str(args, 0, 'open')
          mode = lib.opt_str(args, 1, 'open', 'r')
          if not re.match(r'^[rwa]\+?b?$', mode):
               raise _bad(1, 'open', 'invalid mode')
          try:
               handle = vm.host.open(path, mode)
          except PermissionError as error:
               return [None, str(error), 13]
          except OSError as error:
               return [None, f'{to_text(path)}: {error.strerror or "No such file or directory"}',
                       error.errno or 2]
          crlf = False
          if 'b' not in mode and '+' not in mode and vm.host.text_newline == '\r\n':
               # Windows text mode, as the C library there does it.
               if mode.startswith('r'):
                    data = handle.read()
                    handle.close()
                    handle = pyio.BytesIO(data.replace(b'\r\n', b'\n'))
               else:
                    crlf = True
          f = new_file(handle, path)
          f.crlf = crlf
          return [f]

     def read_line(f, keep):
          line = f.handle.readline()
          if not line:
               return None
          text = line.decode('latin-1')
          if not keep and text.endswith('\n'):
               text = text[:-1]
          return text

     def read_number(f):
          data = []
          handle = f.handle
          while True:
               c = handle.read(1)
               if not c:
                    break
               ch = c.decode('latin-1')
               if not data and ch in ' \t\r\n\f\v':
                    continue
               if ch in '0123456789+-.eExXabcdefABCDEF':
                    data.append(ch)
                    continue
               handle.seek(-1, 1)
               break
          return str_to_number(''.join(data)) if data else None

     def read(f, formats, fname):
          try:
               return read_formats(f, formats, fname)
          except (OSError, ValueError) as error:
               if isinstance(error, ValueError) and 'invalid format' in str(error):
                    raise
               return [None, f'{to_text(f.name)}: {error}', 9]

     def read_formats(f, formats, fname):
          if f.handle is None:
               return [None]
          if not formats:
               formats = ['l']
          results = []
          for k, fmt in enumerate(formats):
               if is_number(fmt):
                    n = int(fmt)
                    data = f.handle.read(n)
                    if n == 0:
                         pos = f.handle.tell()
                         more = f.handle.read(1)
                         f.handle.seek(pos)
                         results.append('' if more else None)
                    else:
                         results.append(data.decode('latin-1') if data else None)
               else:
                    code = fmt.lstrip('*')[:1] if type(fmt) is str else ''
                    if code == 'a':
                         results.append(f.handle.read().decode('latin-1'))
                    elif code == 'l':
                         results.append(read_line(f, False))
                    elif code == 'L':
                         results.append(read_line(f, True))
                    elif code == 'n':
                         results.append(read_number(f))
                    else:
                         raise _bad(k + 1, fname, 'invalid format')
               if results[-1] is None:
                    break
          return results

     def write(f, values, fname):
          for k, v in enumerate(values):
               if type(v) is str:
                    text = v
               elif is_number(v):
                    text = number_to_str(v, vm.version)
               else:
                    raise _typeerror(values, k, fname, 'string')
               if f.writer is not None:
                    f.writer(to_text(text))
               else:
                    try:
                         if f.crlf:
                              text = text.replace('\n', '\r\n')
                         f.handle.write(text.encode('latin-1'))
                    except OSError as error:
                         return [None, f'{to_text(f.name)}: {error}', 9]
          return [f]

     def lines_iterator(f, formats, close):
          def step(a):
               if f.closed:
                    raise LuaError('file is already closed')
               r = read(f, formats, 'lines')
               if r and r[0] is None and close:
                    close_file(f)
               return r
          return PyFunction(step, 'lines_iterator')

     def close_file(f):
          if f.handle is not None and f.writer is None:
               f.handle.close()
          f.closed = True

     # file methods
     def m_read(args):
          return read(check_file(args, 0, 'read'), args[1:], 'read')

     def m_write(args):
          return write(check_file(args, 0, 'write'), args[1:], 'write')

     def m_lines(args):
          return [lines_iterator(check_file(args, 0, 'lines'), args[1:], False)]

     def m_close(args):
          f = check_file(args, 0, 'close')
          if f.writer is not None:
               return [None, 'cannot close standard file']
          close_file(f)
          return [True]

     def m_seek(args):
          f = check_file(args, 0, 'seek')
          whence = lib.opt_str(args, 1, 'seek', 'cur')
          offset = lib.opt_int(args, 2, 'seek', 0)
          if whence not in ('set', 'cur', 'end'):
               raise _bad(1, 'seek', f"invalid option '{whence}'")
          if f.handle is None:
               return [0]
          f.handle.seek(offset, {'set': 0, 'cur': 1, 'end': 2}[whence])
          return [f.handle.tell()]

     def m_flush(args):
          f = check_file(args, 0, 'flush')
          if f.handle is not None and f.writer is None:
               f.handle.flush()
          return [True]

     def m_setvbuf(args):
          check_file(args, 0, 'setvbuf')
          return [True]

     for name, fn in [('read', m_read), ('write', m_write), ('lines', m_lines),
                      ('close', m_close), ('seek', m_seek), ('flush', m_flush),
                      ('setvbuf', m_setvbuf)]:
          _fn(methods, name, fn)

     # io functions
     def io_write(args):
          return write(state['output'], args, 'write')

     def io_read(args):
          return read(state['input'], args, 'read')

     def io_lines(args):
          path = _arg(args, 0)
          if path is None:
               return [lines_iterator(state['input'], [], False)]
          r = open_([path, 'r'])
          if r[0] is None:
               raise LuaError(r[1])
          return [lines_iterator(r[0], args[1:], True)]

     def io_close(args):
          if not args:
               return m_close([state['output']])
          return m_close(args)

     def io_type(args):
          f = _arg(args, 0)
          if not isinstance(f, LuaFile):
               return [None]
          return ['closed file' if f.closed else 'file']

     def io_output(args):
          f = _arg(args, 0)
          if f is None:
               return [state['output']]
          if type(f) is str:
               r = open_([f, 'w'])
               if r[0] is None:
                    raise LuaError(r[1])
               f = r[0]
          state['output'] = check_file([f], 0, 'output')
          return [f]

     def io_input(args):
          f = _arg(args, 0)
          if f is None:
               return [state['input']]
          if type(f) is str:
               r = open_([f, 'r'])
               if r[0] is None:
                    raise LuaError(r[1])
               f = r[0]
          state['input'] = check_file([f], 0, 'input')
          return [f]

     def popen(args):
          command = lib.check_str(args, 0, 'popen')
          mode = lib.opt_str(args, 1, 'popen', 'r')
          if mode != 'r':
               return [None, 'popen: only reading the output of a command is supported']
          code, output = vm.host.execute(command)
          return [new_file(pyio.BytesIO(to_bytes_text(output).encode('latin-1')),
                           command)]

     def tmpfile(args):
          return [new_file(pyio.BytesIO(), 'tmpfile')]

     def io_flush(args):
          f = state['output']
          if f.handle is not None and f.writer is None:
               f.handle.flush()
          return [True]

     for name, fn in [('open', open_), ('write', io_write), ('read', io_read),
                      ('flush', io_flush),
                      ('lines', io_lines), ('close', io_close), ('type', io_type),
                      ('output', io_output), ('input', io_input), ('popen', popen),
                      ('tmpfile', tmpfile)]:
          _fn(io, name, fn)
     io.set('stdout', stdout)
     io.set('stderr', stderr)
     io.set('stdin', stdin)
     return io


# --- coroutine ---------------------------------------------------------------------------

def _install_coroutine(vm, lib):
     co = LuaTable()

     def check_co(args, i, fname):
          c = _arg(args, i)
          if type(c) is not Coroutine:
               raise _typeerror(args, i, fname, 'coroutine')
          return c

     def create(args):
          return [Coroutine(vm, lib.check_fn(args, 0, 'create'))]

     def resume(args):
          return check_co(args, 0, 'resume').resume(args[1:])

     def yield_(args):
          current = vm.current_coroutine
          if current is None:
               raise LuaError('attempt to yield from outside a coroutine')
          if getattr(current, 'c_calls', 0):
               raise LuaError('attempt to yield across a C-call boundary')
          return current.yield_(args)

     def status(args):
          co = check_co(args, 0, 'status')
          if co is main_thread[0]:
               return ['running' if vm.current_coroutine is None else 'normal']
          return [co.status]

     main_thread = [None]

     def running(args):
          current = vm.current_coroutine
          if vm.version == '5.1':
               return [current]
          if current is None:
               # 5.2+ always name a thread: the main one when outside any coroutine
               if main_thread[0] is None:
                    main_thread[0] = Coroutine(vm, None)
                    main_thread[0].status = 'running'
               return [main_thread[0], True]
          return [current, False]

     def wrap(args):
          c = Coroutine(vm, lib.check_fn(args, 0, 'wrap'))

          def call(a):
               r = c.resume(a)
               if r[0] is True:
                    return r[1:]
               value = r[1]
               raise LuaError(value, level=1 if type(value) is str and
                              not re.match(r'^.+:\d+: ', value) else 0)
          return [PyFunction(call, 'wrapped_coroutine')]

     def isyieldable(args):
          current = vm.current_coroutine
          return [current is not None and not getattr(current, 'c_calls', 0)]

     for name, fn in [('create', create), ('resume', resume), ('yield', yield_),
                      ('status', status), ('running', running), ('wrap', wrap)]:
          _fn(co, name, fn)
     if vm.version != '5.1':
          _fn(co, 'isyieldable', isyieldable)
     return co


# --- debug ------------------------------------------------------------------------------

def _install_debug(vm, lib):
     d = LuaTable()
     hooks = {'hook': None, 'mask': '', 'count': 0}

     def frame_at(level):
          if 1 <= level <= len(vm.stack):
               return vm.stack[-level]
          return None

     def info_for(fn, frame, what):
          t = LuaTable()
          if type(fn) is LuaFunction:
               proto = fn.proto
               main = proto.name == 'main chunk'
               if 'S' in what:
                    t.set('source', proto.source or '@' + to_bytes_text(proto.chunk))
                    t.set('short_src', to_bytes_text(proto.chunk))
                    t.set('what', 'main' if main else 'Lua')
                    t.set('linedefined', 0 if main else (proto.line or 0))
                    t.set('lastlinedefined', -1 if main else proto.last_line)
               if 'l' in what:
                    t.set('currentline', frame.line if frame is not None and
                          frame.line is not None else -1)
               if 'u' in what:
                    t.set('nups', len(fn.upvals))
                    if vm.version != '5.1':
                         t.set('nparams', proto.nparams)
                         t.set('isvararg', proto.is_vararg)
               if 'L' in what:
                    lines = LuaTable()
                    for line in proto.active_lines:
                         lines.set(line, True)
                    t.set('activelines', lines)
               if 'n' in what and (main or frame is None):
                    t.set('namewhat', '')
               if 'n' in what and not main and frame is not None:
                    # the name the caller used, as Lua derives it from the call
                    m = re.match(r"^(global|local|field|method|upvalue) '(.*)'$",
                                 frame.calldesc or '')
                    if m:
                         t.set('name', m.group(2))
                         t.set('namewhat', m.group(1))
                    else:
                         t.set('namewhat', '')
          else:
               if 'S' in what:
                    t.set('source', '=[C]')
                    t.set('short_src', '[C]')
                    t.set('what', 'C')
                    t.set('linedefined', -1)
                    t.set('lastlinedefined', -1)
               if 'l' in what:
                    t.set('currentline', -1)
               if 'u' in what:
                    t.set('nups', 0)
               if 'n' in what and type(fn) is PyFunction:
                    t.set('name', to_bytes_text(fn.name))
                    t.set('namewhat', '')
          if 'f' in what:
               t.set('func', fn)
          return t

     def getinfo(args):
          start = 0
          if type(_arg(args, 0)) is Coroutine:
               start = 1
          target = _arg(args, start)
          what = lib.opt_str(args, start + 1, 'getinfo', 'flnSu')
          valid = 'SlnufL' if vm.version == '5.1' else 'SlnrutfL'
          if any(c not in valid for c in what):
               raise _bad(start + 1, 'getinfo', 'invalid option')
          if type(target) in (LuaFunction, PyFunction):
               return [info_for(target, None, what.replace('l', ''))]
          level = lib.check_int(args, start, 'getinfo')
          if level == 0:
               return [info_for(d.get('getinfo'), None, what)]
          frame = frame_at(level)
          if frame is None:
               return [None]
          return [info_for(frame.fn, frame, what)]

     def traceback(args):
          message = _arg(args, 0)
          if type(message) is Coroutine:
               message = _arg(args, 1)
          if message is not None and type(message) not in (str, int, float):
               return [message]
          lines = [] if message is None else [vm.tostr(message)]
          lines.append('stack traceback:')
          for frame in reversed(vm.stack):
               proto = frame.fn.proto
               where = proto.positions[frame.line - 1] \
                    if proto.positions and frame.line and frame.line <= len(proto.positions) \
                    else f'{proto.chunk}:{frame.line if frame.line is not None else "?"}'
               name = 'main chunk' if proto.name == 'main chunk' \
                    else f"function '{proto.name}'"
               lines.append(f'\t{where}: in {name}')
          return ['\n'.join(to_bytes_text(line) for line in lines)]

     def getlocal(args):
          level = lib.check_int(args, 0, 'getlocal')
          n = lib.check_int(args, 1, 'getlocal')
          frame = frame_at(level)
          if frame is None:
               raise _bad(0, 'getlocal', 'level out of range')
          names = frame.fn.proto.slotnames
          live = [(names[i], cell.v) for i, cell in enumerate(frame.regs)
                  if cell is not None and i < len(names)]
          if 1 <= n <= len(live):
               return [live[n - 1][0], live[n - 1][1]]
          return [None]

     def setlocal(args):
          level = lib.check_int(args, 0, 'setlocal')
          n = lib.check_int(args, 1, 'setlocal')
          frame = frame_at(level)
          if frame is None:
               raise _bad(0, 'setlocal', 'level out of range')
          names = frame.fn.proto.slotnames
          live = [(i, cell) for i, cell in enumerate(frame.regs)
                  if cell is not None and i < len(names)]
          if 1 <= n <= len(live):
               live[n - 1][1].v = _arg(args, 2)
               return [names[live[n - 1][0]]]
          return [None]

     def getupvalue(args):
          fn = lib.check_fn(args, 0, 'getupvalue')
          n = lib.check_int(args, 1, 'getupvalue')
          if type(fn) is LuaFunction and 1 <= n <= len(fn.upvals):
               return [fn.proto.upnames[n - 1], fn.upvals[n - 1].v]
          return [None]

     def setupvalue(args):
          fn = lib.check_fn(args, 0, 'setupvalue')
          n = lib.check_int(args, 1, 'setupvalue')
          if type(fn) is LuaFunction and 1 <= n <= len(fn.upvals):
               fn.upvals[n - 1].v = _arg(args, 2)
               return [fn.proto.upnames[n - 1]]
          return [None]

     def upvalue_cell(args, fname):
          fn = lib.check_fn(args, 0, fname)
          n = lib.check_int(args, 1, fname)
          if type(fn) is not LuaFunction or not 1 <= n <= len(fn.upvals):
               raise _bad(1, fname, 'invalid upvalue index')
          return fn, n - 1

     def upvalueid(args):
          fn = lib.check_fn(args, 0, 'upvalueid')
          n = lib.check_int(args, 1, 'upvalueid')
          if type(fn) is PyFunction and 1 <= n <= fn.nups:
               return [LightUserdata(('C', id(fn), n))]
          fn, i = upvalue_cell(args, 'upvalueid')
          return [LightUserdata(id(fn.upvals[i]))]

     def upvaluejoin(args):
          f1, i1 = upvalue_cell(args, 'upvaluejoin')
          f2, i2 = upvalue_cell(args[2:], 'upvaluejoin')
          f1.upvals[i1] = f2.upvals[i2]
          return []

     def getuservalue(args):
          v = _arg(args, 0)
          full = isinstance(v, LuaUserdata) and type(v) is not LightUserdata
          return [getattr(v, 'uservalue', None) if full else None]

     def setuservalue(args):
          v = _arg(args, 0)
          if not isinstance(v, LuaUserdata) or type(v) is LightUserdata:
               raise _typeerror(args, 0, 'setuservalue', 'userdata')
          value = _arg(args, 1)
          if value is not None and type(value) is not LuaTable:
               raise _bad(1, 'setuservalue', 'table expected')
          v.uservalue = value
          return [v]

     def getmetatable_(args):
          return [vm.getmetatable(_arg(args, 0))]

     def setmetatable_(args):
          v = _arg(args, 0)
          mt = _arg(args, 1)
          if mt is not None and type(mt) is not LuaTable:
               raise _bad(1, 'setmetatable', 'nil or table expected')
          if type(v) is LuaTable:
               v.metatable = mt
               set_weak_mode(v, mt.get('__mode') if mt is not None else None)
          elif type(v) is str:
               vm.string_meta = mt
          elif isinstance(v, LuaUserdata):
               v.metatable = mt
          else:
               if mt is None:
                    vm.type_metas.pop(type_name(v), None)
               else:
                    vm.type_metas[type_name(v)] = mt
          return [v if vm.version != '5.1' else True]

     def getregistry(args):
          registry = vm.registry
          if not isinstance(registry, LuaTable):
               registry = vm.registry = LuaTable()
          return [registry]

     def getfenv_(args):
          v = _arg(args, 0)
          if type(v) is LuaFunction:
               return [v.env]
          if type(v) is Coroutine:
               return [v.env]
          return [vm.globals if type(v) is PyFunction else None]

     def setfenv_(args):
          v = _arg(args, 0)
          t = lib.check_table(args, 1, 'setfenv')
          if type(v) is LuaFunction:
               v.env = t
               return [v]
          if type(v) is Coroutine:
               v.env = t
               return [v]
          raise LuaError("'setfenv' cannot change environment of given object")

     def sethook(args):
          start = 1 if type(_arg(args, 0)) is Coroutine else 0
          hooks['hook'] = _arg(args, start)
          hooks['mask'] = _arg(args, start + 1) or ''
          hooks['count'] = _arg(args, start + 2) or 0
          return []

     def gethook(args):
          if hooks['hook'] is None:
               return [None]
          return [hooks['hook'], hooks['mask'], hooks['count']]

     def debug_(args):
          return []

     for name, fn in [('getinfo', getinfo), ('traceback', traceback),
                      ('getlocal', getlocal), ('setlocal', setlocal),
                      ('getupvalue', getupvalue), ('setupvalue', setupvalue),
                      ('getmetatable', getmetatable_), ('setmetatable', setmetatable_),
                      ('getregistry', getregistry), ('sethook', sethook),
                      ('gethook', gethook), ('debug', debug_)]:
          _fn(d, name, fn)
     if vm.version != '5.1':
          for name, fn in [('upvalueid', upvalueid), ('upvaluejoin', upvaluejoin),
                           ('getuservalue', getuservalue), ('setuservalue', setuservalue)]:
               _fn(d, name, fn)
     if vm.version == '5.1':
          _fn(d, 'getfenv', getfenv_)
          _fn(d, 'setfenv', setfenv_)
     return d


# --- package / require ---------------------------------------------------------------------

def _install_package(vm, lib, g):
     package = LuaTable()
     loaded = LuaTable()
     preload = LuaTable()
     package.set('loaded', loaded)
     package.set('preload', preload)
     package.set('path', '?.lua;?\\init.lua' if os.name == 'nt' else '?.lua;?/init.lua')
     package.set('cpath', '')
     package.set('config', '\\\n;\n?\n!\n-\n' if os.name == 'nt' else '/\n;\n?\n!\n-\n')
     for name in ('_G', 'string', 'table', 'math', 'os', 'io', 'coroutine', 'utf8',
                  'debug'):
          value = g.get(name)
          if value is not None:
               loaded.set(name, value)
     loaded.set('package', package)

     def searchpath(name, path):
          tried = []
          base = name.replace('.', '\\' if os.name == 'nt' else '/')
          for template in path.split(';'):
               if not template:
                    continue
               candidate = template.replace('?', base)
               if vm.host.exists(candidate) and not vm.host.is_dir(candidate):
                    return candidate, tried
               tried.append(f"\n\tno file '{candidate}'")
          return None, tried

     def require(args):
          name = lib.check_str(args, 0, 'require')
          value = loaded.get(name)
          if truthy(value):
               return [value]
          loader = preload.get(name)
          extra = None
          if vm.version == '5.1' and value is False:
               loaded.set(name, None)
          if loader is None:
               path = package.get('path')
               if type(path) is not str and vm.version != '5.1':
                    raise LuaError("'package.path' must be a string")
               found, tried = searchpath(name, path if type(path) is str else '')
               if found is None:
                    raise LuaError(f"module '{name}' not found:\n\tno field package.preload"
                                   f"['{name}']" + ''.join(tried))
               r = _load_file(vm, found)
               if r[0] is None:
                    raise LuaError(f"error loading module '{name}' from file '{found}':\n\t"
                                   f"{r[1]}")
               loader = r[0]
               extra = found
          result = vm.call(loader, [name] + ([extra] if extra and vm.version != '5.1' else []))
          value = result[0] if result else None
          if value is not None:
               loaded.set(name, value)
          if loaded.get(name) is None:
               loaded.set(name, True)
          return [loaded.get(name)]

     def search(args):
          name = lib.check_str(args, 0, 'searchpath')
          path = lib.check_str(args, 1, 'searchpath')
          found, tried = searchpath(name, path)
          if found is None:
               return [None, ''.join(tried)]
          return [found]

     _fn(g, 'require', require)

     def loadlib(args):
          return [None, 'dynamic libraries not enabled; check your Lua installation',
                  'absent']
     _fn(package, 'loadlib', loadlib)

     def module(args):
          name = lib.check_str(args, 0, 'module')
          mod = loaded.get(name)
          if type(mod) is not LuaTable:
               mod = g
               for part in name.split('.'):
                    nxt = mod.get(part)
                    if nxt is None:
                         nxt = LuaTable()
                         mod.set(part, nxt)
                    elif type(nxt) is not LuaTable:
                         raise LuaError(f"name conflict for module '{name}'")
                    mod = nxt
               loaded.set(name, mod)
          if mod.get('_NAME') is None:
               mod.set('_M', mod)
               mod.set('_NAME', name)
               mod.set('_PACKAGE', name.rsplit('.', 1)[0] + '.' if '.' in name else '')
          if vm.stack:
               vm.stack[-1].fn.env = mod
          for option in args[1:]:
               vm.call(option, [mod])
          return []

     def seeall(args):
          mod = lib.check_table(args, 0, 'seeall')
          mt = mod.metatable
          if mt is None:
               mt = LuaTable()
               mod.metatable = mt
          mt.set('__index', g)
          return []

     if vm.version == '5.1':
          _fn(g, 'module', module)
          _fn(package, 'seeall', seeall)
     if vm.version != '5.1':
          _fn(package, 'searchpath', search)
     g.set('package', package)


# --- utf8 (5.3) ------------------------------------------------------------------------------

def _install_utf8(vm, lib):
     """Lua 5.3's utf8 library (lutf8lib.c): strict decoding (no overlong
     forms, nothing above U+10FFFF), positions in bytes."""
     u = LuaTable()
     maxunicode = 0x10FFFF
     limits = (0xFF, 0x7F, 0x7FF, 0xFFFF)

     def utf8_decode(s, i):
          """(code, next index) of the sequence starting at s[i], or None."""
          c = ord(s[i]) if i < len(s) else 0
          if c < 0x80:
               return c, i + 1
          res = 0
          count = 0
          while c & 0x40:
               count += 1
               cc = ord(s[i + count]) if i + count < len(s) else 0
               if cc & 0xC0 != 0x80:
                    return None
               res = (res << 6) | (cc & 0x3F)
               c <<= 1
          res |= (c & 0x7F) << (count * 5)
          if count > 3 or res > maxunicode or res <= limits[count]:
               return None
          return res, i + count + 1

     def cont(s, i):
          return i < len(s) and ord(s[i]) & 0xC0 == 0x80

     def relat(pos, length):
          if pos >= 0:
               return pos
          return 0 if -pos > length else length + pos + 1

     def encode(code):
          from .lua_lexer import utf8_encode
          return utf8_encode(code)

     def char(args):
          out = []
          for k in range(len(args)):
               code = lib.check_int(args, k, 'char')
               if not 0 <= code <= maxunicode:
                    raise _bad(k, 'char', 'value out of range')
               out.append(encode(code))
          return [''.join(out)]

     def len_(args):
          s = lib.check_str(args, 0, 'len')
          length = len(s)
          posi = relat(lib.opt_int(args, 1, 'len', 1), length)
          posj = relat(lib.opt_int(args, 2, 'len', -1), length)
          posi -= 1
          if not (0 <= posi <= length):
               raise _bad(1, 'len', 'initial position out of string')
          posj -= 1
          if posj >= length:
               raise _bad(2, 'len', 'final position out of string')
          n = 0
          while posi <= posj:
               decoded = utf8_decode(s, posi)
               if decoded is None:
                    return [None, posi + 1]
               posi = decoded[1]
               n += 1
          return [n]

     def codepoint(args):
          s = lib.check_str(args, 0, 'codepoint')
          length = len(s)
          posi = relat(lib.opt_int(args, 1, 'codepoint', 1), length)
          pose = relat(lib.opt_int(args, 2, 'codepoint', posi), length)
          if posi < 1:
               raise _bad(1, 'codepoint', 'out of range')
          if pose > length:
               raise _bad(2, 'codepoint', 'out of range')
          out = []
          i = posi - 1
          while i < pose:
               decoded = utf8_decode(s, i)
               if decoded is None:
                    raise LuaError('invalid UTF-8 code')
               out.append(decoded[0])
               i = decoded[1]
          return out

     def codes(args):
          s = lib.check_str(args, 0, 'codes')

          def step(a):
               text = a[0]
               length = len(text)
               n = (a[1] if type(a[1]) is int else int(a[1])) - 1
               if n < 0:
                    n = 0
               elif n < length:
                    n += 1
                    while cont(text, n):
                         n += 1
               if n >= length:
                    return [None]
               decoded = utf8_decode(text, n)
               if decoded is None or cont(text, decoded[1]):
                    raise LuaError('invalid UTF-8 code')
               return [n + 1, decoded[0]]
          return [PyFunction(step, 'codes_iterator'), s, 0]

     def offset(args):
          s = lib.check_str(args, 0, 'offset')
          n = lib.check_int(args, 1, 'offset')
          length = len(s)
          posi = lib.opt_int(args, 2, 'offset', 1 if n >= 0 else length + 1)
          if posi < 0:
               posi = 0 if -posi > length else length + posi + 1
          posi -= 1
          if not (0 <= posi <= length):
               raise _bad(2, 'offset', 'position out of range')

          def cont(i):
               return i < length and (ord(s[i]) & 0xC0) == 0x80
          if n == 0:
               while posi > 0 and cont(posi):
                    posi -= 1
          else:
               if cont(posi):
                    raise LuaError('initial position is a continuation byte')
               if n < 0:
                    while n < 0 and posi > 0:
                         posi -= 1
                         while posi > 0 and cont(posi):
                              posi -= 1
                         n += 1
               else:
                    n -= 1
                    while n > 0 and posi < length:
                         posi += 1
                         while cont(posi):
                              posi += 1
                         n -= 1
          return [posi + 1] if n == 0 else [None]

     for name, fn in [('char', char), ('len', len_), ('codepoint', codepoint),
                      ('codes', codes), ('offset', offset)]:
          _fn(u, name, fn)
     u.set('charpattern', '[\x00-\x7F\xC2-\xFD][\x80-\xBF]*')
     return u
