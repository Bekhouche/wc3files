"""
Transferring the data of one map into others (#218).

`transfer(source, target, parts)` copies the object data, triggers and
script, game interface, gameplay constants, imports and sounds of a source
map into a target map, as WarTransfer does, and keeps the target's terrain,
placed units and doodads, regions, cameras and map info. Altered-melee mods
ship this way on many melee maps.

The script
----------
The World Editor writes war3map.j (or war3map.lua) from the map info, the
placed objects and the triggers, so neither map's script can be copied as it
is. The merged script takes, by function:

1. from the target, what the World Editor writes from the target's own map
   info and placed objects: `config`, the player slots, teams and ally
   priorities, `CreateAllUnits` and its helpers, item drop tables, upgrades,
   tech tree, random groups, regions and cameras;
2. from the source, every other function: the triggers, the custom script
   code and what JassHelper adds;
3. `main`: the target's statements up to and including `InitBlizzard()`
   (camera bounds, environment, placement), then the source's statements
   after it (globals, library initializers, triggers). A call the source
   makes before `InitBlizzard()` to one of its own functions is kept;
4. `InitSounds` goes with the sounds: the source's when they are copied;
5. the globals: the source's, with the placement variables (`gg_unit_`,
   `gg_rct_`, `gg_cam_`, `gg_dest_`, `gg_item_`) taken from the target. A
   placement variable the source's triggers name and the target does not
   have is declared as null, so the script still loads, and is reported.

The merged script is checked with the script checker, and refused when it has
an issue that neither map's script has. A JASS source is translated to Lua for
a Lua target (scripts.jass_to_lua); a Lua source cannot go into a JASS target.

Strings: the TRIGSTR_ references of what is copied point into the source's
war3map.wts. Each source string is added to the target's war3map.wts, under
its own number when the target does not use that number, and under a new one
otherwise, and the copied files are rewritten to match.
"""
import os
import re
import tempfile

from wc3files.w3x.imports import (find, import_flags, is_map_data, member_name,
                                  read_imports, write_imports)

PARTS = ('objects', 'triggers', 'interface', 'constants', 'imports', 'sounds')
OBJECT_TYPES = ('w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q')
OBJECT_FILES = tuple('war3map.' + ext for ext in OBJECT_TYPES) \
    + tuple('war3mapSkin.' + ext for ext in OBJECT_TYPES)
TRIGGER_FILES = ('war3map.wtg', 'war3map.wct')
SCRIPTS = ('war3map.j', 'scripts\\war3map.j', 'war3map.lua', 'scripts\\war3map.lua')
SINGLE_FILES = {'interface': ('war3mapSkin.txt',), 'constants': ('war3mapMisc.txt',),
                'sounds': ('war3map.w3s',)}

# The functions the World Editor writes from the map info and the placed
# objects; the merged script takes them from the target.
TARGET_FUNCTIONS = re.compile(
    r'^(?:config|InitCustomPlayerSlots|InitCustomTeams|InitAllyPriorities|CreateRegions'
    r'|CreateCameras|CreateAllDestructables|CreateAllItems|CreateAllUnits|CreatePlayerUnits'
    r'|CreatePlayerBuildings|CreateNeutral\w*|Create(?:Buildings|Units)ForPlayer\d+'
    r'|(?:Unit|Doodad|ItemTable)\d+_DropItems|InitUpgrades(?:_Player\d+)?'
    r'|InitTechTree(?:_Player\d+)?|InitRandomGroups)$')
PLACEMENT = re.compile(r'\bgg_(?:unit|rct|cam|dest|item)_\w+')
SOUND = re.compile(r'\bgg_snd_\w+')
_TRIGSTR = re.compile(rb'TRIGSTR_(\d+)')
_PLACEMENT_CALLS = re.compile(r'\b(?:CreateRegions|CreateCameras|CreateAllDestructables'
                              r'|CreateAllItems|CreateAllUnits|InitBlizzard)\s*\(')


class _Language:
    def __init__(self, lua):
        self.lua = lua
        if lua:
            self.start = re.compile(r'^function\s+([\w.:]+)\s*\(')
            self.end = re.compile(r'^end\b')
            self.declared = re.compile(r'^\s*(\w+)\s*=')
        else:
            self.start = re.compile(r'^\s*(?:constant\s+)?function\s+(\w+)\s+takes\b')
            self.end = re.compile(r'^\s*endfunction\b')
            self.declared = re.compile(r'^\s*(?:constant\s+)?\w+\s+(?:array\s+)?(\w+)')

    def null_declaration(self, line, name, newline):
        if self.lua:
            return f'{name} = nil{newline}'
        kind = re.match(r'^\s*(?:constant\s+)?(\w+)', line)
        return f'    {kind.group(1) if kind else "handle"} {name} = null{newline}'


def _lua_function_ends(text):
    """{first line index: last line index} of each top-level function of a
    Lua script. The World Editor writes Lua without indentation, so a nested
    `end` is at column 0 too; the blocks are counted on the tokens."""
    from wc3files.scripts.lua_lexer import tokenize
    ends, depth, start = {}, 0, None
    for token in tokenize(text):
        if token.kind != 'KEYWORD':
            continue
        if token.value in ('function', 'if', 'do', 'repeat'):
            if depth == 0 and token.value == 'function' and token.col == 1:
                start = token.line - 1
            depth += 1
        elif token.value in ('end', 'until'):
            depth -= 1
            if depth == 0 and start is not None:
                ends[start] = token.line - 1
                start = None
    return ends


def _split(text, language):
    """[(kind, name, lines)] for a script: 'globals' (a JASS globals block,
    or the top-level lines of Lua before its first function), 'function',
    and 'text' for anything else, each with its lines."""
    lines = text.splitlines(keepends=True)
    lua_ends = _lua_function_ends(text) if language.lua else {}
    pieces, index = [], 0
    while index < len(lines):
        line = lines[index]
        start = language.start.match(line)
        if start:
            if language.lua:
                if index not in lua_ends:
                    raise ValueError(f'the Lua function on line {index + 1} has no end')
                end = lua_ends[index]
            else:
                end = index
                while end < len(lines) and not language.end.match(lines[end]):
                    end += 1
            pieces.append(('function', start.group(1), lines[index:end + 1]))
            index = end + 1
            continue
        if not language.lua and re.match(r'^\s*globals\b', line):
            end = index
            while end < len(lines) and not re.match(r'^\s*endglobals\b', lines[end]):
                end += 1
            pieces.append(('globals', None, lines[index:end + 1]))
            index = end + 1
            continue
        kind = 'globals' if language.lua and language.declared.match(line) else 'text'
        if pieces and pieces[-1][0] == kind and kind != 'function':
            pieces[-1][2].append(line)
        else:
            pieces.append((kind, None, [line]))
        index += 1
    return pieces


def _functions(pieces):
    return {name: lines for kind, name, lines in pieces if kind == 'function'}


def _calls(line):
    return set(re.findall(r'(\w+)\s*\(', line))


def merge_scripts(source, target, lua, copy_sounds=True):
    """(text, report): the source's script merged into the target's, as the
    module docstring describes. Both are text in the same language."""
    language = _Language(lua)
    newline = '\r\n' if '\r\n' in target else '\n'
    src = _split(source, language)
    tgt = _split(target, language)
    src_functions = _functions(src)
    tgt_functions = _functions(tgt)
    for name, functions in (('source', src_functions), ('target', tgt_functions)):
        if 'main' not in functions:
            raise ValueError(f'the {name} script has no function main')

    def from_target(name):
        return bool(TARGET_FUNCTIONS.match(name)) or (name == 'InitSounds' and not copy_sounds)

    placement = re.compile(PLACEMENT.pattern + ('' if copy_sounds else '|' + SOUND.pattern))
    target_group = [(name, lines) for kind, name, lines in tgt
                    if kind == 'function' and name not in ('main', 'config') and from_target(name)]
    kept = {name for name in src_functions if name != 'main' and not from_target(name)}

    # main: the target's statements to InitBlizzard, then the source's.
    def body(lines):
        inner = lines[1:-1]
        locals_ = [line for line in inner if re.match(r'^\s*local\b', line)]
        statements = [line for line in inner if line not in locals_]
        split = next((i for i, line in enumerate(statements)
                      if re.search(r'\bInitBlizzard\s*\(', line)), None)
        return locals_, statements, split

    t_locals, t_statements, t_split = body(tgt_functions['main'])
    s_locals, s_statements, s_split = body(src_functions['main'])
    if t_split is None or s_split is None:
        raise ValueError('a main has no InitBlizzard() call to split it at')
    before = [line for line in t_statements[:t_split]
              if not re.search(r'\bInitSounds\s*\(', line)]
    own_calls = [line for line in s_statements[:s_split]
                 if _calls(line) & (kept - {'InitSounds'})]
    sounds = 'InitSounds' in kept or (not copy_sounds and 'InitSounds' in tgt_functions)
    if sounds:
        indent = re.match(r'^\s*', next((line for line in before if line.strip()), '    ')).group(0)
        call = f'{indent}InitSounds(){newline}' if lua else f'{indent}call InitSounds(){newline}'
        position = next((i for i, line in enumerate(before) if _PLACEMENT_CALLS.search(line)),
                        len(before))
        before.insert(position, call)
    local_names = set()
    main_locals = []
    for line in t_locals + s_locals:
        name = re.match(r'^\s*local\s+\w+\s+(?:array\s+)?(\w+)', line)
        key = name.group(1) if name else line
        if key not in local_names:
            local_names.add(key)
            main_locals.append(line)
    main = ([tgt_functions['main'][0]] + main_locals + before + own_calls
            + [t_statements[t_split]] + s_statements[s_split + 1:]
            + [tgt_functions['main'][-1]])

    # The globals: the source's, with the target's placement variables.
    target_placement = {}
    for kind, _, lines in tgt:
        if kind == 'globals':
            for line in lines:
                declared = language.declared.match(line)
                if declared and placement.fullmatch(declared.group(1)):
                    target_placement[declared.group(1)] = line
    source_placement = {}
    out, inserted, globals_done = [], False, False
    for kind, name, lines in src:
        if kind == 'globals':
            block = []
            for line in lines:
                declared = language.declared.match(line)
                if declared and placement.fullmatch(declared.group(1)):
                    source_placement[declared.group(1)] = line
                    continue
                block.append(line)
            if not globals_done:
                extra = list(target_placement.values())
                if language.lua:
                    block = extra + block
                else:
                    block = block[:1] + extra + block[1:]
                globals_done = True
            out.append(('globals', None, block))
        elif kind == 'function':
            if name == 'main':
                continue
            if name in kept:
                out.append((kind, name, lines))
            elif not inserted:
                out.extend(('function', n, l) for n, l in target_group)
                inserted = True
        else:
            out.append((kind, name, lines))
    if not inserted:
        first = next((i for i, piece in enumerate(out) if piece[0] == 'function'), len(out))
        out[first:first] = [('function', n, l) for n, l in target_group]
    if not globals_done:
        block = list(target_placement.values())
        if not language.lua:
            block = [f'globals{newline}'] + block + [f'endglobals{newline}']
        out.insert(0, ('globals', None, block))
    out.append(('function', 'main', main))
    out.append(('function', 'config', tgt_functions.get('config', [])))

    text = ''.join(''.join(lines) for _, _, lines in out)
    missing = sorted(set(placement.findall(text)) - set(target_placement))
    declared_missing = []
    if missing:
        lines = [language.null_declaration(source_placement.get(name, ''), name, newline)
                 for name in missing if name in source_placement]
        declared_missing = [name for name in missing if name in source_placement]
        for index, (kind, _, block) in enumerate(out):
            if kind == 'globals':
                if language.lua:
                    out[index] = (kind, None, lines + block)
                else:
                    out[index] = (kind, None, block[:1] + lines + block[1:])
                break
        text = ''.join(''.join(lines) for _, _, lines in out)
    report = {'from_target': [name for name, _ in target_group] + ['config'],
              'from_source': sorted(kept),
              'missing_placement': declared_missing,
              'dropped_source': sorted(set(src_functions) - kept - {'main'})}
    return text, report


def _kept_source(text, lua, copy_sounds):
    """The part of a source script that merge_scripts keeps: everything but
    the functions taken from the target."""
    kept = []
    for kind, name, lines in _split(text, _Language(lua)):
        if kind == 'function' and name != 'main' and (
                TARGET_FUNCTIONS.match(name) or (name == 'InitSounds' and not copy_sounds)):
            continue
        kept.extend(lines)
    return ''.join(kept)


def _script_key(files):
    return next((key for name in SCRIPTS for key in [find(files, name)] if key), None)


def _issues(name, text):
    from wc3files.mcp.edits import _issues as issues
    return issues(name, text)


def _wts(files):
    from wc3files.table.wts_file import WtsFile
    handler = WtsFile()
    key = find(files, 'war3map.wts')
    if key is not None:
        handler.read(files[key])
    return key or 'war3map.wts', handler


def _rewrite_strings(data, table):
    if not table:
        return data, 0
    count = 0

    def one(match):
        nonlocal count
        new = table.get(int(match.group(1)))
        if new is None:
            return match.group(0)
        count += 1
        return f'TRIGSTR_{new:03d}'.encode('ascii')

    return _TRIGSTR.sub(one, data), count


def _rewrite_wct(data, table):
    from wc3files.wtg import MapCustomTextTriggersFile
    handler = MapCustomTextTriggersFile()
    handler.read(data)
    content = handler.get_data()
    count = 0

    def one(text):
        nonlocal count
        raw, n = _rewrite_strings(text.encode('utf-8'), table)
        count += n
        return raw.decode('utf-8')

    content['global_code'] = one(content.get('global_code') or '')
    content['triggers'] = [one(t) if isinstance(t, str) else t
                           for t in content.get('triggers') or []]
    return (handler.import_data(content).to_bytes() if count else data), count


def _object_data(files, name):
    from wc3files.w3o import ObjectFile
    ext = name.rsplit('.', 1)[-1].lower()
    key = find(files, name)
    if key is None:
        return None
    handle = tempfile.NamedTemporaryFile(suffix='_' + name, delete=False)
    try:
        handle.write(files[key])
        handle.close()
        handler = ObjectFile()
        handler.read(handle.name)
        return handler.export_data()[ext]
    finally:
        os.remove(handle.name)


def _object_bytes(data, ext):
    from wc3files.w3o import ObjectFile
    handler = ObjectFile()
    handler.import_data({ext: data})
    return handler.to_bytes(ext)


def transfer(source, target, parts=PARTS, replace_imports=None, prefer='source'):
    """(files, report): the target map's members with `parts` of the
    source's copied in. `source` and `target` are {name: bytes} member
    tables (wc3files.w3x.modes.archive_files); neither is changed.

    Parts: 'objects' (object data, merged with the target's), 'triggers'
    (war3map.wtg, war3map.wct and the script, merged), 'interface'
    (war3mapSkin.txt), 'constants' (war3mapMisc.txt), 'imports' (the
    imported files and war3map.imp), and 'sounds' (war3map.w3s; its files
    are imports). A part the source does not have leaves the target's as it
    is.

    `prefer` says which side wins where both have something: with 'source',
    the source's object takes a rawcode both use, and the source's interface,
    constants, sounds and imported files replace the target's; with 'target',
    the target keeps its own, as a campaign's maps do over the campaign's
    data. `replace_imports` overrides `prefer` for the imported files.

    The report gives `copied` (member names), `kept` (target members a part
    left), `objects` (per type, the rawcodes both sides had, where the side
    `prefer` names won), `script` (merge_scripts' report, with
    `translated` when a JASS source went into a Lua target), `strings`
    (source string number to target number, for each string copied), and
    `notes`."""
    parts = tuple(parts)
    unknown = set(parts) - set(PARTS)
    if unknown:
        raise ValueError(f'unknown parts {sorted(unknown)}; choose from {", ".join(PARTS)}')
    if prefer not in ('source', 'target'):
        raise ValueError(f"prefer must be 'source' or 'target', not {prefer!r}")
    if replace_imports is None:
        replace_imports = prefer == 'source'
    policy = 'second' if prefer == 'source' else 'first'
    from wc3files.w3o.merge import merge_objects
    files = dict(target)
    report = {'copied': [], 'kept': [], 'objects': {}, 'script': None, 'strings': {},
              'notes': []}
    copied = {}

    if 'objects' in parts:
        for name in OBJECT_FILES:
            ext = name.rsplit('.', 1)[-1].lower()
            theirs = _object_data(source, name)
            if theirs is None:
                continue
            mine = _object_data(files, name)
            if mine is None:
                data = find(source, name) and source[find(source, name)]
            else:
                merged, merge = merge_objects({ext: mine}, {ext: theirs},
                                              on_conflict=policy, on_edit_conflict=policy)
                report['objects'][name] = [object_id for _, object_id
                                           in merge['kept_' + policy]]
                data = _object_bytes(merged[ext], ext)
            copied[find(files, name) or name] = data

    for part, names in SINGLE_FILES.items():
        if part not in parts:
            continue
        for name in names:
            key = find(source, name)
            own = find(files, name)
            if key is not None and (own is None or prefer == 'source'):
                copied[own or name] = source[key]
            elif own is not None:
                report['kept'].append(own)

    if 'triggers' in parts:
        for name in TRIGGER_FILES:
            key = find(source, name)
            if key is not None:
                copied[find(files, name) or name] = source[key]
            elif find(files, name) is not None:
                report['kept'].append(find(files, name))

    if 'imports' in parts:
        imp_key, listing = read_imports(files)
        _, theirs = read_imports(source)
        entries = listing['files']
        listed = {member_name(e.get('path', '')).lower() for e in entries}
        flags = import_flags(entries)
        for name, data in source.items():
            if is_map_data(name):
                continue
            key = find(files, name)
            if key is not None and not replace_imports:
                continue
            copied[key or name] = data
        for entry in theirs['files']:
            path = member_name(entry.get('path', ''))
            if path.lower() not in listed and find(source, path) is not None:
                entries.append({'flags': entry.get('flags', flags), 'path': path})
                listed.add(path.lower())
        write_imports(files, imp_key, listing)
        report['copied'].append(imp_key)

    # Strings: number each source string the copied files use in the target.
    src_wts_key, src_wts = _wts(source)
    tgt_wts_key, tgt_wts = _wts(files)
    src_strings = src_wts.export_data()
    tgt_strings = dict(tgt_wts.export_data())
    source_script_key = _script_key(source)
    source_script = source[source_script_key] if source_script_key else None
    used = set()
    texts = list(copied.values())
    if 'triggers' in parts and source_script:
        kept = _kept_source(source_script.decode('utf-8-sig', errors='surrogateescape'),
                            source_script_key.lower().endswith('.lua'), 'sounds' in parts)
        texts.append(kept.encode('utf-8', errors='surrogateescape'))
    for data in texts:
        used.update(int(n) for n in _TRIGSTR.findall(data))
    table = {}
    next_number = max(list(tgt_strings) + list(src_strings) + [-1]) + 1
    for number in sorted(used):
        if number not in src_strings:
            continue
        text = src_strings[number]
        if number not in tgt_strings or tgt_strings[number] == text:
            tgt_strings[number] = text
            table[number] = number
        else:
            tgt_strings[next_number] = text
            table[number] = next_number
            next_number += 1
    report['strings'] = table
    changes = {old: new for old, new in table.items() if old != new}
    for name, data in list(copied.items()):
        if name.lower().endswith('.wct'):
            copied[name], _ = _rewrite_wct(data, changes)
        elif name.lower().rsplit('.', 1)[-1] in ('w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h',
                                                 'w3q', 'wtg', 'txt', 'w3s', 'fdf'):
            copied[name], _ = _rewrite_strings(data, changes)
    if table:
        tgt_wts.import_data(dict(sorted(tgt_strings.items())))
        files[tgt_wts_key] = tgt_wts.to_bytes()
        report['copied'].append(tgt_wts_key)

    if 'triggers' in parts:
        target_key = _script_key(files)
        if source_script_key is None or target_key is None:
            report['notes'].append('the script was not merged: '
                                   + ('the source' if source_script_key is None else
                                      'the target') + ' has none')
        else:
            src_lua = source_script_key.lower().endswith('.lua')
            tgt_lua = target_key.lower().endswith('.lua')
            source_text = _rewrite_strings(source_script, changes)[0].decode(
                'utf-8-sig', errors='surrogateescape')
            target_text = files[target_key].decode('utf-8-sig', errors='surrogateescape')
            translated = False
            if src_lua and not tgt_lua:
                raise ValueError('the source script is Lua and the target is JASS; '
                                 'a Lua script cannot be translated to JASS')
            if tgt_lua and not src_lua:
                from wc3files.scripts.jass_to_lua import jass_to_lua
                from wc3files.scripts.script_file import parse_jass
                source_text = jass_to_lua(parse_jass(source_text), prelude=True)
                translated = True
                report['notes'].append(
                    'the JASS script was translated to Lua; war3map.wct still holds JASS, '
                    'which the World Editor would write into a Lua script on its next save')
            text, script_report = merge_scripts(source_text, target_text, lua=tgt_lua,
                                                copy_sounds='sounds' in parts)
            script_report['translated'] = translated
            new = _issues(target_key, text) - (
                _issues(target_key, source_text) | _issues(target_key, target_text))
            if new:
                raise ValueError(f'the merged script has {len(new)} new issue(s): '
                                 + '; '.join(sorted(new)[:5]))
            copied[target_key] = text.encode('utf-8', errors='surrogateescape')
            report['script'] = script_report

    for name, data in copied.items():
        files[name] = data
        report['copied'].append(name)
    report['copied'] = sorted(set(report['copied']), key=str.lower)
    return files, report
