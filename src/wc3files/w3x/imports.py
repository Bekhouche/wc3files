"""
Imported files of a map: the members listed in war3map.imp (#212).

`import_folder` adds every file of a folder tree to a map at the same
relative paths and lists each one in war3map.imp (#215). The World Editor
imports one file or one folder at a time, without its subfolders.

Functions here work on a map's members as a {name: bytes} table, the shape
`wc3files.w3x.modes.archive_files` returns, and give back a new table.
"""
import os
import re

from wc3files.imp.imp_file import ImpFile

# What 42,786 of the 48,487 entries of the corpus maps' war3map.imp files
# carry, so the value a new entry takes when the map has none to copy.
DEFAULT_FLAGS = 13

IMP = 'war3map.imp'


def find(files, name):
    """The key of member `name` in `files`, ignoring case, or None."""
    lower = name.lower()
    return next((key for key in files if key.lower() == lower), None)


def import_flags(entries):
    """The flags byte for a new war3map.imp entry: what the map's own
    entries use most, or DEFAULT_FLAGS."""
    counts = {}
    for entry in entries:
        counts[entry.get('flags')] = counts.get(entry.get('flags'), 0) + 1
    return max(counts, key=counts.get) if counts else DEFAULT_FLAGS


def read_imports(files):
    """(key, data) of the map's war3map.imp: its member name, or IMP when
    the map has none, and its get_data() dict."""
    key = find(files, IMP) or IMP
    imports = ImpFile()
    if key in files:
        imports.read(files[key])
    data = imports.get_data()
    data.setdefault('files', [])
    return key, data


def write_imports(files, key, data):
    files[key] = ImpFile().import_data(data).to_bytes()


def member_name(path):
    """A member name as the game writes it: backslashes, no leading one."""
    return path.replace('/', '\\').lstrip('\\')


def is_map_data(name):
    """True for a member the map itself owns rather than an import: the
    war3map.* and war3campaign.* files at the root, the script under
    scripts\\, and the archive's own (listfile) and (attributes)."""
    lower = member_name(name).lower()
    if lower.startswith('('):
        return True
    if '\\' not in lower:
        return lower.startswith(('war3map', 'war3campaign'))
    return lower in ('scripts\\war3map.j', 'scripts\\war3map.lua')


def folder_members(folder, prefix=''):
    """[(member name, file path)] for every file under `folder`, sorted by
    name. `prefix` is put before each name, as `war3mapImported\\`."""
    prefix = member_name(prefix)
    if prefix and not prefix.endswith('\\'):
        prefix += '\\'
    found = []
    for directory, subdirectories, names in os.walk(folder):
        subdirectories.sort()
        for name in names:
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, folder)
            found.append((prefix + member_name(relative), path))
    return sorted(found, key=lambda item: item[0].lower())


def import_folder(files, folder, prefix='', replace=True):
    """(files, report): `files` with every file under `folder` added at its
    relative path, and each one listed in war3map.imp.

    A member the map already has is replaced, or kept when `replace` is
    False. A file whose name is map data (war3map.w3i, war3map.j, ...) is
    skipped, since war3map.imp lists imports and these files are not.
    The report lists the names `added`, `replaced`, `kept` and `skipped`,
    and the number of new war3map.imp entries as `listed`."""
    if not os.path.isdir(folder):
        raise ValueError(f'{folder} is not a folder')
    files = dict(files)
    report = {'added': [], 'replaced': [], 'kept': [], 'skipped': [], 'listed': 0}
    imp_key, listing = read_imports(files)
    entries = listing['files']
    listed = {member_name(entry.get('path', '')).lower() for entry in entries}
    flags = import_flags(entries)
    for name, path in folder_members(folder, prefix):
        if is_map_data(name):
            report['skipped'].append(name)
            continue
        existing = find(files, name)
        if existing is not None and not replace:
            report['kept'].append(existing)
            continue
        with open(path, 'rb') as handle:
            data = handle.read()
        key = existing or name
        files[key] = data
        report['replaced' if existing else 'added'].append(key)
        if key.lower() not in listed:
            entries.append({'flags': flags, 'path': key})
            listed.add(key.lower())
            report['listed'] += 1
    if report['listed']:
        write_imports(files, imp_key, listing)
    return files, report


# --- Moving an import and the references to it (#214) ----------------------

PREFIX = 'war3mapImported\\'

# Text files: a path there is replaced where a character that cannot be
# part of a path bounds it on both sides.
TEXT_EXTENSIONS = frozenset(('j', 'lua', 'ai', 'fdf', 'toc', 'txt', 'slk', 'mdl', 'wts',
                             'ini'))
# Scripts write a backslash twice inside a string literal, and so do the
# string parameters of GUI triggers, which the World Editor copies into the
# script; both forms are rewritten there.
ESCAPED_EXTENSIONS = frozenset(('j', 'lua', 'ai', 'wtg'))
# Binary files that store their strings one after another, each ended by a
# NUL, with no offsets or lengths, so a path there can change length: the
# object files, the map info, the sounds and the GUI triggers.
ZSTRING_EXTENSIONS = frozenset(('w3u', 'w3t', 'w3a', 'w3b', 'w3d', 'w3h', 'w3q', 'w3o',
                                'w3i', 'w3s', 'wtg'))
MODEL_EXTENSIONS = ('mdx', 'mdl')

# An MDX file keeps a texture, attachment or particle emitter path in a
# fixed field of 260 bytes, padded with NULs.
_MDX_PATH = 260
_MDX_PATH_CHUNKS = (b'TEXS', b'ATCH', b'PREM')

_BEFORE = rb'(?<![A-Za-z0-9_\\/.\-])'
_AFTER = rb'(?![A-Za-z0-9_.])'
# A double-quoted string of a JASS or Lua script, escapes included.
_STRING_LITERAL = re.compile(rb'"((?:[^"\\\r\n]|\\.)*)"')


def _extension(name):
    base = name.replace('/', '\\').rsplit('\\', 1)[-1]
    return base.rsplit('.', 1)[-1].lower() if '.' in base else ''


def _stem(name):
    return name[:-(len(_extension(name)) + 1)] if _extension(name) else name


def _canonical(path):
    """The key a reference is looked up by: lower case, backslashes, and a
    model always .mdx, since the game loads either for a reference to one."""
    path = path.replace('\\\\', '\\').replace('/', '\\').lower()
    if _extension(path) in MODEL_EXTENSIONS:
        path = _stem(path) + '.mdx'
    return path


def _path_pattern(path, escaped):
    separator = rb'(?:\\\\|/)' if escaped else rb'(?:\\|/)'
    is_model = _extension(path) in MODEL_EXTENSIONS
    body = _stem(path) if is_model else path
    parts = [re.escape(part.encode('utf-8')) for part in body.split('\\')]
    pattern = separator.join(parts)
    if is_model:
        pattern += rb'\.(?:mdx|mdl)'
    return pattern


def _reference_regex(moves, escaped):
    olds = sorted(moves, key=len, reverse=True)
    alternatives = b'|'.join(_path_pattern(old, escaped) for old in olds)
    return re.compile(_BEFORE + b'(?:' + alternatives + b')' + _AFTER, re.IGNORECASE)


def _new_reference(found, new, escaped):
    """`new` written as the reference `found` was: its separator, and its
    own model extension."""
    text = found.decode('utf-8', errors='replace')
    if _extension(new) in MODEL_EXTENSIONS and _extension(text) in MODEL_EXTENSIONS:
        new = _stem(new) + text[-4:]
    if '/' in text:
        new = new.replace('\\', '/')
    elif escaped:
        new = new.replace('\\', '\\\\')
    return new.encode('utf-8')


def _rewrite_text(data, regex, lookup, escaped):
    count = 0

    def one(match):
        nonlocal count
        count += 1
        return _new_reference(match.group(0), lookup[_canonical(
            match.group(0).decode('utf-8', errors='replace'))], escaped)

    return regex.sub(one, data), count


def _mdx_chunks(data):
    """[(tag, start, end)] of an MDX file's chunks, or [] when it does not
    read as one."""
    if data[:4] != b'MDLX':
        return []
    chunks, offset = [], 4
    while offset + 8 <= len(data):
        tag = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], 'little')
        chunks.append((tag, offset + 8, offset + 8 + size))
        offset += 8 + size
    return chunks if offset == len(data) else []


def _rewrite_mdx(data, regex, lookup):
    """(data, count, refused): the MDX with each path replaced inside its
    field. A longer path is written only in a chunk known to keep paths in
    260-byte fields, and only over the field's own NUL padding."""
    chunks = _mdx_chunks(data)
    out = bytearray(data)
    count, refused = 0, []
    for match in regex.finditer(data):
        start, end = match.span()
        if end >= len(data) or data[end] != 0:
            continue
        new = _new_reference(match.group(0), lookup[_canonical(
            match.group(0).decode('utf-8', errors='replace'))], False)
        if len(new) > end - start:
            in_field = any(tag in _MDX_PATH_CHUNKS and begin <= start < stop
                           for tag, begin, stop in chunks)
            if (not in_field or len(new) >= _MDX_PATH
                    or any(data[end:start + len(new) + 1])):
                refused.append(match.group(0).decode('utf-8', errors='replace'))
                continue
        out[start:start + len(new)] = new
        out[start + len(new):max(end, start + len(new) + 1)] = \
            bytes(max(end, start + len(new) + 1) - start - len(new))
        count += 1
    return bytes(out), count, refused


def _reads(name, data):
    """True when `data` reads as the format of member `name`, for the
    binary files a move edits; a format wc3files does not read counts."""
    import tempfile
    from wc3files.cli.formats import FORMATS, format_for_name
    fmt = format_for_name(name)
    if fmt not in FORMATS:
        return True
    handle = tempfile.NamedTemporaryFile(suffix='_' + name.replace('\\', '/').rsplit(
        '/', 1)[-1], delete=False)
    try:
        handle.write(data)
        handle.close()
        FORMATS[fmt].dump(handle.name)
        return True
    except Exception:
        return False
    finally:
        os.remove(handle.name)


def _basename(path):
    return path.replace('\\', '/').rsplit('/', 1)[-1]


def _unresolved(files, moves, moved_names):
    """[{'member', 'text'}] for each place a moved file still shows after
    the move: its old path, or its file name alone, as in a path the script
    builds while it runs. A path in another folder that ends in the same
    file name is another file, and is not listed."""
    if not moves:
        return []
    names = b'|'.join(_path_pattern(_basename(old), False)
                      for old in sorted(moves, key=lambda o: len(_basename(o)), reverse=True))
    regex = re.compile(rb'[A-Za-z0-9_\\/.\- ]{0,200}?(?<![A-Za-z0-9_])(?:' + names + b')'
                       + _AFTER, re.IGNORECASE)
    olds = {_canonical(old) for old in moves}
    bare = {_canonical(_basename(old)) for old in moves}
    news = {_canonical(new) for new in moves.values()}
    # A script string that ends in a moved file's old folder, as in
    # "war3mapImported\\" + name, builds a path while the script runs.
    folders = {_canonical(old).rsplit('\\', 1)[0] + '\\' for old, new in moves.items()
               if '\\' in old and _canonical(old).rsplit('\\', 1)[0]
               != _canonical(new).rpartition('\\')[0]}
    found = []
    for member, data in files.items():
        ext = _extension(member)
        if member in moved_names or not (ext in TEXT_EXTENSIONS or ext in ZSTRING_EXTENSIONS
                                         or ext in ('mdx', 'wct')):
            continue
        for match in regex.finditer(data):
            text = match.group(0).decode('utf-8', errors='replace').strip()
            path = _canonical(text)
            if path in news:
                continue
            if any(path.endswith(old) for old in olds):
                found.append({'member': member, 'text': text,
                              'reason': 'the old path is still there'})
            elif path in bare:
                found.append({'member': member, 'text': text,
                              'reason': 'the file name alone, which may build the old path'})
        if folders and (ext in ESCAPED_EXTENSIONS or ext == 'wct'):
            for match in _STRING_LITERAL.finditer(data):
                text = match.group(1).decode('utf-8', errors='replace')
                if any(_canonical(text).endswith(folder) for folder in folders):
                    found.append({'member': member, 'text': text,
                                  'reason': 'a moved file\'s old folder, which may build '
                                            'the old path'})
    return found


def _rewrite_wct(data, script_regex, text_regex, lookup):
    """(data, count) for war3map.wct, whose custom-text triggers carry their
    lengths, so it is rewritten through its reader and writer. The World
    Editor writes war3map.j from these triggers again on its next save."""
    from wc3files.wtg import MapCustomTextTriggersFile
    handler = MapCustomTextTriggersFile()
    handler.read(data)
    if handler.to_bytes() != data:
        raise ValueError('does not write back as it reads')
    content = handler.get_data()
    count = 0

    def rewrite(text):
        nonlocal count
        raw, first = _rewrite_text(text.encode('utf-8'), script_regex, lookup, True)
        raw, second = _rewrite_text(raw, text_regex, lookup, False)
        count += first + second
        return raw.decode('utf-8')

    content['global_code'] = rewrite(content.get('global_code') or '')
    content['triggers'] = [rewrite(text) if isinstance(text, str) else text
                           for text in content.get('triggers') or []]
    if not count:
        return data, 0
    return handler.import_data(content).to_bytes(), count


def move_imports(files, moves, portraits=True):
    """(files, report): `files` with each member of `moves` ({old: new})
    renamed, and every reference to it rewritten: object data, the map info,
    sounds, GUI triggers, the script, war3map.imp, models, FDF and the other
    text files. Names are matched ignoring case, with either separator, and
    a model as .mdl or .mdx, as the game loads it.

    With `portraits`, a model's `<name>_portrait` model moves with it, since
    the game finds the portrait by name.

    The report gives `moved` [(old, new)], `updated` {member: references
    rewritten}, and `unresolved`, the places where a moved file's name still
    shows: a path the script builds while it runs, a reference an MDX field
    has no room for, or a binary file left as it was because it did not read
    before or after the edit."""
    files = dict(files)
    plan = {}
    for old, new in moves.items():
        key = find(files, member_name(old))
        if key is None:
            raise ValueError(f'{old} is not in the map')
        new = member_name(new)
        if not new or new.endswith('\\'):
            raise ValueError(f'{new!r} is not a file name')
        plan[key] = new
    if portraits:
        for old, new in list(plan.items()):
            if _extension(old) in MODEL_EXTENSIONS and _extension(new) in MODEL_EXTENSIONS:
                portrait = find(files, _stem(old) + '_portrait.' + _extension(old))
                if portrait is not None and portrait not in plan:
                    plan[portrait] = _stem(new) + '_portrait.' + _extension(new)
    staying = {key.lower() for key in files} - {old.lower() for old in plan}
    for old, new in plan.items():
        if new.lower() in staying:
            raise ValueError(f'{old} cannot move to {new}: the map already has that file')
    if len({new.lower() for new in plan.values()}) != len(plan):
        raise ValueError('two files would move to the same name')

    lookup = {_canonical(old): new for old, new in plan.items()}
    text_regex = _reference_regex(plan, escaped=False)
    script_regex = _reference_regex(plan, escaped=True)
    report = {'moved': sorted(plan.items(), key=lambda item: item[0].lower()),
              'updated': {}, 'unresolved': []}

    for old, new in plan.items():
        files[new] = files.pop(old)
    moved_names = set(plan.values())

    for member in sorted(files, key=str.lower):
        ext = _extension(member)
        data = files[member]
        if ext in ESCAPED_EXTENSIONS:
            data, count = _rewrite_text(data, script_regex, lookup, True)
            more_data, more = _rewrite_text(data, text_regex, lookup, False)
            data, count = more_data, count + more
        elif ext in TEXT_EXTENSIONS or ext in ZSTRING_EXTENSIONS:
            data, count = _rewrite_text(data, text_regex, lookup, False)
        elif ext == 'wct':
            try:
                data, count = _rewrite_wct(data, script_regex, text_regex, lookup)
            except Exception as error:
                report['unresolved'].append({'member': member, 'text': '', 'reason': (
                    f'not read ({error}); left as it was')})
                continue
        elif ext == 'mdx':
            data, count, refused = _rewrite_mdx(data, text_regex, lookup)
            report['unresolved'].extend(
                {'member': member, 'text': text, 'reason': 'no room in the MDX field'}
                for text in refused)
        else:
            continue
        if not count:
            continue
        if ext in ZSTRING_EXTENSIONS:
            # A byte edit is kept only when the file reads before and after.
            problem = ('does not read, so the edit cannot be checked'
                       if not _reads(member, files[member]) else
                       'did not read after the edit' if not _reads(member, data) else None)
            if problem:
                report['unresolved'].append({'member': member, 'text': '',
                                             'reason': problem + '; left as it was'})
                continue
        files[member] = data
        report['updated'][member] = count

    imp_key, listing = read_imports(files)
    renamed = 0
    by_name = {old.lower(): new for old, new in plan.items()}
    for entry in listing['files']:
        new = by_name.get(member_name(entry.get('path', '')).lower())
        if new is not None:
            entry['path'] = new
            renamed += 1
    if renamed:
        write_imports(files, imp_key, listing)
        report['updated'][imp_key] = renamed

    report['unresolved'].extend(_unresolved(files, plan, moved_names))
    return files, report


def strip_prefix_moves(files, names=None, prefix=PREFIX):
    """({old: new}, skipped): the moves that take `prefix` off each member
    under it, or off `names` only. A member whose name without the prefix
    the map already has is skipped, as [(old, new)]."""
    prefix = member_name(prefix)
    if not prefix.endswith('\\'):
        prefix += '\\'
    chosen = [find(files, member_name(name)) for name in names] if names else list(files)
    moves, skipped = {}, []
    taken = {key.lower() for key in files}
    for key in chosen:
        if key is None or not key.lower().startswith(prefix.lower()):
            continue
        new = key[len(prefix):]
        if new.lower() in taken:
            skipped.append((key, new))
            continue
        moves[key] = new
        taken.add(new.lower())
    return moves, skipped


# --- File names with more than one dot (#213) --------------------------------

def multi_dot_members(files):
    """The members whose file name has more than one dot, sorted. As the
    author of Import Fixer for Reforged 3.00's Multi-Dot Bug describes it,
    patch 3.0 takes the extension from the first dot, so the game does not
    load `TownHallElfV1.01.mdx`, while the World Editor still shows it. Map
    data is left out; a dot in a folder name is not counted."""
    return sorted((name for name in files
                   if _basename(name).count('.') > 1 and not is_map_data(name)),
                  key=str.lower)


def multi_dot_moves(files):
    """({old: new}, skipped): the moves that turn each dot but the last of a
    file name into an underscore (`TownHallElfV1.01.mdx` becomes
    `TownHallElfV1_01.mdx`). A member whose new name the map already has, or
    that another member takes first, is skipped, as [(old, new)]."""
    return undot_moves(files, {old: old for old in multi_dot_members(files)})


def undotted(name):
    """`name` with each dot but the last of its file name an underscore."""
    folder, _, base = name.replace('/', '\\').rpartition('\\')
    stem, _, ext = base.rpartition('.')
    return (folder + '\\' if folder else '') + stem.replace('.', '_') + '.' + ext


def undot_moves(files, moves):
    """({old: new}, skipped): `moves` with every new name that has more than
    one dot undotted, and the rest kept. A move whose undotted name the map
    already has, or that another move takes first, is skipped, as
    [(old, new)]."""
    taken = {key.lower() for key in files} - {old.lower() for old in moves}
    out, skipped = {}, []
    for old, new in moves.items():
        if _basename(new).count('.') > 1 and not is_map_data(new):
            new = undotted(new)
        if new == old:
            continue
        if new.lower() in taken:
            skipped.append((old, new))
            continue
        out[old] = new
        taken.add(new.lower())
    return out, skipped
