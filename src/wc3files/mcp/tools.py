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
import math
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
    if not base.lower().endswith('.lua'):
        from wc3files.scripts.leaks import find_leaks
        findings = findings + find_leaks(script.get_data())
    findings = _findings(findings)
    return limited({'path': path, 'script': name,
                    'language': 'lua' if base.lower().endswith('.lua') else 'jass',
                    'counts': _counts(findings), 'findings': findings}, max_chars)


def _findings(findings):
    """Checker and leak findings in line order, with only the keys of the
    Finding result (a leak's function is already in its message)."""
    out = [{'severity': f.get('severity'), 'message': f.get('message'), 'line': f.get('line')}
           for f in findings]
    return sorted(out, key=lambda f: (f['line'] is None, f['line'] or 0))


def _counts(findings):
    counts = {}
    for finding in findings:
        counts[finding['severity']] = counts.get(finding['severity'], 0) + 1
    return counts


COMPILE_LANGUAGES = ('vjass', 'zinc', 'cjass', 'luck', 'jass')
_LANGUAGE_OF = {'.j': 'vjass', '.jass': 'vjass', '.vj': 'vjass', '.vjass': 'vjass',
                '.zn': 'zinc', '.zinc': 'zinc', '.cj': 'cjass', '.cjass': 'cjass',
                '.luck': 'luck'}


def compile_script(roots, code='', path='', language='', include_jass=True,
                   max_chars=DEFAULT_MAX_CHARS):
    """Compile vJass, Zinc, cJass or Luck to plain JASS, as `wc3files vjass`,
    `cjass` and `luck` do, and check the result as `jass --check` does.
    `code` is source text; `path` a source file, a folder of Luck sources,
    or a map whose war3map.j is compiled. A compile error is part of the
    result, not a failure of the tool."""
    from wc3files.scripts.checker import check_program
    from wc3files.scripts.jass_parser import parse
    from wc3files.scripts.leaks import find_leaks
    if bool(code) == bool(path):
        raise ValueError('give either code or path')
    language = (language or '').lower()
    sources, label = [], '<code>'
    if path:
        resolved = roots.resolve(path)
        label = resolved
        if os.path.isdir(resolved):
            from wc3files.luck import project_sources
            sources = project_sources([resolved])
            language = language or 'luck'
        elif _is_map(resolved):
            found = _members(resolved, ('war3map.j', 'scripts\\war3map.j'))
            name, data = _first(found, ('war3map.j', 'scripts\\war3map.j'))
            if data is None:
                raise ValueError(f'{resolved} has no war3map.j')
            sources = [(name, data.decode('utf-8', 'replace'))]
        else:
            with open(resolved, 'rb') as handle:
                sources = [(resolved, handle.read().decode('utf-8-sig', 'replace'))]
            language = language or _LANGUAGE_OF.get(os.path.splitext(resolved)[1].lower(), '')
    else:
        sources = [('source.luck' if language == 'luck' else '<code>', code)]
    language = language or 'vjass'
    if language not in COMPILE_LANGUAGES:
        raise ValueError(f"language is one of {', '.join(COMPILE_LANGUAGES)}, not {language!r}")
    errors, warnings, jass = [], [], ''
    try:
        jass, warnings = _compile(language, sources)
    except Exception as error:          # the compiler's own error, reported as data
        errors.append(_compile_error(error))
    findings = []
    if jass:
        try:
            program = parse(jass)
            findings = _findings(check_program(program) + find_leaks(program))
        except Exception as error:
            errors.append(_compile_error(error))
    result = {'path': label, 'language': language, 'ok': not errors and not any(
                  f['severity'] == 'issue' for f in findings),
              'errors': errors, 'warnings': warnings, 'counts': _counts(findings),
              'findings': findings, 'jass': jass if include_jass else '',
              'lines': jass.count('\n')}
    return limited(result, max_chars)


def _compile(language, sources):
    """(JASS, warnings) for `sources` in `language`."""
    text = '\n'.join(source for _, source in sources)
    name = sources[0][0]
    if language == 'luck':
        from wc3files.luck import compile_sources
        result = compile_sources(sources)
        return result.jass, list(result.warnings)
    if language == 'cjass':
        from wc3files.cjass import cjass_to_vjass
        text = cjass_to_vjass(text, name)
    elif language == 'zinc':
        text = f'//! zinc\n{text}\n//! endzinc'
    elif language == 'jass':
        from wc3files.scripts.jass_parser import parse
        parse(text)
        return text, []
    from wc3files.vjass import VJassCompiler
    result = VJassCompiler().compile(text, name)
    return result.jass, [str(w.message) for w in result.warnings]


def _compile_error(error):
    location = getattr(error, 'location', None)
    line = getattr(location, 'line', None) or getattr(error, 'line', None) or None
    source = getattr(location, 'file', None) or getattr(error, 'filename', None)
    message = getattr(error, 'message', None) or str(error)
    return {'message': str(message), 'line': line, 'file': source}


RUN_MAX_SECONDS = 600.0
RUN_MAX_STEPS = 50_000_000


def run_script(roots, path, seconds=10.0, players=1, max_steps=RUN_MAX_STEPS,
               max_chars=DEFAULT_MAX_CHARS):
    """Run a map's script, or a .lua or .j file, against stubs of the game
    API, as `wc3files run-map` does: config and main, then `seconds` of
    game time with its timers and triggers. A JASS script is translated to
    Lua first. Returns what failed and what the map displayed."""
    from wc3files.scripts.wc3_runtime import MapRuntime, load_map_script
    path = roots.resolve(path)
    if not (0 <= seconds <= RUN_MAX_SECONDS):
        raise ValueError(f'seconds is between 0 and {RUN_MAX_SECONDS:g}')
    if not (1 <= players <= 24):
        raise ValueError('players is between 1 and 24')
    if not (1 <= max_steps <= RUN_MAX_STEPS):
        raise ValueError(f'max_steps is between 1 and {RUN_MAX_STEPS}')
    source, chunkname, natives = load_map_script(path)    # refuses a protected map
    runtime = MapRuntime(players=players, max_steps=max_steps)
    runtime.declare_natives(natives)
    report = runtime.run(source, chunkname, seconds=seconds)
    return limited({'path': path, 'script': chunkname, 'ok': not report.errors,
                    'errors': list(report.errors), 'warnings': list(report.warnings),
                    'messages': list(report.messages), 'game_time': report.game_time,
                    'callbacks': report.callbacks, 'stopped': report.stopped}, max_chars)


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
    sequences = []
    for sequence in model.sequences:
        start, end = (list(sequence.get('interval') or []) + [None, None])[:2]
        sequences.append({'name': sequence.get('name'), 'start': start, 'end': end})
    # A texture with an empty path is a replaceable one (team colour, team
    # glow, ...), named by its replaceable id. The key is 'path': reading
    # 'image' gave None for every texture before 0.6.3.
    textures = [{'path': texture.get('path') or '',
                 'replaceable_id': texture.get('replaceableId') or 0}
                for texture in model.textures]
    return plain({'path': path, 'member': member or None, 'name': model.name,
                  'format': model.get_file_type().value, 'version': model.version,
                  'geosets': len(model.geosets), 'sequences': sequences,
                  'textures': textures})


def replay_info(roots, path, chat=True, max_chars=DEFAULT_MAX_CHARS):
    """A replay's game, players with team, colour and race, who left and
    when, its chat with timestamps, and its W3MMD results when it carries
    them (wc3files.w3g.summary, #263)."""
    from wc3files import ReplayFile
    from wc3files.w3g.summary import summarize
    path = roots.resolve(path)
    replay = ReplayFile()
    replay.read(path)
    return limited({'path': path, **summarize(replay.get_data(), chat=chat)}, max_chars)


# ---- the rest of a map (#271) -------------------------------------------

PLACED_KINDS = {'units': 'war3mapUnits.doo', 'doodads': 'war3map.doo'}
RECORD_KINDS = {'regions': 'war3map.w3r', 'cameras': 'war3map.w3c', 'sounds': 'war3map.w3s',
                'imports': 'war3map.imp', 'strings': 'war3map.wts', 'terrain': 'war3map.w3e'}
DEFAULT_LIMIT = 500


def _map_members(roots, path, names):
    """(resolved path, {lower name: (name, bytes)}) for a map, refusing
    anything that is not a .w3x or .w3m map."""
    path = roots.resolve(path)
    if not _is_map(path):
        raise ValueError(f'{path} is not a .w3x or .w3m map')
    return path, _members(path, tuple(names))


def _dump(name, data):
    """What `wc3files dump` gives for the member `name` holding `data`."""
    from wc3files.cli.formats import FORMATS, format_for_name
    base = name.replace('\\', '/').rsplit('/', 1)[-1]
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, base)
        with open(target, 'wb') as handle:
            handle.write(data)
        return FORMATS[format_for_name(base)].dump(target)


def _degrees(radians):
    return None if radians is None else round(math.degrees(radians) % 360, 2)


def _placed(kind, record, index):
    x, y, z = (list(record.get('position') or []) + [None] * 3)[:3]
    scale = record.get('scale')
    out = {'index': index, 'id': record.get('id'), 'x': x, 'y': y, 'z': z,
           'facing': _degrees(record.get('rotation')),
           'scale': list(scale) if isinstance(scale, (list, tuple)) else scale,
           'variation': record.get('variation')}
    if kind == 'units':
        out.update({'owner': record.get('owner'), 'hp': record.get('hp'),
                    'mana': record.get('mp'), 'creation_number': record.get('creation_number')})
    else:
        out.update({'life': record.get('life'), 'editor_id': record.get('editor_id')})
    return out


def placed_objects(roots, path, kind='units', id='', owner=None, limit=DEFAULT_LIMIT):
    """The units (war3mapUnits.doo) or doodads (war3map.doo) placed on a
    map, in file order, optionally only those of type `id` or, for units,
    of player `owner`."""
    if kind not in PLACED_KINDS:
        raise ValueError(f'kind must be one of {", ".join(PLACED_KINDS)}')
    member = PLACED_KINDS[kind]
    path, found = _map_members(roots, path, (member,))
    name, data = _first(found, (member,))
    if data is None:
        return plain({'path': path, 'kind': kind, 'total': 0, 'objects': [], 'truncated': False})
    records = _dump(name, data).get(kind) or []
    objects = []
    total = 0
    for index, record in enumerate(records):
        if id and record.get('id') != id:
            continue
        if owner is not None and kind == 'units' and record.get('owner') != owner:
            continue
        total += 1
        if len(objects) < limit:
            objects.append(_placed(kind, record, index))
    return plain({'path': path, 'kind': kind, 'total': total, 'objects': objects,
                  'truncated': total > len(objects)})


def _terrain_summary(data):
    tiles = data.get('tiles') or []
    textures = {}
    layers = [t.get('layer_height') for t in tiles if t.get('layer_height') is not None]
    for tile in tiles:
        textures[tile.get('ground_texture')] = textures.get(tile.get('ground_texture'), 0) + 1
    ground = list(data.get('ground_tilesets') or [])

    def texture_name(index):
        return str(ground[index] if isinstance(index, int) and 0 <= index < len(ground) else index)
    return {
        'version': data.get('version'),
        'main_tileset': data.get('main_tileset'),
        'custom_tileset': data.get('custom_tileset'),
        'ground_tilesets': ground,
        'cliff_tilesets': list(data.get('cliff_tilesets') or []),
        'width': data.get('width'),
        'height': data.get('height'),
        'offset_x': data.get('offset_x'),
        'offset_y': data.get('offset_y'),
        'corners': len(tiles),
        'water_corners': sum(1 for t in tiles if t.get('is_water')),
        'ramp_corners': sum(1 for t in tiles if t.get('is_ramp')),
        'blight_corners': sum(1 for t in tiles if t.get('is_blight')),
        'cliff_levels': [min(layers), max(layers)] if layers else None,
        'ground_texture_use': {texture_name(i): n for i, n in
                               sorted(textures.items(), key=lambda kv: -kv[1])},
    }


def map_records(roots, path, kind, max_chars=DEFAULT_MAX_CHARS):
    """A map's regions, cameras, sounds, imports or strings as records, or
    a summary of its terrain."""
    if kind not in RECORD_KINDS:
        raise ValueError(f'kind must be one of {", ".join(RECORD_KINDS)}')
    member = RECORD_KINDS[kind]
    path, found = _map_members(roots, path, (member,))
    name, data = _first(found, (member,))
    result = {'path': path, 'kind': kind, 'member': member}
    if data is None:
        if kind != 'terrain':
            result['records'] = []
        return limited(result, max_chars)
    dumped = _dump(name, data)
    if kind == 'terrain':
        result['terrain'] = _terrain_summary(dumped)
    elif kind == 'strings':
        result['records'] = [{'id': k, 'text': v} for k, v in dumped.items()]
    elif kind == 'regions':
        regions = dumped.get('regions') or {}
        regions = list(regions.values()) if isinstance(regions, dict) else list(regions)
        for region in regions:
            # No weather is stored as four NUL bytes.
            if isinstance(region.get('weather_effect'), str):
                region['weather_effect'] = region['weather_effect'].strip(chr(0))
        result['records'] = regions
    elif kind == 'cameras':
        result['records'] = list(dumped.get('cameras') or [])
    elif kind == 'sounds':
        result['records'] = list(dumped.get('sounds') or [])
    else:
        result['records'] = list(dumped.get('files') or [])
    return limited(result, max_chars)


def _count(fn):
    """fn(), or None when the member cannot be read."""
    try:
        return fn()
    except (ValueError, KeyError, EOFError, IndexError):
        return None


_RECORD_LISTS = {'regions': 'regions', 'cameras': 'cameras', 'sounds': 'sounds',
                 'imports': 'files'}


def map_overview(roots, path):
    """One call that says what a map holds."""
    from wc3files import ObjectFile
    from wc3files.wtg import MapTriggersFile
    info = map_info(roots, path)
    path = info['path']
    wanted = (list(PLACED_KINDS.values()) + list(RECORD_KINDS.values())
              + [f'war3map.{t}' for t in OBJECT_TYPES] + ['war3map.wtg'] + list(SCRIPTS))
    found = _members(path, tuple(wanted))

    def records(kind):
        name, data = _first(found, (RECORD_KINDS[kind],))
        if data is None:
            return 0
        dumped = _dump(name, data)
        return len(dumped if kind == 'strings' else (dumped.get(_RECORD_LISTS[kind]) or []))

    def placed(kind):
        name, data = _first(found, (PLACED_KINDS[kind],))
        return 0 if data is None else len(_dump(name, data).get(kind) or [])

    def object_tables(kind, data):
        handler = ObjectFile()
        handler.read(data, object_type=kind)
        return {table: len(rows) for table, rows in handler.to_slk(kind).items()}

    objects = {}
    for kind in OBJECT_TYPES:
        _name, data = _first(found, (f'war3map.{kind}',))
        if data is not None:
            objects[kind] = _count(lambda kind=kind, data=data: object_tables(kind, data))

    def triggers():
        _name, data = _first(found, ('war3map.wtg',))
        if data is None:
            return 0
        handler = MapTriggersFile()
        handler.read(data)
        return len(handler.get_triggers())

    terrain = None
    name, data = _first(found, ('war3map.w3e',))
    if data is not None:
        summary = _count(lambda: _terrain_summary(_dump(name, data)))
        if summary:
            terrain = {k: summary[k] for k in ('main_tileset', 'width', 'height')}
    script, _data = _first(found, SCRIPTS)
    return plain({
        'path': path,
        'name': info['name'],
        'author': info['author'],
        'suggested_players': info['suggested_players'],
        'players': len(info['players']),
        'forces': len(info['forces']),
        'script': script,
        'script_language': info['script_language'],
        'game_version': info['game_version'],
        'members': info['members'],
        'terrain': terrain,
        'counts': {
            'units': _count(lambda: placed('units')),
            'doodads': _count(lambda: placed('doodads')),
            'regions': _count(lambda: records('regions')),
            'cameras': _count(lambda: records('cameras')),
            'sounds': _count(lambda: records('sounds')),
            'imports': _count(lambda: records('imports')),
            'strings': _count(lambda: records('strings')),
            'triggers': _count(triggers),
        },
        'objects': objects,
    })
