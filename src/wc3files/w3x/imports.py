"""
Imported files of a map: the members listed in war3map.imp (#212).

`import_folder` adds every file of a folder tree to a map at the same
relative paths and lists each one in war3map.imp (#215). The World Editor
imports one file or one folder at a time, without its subfolders.

Functions here work on a map's members as a {name: bytes} table, the shape
`wc3files.w3x.modes.archive_files` returns, and give back a new table.
"""
import os

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
