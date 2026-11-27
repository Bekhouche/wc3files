"""
Stable keys for the strings of a map, a diff between two versions of a map
by key, and translation files keyed the same way (#224).

`TRIGSTR_` numbers change between versions of a map, so a translation made
against them does not carry over. `string_keys(files)` names each string by
where it is used instead:

- `w3u/h000/Name`, `w3a/A000/Ubertip/2`: an object's text field, with the
  level for a field that has levels (`w3u` ... `w3h`; `@skin` is added for
  the war3mapSkin files);
- `w3i/map_name`, `w3i/players/3/name`, `w3i/loading_screen/text`: the map
  info, players by their player number;
- `script/Trig_Intro_Actions/2`: the second `TRIGSTR_` string of that
  function of war3map.j or war3map.lua, which is where GUI triggers put
  their text;
- `war3map.wtg/4`, and the like: a `TRIGSTR_` string only another file uses,
  by its order in that file.

A string used in several places takes the first of these keys. Strings of
war3map.wts that nothing uses get no key.

`diff_strings(old, new)` compares two maps by key. `translation(files,
previous)` makes a translation file, `{'strings': {key: {'source',
'translation'}}}`; given the file of an older version, it keeps each
translation whose source text is unchanged and marks the rest. And
`apply_translation(files, table)` writes the translations into the map:
`war3map.wts` for a `TRIGSTR_` string, the object data or the map info for
text kept inline.
"""

import os
import re
import tempfile

from wc3files.w3x.strings import map_strings, _member, _strings

_TRIGSTR = re.compile(r'^\s*TRIGSTR_(\d+)\s*$', re.I)
_TRIGSTR_ANY = re.compile(r'TRIGSTR_(\d+)', re.I)
_W3I_TEXT = ('map_name', 'author_name', 'map_description', 'players_recommended',
             'loading_screen/text', 'loading_screen/title', 'loading_screen/subtitle',
             'prologue/text', 'prologue/title', 'prologue/subtitle')
_JASS_FUNCTION = re.compile(r'^\s*(?:constant\s+)?function\s+(\w+)')
_LUA_FUNCTION = re.compile(r'^\s*(?:local\s+)?function\s+([\w.:]+)\s*\(')


def _w3i(files):
    from wc3files.cli.formats import FORMATS
    name, data = _member(files, 'war3map.w3i')
    if not data:
        return None, None
    path = tempfile.mktemp(suffix='.w3i')
    try:
        with open(path, 'wb') as handle:
            handle.write(data)
        return name, FORMATS['w3i'].dump(path)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _w3i_entries(info):
    """[(key, path, value)] for the text fields of the map info."""
    out = []
    for field in _W3I_TEXT:
        node = info
        for part in field.split('/'):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str) and node:
            out.append(('w3i/' + field, field.split('/'), node))
    for kind, number in (('players', 'id'), ('forces', None)):
        for index, record in enumerate(info.get(kind) or ()):
            value = record.get('name')
            if isinstance(value, str) and value:
                label = record.get(number, index) if number else index
                out.append((f'w3i/{kind}/{label}/name', [kind, index, 'name'], value))
    return out


def _script_entries(name, text):
    """[(key, string id)] for the TRIGSTR_ strings of a script, by function."""
    pattern = _LUA_FUNCTION if name.lower().endswith('.lua') else _JASS_FUNCTION
    function, counts, out = '(top)', {}, []
    for line in text.split('\n'):
        match = pattern.match(line)
        if match:
            function = match.group(1)
        for found in _TRIGSTR_ANY.finditer(line):
            counts[function] = counts.get(function, 0) + 1
            out.append((f'script/{function}/{counts[function]}', int(found.group(1))))
    return out


def string_keys(files):
    """{key: {'text', 'string_id', 'source'}}: every string of the map with a
    key, as the module docstring says. `source` says where the text lives,
    for apply_translation."""
    strings = _strings(files)
    keyed = {}
    by_id = set()

    def add(key, string_id, text, source):
        base, n = key, 2
        while key in keyed:
            key = f'{base}#{n}'
            n += 1
        if string_id is not None:
            if string_id in by_id:
                return
            by_id.add(string_id)
        keyed[key] = {'text': text, 'string_id': string_id, 'source': source}

    for entry in map_strings(files)['objects']:
        skin = '@skin' if 'skin' in entry['file'].lower() else ''
        key = f"{entry['type']}{skin}/{entry['id']}/{entry['field']}"
        if entry['level'] is not None:
            key += f"/{entry['level']}"
        if entry['text'] is None:
            continue
        add(key, entry['string_id'], entry['text'],
            None if entry['string_id'] is not None else
            ('object', entry['file'], entry['type'], entry['id'], entry['mod'],
             entry['slot']))

    w3i_name, info = _w3i(files)
    if info is not None:
        for key, path, value in _w3i_entries(info):
            match = _TRIGSTR.match(value)
            if match:
                string_id = int(match.group(1))
                if string_id in strings:
                    add(key, string_id, strings[string_id], None)
            else:
                add(key, None, value, ('w3i', w3i_name, path))

    scripts = [n for n in ('war3map.j', 'scripts\\war3map.j', 'war3map.lua',
                           'scripts\\war3map.lua')]
    seen_files = set()
    for script in scripts:
        name, data = _member(files, script)
        if not data:
            continue
        seen_files.add(name.lower())
        for key, string_id in _script_entries(name, data.decode('utf-8', 'replace')):
            if string_id in strings:
                add(key, string_id, strings[string_id], None)

    skip = {'war3map.wts', 'war3map.w3i'}
    for name in sorted(files):
        data = files[name]
        lowered = name.lower()
        if not data or lowered in skip or lowered in seen_files \
                or lowered.rsplit('.', 1)[-1] in ('w3u', 'w3t', 'w3a', 'w3q', 'w3b', 'w3d', 'w3h'):
            continue
        n = 0
        for match in _TRIGSTR_ANY.finditer(data.decode('latin-1')):
            string_id = int(match.group(1))
            if string_id in strings and string_id not in by_id:
                n += 1
                add(f'{name}/{n}', string_id, strings[string_id], None)
    return keyed


def diff_strings(old_files, new_files):
    """{'added', 'removed', 'changed', 'unchanged'}: keys and texts of two
    versions of a map compared by key."""
    old, new = string_keys(old_files), string_keys(new_files)
    return {
        'added': {k: new[k]['text'] for k in new if k not in old},
        'removed': {k: old[k]['text'] for k in old if k not in new},
        'changed': {k: {'old': old[k]['text'], 'new': new[k]['text']}
                    for k in new if k in old and old[k]['text'] != new[k]['text']},
        'unchanged': sum(1 for k in new if k in old and old[k]['text'] == new[k]['text']),
    }


def translation(files, previous=None):
    """A translation file for the map. With `previous` (the file made for an
    older version), a translation is kept where the source text is the same;
    where it changed, the old translation and source are kept beside it with
    status `changed`; a new key has status `new`. Keys gone from the map are
    dropped and counted."""
    strings = {}
    old = (previous or {}).get('strings') or {}
    counts = {'kept': 0, 'changed': 0, 'new': 0, 'dropped': 0}
    for key, entry in string_keys(files).items():
        record = {'source': entry['text'], 'translation': ''}
        before = old.get(key)
        if previous is not None:
            if before is None:
                record['status'] = 'new'
                counts['new'] += 1
            elif before.get('source') == entry['text']:
                record['translation'] = before.get('translation', '')
                counts['kept'] += 1
            else:
                record.update(status='changed', translation=before.get('translation', ''),
                              old_source=before.get('source'))
                counts['changed'] += 1
        strings[key] = record
    if previous is not None:
        counts['dropped'] = sum(1 for key in old if key not in strings)
    return {'version': 1, 'strings': strings}, counts


def apply_translation(files, table, force=False):
    """(files, report): the map with each translation of `table` written in.
    A translation whose source is not the map's current text, or marked
    `changed` by translation() and not yet reviewed (the translator removes
    the mark), is skipped (`stale`) unless `force`; an empty one is `untranslated`; a key the map
    does not have is `unknown`."""
    from wc3files.table.wts_file import WtsFile
    files = dict(files)
    keys = string_keys(files)
    report = {'applied': 0, 'untranslated': 0, 'stale': [], 'unknown': []}
    wts_name, wts_data = _member(files, 'war3map.wts')
    wts = WtsFile()
    if wts_data:
        wts.read(wts_data)
    strings = dict(wts.export_data())
    objects, w3i_edits = {}, []
    for key, record in (table.get('strings') or {}).items():
        entry = keys.get(key)
        if entry is None:
            report['unknown'].append(key)
            continue
        text = record.get('translation') or ''
        if not text:
            report['untranslated'] += 1
            continue
        reviewed = record.get('status') != 'changed'
        if (record.get('source') != entry['text'] or not reviewed) and not force:
            report['stale'].append(key)
            continue
        if entry['string_id'] is not None:
            strings[entry['string_id']] = text
        elif entry['source'][0] == 'object':
            objects.setdefault(entry['source'][1], []).append((entry['source'], text))
        else:
            w3i_edits.append((entry['source'], text))
        report['applied'] += 1
    if wts_data or strings:
        files[wts_name or 'war3map.wts'] = WtsFile().import_data(strings).to_bytes()
    for name, edits in objects.items():
        files[name] = _edit_objects(files[name], edits)
    if w3i_edits:
        files[w3i_edits[0][0][1]] = _edit_w3i(files, w3i_edits)
    return files, report


def _edit_objects(data, edits):
    from wc3files.w3o.object_file import ObjectFile
    ext = edits[0][0][2]
    reader = ObjectFile()
    reader.read(data, ext)
    exported = reader.export_data()
    table = exported[ext]
    for (_, _, _, object_id, mod_id, level), text in edits:
        for section in ('original', 'custom'):
            mods = (table.get(section) or {}).get(object_id)
            if mods is None or mod_id not in mods:
                continue
            mod = mods[mod_id]
            if isinstance(mod.get('value'), dict):
                mod['value'][level] = text
            else:
                mod['value'] = text
    reader.import_data(exported)
    return reader.to_bytes(ext)


def _edit_w3i(files, edits):
    from wc3files.cli.formats import FORMATS
    name, info = _w3i(files)
    for (_, _, path), text in edits:
        node = info
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = text
    target = tempfile.mktemp(suffix='.w3i')
    try:
        FORMATS['w3i'].build(info, target)
        with open(target, 'rb') as handle:
            return handle.read()
    finally:
        if os.path.exists(target):
            os.remove(target)
