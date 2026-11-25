"""The edit tools of the wc3files MCP server (#272), as plain functions.

The server registers them only when it is started with --allow-write. Each
one reads the whole map (a protected map is refused, as for reading),
applies its changes in memory, refuses anything it cannot apply exactly,
writes a new map, then reads that map back and checks the changed members
before it reports success. The source map is changed only when `out` names
it; by default the result goes to <name>.edited.w3x beside it.

Like tools.py, nothing here imports the `mcp` package.
"""
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
