"""
A map's object data as Grim Extension Pack's ObjectMerger sees it.

`ObjectData` holds the seven object tables of a map (war3map.w3u, .w3t, .w3b,
.w3d, .w3a, .w3h, .w3q), each an 'original' table (changes to standard objects)
and a 'custom' table (new objects and their base id), in the dict layout
`wc3files.w3o.ObjectFile` reads and writes. On top of that it implements what
ObjectMerger does to them: create and modify objects, apply and reset
changes, merge object files, and generate ids.

The behaviour follows ObjectMerger 1.0's own code (Grim Extension Pack,
PitzerMike), read from the binary rather than guessed:

- an object exists when it is a custom object, a changed standard object, or a
  row of the game's data tables;
- `generateid` is a counter, not a search: it starts at `u~~~`, takes the base
  id's first character and steps the last three through the printable
  punctuation `!"#$%&()*+,-./:;<=>?@[]^_{|}~` (no letters, digits, quote,
  backslash or backquote), and it does not check whether the id is free;
- a change needs a level (or a doodad variation) exactly when the field
  repeats per level, and the value must parse as the field's type.
"""

import copy
import os
import tempfile

from wc3files.w3o import ObjectFile
from wc3files.w3o.data import ObjectFileType
from wc3files.w3o.object_metadata import ObjectMetaData
from wc3files.w3o.object_base_data import BaseObjectData
from wc3files.data import BinaryDataType


EXTENSIONS = ('w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q')
LEVELED = ('w3a', 'w3d', 'w3q')
MAP_FILES = {ext: f'war3map.{ext}' for ext in EXTENSIONS}

# setobjecttype() accepts the extensions and these names.
TYPE_NAMES = {
     'units': 'w3u', 'items': 'w3t', 'destructables': 'w3b', 'destructibles': 'w3b',
     'doodads': 'w3d', 'abilities': 'w3a', 'buffs': 'w3h', 'effects': 'w3h',
     'upgrades': 'w3q',
}

# The groups makechange/resetchange/resetobject apply to.
GROUPS = ('current', 'original', 'custom', 'modded', 'unmodded')


class ObjectMergerError(Exception):
     """An error ObjectMerger itself reports (a bad id, field or value)."""


def _next_char(c):
     """ObjectMerger's OBJ::nextchar: the next character of a generated id."""
     special = {'&': '(', '/': ':', '@': '[', '[': ']', '_': '{', '~': '!'}
     if c in special:
          return special[c]
     return chr(ord(c) + 1)


def resolve_type(name):
     """An object type name or extension to the extension, or None."""
     if not isinstance(name, str):
          return None
     key = name.strip()
     if key in EXTENSIONS:
          return key
     return TYPE_NAMES.get(key)


def valid_id(value):
     return isinstance(value, str) and len(value) == 4


class ObjectData:
     """The object tables of one map.

     files
          The map's files (`{name: bytes}`); the war3map.w3u ... w3q among them
          are loaded. Others are ignored.
     metadata, base_data
          The field definitions and the standard objects to check against;
          the bundled game data by default.
     """

     def __init__(self, files=None, metadata=None, base_data=None):
          self.metadata = metadata or ObjectMetaData.default()
          self.base_data = base_data or BaseObjectData.default()
          self.tables = {ext: {'original': {}, 'custom': {}} for ext in EXTENSIONS}
          self._objects = {}
          self.changed = set()
          self._counters = {}
          self._fields = {}
          for name, data in (files or {}).items():
               ext = _map_object_ext(name)
               if ext is not None and data:
                    self.load(ext, data)

     # -- loading and saving --------------------------------------------------

     def load(self, ext, data):
          obj = ObjectFile()
          obj.read(bytes(data), ext)
          self.tables[ext] = obj.export_data()[ext]
          self._objects[ext] = obj

     def to_bytes(self, ext):
          """The war3map.<ext> file for this table."""
          obj = self._objects.get(ext)
          if obj is None:
               obj = ObjectFile()
               obj.set_object_type(ObjectFileType(ext))
          data = obj.export_data()
          data[ext] = self.tables[ext]
          obj.import_data(data)
          handle, path = tempfile.mkstemp(suffix='.' + ext)
          os.close(handle)
          try:
               obj.write(path)
               with open(path, 'rb') as fh:
                    return fh.read()
          finally:
               os.remove(path)

     def changed_files(self):
          """`{map file name: bytes}` for every table that was changed."""
          return {MAP_FILES[ext]: self.to_bytes(ext) for ext in sorted(self.changed)}

     # -- queries ----------------------------------------------------------------

     def is_standard(self, ext, object_id):
          try:
               return object_id in self.base_data.ids(ObjectFileType(ext))
          except Exception:
               return False

     def exists(self, ext, object_id):
          table = self.tables[ext]
          return (object_id in table['custom'] or object_id in table['original']
                  or self.is_standard(ext, object_id))

     def field(self, ext, field_id):
          """The metadata row of a field, or None when the type has no such
          field."""
          key = (ext, field_id)
          if key in self._fields:
               return self._fields[key]
          row = None
          try:
               row = self.metadata.field(ObjectFileType(ext), field_id)
          except KeyError:
               if ext == 'w3t':
                    # Items share the unit metadata; their own table holds only
                    # the item-only fields.
                    try:
                         unit_row = self.metadata.field(ObjectFileType.Units, field_id)
                         if unit_row.get('useItem'):
                              row = unit_row
                    except KeyError:
                         pass
          self._fields[key] = row
          return row

     def is_leveled(self, ext, field_id):
          row = self.field(ext, field_id)
          if row is None or ext not in LEVELED:
               return False
          try:
               return int(row.get('repeat') or 0) > 0
          except (TypeError, ValueError):
               return False

     def group(self, ext, group, current=None):
          """The objects a group identifier stands for: (table, id) pairs."""
          table = self.tables[ext]
          if group == 'current':
               if current is None:
                    raise ObjectMergerError("No object has been loaded, use 'modifyobject' "
                                            "or 'createobject' to do so")
               return [current]
          if group == 'custom':
               return [('custom', i) for i in list(table['custom'])]
          if group == 'modded':
               return [('original', i) for i in list(table['original'])]
          standard = list(self.base_data.ids(ObjectFileType(ext)))
          if group == 'unmodded':
               return [('original', i) for i in standard if i not in table['original']]
          if group == 'original':
               return [('original', i) for i in standard]
          raise ObjectMergerError('Invalid group identifier, expected current, original, '
                                  'custom, modded or unmodded')

     # -- objects -------------------------------------------------------------------

     def create(self, ext, base, new):
          """createobject: a new custom object `new` copied from `base`. An
          existing object with the new id is replaced."""
          for value in (base, new):
               if not valid_id(value):
                    raise ObjectMergerError(f'Invalid object id {value}')
          table = self.tables[ext]
          if base in table['custom']:
               entry = copy.deepcopy(table['custom'][base])
          elif self.exists(ext, base):
               entry = {'ID': base}
               if base in table['original']:
                    entry.update(copy.deepcopy(table['original'][base]))
          else:
               raise ObjectMergerError(f"The base object {base} doesn't exist")
          table['custom'][new] = entry
          self.changed.add(ext)
          return ('custom', new)

     def modify(self, ext, object_id):
          """modifyobject: the existing object to change."""
          if not valid_id(object_id):
               raise ObjectMergerError(f'Invalid object id {object_id}')
          table = self.tables[ext]
          if object_id in table['custom']:
               return ('custom', object_id)
          if object_id in table['original'] or self.is_standard(ext, object_id):
               return ('original', object_id)
          raise ObjectMergerError(f"The base object {object_id} doesn't exist")

     def reset_object(self, ext, target):
          table_name, object_id = target
          table = self.tables[ext][table_name]
          if object_id not in table:
               return
          if table_name == 'custom':
               table[object_id] = {'ID': table[object_id].get('ID', object_id)}
          else:
               del table[object_id]
          self.changed.add(ext)

     def generate_id(self, ext, base):
          """generateid: the next id of ObjectMerger's counter for this type."""
          if not valid_id(base):
               raise ObjectMergerError(f'Invalid object id {base}')
          counter = list(self._counters.get(ext, 'u~~~'))
          counter[0] = base[0]
          for position in (3, 2, 1):
               counter[position] = _next_char(counter[position])
               if counter[position] != '!':
                    break
          value = ''.join(counter)
          self._counters[ext] = value
          return value

     # -- changes -------------------------------------------------------------------

     def binary_type(self, ext, field_id, row):
          from wc3files.w3o.object_override import _load_confirmed_mod_id_types
          confirmed = _load_confirmed_mod_id_types().get(f'{ext}:{field_id}')
          if confirmed is not None:
               return confirmed
          kind = (row or {}).get('type')
          if kind in ('int', 'bool'):
               return BinaryDataType.INT.value
          if kind == 'real':
               return BinaryDataType.REAL.value
          if kind == 'unreal':
               return BinaryDataType.UNREAL.value
          return BinaryDataType.STRING.value

     def convert(self, ext, field_id, row, text):
          """A change's value text as the field stores it."""
          kind = self.binary_type(ext, field_id, row)
          if kind == BinaryDataType.INT.value:
               stripped = text.strip()
               try:
                    return int(stripped, 10), kind
               except ValueError:
                    pass
               try:
                    as_float = float(stripped)
               except ValueError:
                    as_float = None
               if as_float is not None and as_float.is_integer():
                    return int(as_float), kind
               raise ObjectMergerError(f'The value {text} is not a valid integer')
          if kind in (BinaryDataType.REAL.value, BinaryDataType.UNREAL.value):
               try:
                    return float(text.strip()), kind
               except ValueError:
                    raise ObjectMergerError(f'The value {text} is not a valid real') from None
          return text, kind

     def check_change(self, ext, field_id, level):
          """The field's metadata row; raises for an unknown field or a bad
          level."""
          row = self.field(ext, field_id)
          if row is None:
               raise ObjectMergerError(f'Invalid object field {field_id}')
          if level is not None:
               try:
                    number = int(str(level).strip())
               except ValueError:
                    raise ObjectMergerError(
                         f'The level/variation value {level} is not numeric') from None
               if number <= 0:
                    raise ObjectMergerError(
                         f'The level/variation value {level} must not be zero or negative')
          return row

     def make_change(self, ext, target, field_id, value, level=None):
          row = self.check_change(ext, field_id, level)
          converted, kind = self.convert(ext, field_id, row, value)
          table_name, object_id = target
          table = self.tables[ext][table_name]
          entry = table.setdefault(object_id, {} if table_name == 'original'
                                   else {'ID': object_id})
          if level is None:
               entry[field_id] = {'value': converted, 'type': kind}
          else:
               number = int(str(level).strip())
               try:
                    pointer = int(row.get('data') or 0)
               except (TypeError, ValueError):
                    pointer = 0
               mod = entry.get(field_id)
               if mod is None or not isinstance(mod.get('value'), dict):
                    mod = {'value': {}, 'ptr': {}, 'type': kind}
                    entry[field_id] = mod
               mod['type'] = kind
               mod['value'][number] = converted
               mod['ptr'][number] = pointer
          self.changed.add(ext)

     def reset_change(self, ext, target, field_id, level=None):
          self.check_change(ext, field_id, level)
          table_name, object_id = target
          entry = self.tables[ext][table_name].get(object_id)
          if entry is None or field_id not in entry:
               return
          if level is None:
               del entry[field_id]
          else:
               mod = entry[field_id]
               number = int(str(level).strip())
               if isinstance(mod.get('value'), dict):
                    for part in ('value', 'ptr', 'end', 'seq'):
                         if isinstance(mod.get(part), dict):
                              mod[part].pop(number, None)
                    if not mod['value']:
                         del entry[field_id]
          if table_name == 'original' and not entry:
               del self.tables[ext]['original'][object_id]
          self.changed.add(ext)

     # -- merging ---------------------------------------------------------------------

     def merge(self, ext, data, mode='merge'):
          """mergefile: merge an object file's objects into this map.

          merge
               changes are added to an object with the same id;
          replace
               an object with the same id is replaced;
          import
               a custom object whose id is taken gets a new id."""
          if mode not in ('merge', 'replace', 'import'):
               raise ObjectMergerError('Unsupported merge mode, expected merge, replace '
                                       'or import')
          other = ObjectFile()
          other.read(bytes(data), ext)
          incoming = other.export_data()[ext]
          table = self.tables[ext]
          for name in ('original', 'custom'):
               for object_id, entry in incoming.get(name, {}).items():
                    target_id = object_id
                    if mode == 'import' and name == 'custom' and \
                              (object_id in table['custom'] or self.is_standard(ext, object_id)):
                         target_id = self._free_id(ext, entry.get('ID', object_id))
                    if mode == 'merge' and target_id in table[name]:
                         existing = table[name][target_id]
                         for field_id, mod in entry.items():
                              if field_id == 'ID':
                                   continue
                              if isinstance(mod.get('value'), dict) and \
                                        isinstance(existing.get(field_id, {}).get('value'), dict):
                                   for part in ('value', 'ptr'):
                                        existing[field_id][part].update(mod[part])
                              else:
                                   existing[field_id] = copy.deepcopy(mod)
                    else:
                         table[name][target_id] = copy.deepcopy(entry)
          self.changed.add(ext)

     def _free_id(self, ext, base):
          for _ in range(100000):
               candidate = self.generate_id(ext, base)
               if not self.exists(ext, candidate):
                    return candidate
          raise ObjectMergerError(f'No free object id for {base}')


def _map_object_ext(name):
     lowered = name.replace('/', '\\').lower()
     for ext in EXTENSIONS:
          if lowered == f'war3map.{ext}':
               return ext
     return None
