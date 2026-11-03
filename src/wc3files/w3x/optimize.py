"""
Map optimizer passes, as w3x2lni's Slk-mode settings.

- `remove_unused_objects(files)`: drop custom objects nothing refers to.
- `remove_we_only(files)`: drop the files only the World Editor reads.

Each takes and returns a {name: bytes} table, with a report. They are run
by `wc3files optimize`.

## What counts as a reference

Only custom objects are ever removed: a changed standard object can be used
by the game itself (melee start, a shop's built-in list) and is always
kept. A custom object is kept if its id appears

1. in a script: a JASS rawcode ('A000'), any four-character string ("A000",
   FourCC('A000')), or an integer literal equal to the id;
2. in placed units, items and doodads (war3mapUnits.doo, war3map.doo), or
   the map info (war3map.w3i: random unit and item tables), as any
   four-character string in their data;
3. in any text or table file of the map (.txt, .slk, .ini, .fdf, ...);
4. in any field of an object that is kept (ability, build and research
   lists, buffs, requirements, a tooltip's <A000,DataA1>), followed until
   nothing new is found.

This errs on the side of keeping: a stray match only means one object
fewer removed, never a removed object the map uses. What it cannot see is
an id the script builds at run time (from pieces, or arithmetic on ids); a
map that does that should not use this pass.
"""

import math
import os
import re
import tempfile

from wc3files.w3o.object_file import ObjectFile

_OBJECT_EXTS = ('w3u', 'w3t', 'w3a', 'w3q', 'w3b', 'w3d', 'w3h')
_TEXT_EXTS = ('.txt', '.slk', '.ini', '.fdf', '.toc', '.j', '.lua', '.ai', '.wct')
_TOKEN = re.compile(rb'[A-Za-z0-9_]{4}')


def _rawcode_int(object_id):
     value = 0
     for c in object_id:
          value = value * 256 + (ord(c) & 0xFF)
     return value


def _object(data, ext):
     path = tempfile.mktemp(suffix='.' + ext)
     try:
          with open(path, 'wb') as h:
               h.write(data)
          obj = ObjectFile()
          obj.read(path)
          return obj
     finally:
          if os.path.exists(path):
               os.remove(path)


def _object_bytes(obj, ext):
     path = tempfile.mktemp(suffix='.' + ext)
     try:
          obj.write(path)
          with open(path, 'rb') as h:
               return h.read()
     finally:
          if os.path.exists(path):
               os.remove(path)


def _strings_in(value, out):
     """Every string inside a JSON-like value."""
     if isinstance(value, str):
          out.append(value)
     elif isinstance(value, dict):
          for v in value.values():
               _strings_in(v, out)
     elif isinstance(value, (list, tuple)):
          for v in value:
               _strings_in(v, out)
     return out


def _tokens(text):
     data = text.encode('latin-1', 'replace') if isinstance(text, str) else text
     return {m.group(0).decode('latin-1') for m in _TOKEN.finditer(data)}


def _structured_tokens(name, data):
     from wc3files.cli.formats import FORMATS
     kind = {'war3mapunits.doo': 'doo', 'war3map.doo': 'doodads',
             'war3map.w3i': 'w3i'}.get(name.lower())
     path = tempfile.mktemp(suffix='.' + name.rsplit('.', 1)[-1])
     try:
          with open(path, 'wb') as h:
               h.write(data)
          dumped = FORMATS[kind].dump(path)
     except Exception:
          return _tokens(data)                 # unreadable: every token counts
     finally:
          if os.path.exists(path):
               os.remove(path)
     return {s for s in _strings_in(dumped, []) if len(s) == 4} | \
          {t for s in _strings_in(dumped, []) for t in _tokens(s)}


def _script_tokens(data, custom):
     text = data.decode('utf-8', 'replace')
     found = set(re.findall(r"'([^'\\\n]{4})'", text))
     found |= set(re.findall(r'"([^"\\\n]{4})"', text))
     by_int = {_rawcode_int(i): i for i in custom}
     for number in re.findall(r'(?<![\w.])(\d{6,10})(?![\w.])', text):
          if int(number) in by_int:
               found.add(by_int[int(number)])
     return found


def _object_tokens(mods):
     found = set()
     for field_id, mod in mods.items():
          if field_id == 'ID':
               continue
          value = mod.get('value')
          values = value.values() if isinstance(value, dict) else [value]
          for v in values:
               if isinstance(v, str):
                    found |= _tokens(v)
     return found


def remove_unused_objects(files):
     """(files, report): `files` without the custom objects nothing refers to.
     report = {'removed': {ext: [ids]}, 'kept': count}."""
     files = dict(files)
     objects = {}
     for ext in _OBJECT_EXTS:
          for skin in ('', 'Skin'):
               name = next((n for n in files if n.lower() == f'war3map{skin}.{ext}'.lower()), None)
               if name is not None:
                    try:
                         objects[name] = (ext, _object(files[name], ext))
                    except Exception:
                         return files, {'removed': {}, 'kept': None,
                                        'skipped': f'{name} could not be read'}
     custom = {}
     standard_refs = set()
     for name, (ext, obj) in objects.items():
          data = obj.export_data()[ext] or {}
          for object_id, mods in (data.get('custom') or {}).items():
               custom.setdefault(object_id, []).append(mods)
          for mods in (data.get('original') or {}).values():
               standard_refs |= _object_tokens(mods)
     if not custom:
          return files, {'removed': {}, 'kept': 0}

     used = set(standard_refs)
     for name, data in files.items():
          lower = name.lower()
          if name in objects or data is None:
               continue
          if lower in ('war3mapunits.doo', 'war3map.doo', 'war3map.w3i'):
               used |= _structured_tokens(name, data)
          elif lower.endswith(('.j', '.lua', '.ai')):
               used |= _script_tokens(data, custom)
          elif lower.endswith(_TEXT_EXTS):
               used |= _tokens(data)

     queue = [i for i in custom if i in used]
     seen = set(queue)
     while queue:
          object_id = queue.pop()
          for mods in custom[object_id]:
               for token in _object_tokens(mods):
                    if token in custom and token not in seen:
                         seen.add(token)
                         queue.append(token)
     keep = seen
     removed = {}
     for name, (ext, obj) in objects.items():
          data = obj.export_data()[ext]
          drop = [i for i in (data.get('custom') or {}) if i not in keep]
          if not drop:
               continue
          for object_id in drop:
               del data['custom'][object_id]
          files[name] = _object_bytes(obj, ext)
          if 'skin' not in name.lower():
               removed[ext] = sorted(drop)
     return files, {'removed': removed, 'kept': len(keep)}


def squeeze_models(files):
     """(files, report): every version 800 .mdx squeezed (see
     wc3files.mdl.squeeze: lossy, about 0.8% precision, a third smaller once
     compressed)."""
     from wc3files.mdl.squeeze import squeeze_mdx
     files = dict(files)
     squeezed = []
     for name, data in files.items():
          if data is not None and name.lower().endswith('.mdx'):
               out = squeeze_mdx(data)
               if out is not data:
                    files[name] = out
                    squeezed.append(name)
     return files, {'squeezed': squeezed}


_REFERENCE = re.compile(r'<([^<>]*)>')
_COMPUTED_TYPES = ('w3a', 'w3u', 'w3t', 'w3q')


def computed_text(files):
     """(files, report): the <A000,DataA1> references in the map's own
     tooltips and other text replaced by the values they stand for, as
     w3x2lni's computed_text does: the referenced object's field (the map's
     change, else the game's default), at that level, rounded down to a
     whole number; `,%` multiplies by 100 first; mindmg/maxdmg/realhp are
     computed from the damage and life fields. A reference that cannot be
     worked out is left as it is, and counted."""
     from wc3files.w3o.data import ObjectFileType
     from wc3files.w3o.object_base_data import BaseObjectData
     from wc3files.w3o.object_metadata import ObjectMetaData, field_display_name
     files = dict(files)
     metadata = ObjectMetaData.default()
     base_data = BaseObjectData.default()
     loaded = {}         # ext -> [(name, ObjectFile)]: the object file and its skin file
     for ext in _COMPUTED_TYPES:
          for prefix in ('war3map', 'war3mapskin'):
               name = next((n for n in files if n.lower() == f'{prefix}.{ext}'), None)
               if name is not None:
                    loaded.setdefault(ext, []).append((name, _object(files[name], ext)))

     def columns(ext, object_id):
          """lower-case column -> value for an object, map over game."""
          object_type = ObjectFileType(ext)
          mods = None
          for _, obj in loaded.get(ext, []):
               data = obj.export_data()[ext] or {}
               found = (data.get('custom') or {}).get(object_id) or \
                    (data.get('original') or {}).get(object_id)
               if found:
                    mods = dict(mods or {}, **found)
          base_id = (mods or {}).get('ID', object_id)
          row = base_data.get(object_type, base_id)
          if row is None and mods is None:
               return None
          out = {str(k).lower(): v for k, v in (row or {}).items()}
          fields = metadata.fields(object_type)
          for field_id, mod in (mods or {}).items():
               if field_id == 'ID' or field_id not in fields:
                    continue
               column = field_display_name(fields[field_id])
               value = mod.get('value')
               if isinstance(value, dict):
                    for level, v in value.items():
                         out[f'{column}{level}'.lower() if level else column.lower()] = v
               else:
                    out[column.lower()] = value
          return out

     cache = {}

     def lookup(object_id):
          if object_id not in cache:
               cache[object_id] = next((c for c in (columns(e, object_id)
                                                    for e in _COMPUTED_TYPES) if c), None)
          return cache[object_id]

     def number(value):
          try:
               return float(value)
          except (TypeError, ValueError):
               return None

     def value_of(reference):
          parts = reference.split(',')
          if len(parts) < 2:
               return None
          row = lookup(parts[0][:4])
          if row is None:
               return None
          key = parts[1].strip().lower()
          get = lambda k: number(row.get(k)) or 0.0
          if key in ('mindmg1', 'mindmg2'):
               n = key[-1]
               result = get(f'dmgplus{n}') + get(f'dice{n}')
          elif key in ('maxdmg1', 'maxdmg2'):
               n = key[-1]
               result = get(f'dmgplus{n}') + get(f'dice{n}') * get(f'sides{n}')
          elif key == 'realhp':
               result = get('hp')
          else:
               result = number(row.get(key))
               if result is None:
                    m = re.match(r'^(.*\D)(\d+)$', key)
                    levels = number(row.get('levels'))
                    if m and levels is not None and int(m.group(2)) > levels:
                         result = 0.0                # past the last level
               if result is None:
                    return None
          if len(parts) > 2 and parts[2].strip() == '%':
               result *= 100
          return str(int(math.floor(result + 0.00005)))

     replaced = failed = 0
     trigstrings = set()

     def substitute_text(text):
          nonlocal replaced, failed

          def one(m):
               nonlocal replaced, failed
               out = value_of(m.group(1))
               if out is None:
                    failed += 1
                    return m.group(0)
               replaced += 1
               return out
          return _REFERENCE.sub(one, text)

     for ext, entries in loaded.items():
      for name, obj in entries:
          data = obj.export_data()[ext]
          changed = False
          for table in ('original', 'custom'):
               for mods in (data.get(table) or {}).values():
                    for field_id, mod in mods.items():
                         if field_id == 'ID':
                              continue
                         value = mod.get('value')
                         items = value.items() if isinstance(value, dict) else [(None, value)]
                         for level, text in list(items):
                              if isinstance(text, str):
                                   m = re.match(r'^TRIGSTR_(\d+)$', text.strip(), re.I)
                                   if m:
                                        trigstrings.add(int(m.group(1)))
                                        continue
                              if not isinstance(text, str) or '<' not in text:
                                   continue
                              new = substitute_text(text)
                              if new != text:
                                   changed = True
                                   if level is None:
                                        mod['value'] = new
                                   else:
                                        mod['value'][level] = new
          if changed:
               files[name] = _object_bytes(obj, ext)
     # text the objects keep in war3map.wts, edited in place there
     wts_name = next((n for n in files if n.lower() == 'war3map.wts'), None)
     if trigstrings and wts_name is not None:
          raw = files[wts_name]
          bom = raw.startswith(b'\xef\xbb\xbf')
          text = raw.decode('utf-8-sig', 'replace')
          pattern = re.compile(r'(STRING\s+(\d+)\s*(?://[^\n]*\r?\n\s*)*\{\r?\n)(.*?)(\r?\n\})',
                               re.DOTALL)

          def entry(m):
               if int(m.group(2)) not in trigstrings or '<' not in m.group(3):
                    return m.group(0)
               return m.group(1) + substitute_text(m.group(3)) + m.group(4)
          new = pattern.sub(entry, text)
          if new != text:
               files[wts_name] = (b'\xef\xbb\xbf' if bom else b'') + new.encode('utf-8')
     return files, {'replaced': replaced, 'left': failed}


# Files only the World Editor reads: the triggers (the script is what the
# game runs), and the regions, cameras and sounds the script recreates
# itself, plus the import list. The same six w3x2lni's remove_we_only drops.
# war3mapUnits.doo is kept (w3x2lni writes a stub in its place, a sign the
# game wants one), and so is war3map.mmp (the minimap preview's icons).
WE_ONLY = ('war3map.wtg', 'war3map.wct', 'war3map.w3r', 'war3map.w3c', 'war3map.w3s',
           'war3map.imp')


def remove_we_only(files):
     """(files, report): `files` without the World Editor's own files.
     Regions, cameras and sounds are removed only when the script creates
     them (CreateRegions/CreateCameras/InitSounds, which the World Editor
     writes into it); otherwise they are kept."""
     files = dict(files)
     script = b''
     for name in ('war3map.j', 'war3map.lua', 'scripts\\war3map.j', 'scripts\\war3map.lua'):
          key = next((n for n in files if n.lower() == name), None)
          if key is not None:
               script += files[key] or b''
     needs = {'war3map.w3r': b'CreateRegions', 'war3map.w3c': b'CreateCameras',
              'war3map.w3s': b'InitSounds'}
     removed = []
     for name in list(files):
          lower = name.lower()
          if lower not in WE_ONLY:
               continue
          if lower in needs and needs[lower] not in script:
               continue
          del files[name]
          removed.append(name)
     return files, {'removed': removed}
