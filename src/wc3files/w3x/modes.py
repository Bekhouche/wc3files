"""
Whole-map Slk and Obj modes, as in w3x2lni.

- `map_to_slk(src, dst)`: every object file's data moves into the literal
  tables the game reads (Units\\UnitData.slk, Units\\AbilityData.slk, ...),
  through ObjectFile.to_override_tables; only the fields no table has a
  column for (models, icons, tooltips: the metadata's 'Profile' fields)
  stay in a much smaller war3map.w3u/w3a/... An object file whose
  conversion would not come back to the same objects is left as it is.
- `map_to_obj(src, dst)`: the reverse. A map that ships those tables gets
  ordinary object files again (ObjectFile.from_override_tables), and the
  tables are removed.

Slk mode is what a released map uses: it is not meant to be edited in the
World Editor afterwards. As in w3x2lni, Slk -> Obj cannot recover what the
tables do not hold, such as the object a custom object was copied from; the
report lists every such guess.

`report` lists, per object file, what happened: converted, kept (and why),
and for Obj mode the rebuild report's guesses.
"""

import os
import tempfile

from wc3files.w3o.data import ObjectFileType
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3o.object_override import literal_table_paths
from wc3files.table.slk_file import SlkFile

_OBJECT_EXTS = ('w3u', 'w3t', 'w3a', 'w3q', 'w3b', 'w3d', 'w3h')


def _read_files(src):
     from wc3files.w3x import W3xFile
     archive = W3xFile()
     archive.read(src)
     return {name: bytes(data) for name, data in (archive.files or {}).items()
             if data is not None and name.lower() not in ('(listfile)', '(attributes)')}


def _write_map(files, dst):
     from wc3files.w3x import W3xFile
     files = dict(files)
     files['(listfile)'] = '\r\n'.join(files).encode('utf-8') + b'\r\n'
     W3xFile().build_from_files(files, dst)


def _find(files, name):
     lower = name.lower()
     return next((k for k in files if k.lower() == lower), None)


def _object(data, ext):
     handle = tempfile.NamedTemporaryFile(suffix='.' + ext, delete=False)
     handle.write(data)
     handle.close()
     try:
          obj = ObjectFile()
          obj.read(handle.name)
          return obj
     finally:
          os.remove(handle.name)


def _object_bytes(obj, ext):
     path = tempfile.mktemp(suffix='.' + ext)
     try:
          obj.write(path)
          with open(path, 'rb') as h:
               return h.read()
     finally:
          if os.path.exists(path):
               os.remove(path)


def _slk_bytes(rows):
     path = tempfile.mktemp(suffix='.slk')
     try:
          slk = SlkFile()
          slk.import_data(rows, id_column='id')
          slk.write(path, id_column='id')
          with open(path, 'rb') as h:
               return h.read()
     finally:
          if os.path.exists(path):
               os.remove(path)


def _slk_rows(data):
     path = tempfile.mktemp(suffix='.slk')
     try:
          with open(path, 'wb') as h:
               h.write(data)
          slk = SlkFile()
          slk.read(path)
          return slk.export_data()
     finally:
          if os.path.exists(path):
               os.remove(path)


def _plain(objects):
     """Objects without the bookkeeping that does not change their meaning."""
     out = {}
     for table in ('original', 'custom'):
          for object_id, mods in ((objects or {}).get(table) or {}).items():
               fields = {}
               for field_id, mod in mods.items():
                    if field_id == 'ID':
                         continue
                    value = mod.get('value')
                    if isinstance(value, dict):
                         value = {level: v for level, v in value.items()}
                    fields[field_id] = _number(value)
               out[(table, object_id)] = fields
     return out


def _number(value):
     if isinstance(value, dict):
          return {k: _number(v) for k, v in value.items()}
     if isinstance(value, float):
          return round(value, 4)
     return value


def map_to_slk(src, dst, files=None):
     """Write `dst`, `src` in Slk mode; returns the report."""
     files = dict(files) if files is not None else _read_files(src)
     report = {}
     for ext in _OBJECT_EXTS:
          name = _find(files, f'war3map.{ext}')
          if name is None:
               continue
          object_type = ObjectFileType(ext)
          targets = literal_table_paths(object_type)
          clash = [t for t in targets if _find(files, t)]
          if clash:
               report[name] = f'kept: the map already has {clash[0]}'
               continue
          try:
               obj = _object(files[name], ext)
               original = obj.export_data()[ext]
               tables, remainder = obj.to_override_tables()
               # Every custom object stays in the object file with its parent:
               # one with no changed field (a plain copy) would vanish otherwise,
               # and the parent is what Obj mode needs to rebuild it.
               for object_id, mods in (original.get('custom') or {}).items():
                    remainder['custom'].setdefault(object_id, {})['ID'] = mods.get('ID')
               for object_id, mods in (original.get('original') or {}).items():
                    if not any(object_id in rows for rows in tables.values()):
                         remainder['original'].setdefault(object_id, {})
               # A field the tables cannot bring back as it was (one equal to
               # the game's default, a value the table rounds) is pinned in
               # the object file, which the game applies over the tables.
               for _ in range(3):
                    rebuilt, _ = ObjectFile.from_override_tables(object_type, tables, remainder)
                    back = rebuilt.export_data()[ext]
                    if not _pin(original, back, remainder):
                         break
          except Exception as error:
               report[name] = f'kept: {type(error).__name__}: {error}'
               continue
          lost = _differences(original, back)
          if lost:
               report[name] = f'kept: {lost}'
               continue
          for path, rows in tables.items():
               files[path] = _slk_bytes(rows)
          if remainder['original'] or remainder['custom']:
               rest = ObjectFile()
               data = {e: None for e in _OBJECT_EXTS}
               data[ext] = remainder
               rest.import_data(data)
               rest._version = obj._version
               rest._skin_used = obj._skin_used
               files[name] = _object_bytes(rest, ext)
               kept = len(remainder['original']) + len(remainder['custom'])
          else:
               del files[name]
               kept = 0
          report[name] = (f'converted: {len(tables)} table(s), '
                          f'{kept} object(s) keep fields only the object file can hold')
     _write_map(files, dst)
     return report


def _pin(original, back, remainder):
     """Copy into `remainder` every original field `back` does not hold as
     it was; returns how many were pinned."""
     a = _plain(original)
     b = _plain(back)
     pinned = 0
     for (table, object_id), fields in a.items():
          have = b.get((table, object_id), {})
          for field_id, value in fields.items():
               if have.get(field_id) == value:
                    continue
               target = remainder[table].setdefault(object_id, {})
               if table == 'custom':
                    target['ID'] = original[table][object_id].get('ID')
               if field_id not in target:
                    target[field_id] = original[table][object_id][field_id]
                    pinned += 1
     return pinned


def _differences(original, back):
     """What a table round trip changed, as text, or '' when nothing did. A
     custom object's parent is not compared: tables do not hold it, and the
     game does not need it."""
     a = _plain(original)
     b = _plain(back)
     # a standard object listed with no change does nothing either way
     missing = [k for k in a if k not in b and (k[0] == 'custom' or a[k])]
     if missing:
          return f'{len(missing)} object(s) would be lost ({missing[0][1]})'
     for key, fields in a.items():
          for field_id, value in fields.items():
               if b[key].get(field_id) != value:
                    return (f'{key[1]}.{field_id} would change '
                            f'({str(value)[:40]} -> {str(b[key].get(field_id))[:40]})')
     return ''


def map_to_obj(src, dst, files=None):
     """Write `dst`, `src` with its literal tables turned back into object
     files; returns the report."""
     files = dict(files) if files is not None else _read_files(src)
     report = {}
     for ext in _OBJECT_EXTS:
          object_type = ObjectFileType(ext)
          tables = {}
          for path in literal_table_paths(object_type):
               key = _find(files, path)
               if key is not None:
                    tables[path] = _slk_rows(files[key])
          if not tables:
               continue
          name = _find(files, f'war3map.{ext}') or f'war3map.{ext}'
          remainder = {'original': {}, 'custom': {}}
          version = skin = None
          if name in files:
               obj = _object(files[name], ext)
               remainder = obj.export_data()[ext]
               version, skin = obj._version, obj._skin_used
          rebuilt, rebuild_report = ObjectFile.from_override_tables(object_type, tables, remainder)
          rebuilt._version = version
          rebuilt._skin_used = skin
          files[name] = _object_bytes(rebuilt, ext)
          for path in tables:
               del files[_find(files, path)]
          guesses = {k: len(v) for k, v in rebuild_report.items() if v}
          report[name] = 'rebuilt from ' + ', '.join(sorted(tables)) + \
               (f' ({", ".join(f"{n} {k}" for k, n in guesses.items())})' if guesses else '')
     _write_map(files, dst)
     return report
