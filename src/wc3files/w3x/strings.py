"""The strings of a map, by object and field, and a check of its tooltips
(#252).

`map_strings(files)` lists, for a map's `{name: bytes}` table:

- every text field of its object data (name, tooltips, description, editor
  suffix, ...) and every other field holding a TRIGSTR_ reference, with the
  object, the field, the level, the string id when the text is in
  war3map.wts, and the text. Text kept in the object data itself (as an
  optimized map has it) is listed the same way, with no string id;
- the other files that refer to war3map.wts strings (map info, script,
  triggers, ...), with the ids each one uses;
- the strings of war3map.wts that nothing refers to.

`check_strings(files)` reports, per object and field:

- `missing string`: a TRIGSTR_ reference with no entry in war3map.wts; the
  game shows the reference itself;
- `unresolved reference`: a <A000,DataA1> reference in the text whose
  object or field cannot be found, as `optimize --computed-text` resolves
  them; the game shows nothing in its place. On the game's own tooltips
  this resolves 2,878 of 2,880 references; the two left refer to ACbr, which
  has tooltips but no ability data;
- `other object's name`: a short tooltip (Tip) that names another object of
  the same type of this map and not the object's own name, which is usually
  a copied object whose tooltip was not changed. A unit that trains another
  has that unit in its long tooltip, so only Tip is checked.
"""
import re

from wc3files.w3o.data import ObjectFileType

OBJECT_FILES = ('w3u', 'w3t', 'w3a', 'w3q', 'w3b', 'w3d', 'w3h')
TEXT_FIELDS = ('Name', 'Tip', 'Ubertip', 'Description', 'EditorSuffix', 'EditorName',
               'Researchtip', 'Researchubertip', 'Untip', 'Unubertip', 'Awakentip',
               'Revivetip', 'Bufftip', 'Buffubertip', 'Propernames')
_NAME_FIELD = {'w3h': 'EditorName'}
_TRIGSTR = re.compile(r'^\s*TRIGSTR_(\d+)\s*$', re.I)
_TRIGSTR_BYTES = re.compile(rb'TRIGSTR_(\d+)', re.I)
_REFERENCE = re.compile(r'<([^<>]*)>')
_MARKUP = re.compile(r'\|c[0-9a-fA-F]{8}|\|r|\|n', re.I)


def _member(files, name):
    """(stored name, bytes) of a member, by case-insensitive name."""
    for stored, data in files.items():
        if stored.lower().replace('/', '\\') == name:
            return stored, data
    return None, None


def _strings(files):
    from wc3files.table.wts_file import WtsFile
    _, data = _member(files, 'war3map.wts')
    if not data:
        return {}
    table = WtsFile()
    table.read(data)
    return dict(table.export_data())


def _fields(ext):
    """mod id -> metadata row for one object type; items add the unit rows
    marked useItem, which hold their name and tooltips."""
    from wc3files.w3o.object_metadata import ObjectMetaData
    metadata = ObjectMetaData.default()
    fields = dict(metadata.fields(ObjectFileType(ext)))
    if ext == 'w3t':
        for mod, definition in metadata.fields(ObjectFileType.Units).items():
            if str(definition.get('useItem')) == '1':
                fields.setdefault(mod, definition)
    return fields


def _objects(files, ext):
    """[(file name, object table)] for the object file and its skin file."""
    from wc3files.w3o.object_file import ObjectFile
    out = []
    for prefix in ('war3map', 'war3mapskin'):
        name, data = _member(files, f'{prefix}.{ext}')
        if not data:
            continue
        reader = ObjectFile()
        reader.read(data, ext)
        out.append((name, reader.export_data().get(ext) or {}))
    return out


def _values(value):
    """[(level, value)]: level None for a field without levels."""
    if isinstance(value, dict):
        return sorted(value.items())
    return [(None, value)]


def _plain(text):
    return _MARKUP.sub('', text or '')


def map_strings(files):
    """{'objects': [...], 'references': [...], 'unused': [...]}; see the
    module docstring."""
    from wc3files.w3o.object_metadata import field_display_name
    strings = _strings(files)
    used = {}
    entries = []
    object_names = set()
    for ext in OBJECT_FILES:
        fields = _fields(ext)
        for file_name, table in _objects(files, ext):
            object_names.add(file_name.lower())
            for section in ('original', 'custom'):
                for object_id, mods in (table.get(section) or {}).items():
                    for mod_id, mod in mods.items():
                        if mod_id == 'ID':
                            continue
                        definition = fields.get(mod_id)
                        field = field_display_name(definition) if definition else mod_id
                        for level, value in _values(mod.get('value')):
                            if not isinstance(value, str):
                                continue
                            match = _TRIGSTR.match(value)
                            if not match and field not in TEXT_FIELDS:
                                continue
                            string_id = int(match.group(1)) if match else None
                            if string_id is not None:
                                used.setdefault(string_id, set()).add(file_name)
                            entries.append({
                                'file': file_name, 'type': ext, 'id': object_id,
                                'base': mods.get('ID', object_id), 'custom': section == 'custom',
                                'field': field, 'mod': mod_id,
                                'level': level if definition is None or
                                int(definition.get('repeat') or 0) > 0 else None,
                                'string_id': string_id,
                                'text': strings.get(string_id) if match else value,
                                # where the value is stored, for an edit
                                'slot': level,
                            })
    references = []
    for name, data in sorted(files.items()):
        lowered = name.lower()
        if lowered in object_names or lowered == 'war3map.wts' or not data:
            continue
        ids = sorted({int(m.group(1)) for m in _TRIGSTR_BYTES.finditer(data)})
        if ids:
            references.append({'file': name, 'string_ids': ids})
            for string_id in ids:
                used.setdefault(string_id, set()).add(name)
    unused = [{'string_id': i, 'text': strings[i]} for i in sorted(strings) if i not in used]
    return {'strings': len(strings), 'objects': entries, 'references': references,
            'unused': unused}


def _names(entries):
    """(type, object id) -> the object's name as the map sets it, or the
    game's name for it."""
    from wc3files.w3o.object_names import ObjectNames
    game = ObjectNames.base_game()
    names = {}
    for entry in entries:
        field = _NAME_FIELD.get(entry['type'], 'Name')
        if entry['field'] == field and entry['text'] and entry['level'] in (None, 0, 1):
            names.setdefault((entry['type'], entry['id']), _plain(entry['text']).strip())
    for entry in entries:
        key = (entry['type'], entry['id'])
        if key not in names:
            name = game.get(entry['id']) or game.get(entry['base'])
            if name:
                names[key] = name
    return names


def _contains(text, name):
    """Whether `name` is in `text` as whole words, ignoring case. The plain
    substring test first: a map with thousands of objects would otherwise
    compile a pattern for every tooltip and name."""
    if name.lower() not in text.lower():
        return False
    return re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text, re.I) is not None


def check_strings(files):
    """[finding]: each {'severity', 'kind', 'file', 'type', 'id', 'field',
    'level', 'string_id', 'message'}. The first two kinds are issues, the
    third a note; see the module docstring."""
    from wc3files.w3x.optimize import reference_resolver
    listing = map_strings(files)
    entries = listing['objects']
    issues = []

    def report(entry, kind, message):
        severity = 'note' if kind == "other object's name" else 'issue'
        issues.append({'severity': severity, 'kind': kind, 'file': entry['file'], 'type': entry['type'],
                       'id': entry['id'], 'field': entry['field'], 'level': entry['level'],
                       'string_id': entry['string_id'], 'message': message})

    resolve = None
    for entry in entries:
        if entry['string_id'] is not None and entry['text'] is None:
            report(entry, 'missing string',
                   f'TRIGSTR_{entry["string_id"]:03d} is not in war3map.wts')
            continue
        text = entry['text'] or ''
        if '<' not in text:
            continue
        for match in _REFERENCE.finditer(text):
            reference = match.group(1)
            if ',' not in reference:
                continue
            if resolve is None:
                resolve = reference_resolver(files)
            if resolve(reference) is not None:
                continue
            object_id = reference.split(',')[0].strip()[:4]
            what = ('no object ' + object_id if resolve.lookup(object_id) is None
                    else 'no field ' + reference.split(',')[1].strip() + ' on ' + object_id)
            report(entry, 'unresolved reference', f'<{reference}>: {what}')

    names = _names(entries)
    by_type = {}
    for (ext, object_id), name in names.items():
        if len(name) >= 4:
            by_type.setdefault(ext, []).append((object_id, name, name.lower()))
    named = {}
    for entry in entries:
        if entry['field'] != 'Tip' or not entry['text']:
            continue
        own = names.get((entry['type'], entry['id']))
        text = _plain(entry['text'])
        if not own or _contains(text, own):
            continue
        low, own_low = text.lower(), own.lower()
        others = sorted({name for object_id, name, name_low in by_type.get(entry['type'], ())
                         if name_low in low and object_id != entry['id']
                         and name_low not in own_low and own_low not in name_low
                         and _contains(text, name)},
                        key=lambda n: (-len(n), n))
        if others:
            named.setdefault((entry['file'], entry['id'], others[0]), []).append(entry)
    # Once per object and name: an ability repeats its tip at every level.
    for (_, _, other), found in named.items():
        own = names.get((found[0]['type'], found[0]['id']))
        levels = [e['level'] for e in found if e['level']]
        at = (f' (levels {levels[0]} to {levels[-1]})' if len(levels) > 1 else '')
        report(found[0], "other object's name",
               f'names {other!r}, not the object\'s own name {own!r}{at}')
    return issues
