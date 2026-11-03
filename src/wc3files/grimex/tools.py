"""
The Grim Extension Pack tools: their Lua functions and their command lines.

Each tool works on a `ToolContext` (the map's files, its object data, the
log, where files are looked up) and offers what the real tool offers:

ObjectMerger
     Lua: `setobjecttype`, `currentobjecttype`, `objectexists`,
     `createobject`, `modifyobject`, `currentobject`, `makechange`,
     `resetchange`, `resetobject`, `generateid`, `mergefile`, and the
     constants `current`, `original`, `custom`, `modded`, `unmodded`,
     `merge`, `replace`, `import`.
     Command line: `w3a AHbz A000 anam "Name" Hbz1 1 33` (create an object),
     `[m|r|i] file.w3a ...` (merge object files), `script.lua ...`.
FileImporter
     Lua: `importfile(disk, archive)`, `importdirectory(dir)`,
     `registercallback(fn)`. Command line: `directory` or
     `diskpath archivepath ...`.
FileExporter
     Lua: `exportfile(archive, disk)`, `exportgroup(group, dir)` with
     `triggers`, `objects`, `constants`, `environment`, `imports`.
     Command line: `[targetdir]`.
ConstantMerger
     Lua: `mergefile(path)`, `setvalue(section, key, value)`. Command line:
     `war3mapMisc.txt ...`.

Every tool also has `log`, `logf` and `logt`, which write to the log
(`logs\\grimex.txt` for the real tools).

The names, arguments and error messages are those of Grim Extension Pack 1.0,
read from its binaries.
"""

import os
import re

from wc3files.scripts.lua_vm import (
     LuaError, LuaTable, PyFunction, LuaFunction, to_text, to_bytes_text,
     number_to_str,
)
from .convert import convert
from .objects import (
     ObjectData, ObjectMergerError, EXTENSIONS, MAP_FILES, GROUPS, resolve_type, valid_id,
)


class ToolError(Exception):
     """A tool refused its command line or failed."""


class ToolContext:
     """What the tools work on.

     files
          The map's files, `{archive path: bytes}`; tools change it in place.
     find_file
          `find_file(path) -> real path or None`: where a path a command line
          or script names is on disk (lookup folders, the workspace).
     host
          The Lua host scripts run against (`lua_stdlib.LuaHost`).
     """

     def __init__(self, files, find_file, host=None, write_file=None):
          self.files = files
          self.find_file = find_file
          self.host = host
          self.write_file = write_file
          self.log = []
          self._objects = None
          self.callbacks = []

     @property
     def objects(self):
          if self._objects is None:
               self._objects = ObjectData(self.files)
          return self._objects

     def commit_objects(self):
          """Write the changed object tables back into the map's files."""
          if self._objects is None:
               return []
          changed = self._objects.changed_files()
          for name, data in changed.items():
               existing = _find_member(self.files, name)
               self.files[existing or name] = data
          self._objects.changed.clear()
          return list(changed)

     def read_disk(self, path):
          real = self.find_file(path)
          if real is None or not os.path.isfile(real):
               return None
          with open(real, 'rb') as fh:
               return fh.read()


def _find_member(files, name):
     lowered = name.replace('/', '\\').lower()
     for member in files:
          if member.replace('/', '\\').lower() == lowered:
               return member
     return None


def split_arguments(text):
     """Split a command line the way a Windows program sees its arguments."""
     args = []
     current = []
     quoted = False
     started = False
     i = 0
     while i < len(text):
          c = text[i]
          if c == '\\':
               j = i
               while j < len(text) and text[j] == '\\':
                    j += 1
               count = j - i
               if j < len(text) and text[j] == '"':
                    current.append('\\' * (count // 2))
                    if count % 2:
                         current.append('"')
                         i = j + 1
                    else:
                         i = j
                    started = True
                    continue
               current.append('\\' * count)
               started = True
               i = j
               continue
          if c == '"':
               quoted = not quoted
               started = True
               i += 1
               continue
          if c in ' \t' and not quoted:
               if started:
                    args.append(''.join(current))
                    current = []
                    started = False
               i += 1
               continue
          current.append(c)
          started = True
          i += 1
     if started:
          args.append(''.join(current))
     return args


# --- Lua helpers ------------------------------------------------------------

def _safe_string(vm, value):
     """lua_tosafestring: a string or number argument as text."""
     if isinstance(value, str):
          return to_text(value)
     if isinstance(value, (int, float)) and not isinstance(value, bool):
          return to_text(number_to_str(value, vm.version))
     return ''


def _to_integer(value):
     """lua_tointeger: numbers and numeric strings, 0 for anything else."""
     if isinstance(value, bool) or value is None:
          return 0
     if isinstance(value, (int, float)):
          return int(value)
     if isinstance(value, str):
          from wc3files.scripts.lua_vm import str_to_number
          number = str_to_number(value)
          return int(number) if number is not None else 0
     return 0


def _fail(message):
     raise LuaError(message)


def install_log(vm, ctx):
     def log(args):
          ctx.log.append(_safe_string(vm, args[0] if args else None))
          return []

     def logf(args):
          ctx.log.append(''.join(_safe_string(vm, a) for a in args))
          return []

     def logt(args):
          t = args[0] if args else None
          if isinstance(t, LuaTable):
               for key, value in t.items():
                    ctx.log.append(_safe_string(vm, value))
          return []

     vm.register('log', log)
     vm.register('logf', logf)
     vm.register('logt', logt)


# --- ObjectMerger -----------------------------------------------------------

GROUP_VALUES = {name: index for index, name in enumerate(GROUPS)}
MERGE_MODES = {'merge': 0, 'replace': 1, 'import': 2}


class ObjectMerger:
     """ObjectMerger: create and change objects of the map."""

     name = 'ObjectMerger'

     def __init__(self, ctx):
          self.ctx = ctx
          self.ext = None
          self.current = None

     @property
     def objects(self):
          return self.ctx.objects

     # -- Lua -------------------------------------------------------------------

     def install(self, vm):
          ctx = self.ctx
          install_log(vm, ctx)

          def need_type():
               if self.ext is None:
                    _fail("No object type has been loaded, use 'setobjectype' to do so")

          def call(fn):
               def run(args):
                    try:
                         return fn(args)
                    except ObjectMergerError as error:
                         raise LuaError(str(error)) from None
               return run

          def setobjecttype(args):
               name = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'setobjecttype {name}')
               ext = resolve_type(name)
               if ext is None:
                    _fail(f'Unsupported object type {name}, w3u, w3t, w3b, w3d, w3a, w3h or '
                          f'w3q expected')
               if ext != self.ext:
                    self.current = None
               self.ext = ext
               return []

          def currentobjecttype(args):
               return [self.ext or '']

          def objectexists(args):
               object_id = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'objectexists {object_id}')
               if not valid_id(object_id):
                    _fail(f'Invalid object id {object_id}')
               need_type()
               return [self.objects.exists(self.ext, object_id)]

          def modifyobject(args):
               object_id = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'modifyobject {object_id}')
               if not valid_id(object_id):
                    _fail(f'Invalid object id {object_id}')
               need_type()
               self.current = self.objects.modify(self.ext, object_id)
               return []

          def createobject(args):
               base = _safe_string(vm, args[0] if args else None)
               new = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'createobject {new}')
               for value in (base, new):
                    if not valid_id(value):
                         _fail(f'Invalid object id {value}')
               need_type()
               self.current = self.objects.create(self.ext, base, new)
               return []

          def currentobject(args):
               if self.ext is None or self.current is None:
                    return ['']
               return [self.current[1]]

          def generateid(args):
               base = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'generateid {base}')
               if not valid_id(base):
                    _fail(f'Invalid object id {base}')
               need_type()
               return [self.objects.generate_id(self.ext, base)]

          def group_of(value):
               index = _to_integer(value)
               if not 0 <= index < len(GROUPS):
                    _fail('Invalid group identifier, expected current, original, custom, '
                          'modded or unmodded')
               return GROUPS[index]

          def makechange(args):
               group = group_of(args[0] if args else None)
               field = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'makechange {GROUP_VALUES[group]} {field}')
               need_type()
               leveled = self.objects.is_leveled(self.ext, field)
               if self.objects.field(self.ext, field) is None:
                    _fail(f'Invalid object field {field}')
               if leveled:
                    if len(args) != 4:
                         _fail('Invalid number of arguments, expected 4 arguments including '
                               'level')
                    level = args[2]
                    if _safe_string(vm, level) == '' or not re.match(
                              r'^\s*-?\d+(\.\d*)?\s*$', _safe_string(vm, level)):
                         _fail('The level argument must be numeric')
                    level = _safe_string(vm, level).split('.')[0]
                    value = _safe_string(vm, args[3])
               else:
                    if len(args) != 3:
                         _fail('Invalid number of arguments, expected 3 arguments excluding '
                               'level')
                    level = None
                    value = _safe_string(vm, args[2])
               targets = self.objects.group(self.ext, group, self.current)
               for target in targets:
                    self.objects.make_change(self.ext, target, field, value, level)
               return []

          def resetchange(args):
               group = group_of(args[0] if args else None)
               field = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'resetchange {GROUP_VALUES[group]} {field}')
               need_type()
               if self.objects.field(self.ext, field) is None:
                    _fail(f'Invalid object field {field}')
               if self.objects.is_leveled(self.ext, field):
                    if len(args) != 3:
                         _fail('Invalid number of arguments, expected 3 arguments including '
                               'level')
                    level = _safe_string(vm, args[2])
               else:
                    if len(args) != 2:
                         _fail('Invalid number of arguments, expected 2 arguments excluding '
                               'level')
                    level = None
               for target in self.objects.group(self.ext, group, self.current):
                    self.objects.reset_change(self.ext, target, field, level)
               return []

          def resetobject(args):
               group = group_of(args[0] if args else None)
               ctx.log.append(f'resetobject {GROUP_VALUES[group]}')
               need_type()
               for target in self.objects.group(self.ext, group, self.current):
                    self.objects.reset_object(self.ext, target)
               return []

          def mergefile(args):
               path = _safe_string(vm, args[0] if args else None)
               mode_value = _to_integer(args[1]) if len(args) > 1 else 0
               ctx.log.append(f'mergefile {path} {mode_value}')
               need_type()
               if not path.lower().endswith('.' + self.ext):
                    _fail(f'Unsupported file extension, expected {self.ext}')
               modes = {v: k for k, v in MERGE_MODES.items()}
               if mode_value not in modes:
                    _fail('Unsupported merge mode, expected merge, replace or import')
               data = ctx.read_disk(path)
               if data is None:
                    _fail(f"{path} doesn't exist")
               self.objects.merge(self.ext, data, modes[mode_value])
               return []

          for name, fn in [
               ('setobjecttype', setobjecttype), ('currentobjecttype', currentobjecttype),
               ('objectexists', objectexists), ('modifyobject', modifyobject),
               ('createobject', createobject), ('currentobject', currentobject),
               ('generateid', generateid), ('makechange', makechange),
               ('resetchange', resetchange), ('resetobject', resetobject),
               ('mergefile', mergefile),
          ]:
               vm.register(name, call(fn))
          for name, value in list(GROUP_VALUES.items()) + list(MERGE_MODES.items()):
               vm.globals.set(name, value)

     def finish(self):
          return self.ctx.commit_objects()

     # -- command line ---------------------------------------------------------------

     def run_command(self, args, run_lua):
          if not args:
               raise ToolError(_OBJECTMERGER_USAGE)
          if args[0].lower().endswith('.lua'):
               for path in args:
                    run_lua(self, path)
               return
          first = args[0].lower()
          if first in EXTENSIONS:
               self._generate(args)
          elif first in ('m', 'r', 'i'):
               self._merge_files(args[1:], {'m': 'merge', 'r': 'replace', 'i': 'import'}[first])
          else:
               self._merge_files(args, 'merge')
          self.finish()

     def _generate(self, args):
          if len(args) < 3:
               raise ToolError(_OBJECTMERGER_USAGE)
          ext, base, new = args[0].lower(), args[1], args[2]
          if not valid_id(base):
               raise ToolError(f'Invalid original id {base}')
          if not valid_id(new):
               raise ToolError(f'Invalid new id {new}')
          objects = self.objects
          try:
               target = objects.create(ext, base, new)
               rest = args[3:]
               i = 0
               while i < len(rest):
                    field = rest[i]
                    if objects.field(ext, field) is None:
                         raise ObjectMergerError(f'Invalid object field {field}')
                    if objects.is_leveled(ext, field):
                         if i + 2 >= len(rest):
                              raise ObjectMergerError(
                                   f'The value for the change {field} is missing')
                         objects.make_change(ext, target, field, rest[i + 2], rest[i + 1])
                         i += 3
                    else:
                         if i + 1 >= len(rest):
                              raise ObjectMergerError(
                                   f'The value for the change {field} is missing')
                         objects.make_change(ext, target, field, rest[i + 1])
                         i += 2
          except ObjectMergerError as error:
               raise ToolError(str(error)) from None

     def _merge_files(self, paths, mode):
          if not paths:
               raise ToolError(_OBJECTMERGER_USAGE)
          for path in paths:
               ext = os.path.splitext(path)[1].lstrip('.').lower()
               if ext not in EXTENSIONS:
                    raise ToolError(f'Unknown file extension {ext}')
               data = self.ctx.read_disk(path)
               if data is None:
                    raise ToolError(f'Could not open {path}')
               try:
                    self.objects.merge(ext, data, mode)
               except ObjectMergerError as error:
                    raise ToolError(str(error)) from None


_OBJECTMERGER_USAGE = (
     'Wrong parameters for object merger, usage:\n'
     'map.w3x lookuppaths [ m | r | i ] mergefile { moremergefiles }\n'
     'map.w3x lookuppaths ( w3u | w3t | w3b | w3d | w3a | w3h | w3q ) originalid newid '
     '{ changeid [ level | variation ] value }\n'
     'map.w3x lookuppaths script.lua { script.lua }')


# --- FileImporter ----------------------------------------------------------------

_DIRECTORY_CONVERSIONS = {'.jpg': '.blp', '.bmp': '.blp', '.tga': '.blp',
                          '.mdl': '.mdx'}

_ICON_PREFIXES = {
     'CMD': 'ReplaceableTextures\\CommandButtons\\',
     'PAS': 'ReplaceableTextures\\PassiveButtons\\',
     'AUT': 'ReplaceableTextures\\AutocastButtons\\',
}


class FileImporter:
     """FileImporter: import files from disk into the map.

     Files are imported as they are. The real tool also converts .tga/.bmp/
     .jpg images to .blp and .mdl models to .mdx while importing; that is not
     done here, and is reported in the log."""

     name = 'FileImporter'

     def __init__(self, ctx):
          self.ctx = ctx
          self.vm = None

     def install(self, vm):
          self.vm = vm
          ctx = self.ctx
          install_log(vm, ctx)

          def importfile(args):
               disk = _safe_string(vm, args[0] if args else None)
               archive = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'importfile {disk} {archive}')
               try:
                    self.import_file(disk, archive)
               except ToolError as error:
                    raise LuaError(str(error)) from None
               return []

          def importdirectory(args):
               directory = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'importdirectory {directory}')
               try:
                    self.import_directory(directory)
               except ToolError as error:
                    raise LuaError(str(error)) from None
               return []

          def registercallback(args):
               fn = args[0] if args else None
               if type(fn) not in (LuaFunction, PyFunction):
                    raise LuaError('registercallback expects a function')
               ctx.callbacks.append(fn)
               return []

          vm.register('importfile', importfile)
          vm.register('importdirectory', importdirectory)
          vm.register('registercallback', registercallback)

     def import_file(self, disk, archive):
          data = self.ctx.read_disk(disk)
          if data is None:
               raise ToolError(f"Import file {disk} doesn't exist")
          archive = archive.replace('/', '\\')
          for callback in self.ctx.callbacks:
               if self.vm is None:
                    break
               result = self.vm.call(callback, [to_bytes_text(disk), to_bytes_text(archive)])
               value = result[0] if result else None
               if value is False:
                    return
               if isinstance(value, str):
                    archive = to_text(value)
               elif value is not None:
                    raise ToolError('Invalid return values from callback function')
          converted = convert(disk, data, archive)
          if converted is None:
               self._put(archive, data)
               return
          for path, content in converted:
               self._put(path, content)
               self.ctx.log.append(f'{disk}: converted to {path}')

     def _put(self, archive, data):
          existing = _find_member(self.ctx.files, archive)
          self.ctx.files[existing or archive] = data
          _register_import(self.ctx.files, archive)

     def import_directory(self, directory):
          real = self.ctx.find_file(directory)
          if real is None or not os.path.isdir(real):
               raise ToolError(f"Import directory {directory} doesn't exist")
          for folder, _, names in os.walk(real):
               for filename in sorted(names):
                    full = os.path.join(folder, filename)
                    archive = os.path.relpath(full, real).replace(os.sep, '\\')
                    stem, extension = os.path.splitext(archive)
                    target = _DIRECTORY_CONVERSIONS.get(extension.lower())
                    if target is not None:
                         converted = convert(full, self.ctx.read_disk(full) or b'',
                                             stem + target)
                         if converted is not None:
                              for path, content in converted:
                                   self._put(path, content)
                              continue
                         # not converted: the original file under its own path
                         self.import_file(full, archive)
                         continue
                    for prefix, folder_path in _ICON_PREFIXES.items():
                         base = os.path.basename(archive)
                         if base.upper().startswith(prefix) and '\\' not in archive:
                              archive = folder_path + base[len(prefix):]
                              break
                    self.import_file(full, archive)

     def run_command(self, args, run_lua):
          if not args:
               raise ToolError('Wrong parameters for file importer, usage:\n'
                               'map.w3x lookuppaths ( directory | { diskfilepath '
                               'archivefilename } )\n'
                               'map.w3x lookuppaths script.lua { script.lua }')
          if args[0].lower().endswith('.lua'):
               for path in args:
                    run_lua(self, path)
               return
          if len(args) == 1:
               self.import_directory(args[0])
               return
          if len(args) % 2:
               raise ToolError('FileImporter needs pairs of disk and archive paths')
          for disk, archive in zip(args[::2], args[1::2]):
               self.import_file(disk, archive)

     def finish(self):
          return []


def _register_import(files, archive):
     """Add a path to war3map.imp, the editor's list of imported files."""
     from wc3files.imp.imp_file import ImpFile
     name = _find_member(files, 'war3map.imp') or 'war3map.imp'
     imp = ImpFile()
     if name in files and files[name]:
          imp._data = imp._read(_reader(files[name]))
     paths = {entry['path'].lower() for entry in imp.get_data()['files']}
     if archive.lower() in paths:
          return
     imp.get_data()['files'].append({'flags': 13, 'path': archive})
     files[name] = _imp_bytes(imp)


def _reader(data):
     from wc3files.files import BinaryFile
     reader = BinaryFile(bytes(data), 'read')
     reader.open()
     return reader


def _imp_bytes(imp):
     from wc3files.files import BinaryFile
     writer = BinaryFile(b'', 'write')
     writer.open()
     imp._write(writer, imp.get_data())
     return writer.file.getvalue()


# --- FileExporter -----------------------------------------------------------------

EXPORT_GROUPS = ('triggers', 'objects', 'constants', 'environment', 'imports')
_ENVIRONMENT_FILES = ('war3mapMap.tga', 'war3mapPreview.tga', 'war3mapPath.tga',
                      'war3mapMap.blp', 'war3map.w3e', 'war3map.mmp', 'war3map.wpm',
                      'war3map.shd', 'war3map.doo', 'war3mapUnits.doo', 'war3map.w3r',
                      'war3map.w3c')
_CONSTANT_FILES = ('war3mapMisc.txt', 'war3mapSkin.txt', 'war3mapExtra.txt')


class FileExporter:
     """FileExporter: copy files out of the map onto disk."""

     name = 'FileExporter'

     def __init__(self, ctx):
          self.ctx = ctx

     def install(self, vm):
          ctx = self.ctx
          install_log(vm, ctx)

          def exportfile(args):
               archive = _safe_string(vm, args[0] if args else None)
               disk = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'exportfile {archive} {disk}')
               try:
                    self.export_file(archive, disk)
               except ToolError as error:
                    raise LuaError(str(error)) from None
               return []

          def exportgroup(args):
               index = _to_integer(args[0] if args else None)
               target = _safe_string(vm, args[1] if len(args) > 1 else None)
               ctx.log.append(f'exportgroup {index} {target}')
               if not 0 <= index < len(EXPORT_GROUPS):
                    raise LuaError('Invalid group identifier, expected triggers, objects, '
                                   'constants, environment or imports')
               try:
                    self.export_group(EXPORT_GROUPS[index], target)
               except ToolError as error:
                    raise LuaError(str(error)) from None
               return []

          vm.register('exportfile', exportfile)
          vm.register('exportgroup', exportgroup)
          for index, name in enumerate(EXPORT_GROUPS):
               vm.globals.set(name, index)

     def export_file(self, archive, disk):
          member = _find_member(self.ctx.files, archive)
          if member is None:
               raise ToolError(f'Could not export {archive} to {disk}')
          self._write(disk, self.ctx.files[member])

     def _write(self, disk, data):
          if self.ctx.write_file is None:
               raise ToolError(f'Could not write {disk}')
          try:
               self.ctx.write_file(disk, data)
          except OSError:
               raise ToolError(f'Could not write {disk}') from None

     def export_group(self, group, target):
          files = self.ctx.files

          def out(sub, name):
               member = _find_member(files, name)
               if member is not None:
                    self._write(target.rstrip('\\/') + '\\' + sub + name, files[member])

          if group == 'triggers':
               for name in ('war3map.wtg', 'war3map.wct'):
                    out('Triggers\\', name)
          elif group == 'objects':
               for name in MAP_FILES.values():
                    out('Objects\\', name)
          elif group == 'constants':
               for name in _CONSTANT_FILES:
                    out('Constants\\', name)
          elif group == 'environment':
               for name in _ENVIRONMENT_FILES:
                    out('Environment\\', name)
          elif group == 'imports':
               from wc3files.imp.imp_file import ImpFile
               member = _find_member(files, 'war3map.imp')
               if member is None:
                    return
               imp = ImpFile()
               imp._data = imp._read(_reader(files[member]))
               for entry in imp.get_data()['files']:
                    out('Imports\\', entry['path'])

     def run_command(self, args, run_lua):
          if args and args[0].lower().endswith('.lua'):
               for path in args:
                    run_lua(self, path)
               return
          if len(args) > 1:
               raise ToolError('Wrong parameters for file exporter, usage:\n'
                               'map.w3x lookuppaths [targetdir]\n'
                               'map.w3x lookuppaths script.lua { script.lua }')
          target = args[0] if args else 'Export'
          for group in EXPORT_GROUPS:
               self.export_group(group, target)

     def finish(self):
          return []


# --- ConstantMerger ----------------------------------------------------------------

class ConstantMerger:
     """ConstantMerger: merge gameplay constants (war3mapMisc.txt,
     war3mapSkin.txt, war3mapExtra.txt) into the map."""

     name = 'ConstantMerger'

     def __init__(self, ctx):
          self.ctx = ctx

     def install(self, vm):
          ctx = self.ctx
          install_log(vm, ctx)

          def mergefile(args):
               path = _safe_string(vm, args[0] if args else None)
               ctx.log.append(f'mergefile {path}')
               try:
                    self.merge_file(path)
               except ToolError as error:
                    raise LuaError(str(error)) from None
               return []

          def setvalue(args):
               section = _safe_string(vm, args[0] if args else None)
               key = _safe_string(vm, args[1] if len(args) > 1 else None)
               value = _safe_string(vm, args[2] if len(args) > 2 else None)
               ctx.log.append(f'setvalue [{section}] {key}={value}')
               self.set_value('war3mapMisc.txt', section, key, value)
               return []

          vm.register('mergefile', mergefile)
          vm.register('setvalue', setvalue)

     def merge_file(self, path):
          name = os.path.basename(path.replace('\\', '/'))
          target = next((n for n in _CONSTANT_FILES if n.lower() == name.lower()), None)
          if target is None:
               raise ToolError(f'{path}: expected war3mapMisc.txt, war3mapSkin.txt or '
                               f'war3mapExtra.txt')
          data = self.ctx.read_disk(path)
          if data is None:
               raise ToolError(f"{path} doesn't exist")
          for section, key, value in _ini_items(data.decode('utf-8', 'replace')):
               self.set_value(target, section, key, value)

     def set_value(self, name, section, key, value):
          member = _find_member(self.ctx.files, name) or name
          text = self.ctx.files.get(member, b'').decode('utf-8', 'replace')
          self.ctx.files[member] = _ini_set(text, section, key, value).encode('utf-8')

     def run_command(self, args, run_lua):
          if not args:
               raise ToolError('Wrong parameters for constant merger, usage:\n'
                               'map.w3x lookuppaths mergefile { moremergefiles }\n'
                               'map.w3x lookuppaths script.lua { script.lua }')
          if args[0].lower().endswith('.lua'):
               for path in args:
                    run_lua(self, path)
               return
          for path in args:
               self.merge_file(path)

     def finish(self):
          return []


def _ini_items(text):
     section = ''
     for line in text.splitlines():
          stripped = line.strip()
          if not stripped or stripped.startswith(('//', ';')):
               continue
          if stripped.startswith('[') and stripped.endswith(']'):
               section = stripped[1:-1]
               continue
          if '=' in stripped:
               key, _, value = stripped.partition('=')
               yield section, key.strip(), value.strip()


def _ini_set(text, section, key, value):
     lines = text.splitlines()
     start = None
     for i, line in enumerate(lines):
          if line.strip().lower() == f'[{section.lower()}]':
               start = i
               break
     if start is None:
          if lines and lines[-1].strip():
               lines.append('')
          lines.extend([f'[{section}]', f'{key}={value}'])
          return '\r\n'.join(lines) + '\r\n'
     end = len(lines)
     for i in range(start + 1, len(lines)):
          if lines[i].strip().startswith('['):
               end = i
               break
     for i in range(start + 1, end):
          name = lines[i].split('=', 1)[0].strip()
          if '=' in lines[i] and name.lower() == key.lower():
               lines[i] = f'{key}={value}'
               return '\r\n'.join(lines) + '\r\n'
     insert = end
     while insert > start + 1 and not lines[insert - 1].strip():
          insert -= 1
     lines.insert(insert, f'{key}={value}')
     return '\r\n'.join(lines) + '\r\n'


# --- PathMapper, TileSetter, TriggerMerger ------------------------------------------

def _parsed(cls, data):
     handler = cls()
     handler._data = handler._read(_reader(data))
     return handler


def _serialized(handler):
     from wc3files.files import BinaryFile
     writer = BinaryFile(b'', 'write')
     writer.open()
     handler._write(writer, handler.get_data())
     return writer.file.getvalue()


def _map_file(files, name):
     member = _find_member(files, name)
     if member is None:
          raise ToolError(f'the map has no {name}')
     return member


def _no_lua(tool):
     raise ToolError(f'Lua scripts for the {tool} are not supported: its Lua functions are '
                     f'not documented; use the command-line form')


_TILE_ID = re.compile(r'^(?:[A-Za-z0-9]{4})+$')
_HEX_MASK = re.compile(r'^[0-9A-Fa-f]{1,2}$')


class PathMapper:
     """PathMapper: change the pathing map (war3map.wpm) where a tile or a
     pathing value matches.

          PathMapper <tiles | mask> <value> [check] [apply]

     tiles are 4-character ground tile IDs written together (LdrtLgrs), a mask
     is one hexadecimal byte; value is hexadecimal. The check is ! (tiles not
     listed) for tiles, 1 (every mask bit set) or 0 (no bit outside the mask
     set) for a mask; without one, a listed tile or a value equal to the mask
     matches. The apply mode is |, & or ^; without one the value replaces the
     cell's.

     A pathing cell is a quarter of a tile in each direction; its tile is the
     one at the nearest tile corner (war3map.w3e keeps a ground tile per
     corner, and the game blends the four around a tile).
     """

     name = 'PathMapper'

     def __init__(self, ctx):
          self.ctx = ctx

     def install(self, vm):
          _no_lua(self.name)

     def run_command(self, args, run_lua):
          if args and args[0].lower().endswith('.lua'):
               _no_lua(self.name)
          if len(args) < 2:
               raise ToolError('Wrong parameters for path mapper, usage:\n'
                               'map.w3x lookuppaths tiles|mask value [check] [apply]')
          selector, value = args[0], args[1]
          check = apply = None
          for extra in args[2:]:
               if extra in ('|', '&', '^'):
                    apply = extra
               elif extra in ('!', '0', '1'):
                    check = extra
               else:
                    raise ToolError(f'PathMapper: unknown mode {extra!r}')
          try:
               value = int(value, 16)
          except ValueError:
               raise ToolError(f'PathMapper: the value {args[1]!r} is not hexadecimal') from None
          if not 0 <= value <= 0xFF:
               raise ToolError(f'PathMapper: the value {args[1]} is not one byte')
          if _HEX_MASK.match(selector):
               if check == '!':
                    raise ToolError('PathMapper: ! is a check for tiles, a mask takes 0 or 1')
               self.map_cells(mask=int(selector, 16), check=check, value=value, apply=apply)
          elif _TILE_ID.match(selector):
               if check in ('0', '1'):
                    raise ToolError('PathMapper: 0 and 1 are checks for a mask, tiles take !')
               tiles = [selector[i:i + 4] for i in range(0, len(selector), 4)]
               self.map_cells(tiles=tiles, check=check, value=value, apply=apply)
          else:
               raise ToolError(f'PathMapper: {selector!r} is neither tile IDs nor a hexadecimal mask')

     def map_cells(self, value, tiles=None, mask=None, check=None, apply=None):
          from wc3files.wpm import PathingFile
          files = self.ctx.files
          member = _map_file(files, 'war3map.wpm')
          pathing = _parsed(PathingFile, files[member])
          data = pathing.get_data()
          width, height = data['width'], data['height']
          cells = bytearray(data['cells'])
          if tiles is not None:
               cell_tiles = self._cell_tiles(width, height)
               wanted = {tile.lower() for tile in tiles}
          changed = 0
          for index, old in enumerate(cells):
               if tiles is not None:
                    listed = cell_tiles[index] in wanted
                    match = not listed if check == '!' else listed
               elif check == '1':
                    match = old & mask == mask
               elif check == '0':
                    match = old & ~mask & 0xFF == 0
               else:
                    match = old == mask
               if not match:
                    continue
               new = (old | value if apply == '|' else old & value if apply == '&'
                      else old ^ value if apply == '^' else value)
               if new != old:
                    cells[index] = new
                    changed += 1
          data['cells'] = bytes(cells)
          files[member] = _serialized(pathing)
          self.ctx.log.append(f'PathMapper: {changed} of {len(cells)} pathing cells changed')

     def _cell_tiles(self, width, height):
          from wc3files.w3e import TerrainFile
          files = self.ctx.files
          terrain = _parsed(TerrainFile, files[_map_file(files, 'war3map.w3e')]).get_data()
          corners_x, corners_y = terrain['width'], terrain['height']
          if (corners_x - 1) * 4 != width or (corners_y - 1) * 4 != height:
               raise ToolError(f'war3map.wpm ({width}x{height}) does not match war3map.w3e '
                               f'({corners_x}x{corners_y} tile corners)')
          names = [tile.lower() for tile in terrain['ground_tilesets']]
          points = terrain['tiles']
          out = []
          for y in range(height):
               corner_y = y // 4 + (1 if y % 4 >= 2 else 0)
               for x in range(width):
                    corner_x = x // 4 + (1 if x % 4 >= 2 else 0)
                    texture = points[corner_y * corners_x + corner_x]['ground_texture']
                    out.append(names[texture] if texture < len(names) else None)
          return out

     def finish(self):
          return []


class TileSetter:
     """TileSetter: set the map's tileset and its ground tiles (up to 16).

          TileSetter <tileset> <tiles>

     The ground tiles of war3map.w3e are kept by index, as the World Editor's
     own tileset dialog does: the terrain keeps its layout and shows the new
     tile at each index. A list shorter than the indices the terrain uses is
     refused. The tileset letter is set in war3map.w3e and war3map.w3i.
     """

     name = 'TileSetter'

     def __init__(self, ctx):
          self.ctx = ctx

     def install(self, vm):
          _no_lua(self.name)

     def run_command(self, args, run_lua):
          if args and args[0].lower().endswith('.lua'):
               _no_lua(self.name)
          if len(args) != 2 or len(args[0]) != 1:
               raise ToolError('Wrong parameters for tile setter, usage:\n'
                               'map.w3x lookuppaths tileset tiles')
          tileset, text = args
          if not _TILE_ID.match(text):
               raise ToolError(f'TileSetter: {text!r} is not a list of 4-character tile IDs')
          tiles = [text[i:i + 4] for i in range(0, len(text), 4)]
          if len(tiles) > 16:
               raise ToolError(f'TileSetter: {len(tiles)} tiles, the limit is 16')
          if len({t.lower() for t in tiles}) != len(tiles):
               raise ToolError('TileSetter: a tile is listed twice')
          self.set_tiles(tileset, tiles)

     def set_tiles(self, tileset, tiles):
          from wc3files.w3e import TerrainFile
          files = self.ctx.files
          member = _map_file(files, 'war3map.w3e')
          terrain = _parsed(TerrainFile, files[member])
          data = terrain.get_data()
          used = max((point['ground_texture'] for point in data['tiles']), default=-1)
          if used >= len(tiles):
               raise ToolError(f'TileSetter: the terrain uses ground tile {used + 1} of the '
                               f'map, the new list has {len(tiles)}')
          data['main_tileset'] = tileset
          data['ground_tilesets'] = list(tiles)
          data['custom_tileset'] = True
          files[member] = _serialized(terrain)
          self.ctx.log.append(f'TileSetter: tileset {tileset}, tiles {"".join(tiles)}')
          info = _find_member(files, 'war3map.w3i')
          if info is None:
               return
          from wc3files.w3i import W3iFile
          try:
               w3i = _parsed(W3iFile, files[info])
          except Exception as error:
               self.ctx.log.append(f'TileSetter: war3map.w3i left as it is ({error})')
               return
          w3i.get_data()['map_main_ground_type'] = tileset
          files[info] = _serialized(w3i)

     def finish(self):
          return []


class TriggerMerger:
     """TriggerMerger: merge the GUI variables and triggers of a wtg/wct pair
     into the map (wc3files.wtg.merge). Each argument names one file of a
     pair; the other is the file with the same name and the other extension."""

     name = 'TriggerMerger'

     def __init__(self, ctx):
          self.ctx = ctx

     def install(self, vm):
          _no_lua(self.name)

     def run_command(self, args, run_lua):
          if args and args[0].lower().endswith('.lua'):
               _no_lua(self.name)
          if not args:
               raise ToolError('Wrong parameters for trigger merger, usage:\n'
                               'map.w3x lookuppaths file.wtg { morefiles }')
          for path in args:
               self.merge_pair(path)

     def merge_pair(self, path):
          from wc3files.wtg import MapTriggersFile, MapCustomTextTriggersFile
          from wc3files.wtg.merge import merge_triggers, TriggerMergeError
          stem, extension = os.path.splitext(path)
          if extension.lower() not in ('.wtg', '.wct'):
               raise ToolError(f'{path}: expected a .wtg or .wct file')
          source_wtg = self.ctx.read_disk(stem + '.wtg')
          source_wct = self.ctx.read_disk(stem + '.wct')
          for data, ext in ((source_wtg, '.wtg'), (source_wct, '.wct')):
               if data is None:
                    raise ToolError(f"{stem + ext} doesn't exist")
          files = self.ctx.files
          wtg_member = _map_file(files, 'war3map.wtg')
          wct_member = _map_file(files, 'war3map.wct')
          try:
               target_wtg = _parsed(MapTriggersFile, files[wtg_member])
               target_wct = _parsed(MapCustomTextTriggersFile, files[wct_member])
               source = _parsed(MapTriggersFile, source_wtg)
               source_text = _parsed(MapCustomTextTriggersFile, source_wct)
          except (ValueError, KeyError) as error:
               raise ToolError(f'TriggerMerger: {error}') from None
          try:
               summary = merge_triggers(target_wtg.get_data(), target_wct.get_data(),
                                        source.get_data(), source_text.get_data(),
                                        header_name=os.path.basename(stem) + ' Header')
          except TriggerMergeError as error:
               raise ToolError(f'TriggerMerger: {error}') from None
          files[wtg_member] = _serialized(target_wtg)
          files[wct_member] = _serialized(target_wct)
          self.ctx.log.append(f'TriggerMerger {path}: {len(summary["variables"])} variable(s), '
                              f'{len(summary["triggers"])} trigger(s)'
                              + (f', header in {summary["header"]!r}' if summary['header'] else ''))

     def finish(self):
          return []


TOOLS = {tool.name.lower(): tool for tool in (ObjectMerger, FileImporter, FileExporter,
                                              ConstantMerger, PathMapper, TileSetter,
                                              TriggerMerger)}

# Grim Extension Pack tools that are recognized but not implemented here: the
# PatchGenerator writes World Editor patches (UMSWE), which have no use
# outside an installed editor.
UNSUPPORTED_TOOLS = ('PatchGenerator',)
