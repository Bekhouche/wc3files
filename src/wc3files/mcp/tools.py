"""The tools of the wc3files MCP server (#179), as plain functions.

Each takes paths as given by the client, checks them against the folders
the server was started with (`Roots`), and returns JSON-ready data: dicts,
lists, strings and numbers, with bytes as base64 and NaN or infinity as a
string. They only read; nothing here writes a file outside a temporary
folder. A protected map is refused with the same ProtectedMapError the
command line gives.

They do not import the `mcp` package, so they can be used and tested
without it.
"""
import base64
import json
import os
import tempfile

from wc3files.w3x.w3x_file import (ProtectedMapError, W3xFile, W3xReadMode,
                                   protection_reason, read_map)

MAP_EXTENSIONS = ('.w3x', '.w3m')
LISTED_EXTENSIONS = ('.w3x', '.w3m', '.w3n', '.mdx', '.mdl', '.w3g')
OBJECT_TYPES = ('w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q')
SCRIPTS = ('war3map.lua', 'scripts\\war3map.lua', 'war3map.j', 'scripts\\war3map.j')
TEXT_EXTENSIONS = ('.j', '.lua', '.ai', '.txt', '.fdf', '.toc', '.slk', '.wts', '.mdl', '.cfg')
MAX_LISTED = 1000
DEFAULT_MAX_CHARS = 200_000

# war3map.w3i player codes, as World Editor writes them.
PLAYER_TYPES = {1: 'user', 2: 'computer', 3: 'neutral', 4: 'rescuable'}
PLAYER_RACES = {0: 'selectable', 1: 'human', 2: 'orc', 3: 'undead', 4: 'night elf'}


class Roots(object):
    """The folders the server may read. A relative path is taken from the
    first one; any path that resolves outside all of them is refused."""

    def __init__(self, folders):
        if not folders:
            raise ValueError('the server needs at least one --root folder')
        self.folders = []
        for folder in folders:
            real = os.path.realpath(folder)
            if not os.path.isdir(real):
                raise ValueError(f'--root {folder!r} is not a folder')
            self.folders.append(real)

    def resolve(self, path):
        path = os.fspath(path or '')
        if not os.path.isabs(path):
            path = os.path.join(self.folders[0], path)
        real = os.path.realpath(path)
        key = os.path.normcase(real)
        for folder in self.folders:
            base = os.path.normcase(folder)
            if key == base or key.startswith(base.rstrip(os.sep) + os.sep):
                return real
        raise PermissionError(
            f'{path} is outside the folders this server may read: '
            + ', '.join(self.folders))


def plain(value):
    """`value` as JSON-ready data."""
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return {'base64': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, float) and (value != value or value in (float('inf'), float('-inf'))):
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, 'value'):        # an Enum
        return plain(value.value)
    return str(value)


def limited(result, max_chars):
    """`result`, or ValueError when its JSON is longer than `max_chars`: a
    whole map's data would fill an assistant's context for nothing."""
    result = plain(result)
    size = len(json.dumps(result, ensure_ascii=False))
    if max_chars and size > max_chars:
        raise ValueError(
            f'the result is {size} characters, more than max_chars={max_chars}. '
            f'Ask for less (one member, one object type, some ids or one trigger), '
            f'or raise max_chars.')
    return result


def _is_map(path):
    return path.lower().endswith(MAP_EXTENSIONS)


def _members(path, names):
    """{lower name: (name, bytes)} of the `names` a map holds."""
    archive = read_map(path, members=tuple(names))
    return {name.lower(): (name, bytes(data)) for name, data in archive.files.items()
            if data is not None and name != '(listfile)'}


def _first(found, names):
    for name in names:
        if name.lower() in found:
            return found[name.lower()]
    return None, None


def _listed_names(archive):
    names = []
    for line in (archive.files.get('(listfile)') or b'').splitlines():
        name = line.decode('utf-8', 'surrogateescape').strip()
        if name:
            names.append(name)
    return names


# ---- tools ---------------------------------------------------------------

def list_files(roots, folder='', pattern=''):
    """Maps, campaigns, models and replays under `folder` (a root by
    default), with their sizes. `pattern` keeps the names containing it."""
    start = roots.resolve(folder) if folder else None
    starts = [start] if start else roots.folders
    wanted = (pattern or '').lower()
    files = []
    for top in starts:
        for current, dirs, names in os.walk(top):
            dirs.sort()
            for name in sorted(names):
                if not name.lower().endswith(LISTED_EXTENSIONS):
                    continue
                if wanted and wanted not in name.lower():
                    continue
                full = os.path.join(current, name)
                files.append({'path': full, 'size': os.path.getsize(full)})
                if len(files) >= MAX_LISTED:
                    return {'files': files, 'truncated': True}
    return {'files': files, 'truncated': False}


def map_info(roots, path):
    """What war3map.w3i says about a map, with its strings resolved."""
    from wc3files.w3i import W3iFile
    path = roots.resolve(path)
    if not _is_map(path):
        raise ValueError(f'{path} is not a .w3x or .w3m map')
    archive = read_map(path, members=('war3map.w3i', 'war3map.wts'))
    found = {name.lower(): bytes(data) for name, data in archive.files.items() if data is not None}
    if 'war3map.w3i' not in found:
        raise ValueError(f'{path} has no war3map.w3i')
    info = W3iFile()
    info.read(found['war3map.w3i'])
    if 'war3map.wts' in found:
        info.apply_strings(found['war3map.wts'])
    data = info.get_data()
    players = [{'id': p.get('id'), 'name': p.get('name'),
                'type': PLAYER_TYPES.get(p.get('type'), p.get('type')),
                'race': PLAYER_RACES.get(p.get('race'), p.get('race'))}
               for p in data.get('players') or []]
    forces = [{'name': f.get('name'),
               'players': [i for i in range(32) if (f.get('player_mask') or 0) >> i & 1]}
              for f in data.get('forces') or []]
    return plain({
        'path': path,
        'name': data.get('map_name'),
        'author': data.get('author_name'),
        'description': data.get('map_description'),
        'suggested_players': data.get('players_recommended'),
        'width': data.get('map_width'),
        'height': data.get('map_height'),
        'script_language': data.get('script_type'),
        'game_version': data.get('war3_version'),
        'editor_version': data.get('editor_version'),
        'w3i_version': data.get('file_version'),
        'players': players,
        'forces': forces,
        'members': len(_listed_names(archive)),
    })


def list_members(roots, path):
    """The files a map holds, with their stored and unpacked sizes, read
    from the archive tables without unpacking anything."""
    path = roots.resolve(path)
    if not _is_map(path):
        raise ValueError(f'{path} is not a .w3x or .w3m map')
    archive = W3xFile()
    archive.read(path, mode=W3xReadMode.SIMPLE)
    if archive.protected:
        raise ProtectedMapError(
            f'{path} is protected: {protection_reason(archive)}. '
            f'Reading it as it is would lose those files.')
    blocks = archive._data['BLOCK_TABLE']
    members = []
    for name in _listed_names(archive):
        entry = archive.get_hash_table_entry(name)
        block = blocks[entry['FileBlockIndex']] if entry is not None else None
        members.append({'name': name,
                        'size': block['FileSize'] if block else None,
                        'stored': block['BlockSize'] if block else None})
    return {'path': path, 'members': members}


def read_member(roots, path, member, max_chars=DEFAULT_MAX_CHARS):
    """One member of a map, as the data `wc3files dump` gives for its
    format, as text for a text file, or as its size for anything else."""
    from wc3files.cli.formats import FORMATS, format_for_name
    path = roots.resolve(path)
    if not _is_map(path):
        raise ValueError(f'{path} is not a .w3x or .w3m map')
    base = member.replace('\\', '/').rsplit('/', 1)[-1]
    fmt = format_for_name(base)
    extra = ('war3map.wpm',) if fmt == 'shd' else ()
    found = _members(path, (member,) + extra)
    name, data = _first(found, (member,))
    if data is None:
        raise ValueError(f'{path} has no {member}')
    result = {'path': path, 'member': name, 'size': len(data)}
    if fmt is not None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, base)
            with open(target, 'wb') as handle:
                handle.write(data)
            for other in extra:
                other_name, other_data = _first(found, (other,))
                if other_data is not None:
                    with open(os.path.join(tmp, other), 'wb') as handle:
                        handle.write(other_data)
            result['format'] = fmt
            result['data'] = FORMATS[fmt].dump(target)
    elif base.lower().endswith(TEXT_EXTENSIONS):
        result['text'] = data.decode('utf-8-sig', errors='replace')
    else:
        result['note'] = ('no reader for this member; model_info reads .mdx and .mdl '
                          'members') if base.lower().endswith(('.mdx', '.mdl')) else \
            'binary member with no reader'
    return limited(result, max_chars)


def object_data(roots, path, object_type='', ids=None, max_chars=DEFAULT_MAX_CHARS):
    """The objects of a map's object data files (war3map.w3u, w3t, w3b, w3d,
    w3a, w3h, w3q), or of one object file, with readable field names and
    the map's strings resolved. `object_type` keeps one type, `ids` keeps
    those object ids."""
    from wc3files import ObjectFile
    path = roots.resolve(path)
    types = OBJECT_TYPES
    if object_type:
        if object_type not in OBJECT_TYPES:
            raise ValueError(f'object_type must be one of {", ".join(OBJECT_TYPES)}')
        types = (object_type,)
    if _is_map(path):
        found = _members(path, tuple(f'war3map.{t}' for t in types) + ('war3map.wts',))
        sources = {t: found[f'war3map.{t}'][1] for t in types if f'war3map.{t}' in found}
        wts = found.get('war3map.wts', (None, None))[1]
    else:
        ext = path.rsplit('.', 1)[-1].lower()
        if ext not in OBJECT_TYPES:
            raise ValueError(f'{path} is not a map or an object file ({", ".join(OBJECT_TYPES)})')
        if object_type and ext != object_type:
            return {'path': path, 'objects': {}}
        with open(path, 'rb') as handle:
            sources = {ext: handle.read()}
        wts = None
    keep = {i for i in (ids or [])}
    objects = {}
    for kind, raw in sources.items():
        handler = ObjectFile()
        handler.read(raw, object_type=kind)
        if wts:
            handler.apply_strings(wts)
        tables = handler.to_slk(kind)
        if keep:
            tables = {table: {i: row for i, row in rows.items() if i in keep}
                      for table, rows in tables.items()}
        objects[kind] = tables
    return limited({'path': path, 'objects': objects}, max_chars)


def trigger_text(roots, path, name='', names_only=False, max_chars=DEFAULT_MAX_CHARS):
    """GUI triggers as the World Editor shows them; `name` keeps the
    triggers whose name contains it, and `names_only` gives only their
    names (a large map's triggers run to hundreds of thousands of
    characters)."""
    from wc3files.wtg import MapTriggersFile
    from wc3files.wtg.trigger_text import TriggerText, describe_trigger
    path = roots.resolve(path)
    if _is_map(path):
        _name, data = _first(_members(path, ('war3map.wtg',)), ('war3map.wtg',))
        if data is None:
            raise ValueError(f'{path} has no war3map.wtg')
    else:
        with open(path, 'rb') as handle:
            data = handle.read()
    triggers_file = MapTriggersFile()
    triggers_file.read(data)
    triggers = triggers_file.get_triggers()
    if name:
        triggers = [t for t in triggers if name.lower() in (t.get('name') or '').lower()]
    if names_only:
        return limited({'path': path, 'names': [t.get('name') for t in triggers]}, max_chars)
    text = TriggerText.default()
    return limited({'path': path, 'triggers': [
        {'name': t.get('name'), 'lines': describe_trigger(t, text)} for t in triggers]},
        max_chars)


def check_script(roots, path, max_chars=DEFAULT_MAX_CHARS):
    """JASS or Lua findings for a map's script or a script file, as
    `wc3files jass --check` reports them."""
    from wc3files import ScriptFile
    path = roots.resolve(path)
    if _is_map(path):
        name, data = _first(_members(path, SCRIPTS), SCRIPTS)
        if data is None:
            raise ValueError(f'{path} has no war3map.j or war3map.lua')
    else:
        name = os.path.basename(path)
        with open(path, 'rb') as handle:
            data = handle.read()
    base = name.replace('\\', '/').rsplit('/', 1)[-1]
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, base)
        with open(target, 'wb') as handle:
            handle.write(data)
        script = ScriptFile()
        script.read(target)
    findings = script.validate()
    counts = {}
    for finding in findings:
        counts[finding.get('severity')] = counts.get(finding.get('severity'), 0) + 1
    return limited({'path': path, 'script': name,
                    'language': 'lua' if base.lower().endswith('.lua') else 'jass',
                    'counts': counts, 'findings': findings}, max_chars)


def model_info(roots, path, member=''):
    """A model's name, format, version, sequences, textures and counts. For
    a map, `member` names the model inside it."""
    from wc3files import ModelFile
    path = roots.resolve(path)
    if _is_map(path):
        if not member:
            raise ValueError('give member, the model inside the map (list_members shows them)')
        name, data = _first(_members(path, (member,)), (member,))
        if data is None:
            raise ValueError(f'{path} has no {member}')
        source = name
    else:
        with open(path, 'rb') as handle:
            data = handle.read()
        source = os.path.basename(path)
    base = source.replace('\\', '/').rsplit('/', 1)[-1]
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, base)
        with open(target, 'wb') as handle:
            handle.write(data)
        model = ModelFile()
        model.read(target)
    sequences = [s.get('name') if isinstance(s, dict) else s for s in model.sequences]
    textures = [t.get('image') if isinstance(t, dict) else t for t in model.textures]
    return plain({'path': path, 'member': member or None, 'name': model.name,
                  'format': model.get_file_type().value, 'version': model.version,
                  'geosets': len(model.geosets), 'sequences': sequences,
                  'textures': textures})


def replay_info(roots, path):
    """A replay's game, map, length and players, and its W3MMD results
    when it carries them."""
    from wc3files import ReplayFile
    from wc3files.w3g import mmd as _mmd
    path = roots.resolve(path)
    replay = ReplayFile()
    replay.read(path)
    data = replay.get_data()
    header = data.get('header') or {}
    sub = header.get('sub') or {}
    game = data.get('compressed_data') or {}
    result = {
        'path': path,
        'game_name': game.get('GameName'),
        'map': game.get('MapName'),
        'host': game.get('CreatorName'),
        'length_ms': sub.get('replay_length'),
        'version': sub.get('version_number'),
        'build': sub.get('build_number'),
        'reforged': header.get('reforged'),
        'players': [{'id': p.get('player_id'), 'name': p.get('name'),
                     'race': p.get('race'), 'host': p.get('is_host')}
                    for p in game.get('Players') or []],
    }
    stats = _mmd.from_replay(data)
    if stats.messages:
        result['w3mmd'] = {'players': stats.players, 'winners': stats.winners(),
                           'losers': stats.losers()}
    return plain(result)
