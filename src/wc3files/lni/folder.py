"""
Lni mode: a whole map as a folder of text files, and back.

`map_to_lni(map_path, folder)` writes the folder; `lni_to_map(folder,
map_path)` builds the map again. The layout follows w3x2lni's:

     MyMap/
          wc3files-lni.json   what each file of the map became (the manifest)
          table/              object data as Lni text (unit.ini, ability.ini, ...),
                              map info, imports, strings, regions, cameras,
                              sounds, placed units and doodads as JSON
          trigger/            war3map.wtg and war3map.wct as JSON
          scripts/            war3map.j, war3map.lua and other scripts
          resource/           models and textures
          sound/              sounds and music
          map/                everything else, as it is (terrain, pathing, ...)

Nothing is lost: each conversion is checked as it is made. A file goes to
JSON only if building it back from that JSON gives its exact bytes, and to
Lni only if reading the Lni back gives the same objects; otherwise the file
is stored as it is under map/. Object files come back with the same
objects, fields and values and the same file version, though not
necessarily byte for byte (the order the editor wrote levels in is not
kept, as w3x2lni does not keep it either).
"""

import json
import os
import tempfile

from .objects import objects_to_lni, lni_to_objects

MANIFEST = 'wc3files-lni.json'

_OBJECT_NAMES = {'w3u': 'unit', 'w3t': 'item', 'w3b': 'destructable', 'w3d': 'doodad',
                 'w3a': 'ability', 'w3h': 'buff', 'w3q': 'upgrade'}

# map file name (lower case) -> FORMATS key, for the files that go to JSON
_JSON_FILES = {
     'war3map.w3i': ('w3i', 'table'), 'war3map.imp': ('imp', 'table'),
     'war3map.wts': ('wts', 'table'), 'war3map.w3c': ('w3c', 'table'),
     'war3map.w3r': ('w3r', 'table'), 'war3map.w3s': ('w3s', 'table'),
     'war3map.mmp': ('mmp', 'table'), 'war3mapunits.doo': ('doo', 'table'),
     'war3map.doo': ('doodads', 'table'), 'war3map.wtg': ('wtg', 'trigger'),
     'war3map.wct': ('wct', 'trigger'), 'war3campaign.w3f': ('w3f', 'table'),
}
_RESOURCE = ('.mdx', '.mdl', '.blp', '.tga', '.dds', '.png', '.jpg', '.bmp', '.tif')
_SOUND = ('.wav', '.mp3', '.flac', '.ogg')
_SCRIPT = ('.j', '.lua', '.ai')
_SKIP = ('(listfile)', '(attributes)', '(signature)')


def _safe_path(name):
     """A map path as a relative path on disk (backslashes become folders)."""
     parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..')]
     return os.path.join(*parts) if parts else '_'


def _write(folder, rel, data):
     path = os.path.join(folder, rel)
     os.makedirs(os.path.dirname(path) or folder, exist_ok=True)
     mode = 'w' if isinstance(data, str) else 'wb'
     with open(path, mode, **({'encoding': 'utf-8', 'newline': '\n'} if mode == 'w' else {})) as h:
          h.write(data)


def _temp(data, suffix):
     handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
     handle.write(data)
     handle.close()
     return handle.name


def _to_json(fmt_key, name, data):
     """JSON text for a file, or None when building it back would differ."""
     from wc3files.cli.formats import FORMATS
     fmt = FORMATS[fmt_key]
     source = _temp(data, '.' + name.rsplit('.', 1)[-1])
     target = source + '.rebuilt'
     try:
          dumped = fmt.dump(source)
          text = json.dumps(dumped, indent=1, ensure_ascii=False)
          fmt.build(json.loads(text), target)
          with open(target, 'rb') as h:
               if h.read() != data:
                    return None
          return text
     except Exception:
          return None
     finally:
          for path in (source, target):
               if os.path.exists(path):
                    os.remove(path)


def _object_file(data, ext):
     from wc3files.w3o.object_file import ObjectFile
     path = _temp(data, '.' + ext)
     try:
          obj = ObjectFile()
          obj.read(path)
          return obj
     finally:
          os.remove(path)


def _strip_seq(objects):
     out = {}
     for table in ('original', 'custom'):
          out[table] = {}
          for object_id, mods in ((objects or {}).get(table) or {}).items():
               out[table][object_id] = {k: ({a: b for a, b in v.items() if a != 'seq'}
                                            if isinstance(v, dict) else v)
                                        for k, v in mods.items()}
     return out


def _to_lni(name, data, ext):
     """(Lni text, extras for the manifest) or None if it does not round-trip."""
     try:
          obj = _object_file(data, ext)
          objects = obj.export_data()[ext]
          text = objects_to_lni(objects, ext)
          if _strip_seq(lni_to_objects(text, ext)) != _strip_seq(objects):
               return None
          return text, {'version': obj._version, 'skin': obj._skin_used}
     except Exception:
          return None


def map_to_lni(map_path, folder, files=None):
     """Write `folder` from a .w3x/.w3m map (or a {name: bytes} table)."""
     if files is None:
          from wc3files.w3x import W3xFile
          archive = W3xFile()
          archive.read(map_path)
          files = archive.files or {}
     os.makedirs(folder, exist_ok=True)
     entries = []
     used = set()
     for name, data in files.items():
          if data is None or name.lower() in _SKIP:
               continue
          data = bytes(data)
          lower = name.lower()
          ext = lower.rsplit('.', 1)[-1] if '.' in lower else ''
          entry = {'name': name}
          base = lower.rsplit('\\', 1)[-1]
          if ext in _OBJECT_NAMES and base.startswith('war3map'):
               converted = _to_lni(name, data, ext)
               if converted is not None:
                    text, extras = converted
                    skin = base.startswith('war3mapskin')
                    rel = os.path.join('table', ('skin_' if skin else '') + _OBJECT_NAMES[ext] + '.ini')
                    if rel.lower() in used or '\\' in name:
                         # the same name in another folder of the map (a locale's)
                         rel = os.path.join('table', _safe_path(name) + '.ini')
                    _write(folder, rel, text)
                    entry.update(path=rel.replace(os.sep, '/'), kind='lni', ext=ext, **extras)
          if 'kind' not in entry and lower in _JSON_FILES:
               fmt_key, where = _JSON_FILES[lower]
               text = _to_json(fmt_key, name, data)
               if text is not None:
                    rel = os.path.join(where, base + '.json')
                    if rel.lower() in used or '\\' in name:
                         rel = os.path.join(where, _safe_path(name) + '.json')
                    _write(folder, rel, text)
                    entry.update(path=rel.replace(os.sep, '/'), kind='json', format=fmt_key)
          if 'kind' not in entry:
               if lower.endswith(_SCRIPT):
                    where = 'scripts'
               elif lower.endswith(_RESOURCE):
                    where = 'resource'
               elif lower.endswith(_SOUND):
                    where = 'sound'
               else:
                    where = 'map'
               rel = os.path.join(where, _safe_path(name))
               while rel.lower() in used:
                    rel += '_'
               _write(folder, rel, data)
               entry.update(path=rel.replace(os.sep, '/'), kind='raw')
          used.add(entry['path'].lower())
          entries.append(entry)
     manifest = {'format': 'wc3files-lni', 'version': 1,
                 'map': os.path.basename(map_path) if map_path else None,
                 'files': entries}
     _write(folder, MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
     return manifest


def lni_files(folder):
     """The map's {name: bytes} table a folder describes."""
     from wc3files.cli.formats import FORMATS
     from wc3files.w3o.object_file import ObjectFile
     with open(os.path.join(folder, MANIFEST), encoding='utf-8') as h:
          manifest = json.load(h)
     files = {}
     for entry in manifest['files']:
          path = os.path.join(folder, *entry['path'].split('/'))
          kind = entry['kind']
          if kind == 'raw':
               with open(path, 'rb') as h:
                    files[entry['name']] = h.read()
               continue
          with open(path, encoding='utf-8', newline='') as h:
               text = h.read()
          suffix = '.' + entry['name'].rsplit('.', 1)[-1]
          target = _temp(b'', suffix)
          try:
               if kind == 'json':
                    FORMATS[entry['format']].build(json.loads(text), target)
               else:
                    ext = entry['ext']
                    obj = ObjectFile()
                    data = {e: None for e in _OBJECT_NAMES}
                    data[ext] = lni_to_objects(text, ext)
                    obj.import_data(data)
                    obj._version = entry.get('version')
                    obj._skin_used = entry.get('skin')
                    obj.write(target)
               with open(target, 'rb') as h:
                    files[entry['name']] = h.read()
          finally:
               os.remove(target)
     return files


def lni_to_map(folder, map_path):
     """Build a .w3x/.w3m map from an Lni folder."""
     from wc3files.w3x import W3xFile
     files = lni_files(folder)
     files['(listfile)'] = '\r\n'.join(n for n in files).encode('utf-8') + b'\r\n'
     W3xFile().build_from_files(files, map_path)
     return files
