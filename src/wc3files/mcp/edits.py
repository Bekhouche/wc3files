"""The edit tools of the wc3files MCP server (#272), as plain functions.

The server registers them only when it is started with --allow-write. Each
one reads the whole map (a protected map is refused, as for reading),
applies its changes in memory, refuses anything it cannot apply exactly,
writes a new map, then reads that map back and checks the changed members
before it reports success. The source map is changed only when `out` names
it; by default the result goes to <name>.edited.w3x beside it.

Like tools.py, nothing here imports the `mcp` package.
"""
import math
import os

from wc3files.w3x.modes import _read_files, _write_map

from .tools import OBJECT_TYPES, _is_map, plain

LEVELED_TYPES = ('w3a', 'w3d', 'w3q')


def target_path(roots, path, out=''):
    """(source, out): the resolved map and the map to write. `out` defaults
    to <name>.edited<ext> beside the source and must be under the roots."""
    source = roots.resolve(path)
    if not _is_map(source):
        raise ValueError(f'{source} is not a .w3x or .w3m map')
    if not os.path.isfile(source):
        raise ValueError(f'{source} does not exist')
    if out:
        target = roots.resolve(out)
        if not _is_map(target):
            raise ValueError(f'out must end in .w3x or .w3m: {target}')
    else:
        stem, ext = os.path.splitext(source)
        target = stem + '.edited' + ext
    return source, target


def _find(files, name):
    lower = name.lower()
    return next((key for key in files if key.lower() == lower), None)


def commit(source, files, changed, out):
    """Write `files` as the map `out`, then read it back and check that every
    member in `changed` ({name: bytes}) holds exactly those bytes."""
    _write_map(files, out)
    written = _read_files(out)
    for name, data in changed.items():
        key = _find(written, name)
        if key is None or written[key] != data:
            raise ValueError(f'{out} was written, but {name} did not read back as written; '
                             f'do not use it')
    return {'path': out, 'source': source, 'overwrote_source': os.path.normcase(out)
            == os.path.normcase(source), 'changed_members': sorted(changed)}


# ---- edit_objects ---------------------------------------------------------

def _field(object_type, base_id, key, fields):
    """(mod id, field definition) for `key`, a four-character mod id such as
    ugol or a field name such as goldcost, as object_data gives it."""
    from wc3files.w3o.object_metadata import field_display_name
    if key in fields:
        return key, fields[key]
    lowered = key.lower()
    candidates = [mod for mod, definition in fields.items()
                  if field_display_name(definition).lower() == lowered
                  or str(definition.get('field', '')).lower() == lowered]
    if len(candidates) > 1 and base_id:
        specific = [mod for mod in candidates
                    if base_id in str(fields[mod].get('useSpecific') or '').split(',')]
        generic = [mod for mod in candidates if not fields[mod].get('useSpecific')]
        candidates = specific or generic or candidates
    if not candidates:
        raise ValueError(f'{object_type}: no field {key!r}; give a field name as object_data '
                         f'shows it or its four-character id')
    if len(candidates) > 1:
        raise ValueError(f'{object_type}: field {key!r} is ambiguous for {base_id or "this object"}'
                         f' ({", ".join(sorted(candidates))}); give the four-character id')
    return candidates[0], fields[candidates[0]]


def _coerce(definition, value, where):
    """`value` as the type the field's metadata says, refusing what does not fit."""
    kind = str(definition.get('type') or '')
    if isinstance(value, bool):
        value = int(value)
    if kind in ('int', 'bool'):
        if isinstance(value, float):
            if value != int(value):
                raise ValueError(f'{where}: {value} is not a whole number')
            value = int(value)
        if not isinstance(value, int):
            raise ValueError(f'{where}: expects a whole number, not {value!r}')
    elif kind in ('real', 'unreal'):
        if isinstance(value, int):
            value = float(value)
        if not isinstance(value, float):
            raise ValueError(f'{where}: expects a number, not {value!r}')
    elif isinstance(value, (int, float)):
        value = str(value)
    elif not isinstance(value, str):
        raise ValueError(f'{where}: expects text, not {value!r}')
    return value


def _set_field(entry, object_type, mod, definition, value, where):
    from wc3files.w3o.data import ObjectFileType
    from wc3files.w3o.object_override import _binary_type_for
    leveled_file = object_type in LEVELED_TYPES
    leveled_field = int(definition.get('repeat') or 0) > 0
    meta_type = definition.get('type')
    if leveled_field:
        if not isinstance(value, dict) or not value:
            raise ValueError(f'{where}: a field with levels takes {{"1": value, "2": value, ...}}')
        levels = {}
        for level, item in value.items():
            try:
                level = int(level)
            except (TypeError, ValueError):
                raise ValueError(f'{where}: level {level!r} is not a number') from None
            if level < 1:
                raise ValueError(f'{where}: levels count from 1')
            levels[level] = _coerce(definition, item, f'{where} level {level}')
    else:
        if isinstance(value, dict):
            raise ValueError(f'{where}: this field has no levels; give one value')
        levels = {0: _coerce(definition, value, where)} if leveled_file else None
        if not leveled_file:
            plain_value = _coerce(definition, value, where)
            code, _sure = _binary_type_for(ObjectFileType(object_type), mod, meta_type, plain_value)
            current = entry.get(mod) or {}
            entry[mod] = {'value': plain_value, 'type': code}
            if 'end' in current:
                entry[mod]['end'] = current['end']
            return
    pointer = int(definition.get('data') or 0)
    current = entry.get(mod)
    if not isinstance(current, dict) or not isinstance(current.get('value'), dict):
        current = {'value': {}, 'type': None, 'ptr': {}}
        entry[mod] = current
    for level, item in levels.items():
        code, _sure = _binary_type_for(ObjectFileType(object_type), mod, meta_type, item)
        if current['type'] is None:
            current['type'] = code
        elif code != current['type']:
            types = current.setdefault('types', {lv: current['type'] for lv in current['value']})
            types[level] = code
        elif 'types' in current:
            current['types'][level] = code
        current['value'][level] = item
        current.setdefault('ptr', {})[level] = pointer
        # A new (field, level) has no recorded file position; without one for
        # every level the writer keeps its own order for the whole entry.
        if isinstance(current.get('seq'), dict) and level not in current['seq']:
            current['seq'][level] = max(current['seq'].values(), default=-1) + 1


def _apply_object_change(table_data, object_type, change, fields, base_data, report):
    if not isinstance(change, dict) or not isinstance(change.get('id'), str):
        raise ValueError(f'each change needs an id: {change!r}')
    object_id = change['id']
    if len(object_id) != 4:
        raise ValueError(f'{object_id!r}: an object id has four characters')
    original, custom = table_data['original'], table_data['custom']
    base = change.get('base')
    if change.get('delete'):
        for table in (original, custom):
            if object_id in table:
                del table[object_id]
                report['deleted'].append(object_id)
                return
        raise ValueError(f'{object_id}: not in the map, nothing to delete')
    if base:
        if object_id in custom or object_id in original:
            raise ValueError(f'{object_id}: already in the map; leave base out to change it')
        if base_data.get(object_type, object_id):
            raise ValueError(f'{object_id}: is a standard game id; a new object needs an unused id')
        if not base_data.get(object_type, base):
            raise ValueError(f'{base}: not a standard {object_type} id to base a new object on')
        custom[object_id] = {'ID': base}
        entry = custom[object_id]
        report['created'].append(object_id)
        base_id = base
        table = 'custom'
    elif object_id in custom:
        entry = custom[object_id]
        base_id = entry.get('ID')
        report['changed'].append(object_id)
        table = 'custom'
    elif object_id in original or base_data.get(object_type, object_id):
        entry = original.setdefault(object_id, {})
        base_id = object_id
        report['changed'].append(object_id)
        table = 'original'
    else:
        raise ValueError(f'{object_id}: neither a standard id nor a custom object of the map; '
                         f'give base to create it')
    touched = report['touched'].setdefault((table, object_id), set())
    for key, value in (change.get('set') or {}).items():
        mod, definition = _field(object_type, base_id, key, fields)
        _set_field(entry, object_type, mod, definition, value, f'{object_id}.{key}')
        touched.add(mod)
    for key in change.get('reset') or []:
        mod, _definition = _field(object_type, base_id, key, fields)
        entry.pop(mod, None)


def edit_objects(roots, path, object_type, changes, out=''):
    """Create, change or delete objects in one object data file of a map."""
    from wc3files import ObjectFile
    from wc3files.w3o.data import ObjectFileType
    from wc3files.w3o.object_base_data import BaseObjectData
    if object_type not in OBJECT_TYPES:
        raise ValueError(f'object_type must be one of {", ".join(OBJECT_TYPES)}')
    if not changes:
        raise ValueError('changes is empty')
    source, out = target_path(roots, path, out)
    files = _read_files(source)
    member = f'war3map.{object_type}'
    key = _find(files, member) or member
    kind = ObjectFileType(object_type)
    handler = ObjectFile()
    if key in files:
        handler.read(files[key], object_type=object_type)
    else:
        handler.set_object_type(kind)
        handler.import_data({object_type: {'original': {}, 'custom': {}}})
    data = handler.export_data()
    table_data = data.get(object_type) or {'original': {}, 'custom': {}}
    table_data.setdefault('original', {})
    table_data.setdefault('custom', {})
    fields = _object_fields(kind)
    base_data = BaseObjectData.default()
    report = {'created': [], 'changed': [], 'deleted': [], 'touched': {}}
    for change in changes:
        _apply_object_change(table_data, object_type, change, fields, base_data, report)
    handler.import_data({object_type: table_data})
    warnings = _warnings(object_type, table_data, report.pop('touched'))
    data_bytes = handler.to_bytes(kind)
    files[key] = data_bytes
    result = commit(source, files, {key: data_bytes}, out)
    result.update({'object_type': object_type, **report, 'warnings': warnings})
    return plain(result)


def _object_fields(kind):
    """The metadata fields of one object type. Items take most of their
    fields (name, tooltip, icon, ...) from the unit metadata, where they are
    marked useItem, so those are added for w3t."""
    from wc3files.w3o.data import ObjectFileType
    from wc3files.w3o.object_metadata import ObjectMetaData
    metadata = ObjectMetaData.default()
    fields = dict(metadata.fields(kind))
    if kind == ObjectFileType('w3t'):
        for mod, definition in metadata.fields(ObjectFileType('w3u')).items():
            if definition.get('useItem') == 1:
                fields.setdefault(mod, definition)
    return fields


def _warnings(object_type, table_data, touched):
    """The editor's limits (minVal, maxVal, value lists) for the fields this
    call set, as warnings: real maps go past them on purpose, and the game
    takes the values, so they do not stop the edit. Fields the call did not
    set are not checked."""
    from wc3files.w3o.object_validator import validate_object_table
    subset = {'original': {}, 'custom': {}}
    for (table, object_id), mods in touched.items():
        entry = table_data[table].get(object_id)
        if entry is None:
            continue
        part = {mod: entry[mod] for mod in mods if mod in entry}
        if 'ID' in entry:
            part['ID'] = entry['ID']
        subset[table][object_id] = part
    return [f'{r["id"]}.{r["field"]}: ' + ', '.join(i['message'] for i in r['issues'])
            for r in validate_object_table(object_type, subset) if r['issues']]


# ---- edit_placed (#273) -----------------------------------------------------

PLACED = {'units': ('war3mapUnits.doo', 'creation_number', ('w3u', 'w3t')),
          'doodads': ('war3map.doo', 'editor_id', ('w3d', 'w3b'))}
_UNIT_SET = ('id', 'x', 'y', 'z', 'facing', 'scale', 'variation', 'owner', 'hp', 'mana')
_DOODAD_SET = ('id', 'x', 'y', 'z', 'facing', 'scale', 'variation', 'life')


def _known_ids(files, kinds):
    """Standard ids of the object types `kinds`, and the custom ids the map
    defines for them."""
    from wc3files import ObjectFile
    from wc3files.w3o.object_base_data import BaseObjectData
    base = BaseObjectData.default()
    known = set()
    for kind in kinds:
        known.update(base.ids(kind))
        key = _find(files, f'war3map.{kind}')
        if key is not None:
            handler = ObjectFile()
            handler.read(files[key], object_type=kind)
            known.update((handler.export_data().get(kind) or {}).get('custom') or {})
    return known


def _bounds(files):
    """(min x, min y, max x, max y) of the terrain, or None without war3map.w3e."""
    from wc3files.w3e import TerrainFile
    key = _find(files, 'war3map.w3e')
    if key is None:
        return None
    terrain = TerrainFile()
    terrain.read(files[key])
    data = terrain.get_data()
    x0, y0 = data['offset_x'], data['offset_y']
    return x0, y0, x0 + (data['width'] - 1) * 128.0, y0 + (data['height'] - 1) * 128.0


def _apply_placed(record, values, kind, known, bounds, where):
    allowed = _UNIT_SET if kind == 'units' else _DOODAD_SET
    for key in values:
        if key not in allowed:
            raise ValueError(f'{where}: {key!r} cannot be set on {kind}; '
                             f'one of {", ".join(allowed)}')
    if 'id' in values:
        type_id = values['id']
        if not isinstance(type_id, str) or len(type_id) != 4:
            raise ValueError(f'{where}: id is a four-character type id')
        if type_id not in known:
            raise ValueError(f'{where}: {type_id} is neither a standard type nor one the map '
                             f'defines')
        if record.get('skin', record.get('id')) == record.get('id'):
            record['skin'] = type_id
        record['id'] = type_id
    position = list(record.get('position') or (0.0, 0.0, 0.0))
    for axis, key in enumerate(('x', 'y', 'z')):
        if key in values:
            if not isinstance(values[key], (int, float)) or isinstance(values[key], bool):
                raise ValueError(f'{where}: {key} is a number')
            position[axis] = float(values[key])
    if bounds and not (bounds[0] <= position[0] <= bounds[2] and bounds[1] <= position[1] <= bounds[3]):
        raise ValueError(f'{where}: {position[0]}, {position[1]} is outside the map, which spans '
                         f'x {bounds[0]} to {bounds[2]} and y {bounds[1]} to {bounds[3]}')
    record['position'] = position
    if 'facing' in values:
        if not isinstance(values['facing'], (int, float)) or isinstance(values['facing'], bool):
            raise ValueError(f'{where}: facing is a number of degrees')
        record['rotation'] = math.radians(float(values['facing']) % 360)
    if 'scale' in values:
        scale = values['scale']
        scale = [scale] * 3 if isinstance(scale, (int, float)) and not isinstance(scale, bool) else scale
        if (not isinstance(scale, (list, tuple)) or len(scale) != 3
                or not all(isinstance(v, (int, float)) and v > 0 for v in scale)):
            raise ValueError(f'{where}: scale is a positive number or [x, y, z]')
        record['scale'] = [float(v) for v in scale]
    for key, field, low, high in (('variation', 'variation', 0, 2 ** 31 - 1),
                                  ('owner', 'owner', 0, 31),
                                  ('hp', 'hp', -1, 2 ** 31 - 1), ('mana', 'mp', -1, 2 ** 31 - 1),
                                  ('life', 'life', 0, 255)):
        if key in values:
            value = values[key]
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f'{where}: {key} is a whole number from {low} to {high}')
            record[field] = value


def _select(records, kind, selector, where):
    """Indices of the records `selector` matches: {"index": n}, {"id": "hfoo"},
    {"creation_number": n} for units or {"editor_id": n} for doodads."""
    number = PLACED[kind][1]
    if not isinstance(selector, dict) or len(set(selector) & {'index', 'id', number}) != 1:
        raise ValueError(f'{where}: select one of index, id or {number}')
    if 'index' in selector:
        index = selector['index']
        if not isinstance(index, int) or not 0 <= index < len(records):
            raise ValueError(f'{where}: index {index!r} is not in 0 to {len(records) - 1}')
        return [index]
    key = 'id' if 'id' in selector else number
    found = [i for i, record in enumerate(records) if record.get(key) == selector[key]]
    if not found:
        raise ValueError(f'{where}: no {kind[:-1]} with {key} {selector[key]!r}')
    return found


def edit_placed(roots, path, kind, add=None, change=None, delete=None, out=''):
    """Add, change or delete the units or doodads placed on a map."""
    from wc3files.doo.map_doodads_file import MapDoodadsFile
    from wc3files.doo.map_units_file import MapUnitsFile
    if kind not in PLACED:
        raise ValueError(f'kind must be one of {", ".join(PLACED)}')
    add, change, delete = list(add or []), list(change or []), list(delete or [])
    if not (add or change or delete):
        raise ValueError('nothing to do: give add, change or delete')
    member, number, object_kinds = PLACED[kind]
    source, out = target_path(roots, path, out)
    files = _read_files(source)
    key = _find(files, member) or member
    handler = MapUnitsFile() if kind == 'units' else MapDoodadsFile()
    if key in files:
        handler.read(files[key])
    data = handler.get_data()
    records = data.setdefault(kind, [])
    known = _known_ids(files, object_kinds)
    bounds = _bounds(files)
    changed, deleted = set(), set()
    for n, item in enumerate(change):
        if not isinstance(item, dict) or not isinstance(item.get('set'), dict) or not item['set']:
            raise ValueError(f'change {n}: give select and a non-empty set')
        for index in _select(records, kind, item.get('select'), f'change {n}'):
            _apply_placed(records[index], item['set'], kind, known, bounds, f'change {n}')
            changed.add(index)
    for n, selector in enumerate(delete):
        deleted.update(_select(records, kind, selector, f'delete {n}'))
    kept = [record for i, record in enumerate(records) if i not in deleted]
    next_number = max((r.get(number) or 0 for r in records), default=-1) + 1
    skins = data.get('skins')
    added = []
    for n, item in enumerate(add):
        if not isinstance(item, dict) or not {'id', 'x', 'y'} <= set(item):
            raise ValueError(f'add {n}: give at least id, x and y')
        record = {'id': item['id'], 'variation': 0, 'position': [0.0, 0.0, 0.0],
                  'rotation': math.radians(270.0), 'scale': [1.0, 1.0, 1.0],
                  'skin': item['id'], 'flags': 2, number: next_number}
        if skins is not None:
            record['has_skin'] = bool(skins)
        if kind == 'units':
            record.update({'owner': 0, 'hp': -1, 'mp': -1})
        else:
            record.update({'life': 100})
        _apply_placed(record, item, kind, known, bounds, f'add {n}')
        kept.append(record)
        added.append({'index': len(kept) - 1, 'id': record['id'], number: next_number})
        next_number += 1
    data[kind] = kept
    handler.import_data(data)
    data_bytes = handler.to_bytes()
    files[key] = data_bytes
    result = commit(source, files, {key: data_bytes}, out)
    result.update({'kind': kind, 'added': added, 'changed': sorted(changed - deleted),
                   'deleted': sorted(deleted), 'total': len(kept)})
    return plain(result)


# ---- edit_records (#274) ----------------------------------------------------

RECORDS = {'regions': 'war3map.w3r', 'cameras': 'war3map.w3c', 'strings': 'war3map.wts'}
_GLOBAL_PREFIX = {'regions': 'gg_rct_', 'cameras': 'gg_cam_'}
_REFERENCE_MEMBERS = ('war3map.j', 'scripts\\war3map.j', 'war3map.lua', 'scripts\\war3map.lua',
                      'war3map.wtg', 'war3map.wct')
_REGION_SET = ('name', 'left', 'right', 'bottom', 'top', 'weather', 'ambient_sound', 'color')
_CAMERA_SET = ('name', 'target_x', 'target_y', 'z_offset', 'rotation', 'angle_of_attack',
               'distance', 'roll', 'field_of_view', 'far_clipping_plane', 'near_clipping_plane')
# A new camera starts as the game's default camera: bj_CAMERA_DEFAULT_* of
# blizzard.j (distance 1650, far z 5000, angle of attack 304, field of view
# 70, roll 0, rotation 90). The near clipping plane is the value the World
# Editor writes, as in the camera files of the corpus.
_CAMERA_DEFAULTS = {'z_offset': 0.0, 'rotation': 90.0, 'angle_of_attack': 304.0,
                    'distance': 1650.0, 'roll': 0.0, 'field_of_view': 70.0,
                    'far_clipping_plane': 5000.0, 'near_clipping_plane': 16.0}


def _global_name(kind, name):
    return _GLOBAL_PREFIX[kind] + name.replace(' ', '_')


def _referenced(files, kind, name):
    """The members that name the region or camera `name` by its script
    global (gg_rct_..., gg_cam_...)."""
    needle = _global_name(kind, name).encode('utf-8')
    found = []
    for member in _REFERENCE_MEMBERS:
        key = _find(files, member)
        if key is not None and needle in files[key]:
            found.append(key)
    return found


def _number(values, key, where):
    value = values[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'{where}: {key} is a number')
    return float(value)


def _set_region(region, values, bounds, where):
    for key in values:
        if key not in _REGION_SET:
            raise ValueError(f'{where}: {key!r} cannot be set on a region; one of '
                             f'{", ".join(_REGION_SET)}')
    if 'name' in values:
        if not isinstance(values['name'], str) or not values['name'].strip():
            raise ValueError(f'{where}: name is a non-empty text')
        region.name = values['name']
    for key in ('left', 'right', 'bottom', 'top'):
        if key in values:
            setattr(region, key, _number(values, key, where))
    if not (region.left < region.right and region.bottom < region.top):
        raise ValueError(f'{where}: left must be below right and bottom below top')
    if bounds and not (bounds[0] <= region.left and region.right <= bounds[2]
                       and bounds[1] <= region.bottom and region.top <= bounds[3]):
        raise ValueError(f'{where}: the region leaves the map, which spans x {bounds[0]} to '
                         f'{bounds[2]} and y {bounds[1]} to {bounds[3]}')
    if 'weather' in values:
        weather = values['weather']
        if weather in ('', None):
            region.weather_effect = b'\x00\x00\x00\x00'
        elif isinstance(weather, str) and len(weather) == 4:
            region.weather_effect = weather.encode('latin-1')
        else:
            raise ValueError(f'{where}: weather is a four-character weather id such as RAhr, '
                             f'or empty for none')
    if 'ambient_sound' in values:
        if not isinstance(values['ambient_sound'], str):
            raise ValueError(f'{where}: ambient_sound is the name of a sound, or empty')
        region.ambient_sound = values['ambient_sound']
    if 'color' in values:
        color = values['color']
        if (not isinstance(color, (list, tuple)) or len(color) != 3
                or not all(isinstance(c, int) and 0 <= c <= 255 for c in color)):
            raise ValueError(f'{where}: color is [red, green, blue], each 0 to 255')
        region.color = tuple(color)


def _set_camera(camera, values, where):
    for key in values:
        if key not in _CAMERA_SET:
            raise ValueError(f'{where}: {key!r} cannot be set on a camera; one of '
                             f'{", ".join(_CAMERA_SET)}')
    if 'name' in values:
        if not isinstance(values['name'], str) or not values['name'].strip():
            raise ValueError(f'{where}: name is a non-empty text')
        camera['cinematic_name'] = values['name']
    for key in _CAMERA_SET[1:]:
        if key in values:
            camera[key] = _number(values, key, where)
    if camera.get('near_clipping_plane', 0) >= camera.get('far_clipping_plane', 1):
        raise ValueError(f'{where}: near_clipping_plane must be below far_clipping_plane')


def _pick(items, name_of, selector, where):
    """Indices matched by {"index": n} or {"name": text}."""
    if not isinstance(selector, dict) or len(set(selector) & {'index', 'name'}) != 1:
        raise ValueError(f'{where}: select one of index or name')
    if 'index' in selector:
        index = selector['index']
        if not isinstance(index, int) or not 0 <= index < len(items):
            raise ValueError(f'{where}: index {index!r} is not in 0 to {len(items) - 1}')
        return [index]
    found = [i for i, item in enumerate(items) if name_of(item) == selector['name']]
    if not found:
        raise ValueError(f'{where}: nothing named {selector["name"]!r}')
    return found


def _edit_named(kind, items, files, add, change, delete, make, apply, name_of, rename):
    """Changes, then deletions, then additions on a list of regions or
    cameras. A deletion or a rename of one the script or triggers name is
    refused: they would no longer compile."""
    changed, deleted = set(), set()
    for n, item in enumerate(change):
        if not isinstance(item, dict) or not isinstance(item.get('set'), dict) or not item['set']:
            raise ValueError(f'change {n}: give select and a non-empty set')
        for index in _pick(items, name_of, item.get('select'), f'change {n}'):
            old = name_of(items[index])
            apply(items[index], item['set'], f'change {n}')
            if name_of(items[index]) != old:
                users = _referenced(files, kind, old)
                if users:
                    raise ValueError(f'change {n}: {old!r} is used as {_global_name(kind, old)} '
                                     f'in {", ".join(users)}; renaming it would break them')
            changed.add(index)
    for n, selector in enumerate(delete):
        for index in _pick(items, name_of, selector, f'delete {n}'):
            users = _referenced(files, kind, name_of(items[index]))
            if users:
                raise ValueError(f'delete {n}: {name_of(items[index])!r} is used as '
                                 f'{_global_name(kind, name_of(items[index]))} in '
                                 f'{", ".join(users)}; deleting it would break them')
            deleted.add(index)
    kept = [item for i, item in enumerate(items) if i not in deleted]
    added = []
    names = {name_of(item) for item in kept}
    for n, values in enumerate(add):
        if not isinstance(values, dict) or not isinstance(values.get('name'), str):
            raise ValueError(f'add {n}: give at least name')
        if values['name'] in names:
            raise ValueError(f'add {n}: {values["name"]!r} is already used; names make the '
                             f'script globals, so they must differ')
        item = make()
        apply(item, values, f'add {n}')
        kept.append(item)
        names.add(values['name'])
        added.append({'index': len(kept) - 1, 'name': values['name']})
    return kept, added, sorted(changed - deleted), sorted(deleted)


def edit_records(roots, path, kind, add=None, change=None, delete=None, out=''):
    """Add, change or delete a map's regions, cameras or strings."""
    if kind not in RECORDS:
        raise ValueError(f'kind must be one of {", ".join(RECORDS)}')
    add, change, delete = list(add or []), list(change or []), list(delete or [])
    if not (add or change or delete):
        raise ValueError('nothing to do: give add, change or delete')
    source, out = target_path(roots, path, out)
    files = _read_files(source)
    member = RECORDS[kind]
    key = _find(files, member) or member
    if kind == 'regions':
        from wc3files.w3r import RegionFile
        from wc3files.w3r.region import Region
        handler = RegionFile()
        if key in files:
            handler.read(files[key])
        regions = list(handler.get_data().values())
        bounds = _bounds(files)
        next_index = max((r.index for r in regions), default=-1) + 1

        def make():
            nonlocal next_index
            region = Region(left=0.0, right=0.0, bottom=0.0, top=0.0, index=next_index,
                            color=(255, 255, 255))
            next_index += 1
            return region
        kept, added, changed, deleted = _edit_named(
            kind, regions, files, add, change, delete, make,
            lambda r, v, w: _set_region(r, v, bounds, w), lambda r: r.name, True)
        handler.import_data({i: region for i, region in enumerate(kept)})
    elif kind == 'cameras':
        from wc3files.w3c import CameraFile
        handler = CameraFile()
        if key in files:
            handler.read(files[key])
        data = handler.get_data()
        template = dict(data['cameras'][0]) if data.get('cameras') else None

        def make():
            camera = dict(template) if template else {}
            camera.update({'target_x': 0.0, 'target_y': 0.0, **_CAMERA_DEFAULTS,
                           'local_pitch': 0.0, 'local_yaw': 0.0, 'local_roll': 0.0})
            return camera
        kept, added, changed, deleted = _edit_named(
            kind, list(data.get('cameras') or []), files, add, change, delete, make,
            _set_camera, lambda c: c.get('cinematic_name'), True)
        data['cameras'] = kept
        handler.import_data(data)
    else:
        from wc3files.table.wts_file import WtsFile
        handler = WtsFile()
        if key in files:
            handler.read(files[key])
        strings = dict(handler.export_data())
        if delete:
            raise ValueError('strings cannot be deleted: other files refer to them by id '
                             '(TRIGSTR_...); change their text instead')
        changed, added = [], []
        for n, item in enumerate(change):
            if (not isinstance(item, dict) or not isinstance(item.get('id'), int)
                    or not isinstance(item.get('text'), str)):
                raise ValueError(f'change {n}: give id (a number) and text')
            if item['id'] not in strings:
                raise ValueError(f'change {n}: no string {item["id"]}; add it instead')
            strings[item['id']] = item['text']
            changed.append(item['id'])
        for n, item in enumerate(add):
            if not isinstance(item, dict) or not isinstance(item.get('text'), str):
                raise ValueError(f'add {n}: give text')
            new_id = max(strings, default=0) + 1
            strings[new_id] = item['text']
            added.append({'id': new_id, 'reference': f'TRIGSTR_{new_id:03d}'})
        for text in strings.values():
            if '\n}' in text.replace('\r\n', '\n'):
                raise ValueError('a string cannot have a line that starts with }: the file '
                                 'format ends a string there')
        handler.import_data(strings)
        deleted = []
    data_bytes = handler.to_bytes()
    files[key] = data_bytes
    result = commit(source, files, {key: data_bytes}, out)
    result.update({'kind': kind, 'added': added, 'changed': changed, 'deleted': deleted})
    return plain(result)
