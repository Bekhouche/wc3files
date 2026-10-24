import argparse
import base64
import json
import os
import struct
import sys

from loguru import logger

from wc3files.w3x import W3xFile
from wc3files.w3o import ObjectFile, SlkFile, BaseObjectData, BaseObjectStrings, CombinedBaseData
from wc3files.w3o.object_metadata import ObjectMetaData
from wc3files.w3o.object_override import literal_table_paths
from wc3files.data import ObjectFileType, BinaryDataType
from wc3files.w3o import ObjectFile
from wc3files.img import (BlpFile, DdsFile, TgaFile, BmpFile, TiffFile, read_png, write_png,
                           resize_box, composite_over, make_disabled_variant, border_path)
from .formats import FORMATS

_IMG_EXTENSIONS = ('.blp', '.dds', '.tga', '.png', '.bmp', '.tiff', '.tif')

BYTES_TAG = '__bytes_b64__'


class _BytesEncoder(json.JSONEncoder):
    # Some formats (e.g. w3g's still-unparsed action-block stream) carry raw bytes in their
    # get_data() dict - JSON has no native binary type, so tag-and-base64 them rather than
    # failing the whole dump. _bytes_decode_hook reverses this on build.
    def default(self, obj):
        if isinstance(obj, bytes):
            return {BYTES_TAG: base64.b64encode(obj).decode('ascii')}
        return super().default(obj)


def _bytes_decode_hook(obj):
    if set(obj) == {BYTES_TAG}:
        return base64.b64decode(obj[BYTES_TAG])
    return obj


def _cmd_unpack(args):
    os.makedirs(args.out_dir, exist_ok=True)
    w3x = W3xFile()
    w3x.read(args.src)
    w3x.export(args.out_dir)
    print(f"Unpacked into {os.path.join(args.out_dir, w3x.current_name)}")


def _cmd_pack(args):
    w3x = W3xFile()
    w3x.build(args.src_dir, args.dst)
    print(f"Wrote {args.dst}")


def _open_casc(args):
    from wc3files.casc import CascStorage
    storage = CascStorage().open(args.install)
    return storage


def _cmd_casc_info(args):
    storage = _open_casc(args)
    print(f"install : {storage.install_path}")
    print(f"product : {storage.product}")
    print(f"version : {storage.version}")
    print(f"build   : {(storage.build_config.get('build-name') or ['?'])[0]}")
    print(f"files   : {len(storage)} root entries")
    print(f"encoding: {len(storage.encoding)} content keys")
    print(f"index   : {len(storage.index)} installed blobs")


def _cmd_casc_list(args):
    storage = _open_casc(args)
    shown = 0
    for entry in storage.files():
        if args.locale is not None and entry.locale != args.locale:
            continue
        print(f"{entry.path}|{entry.locale}" if entry.locale else entry.path)
        shown += 1
        if args.limit and shown >= args.limit:
            break


def _cmd_casc_search(args):
    storage = _open_casc(args)
    # Ask for one more than requested so a truncated result can say so
    # honestly, instead of reporting the limit as if it were the total.
    probe_limit = args.limit + 1 if args.limit else None
    hits = storage.search(args.pattern, locale=args.locale, limit=probe_limit)

    truncated = bool(args.limit) and len(hits) > args.limit
    if truncated:
        hits = hits[:args.limit]

    for entry in hits:
        if args.long:
            info = storage.info(entry)
            installed = 'yes' if info['installed'] else 'NOT INSTALLED'
            print(f"{entry.path}	{info['size']}	{entry.locale or '-'}	{installed}")
        else:
            print(entry.path)

    # The summary goes to stderr so `casc-search ... > list.txt` stays a clean
    # list of paths. Flush stdout first: stderr is unbuffered while a piped
    # stdout is block-buffered, so without this the summary surfaced *above*
    # the results under `2>&1`.
    sys.stdout.flush()
    if not hits:
        print(f"no match for {args.pattern!r}", file=sys.stderr)
    elif truncated:
        print(f"{len(hits)} shown (--limit {args.limit} reached; there are more)",
              file=sys.stderr)
    else:
        print(f"{len(hits)} match(es)", file=sys.stderr)


def _cmd_casc_extract(args):
    storage = _open_casc(args)
    matches = storage.search(args.pattern, locale=args.locale)
    if not matches:
        raise ValueError(f"nothing in this storage matches {args.pattern!r}")

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        written = 0
        for entry in matches:
            # Keep the game's own folder layout, with the ':' mod-archive
            # separators turned into real directories.
            relative = entry.path.replace(':', os.sep).replace('/', os.sep)
            if entry.locale:
                relative = os.path.join(entry.locale, relative)
            dest = os.path.join(args.out_dir, relative)
            size = storage.extract(entry, dest)
            written += 1
            if args.verbose:
                print(f"{entry.path} -> {dest} ({size} bytes)")
        print(f"Extracted {written} file(s) into {args.out_dir}")
    else:
        if len(matches) > 1:
            raise ValueError(
                f"{args.pattern!r} matches {len(matches)} files; give --out-dir "
                f"to extract them all, or narrow the pattern"
            )
        data = storage.read(matches[0])
        dest = args.out or os.path.basename(matches[0].name)
        with open(dest, 'wb') as handle:
            handle.write(data)
        print(f"Wrote {dest} ({len(data)} bytes)")


def _cmd_model_convert(args):
    """Convert a model between MDX (binary) and MDL (text).

    `ModelFile.write()` picks the format from the output path's extension, so
    the direction is whichever pair of extensions is given.
    """
    from wc3files.mdl import ModelFile

    model = ModelFile()
    model.read(args.input)
    source_format = model.get_file_type().value
    model.write(args.output)
    print(f'Wrote {args.output} ({source_format} -> '
          f'{model.get_file_type().value})')


def _cmd_model_info(args):
    """Summarise a model: version, sequences, textures, geometry counts."""
    from wc3files.mdl import ModelFile

    model = ModelFile()
    model.read(args.input)

    if args.json:
        _write_json(model.export_data(), args.out)
        return

    print(f'name       {model.name}')
    print(f'format     {model.get_file_type().value}')
    print(f'version    {model.version}')
    print(f'geosets    {len(model.geosets)}')
    sequences = model.sequences
    textures = model.textures
    print(f'sequences  {len(sequences)}')
    for sequence in sequences[:args.limit]:
        label = sequence.get('name') if isinstance(sequence, dict) else sequence
        print(f'  {label}')
    if len(sequences) > args.limit:
        print(f'  ... {len(sequences) - args.limit} more')
    print(f'textures   {len(textures)}')
    for texture in textures[:args.limit]:
        label = texture.get('image') if isinstance(texture, dict) else texture
        print(f'  {label}')
    if len(textures) > args.limit:
        print(f'  ... {len(textures) - args.limit} more')


def _cmd_jass(args):
    """Parse a script and print it back, optionally stripped and renamed.

    Reading and reprinting is the useful default - it reformats a script and
    proves it parses. `--obfuscate` additionally renames every identifier that
    is safe to rename, and `--strip` removes code nothing reachable calls.
    """
    from wc3files.scripts import ScriptFile
    from wc3files.scripts import (apply_rename, build_rename_table,
                                  find_reachable, print_jass, print_lua,
                                  obfuscate_lua, remove_dead_code)
    from wc3files.scripts.optimizer import DEFAULT_ENTRY_POINTS

    script = ScriptFile()
    script.read(args.input)
    program = script.get_data()
    is_lua = script.get_file_type().value == 'lua'

    if is_lua:
        if args.strip:
            raise ValueError('--strip works on JASS only; Lua has no reachability '
                             'pass in this package')
        if args.obfuscate:
            program = obfuscate_lua(program, extra_protected_names=tuple(args.keep or ()))
        text = print_lua(program)
    else:
        reachable = None
        if args.strip or args.obfuscate:
            entry_points = tuple(args.entry) if args.entry else DEFAULT_ENTRY_POINTS
            reachable = find_reachable(program, entry_points=entry_points)
            if not reachable['reachable_functions']:
                # Nothing is reachable, so --strip would delete the whole file
                # and --obfuscate would rename nothing. Say so instead of
                # quietly producing a no-op (or an empty script).
                raise ValueError(
                    f'nothing is reachable from {list(entry_points)} in '
                    f'{args.input}: --strip would empty the script and '
                    f'--obfuscate would rename nothing. A map script reaches '
                    f'everything from main/config; for a library or fragment, '
                    f'name its real entry points with --entry.')
        if args.strip:
            program = remove_dead_code(program, reachable)
            reachable = find_reachable(program)
        if args.obfuscate:
            table = build_rename_table(program, reachable,
                                       extra_protected_names=tuple(args.keep or ()))
            program = apply_rename(program, table)
            print(f'renamed {len(table)} identifiers', file=sys.stderr)
        text = print_jass(program)

    if args.output and args.output != '-':
        with open(args.output, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(f'Wrote {args.output}')
    else:
        sys.stdout.write(text)


def _cmd_trigger_text(args):
    """Render a war3map.wtg as the text the World Editor would show."""
    from wc3files.wtg import MapTriggersFile
    from wc3files.wtg.trigger_text import TriggerText, describe_trigger

    triggers_file = MapTriggersFile()
    triggers_file.read(args.input)
    triggers = triggers_file.get_triggers()

    text = TriggerText.default()
    if args.trigger_data:
        text = TriggerText()
        text.read_trigger_data(args.trigger_data)

    if args.name:
        wanted = args.name.lower()
        triggers = [t for t in triggers if wanted in (t.get('name') or '').lower()]
        if not triggers:
            raise ValueError(f'no trigger matching {args.name!r}')

    if args.json:
        _write_json([{'name': t.get('name'),
                      'lines': describe_trigger(t, text)} for t in triggers],
                    args.out)
        return

    print(f'{len(triggers)} triggers', file=sys.stderr)
    for trigger in triggers:
        for line in describe_trigger(trigger, text):
            print(line)
        print()


def _cmd_script_to_triggers(args):
    """Reconstruct GUI triggers from a map script (war3map.j).

    Partial by nature: a compiled script has lost the editor's own structure,
    so what comes back is what the script still carries. `coverage` reports
    how much of it was recognised rather than leaving that to guesswork.
    """
    from wc3files.wtg.from_script import reconstruct_from_jass

    # newline='' for the same reason ScriptFile.read uses it: bare CR runs in
    # real map scripts otherwise wreck every reported line number.
    with open(args.input, encoding='utf-8', errors='replace',
              newline='') as handle:
        source = handle.read()
    result = reconstruct_from_jass(source)

    if args.json:
        _write_json(result, args.out)
        return

    coverage = result.get('coverage') or {}
    print(f'triggers  {len(result.get("triggers") or [])}')
    print(f'variables {len(result.get("variables") or [])}')
    print(f'regions   {len(result.get("regions") or [])}')
    print(f'cameras   {len(result.get("cameras") or [])}')
    print(f'sounds    {len(result.get("sounds") or [])}')
    if coverage:
        print('coverage')
        for key, value in coverage.items():
            print(f'  {key}: {value}')
    for trigger in (result.get('triggers') or [])[:args.limit]:
        functions = trigger.get('functions') or []
        print(f'  {trigger.get("name")}  ({len(functions)} functions)')


def _cmd_ui_info(args):
    """Summarise a UI file: .fdf frame tree, .toc list, or a PopcornFX file."""
    import os

    extension = os.path.splitext(args.path)[1].lower()

    if extension == '.toc':
        from wc3files.fdf import TocFile
        toc = TocFile().read(args.path)
        if args.json:
            _write_json(toc.get_data(), args.out)
            return
        print(f'{len(toc.entries)} frame definition files listed')
        for entry in toc.entries:
            print(f'  {entry}')
        return

    if extension == '.fdf':
        from wc3files.fdf import FdfFile
        fdf = FdfFile().read(args.path)
        if args.json:
            _write_json(fdf.get_data(), args.out)
            return
        for path in fdf.includes():
            print(f'include  {path}')
        strings = fdf.strings()
        if strings:
            print(f'{len(strings)} strings')
            for key, text in list(strings.items())[:args.limit]:
                print(f'  {key:<34} {text!r}')
            if len(strings) > args.limit:
                print(f'  ... {len(strings) - args.limit} more')

        def show(items, depth):
            for item in items:
                if item['kind'] != 'block':
                    continue
                label = item['name'] or f'<{item["keyword"].lower()}>'
                inherits = f'  inherits {item["inherits"]}' if item['inherits'] else ''
                print(f'{"  " * depth}{item["type"]} {label}{inherits}')
                show(item['items'], depth + 1)

        blocks = fdf.blocks()
        if blocks:
            print(f'{len(blocks)} blocks')
            show(fdf.items, 1)
        return

    if extension == '.pkcf':
        from wc3files.popcorn import PkcfFile
        _dump_or_print(PkcfFile().read(args.path).get_data(), args)
        return
    if extension == '.pkmm':
        from wc3files.popcorn import PkmmFile
        _dump_or_print(PkmmFile().read(args.path).get_data(), args)
        return
    if extension == '.pkb':
        from wc3files.popcorn import PkbFile
        data = PkbFile().read(args.path).get_data()
        if not args.json:
            data['type_names'] = data['type_names'][:args.limit]
        _dump_or_print(data, args)
        return
    if extension == '.wai':
        from wc3files.wai import WaiFile
        _dump_or_print(WaiFile().read(args.path).get_data(), args)
        return

    raise ValueError(f'ui-info does not handle {extension!r} files; it reads '
                     f'.fdf, .toc, .pkcf, .pkmm, .pkb and .wai')


def _dump_or_print(data, args):
    if args.json:
        _write_json(data, args.out)
        return
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            print(f'{key}:')
            for entry in value:
                print(f'  {entry}')
        elif isinstance(value, dict):
            print(f'{key}:')
            for name, entry in value.items():
                print(f'  {name} = {entry}')
        else:
            print(f'{key}: {value}')


def _cmd_replay_mmd(args):
    """Dump the W3MMD (map meta data) a replay carries."""
    from wc3files import ReplayFile
    from wc3files.w3g import mmd as _mmd

    replay = ReplayFile()
    replay.read(args.replay)
    data = _mmd.from_replay(replay.get_data())

    if not data.messages:
        print('no W3MMD data in this replay', file=sys.stderr)
        return

    if args.json:
        payload = {
            'version': data.version,
            'players': data.players,
            'definitions': data.definitions,
            'variables': data.variables,
            'flags': {pid: sorted(flags) for pid, flags in data.flags.items()},
            'events': data.events,
            'winners': data.winners(),
            'losers': data.losers(),
            'missing_message_ids': data.missing_message_ids(),
            'conflicts': data.conflicts,
        }
        _write_json(payload, args.out)
        return

    print(f'W3MMD version   : {data.version}')
    print(f'messages        : {len(data.messages)}')
    missing = data.missing_message_ids()
    if missing:
        # The standard expects ids 0..n with no gaps; a gap means those
        # messages never made it into the replay.
        print(f'missing ids     : {len(missing)} (never recorded)')
    if data.conflicts:
        print(f'CONFLICTING ids : {sorted(data.conflicts)} - a client sent differing messages')

    print()
    print('players:')
    for pid in sorted(data.players):
        flags = sorted(data.flags.get(pid, ()))
        suffix = ('  [' + ', '.join(flags) + ']') if flags else ''
        print(f'  {pid:>3}  {data.players[pid]}{suffix}')

    if data.variables:
        print()
        print('variables:')
        for pid in sorted(data.variables):
            name = data.players.get(pid, pid)
            print(f'  {name}:')
            for key in sorted(data.variables[pid]):
                print(f'      {key} = {data.variables[pid][key]}')

    if data.events:
        print()
        print(f'events ({len(data.events)}):')
        for event in data.events[:args.max_events]:
            print('  ' + data.describe_event(event))
        if len(data.events) > args.max_events:
            print(f'  ... {len(data.events) - args.max_events} more')


def _cmd_replay_actions(args):
    """Summarize the player actions in a replay."""
    import collections

    from wc3files import ReplayFile
    from wc3files.w3g import actions as _actions

    replay = ReplayFile()
    replay.read(args.replay)
    data = replay.get_data()
    compressed = data['compressed_data']
    blocks = compressed.get('blocks')
    if not blocks:
        print("this replay's block stream did not parse, so it has no readable "
              "actions", file=sys.stderr)
        return

    threshold = _actions.detect_shift_threshold(blocks)
    # Player ids in the action stream are 1-based and line up with the order
    # players appear in the preamble.
    names = {index + 1: player['name']
             for index, player in enumerate(compressed.get('Players') or [])}

    totals = collections.Counter()
    apm_totals = collections.Counter()
    per_id = collections.Counter()
    for player_id, action in _actions.iter_actions(blocks, shift_threshold=threshold):
        totals[player_id] += 1
        per_id[action['id']] += 1
        if _actions.is_apm_action(action):
            apm_totals[player_id] += 1

    minutes = (data['header']['sub'].get('replay_length') or 0) / 60000.0

    print(f"actions      : {sum(totals.values())}")
    print(f"length       : {minutes:.1f} min")
    print(f"id mapping   : {'shifted at 0x%02X' % threshold if threshold else 'classic'}")
    print()
    header = f"{'player':24} {'actions':>9} {'apm-actions':>12}"
    if minutes:
        header += f" {'APM':>7}"
    print(header)
    for player_id in sorted(totals, key=lambda p: -apm_totals.get(p, 0)):
        label = str(names.get(player_id, f'id {player_id}'))
        row = f"{label:24} {totals[player_id]:9} {apm_totals.get(player_id, 0):12}"
        if minutes:
            row += f" {apm_totals.get(player_id, 0) / minutes:7.1f}"
        print(row)

    if args.by_action:
        print()
        print('actions by type:')
        for action_id, count in per_id.most_common(args.by_action):
            print(f'  0x{action_id:02X}: {count}')


def _cmd_casc_export_gamedata(args):
    """Pull the data tables this package bundles out of an installed game.

    The bundled copies came from a real install at some point but nothing
    records which version, and they go stale as Blizzard patches the game.
    This writes the current ones so they can be refreshed deliberately.
    """
    import hashlib

    from wc3files.casc import CascStorage

    storage = CascStorage().open(args.install)
    print(f'game version: {storage.version}')

    # Walk subdirectories too: the per-race *Strings.txt files that carry
    # every object's display name live in a `strings/` subfolder, and a
    # top-level-only scan silently left them out.
    wanted = {}
    for directory in args.bundled_dir or _default_gamedata_dirs():
        if not os.path.isdir(directory):
            continue
        for root, _dirs, filenames in os.walk(directory):
            for name in sorted(filenames):
                wanted.setdefault(name.lower(), os.path.join(root, name))

    if not wanted:
        raise ValueError('no bundled gamedata directories found to compare against')

    # Prefer the game's plain base copy over _HD/_Balance overrides; the
    # strings files only exist per locale.
    base, localized = {}, {}
    for entry in storage.files():
        key = os.path.basename(entry.name).lower()
        if key not in wanted:
            continue
        if entry.locale or 'Locales' in entry.path:
            if args.locale and f'{args.locale}.w3mod'.lower() in entry.path.lower():
                localized.setdefault(key, entry)
        elif entry.path.count(':') == 1:
            base.setdefault(key, entry)

    os.makedirs(args.out_dir, exist_ok=True)
    same = changed = absent = 0
    for key, bundled_path in sorted(wanted.items()):
        entry = base.get(key) or localized.get(key)
        if entry is None:
            absent += 1
            if args.verbose:
                print(f'  {key}: not in this install')
            continue
        data = storage.read(entry)
        with open(bundled_path, 'rb') as handle:
            current = handle.read()
        identical = hashlib.md5(current).digest() == hashlib.md5(data).digest()
        same, changed = (same + 1, changed) if identical else (same, changed + 1)

        # Mirror the layout under the bundled directory so a `strings/` file
        # does not collide with a top-level one of the same name.
        relative = os.path.basename(bundled_path)
        for directory in args.bundled_dir or _default_gamedata_dirs():
            if os.path.isdir(directory) and bundled_path.startswith(directory):
                relative = os.path.relpath(bundled_path, directory)
                break
        dest = os.path.join(args.out_dir, relative)
        os.makedirs(os.path.dirname(dest) or args.out_dir, exist_ok=True)
        with open(dest, 'wb') as handle:
            handle.write(data)
        status = 'identical' if identical else f'DIFFERS ({len(current)} -> {len(data)})'
        print(f'  {relative:40} {status}')

    print()
    print(f'wrote {same + changed} file(s) to {args.out_dir}')
    print(f'identical: {same}   differ: {changed}   not in this install: {absent}')
    if changed:
        print('Nothing was overwritten in the package - copy them over deliberately.')


def _default_gamedata_dirs():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(root, 'w3o', 'gamedata'),
            os.path.join(root, 'scripts', 'gamedata')]


def _read_json(path):
    if path == '-':
        return json.load(sys.stdin, object_hook=_bytes_decode_hook)
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle, object_hook=_bytes_decode_hook)


def _write_json(data, path):
    text = json.dumps(data, indent=2, ensure_ascii=False, cls=_BytesEncoder)
    if path == '-':
        sys.stdout.write(text + '\n')
    else:
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)


def _cmd_obj_to_slk(args):
    obj = ObjectFile()
    obj.read(args.input)
    if args.validate:
        reports = obj.validate()
        if reports:
            print(f"{len(reports)} field(s) don't match the bundled Blizzard metadata schema "
                  f"(see TODO.md's validator entry for exact scope - 'note' = usually "
                  f"intentional, e.g. a value below a declared minVal of 0; 'issue' = "
                  f"more likely a real problem, e.g. an unrecognized enum value):", file=sys.stderr)
            for r in reports:
                level = f" (level {r['level']})" if r['level'] is not None else ""
                for issue in r['issues']:
                    print(f"  [{issue['severity']}] {r['table']}/{r['id']}/{r['field']}{level}: "
                          f"{r['value']!r} - {issue['message']}", file=sys.stderr)
        else:
            print("No metadata validation issues found.", file=sys.stderr)
    base_data = CombinedBaseData(BaseObjectData.default(), BaseObjectStrings.default()) \
        if args.with_base_data else None
    tables = obj.to_slk(base_data=base_data)
    id_column = args.id_column or 'id'
    written = []
    for table_name in ('original', 'custom'):
        rows = tables[table_name]
        if not rows:
            continue
        slk = SlkFile()
        slk.import_data(rows, id_column=id_column)
        out_path = f'{args.output_prefix}.{table_name}.slk'
        slk.write(out_path, id_column=id_column)
        written.append(out_path)
    if not written:
        print("No object data found - nothing to write.", file=sys.stderr)
        return
    for path in written:
        print(f"Wrote {path}")


def _cmd_obj_to_override(args):
    obj = ObjectFile()
    obj.read(args.input)
    base_data = BaseObjectData.default()
    tables, remainder = obj.to_override_tables(base_data=base_data)

    for rel_path, rows in tables.items():
        out_path = os.path.join(args.output_dir, rel_path)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        slk = SlkFile()
        slk.import_data(rows, id_column='id')
        slk.write(out_path, id_column='id')
        print(f"Wrote {out_path} ({len(rows)} rows)")

    remainder_count = len(remainder['original']) + len(remainder['custom'])
    if remainder_count == 0:
        print("No binary object file needed - every override moved to a literal SLK table.")
        return

    object_type = obj.get_object_type()
    metadata = ObjectMetaData.default()
    fields = metadata.fields(object_type)
    stuck_field_names = set()
    for table_name in ('original', 'custom'):
        for mods in remainder[table_name].values():
            for mod_id in mods:
                if mod_id == 'ID':
                    continue
                field_def = fields.get(mod_id)
                stuck_field_names.add(field_def['field'] if field_def else mod_id)

    remainder_obj = ObjectFile()
    remainder_obj.set_object_type(object_type)
    remainder_obj.import_data({object_type.value: remainder})
    remainder_path = os.path.join(args.output_dir, f'war3map.{object_type.value}')
    remainder_obj.write(remainder_path)
    print(f"Wrote {remainder_path} ({remainder_count} object(s) still need this - "
          f"see TODO.md's override-table entry for why): {sorted(stuck_field_names)}")


def _cmd_override_to_obj(args):
    object_type = ObjectFileType(args.category)
    tables = {}
    for rel_path in literal_table_paths(object_type):
        table_path = os.path.join(args.input_dir, rel_path)
        if not os.path.isfile(table_path):
            continue
        slk = SlkFile()
        slk.read(table_path)
        tables[rel_path] = slk.export_data()
        print(f"Read {table_path} ({len(tables[rel_path])} rows)")

    remainder = {'original': {}, 'custom': {}}
    remainder_path = args.remainder or os.path.join(args.input_dir, f'war3map.{object_type.value}')
    if os.path.isfile(remainder_path):
        remainder_obj = ObjectFile()
        remainder_obj.read(remainder_path)
        remainder = remainder_obj.export_data()[object_type.value]
        print(f"Read {remainder_path} ({len(remainder['original']) + len(remainder['custom'])} object(s))")

    if not tables and not remainder['original'] and not remainder['custom']:
        print("Nothing found to rebuild from - no literal-path SLK tables and no remainder "
              "binary file.", file=sys.stderr)
        return 1

    rebuilt, report = ObjectFile.from_override_tables(object_type, tables, remainder)
    rebuilt.write(args.output)
    print(f"Wrote {args.output}")

    for key, label in [('ambiguous_fields', 'field(s) resolved with possible ambiguity'),
                        ('guessed_templates', 'custom object(s) with a best-effort guessed template'),
                        ('uncertain_binary_types', 'field(s) with an unconfirmed binary storage type'),
                        ('unrecognized_columns', 'column(s) with no known field mapping (dropped)'),
                        ('unparseable_values', 'cell(s) with an unparseable numeric value (dropped)')]:
        if report[key]:
            print(f"  {len(report[key])} {label} - see TODO.md's override-table entry for what this means",
                  file=sys.stderr)


def _read_any_image(path):
    """Read a .blp/.dds/.tga/.png/.bmp/.tiff file (format auto-detected: magic bytes
    for blp/dds/png/bmp/tiff, extension for tga, which has no reliable magic) and
    return (width, height, rgba_bytes)."""
    with open(path, 'rb') as handle:
        magic = handle.read(8)
    if magic[:4] == b'DDS ':
        img = DdsFile()
        img.read(path)
        return img.width, img.height, img.get_rgba(0)
    if magic[:4] in (b'BLP0', b'BLP1', b'BLP2'):
        img = BlpFile()
        img.read(path)
        return img.width, img.height, img.get_rgba(0)
    if magic[:8] == b'\x89PNG\r\n\x1a\n':
        return read_png(path)
    if magic[:2] == b'BM':
        img = BmpFile()
        img.read(path)
        return img.width, img.height, img.get_rgba()
    if magic[:2] in (b'II', b'MM'):
        img = TiffFile()
        img.read(path)
        return img.width, img.height, img.get_rgba()
    if path.lower().endswith('.tga'):
        img = TgaFile()
        img.read(path)
        return img.width, img.height, img.get_rgba()
    raise ValueError(f"'{path}' is not a recognized .blp/.dds/.tga/.png/.bmp/.tiff file")


def _write_any_image(path, width, height, rgba, fourcc=b'DXT5', mipmaps=True, alpha='auto'):
    """Write (width, height, rgba_bytes) to a .blp/.dds/.tga/.png/.bmp/.tiff file,
    format chosen by the output path's extension."""
    lower = path.lower()
    if alpha in ('yes', 'no'):
        alpha = alpha == 'yes'
    if lower.endswith('.blp'):
        BlpFile.from_rgba(width, height, rgba, alpha=alpha).write(path)
    elif lower.endswith('.dds'):
        DdsFile.from_rgba(width, height, rgba, fourcc=fourcc, mipmaps=mipmaps).write(path)
    elif lower.endswith('.tga'):
        TgaFile.from_rgba(width, height, rgba).write(path)
    elif lower.endswith('.png'):
        with open(path, 'wb') as handle:
            handle.write(write_png(width, height, rgba))
    elif lower.endswith('.bmp'):
        BmpFile.from_rgba(width, height, rgba).write(path)
    elif lower.endswith(('.tiff', '.tif')):
        TiffFile.from_rgba(width, height, rgba).write(path)
    else:
        raise ValueError(f"'{path}': unrecognized output extension (expected "
                          ".blp/.dds/.tga/.png/.bmp/.tiff)")


def _cmd_img_to_png(args):
    w, h, rgba = _read_any_image(args.input)
    with open(args.output, 'wb') as handle:
        handle.write(write_png(w, h, rgba))
    print(f"Wrote {args.output}")


def _cmd_img_convert(args):
    w, h, rgba = _read_any_image(args.input)
    if args.resize:
        try:
            new_w, new_h = (int(v) for v in args.resize.lower().split('x'))
        except ValueError:
            print(f"error: --resize expects WIDTHxHEIGHT (e.g. 64x64), got {args.resize!r}",
                  file=sys.stderr)
            return 1
        rgba = resize_box(rgba, w, h, new_w, new_h)
        w, h = new_w, new_h
    fourcc = args.fourcc.encode('ascii') if args.fourcc else b'DXT5'
    _write_any_image(args.output, w, h, rgba, fourcc=fourcc, mipmaps=args.mipmaps, alpha=args.alpha)
    print(f"Wrote {args.output} ({w}x{h})")


def _cmd_make_icon(args):
    w, h, rgba = _read_any_image(args.source)
    size = args.size
    rgba = resize_box(rgba, w, h, size, size) if (w, h) != (size, size) else rgba

    if args.disabled_output:
        disabled = make_disabled_variant(rgba, size, size, darken=args.darken,
                                          desaturate=args.desaturate)
        _write_any_image(args.disabled_output, size, size, disabled, alpha=args.alpha)
        print(f"Wrote {args.disabled_output} ({size}x{size}, disabled variant)")

    border = args.border or (border_path('active', reforged=(args.bundled_border == 'reforged'))
                              if args.bundled_border else None)
    if border:
        bw, bh, border_rgba = _read_any_image(border)
        rgba = composite_over(rgba, size, size, border_rgba, bw, bh)

    _write_any_image(args.output, size, size, rgba, alpha=args.alpha)
    print(f"Wrote {args.output} ({size}x{size})")


def _cmd_img_convert_batch(args):
    to_ext = '.' + args.to.lower().lstrip('.')
    fourcc = args.fourcc.encode('ascii') if args.fourcc else b'DXT5'
    converted, skipped, failed = 0, 0, []
    for root, _dirs, filenames in os.walk(args.input_dir):
        rel_root = os.path.relpath(root, args.input_dir)
        for name in filenames:
            if not name.lower().endswith(_IMG_EXTENSIONS):
                continue
            in_path = os.path.join(root, name)
            out_root = os.path.join(args.output_dir, rel_root) if rel_root != '.' else args.output_dir
            out_path = os.path.join(out_root, os.path.splitext(name)[0] + to_ext)
            if os.path.abspath(in_path) == os.path.abspath(out_path):
                skipped += 1
                continue
            try:
                w, h, rgba = _read_any_image(in_path)
                if args.resize:
                    new_w, new_h = (int(v) for v in args.resize.lower().split('x'))
                    rgba = resize_box(rgba, w, h, new_w, new_h)
                    w, h = new_w, new_h
                os.makedirs(out_root, exist_ok=True)
                _write_any_image(out_path, w, h, rgba, fourcc=fourcc, mipmaps=args.mipmaps)
                converted += 1
            except Exception as exc:  # noqa: BLE001 - report and keep going, this is a batch job
                failed.append((in_path, str(exc)))
    print(f"Converted {converted}, skipped {skipped} (same as source), failed {len(failed)}")
    for path, msg in failed:
        print(f"  FAILED {path}: {msg}", file=sys.stderr)
    return 1 if failed and not converted else 0


# Confirmed against this repo's own real fixture set (>2700 .blp files across many
# real maps), not assumed from a tutorial: each icon kind has its own prefix AND its
# own disabled-icon prefix (PAS/DISPAS, ATC/DISATC - NOT a single shared DISBTN
# reused across kinds, which is what this command originally, incorrectly, did).
# Directory placement also differs per kind: PAS lives in its own
# ReplaceableTextures/PassiveButtons/ (not CommandButtons); ATC and UPG share
# CommandButtons/ with BTN; ATT sits loose directly under ReplaceableTextures/ with
# no subdirectory at all. UPG and ATT have no disabled counterpart anywhere in the
# real fixture set - confirmed by an explicit search, not just absence of evidence.
_ICON_KINDS = {
    'active':   {'prefix': 'BTN', 'dir': 'CommandButtons', 'disabled_prefix': 'DISBTN'},
    'passive':  {'prefix': 'PAS', 'dir': 'PassiveButtons', 'disabled_prefix': 'DISPAS'},
    'autocast': {'prefix': 'ATC', 'dir': 'CommandButtons', 'disabled_prefix': 'DISATC'},
    'upgrade':  {'prefix': 'UPG', 'dir': 'CommandButtons', 'disabled_prefix': None},
    'attack':   {'prefix': 'ATT', 'dir': None,             'disabled_prefix': None},
}
_DISABLED_DIR = 'CommandButtonsDisabled'  # shared by every kind that has a disabled variant


def _cmd_make_icon_set(args):
    w, h, rgba = _read_any_image(args.source)
    size = args.size
    base = resize_box(rgba, w, h, size, size) if (w, h) != (size, size) else rgba
    ext = '.' + args.format.lstrip('.')

    kinds = [k.strip() for k in args.kinds.split(',') if k.strip()]
    unknown = [k for k in kinds if k not in _ICON_KINDS]
    if unknown:
        print(f"error: unknown icon kind(s) {unknown} - choose from: {sorted(_ICON_KINDS)}",
              file=sys.stderr)
        return 1

    borders = {'active': args.active_border, 'passive': args.passive_border,
               'autocast': args.autocast_border, 'upgrade': args.upgrade_border,
               'attack': args.attack_border}
    disabled = make_disabled_variant(base, size, size, darken=args.darken, desaturate=args.desaturate)

    written = []
    for kind in kinds:
        spec = _ICON_KINDS[kind]
        icon = base
        border = borders.get(kind)
        if not border and args.bundled_borders:
            border = border_path(kind, reforged=(args.bundled_borders == 'reforged'))
        if border:
            bw, bh, border_rgba = _read_any_image(border)
            icon = composite_over(base, size, size, border_rgba, bw, bh)

        rt_dir = os.path.join(args.output_dir, 'ReplaceableTextures')
        out_dir = os.path.join(rt_dir, spec['dir']) if spec['dir'] else rt_dir
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{spec['prefix']}{args.name}{ext}")
        _write_any_image(out_path, size, size, icon, alpha=args.alpha)
        written.append(out_path)

        if spec['disabled_prefix']:
            dis_dir = os.path.join(rt_dir, _DISABLED_DIR)
            os.makedirs(dis_dir, exist_ok=True)
            dis_path = os.path.join(dis_dir, f"{spec['disabled_prefix']}{args.name}{ext}")
            _write_any_image(dis_path, size, size, disabled, alpha=args.alpha)
            written.append(dis_path)

    for path in written:
        print(f"Wrote {path}")


_LEVEL_INDEXED_TYPES = (ObjectFileType.Doodads, ObjectFileType.Abilities, ObjectFileType.Upgrades)


def _cmd_set_object_icon(args):
    obj = ObjectFile()
    obj.read(args.object_file)
    object_type = obj.get_object_type()
    if object_type in _LEVEL_INDEXED_TYPES:
        print(f"error: {object_type.value} objects store per-level modification data - "
              "this simple field-setter only handles flat (non-level-indexed) fields "
              "(Units/Items/Destructables/Buffs), not this category", file=sys.stderr)
        return 1
    data = obj.export_data()[object_type.value]

    target = None
    for table_name in ('original', 'custom'):
        if args.object_id in data[table_name]:
            target = data[table_name][args.object_id]
            break
    if target is None:
        print(f"error: object id '{args.object_id}' not found in either the original or "
              f"custom table of {args.object_file}", file=sys.stderr)
        return 1

    target[args.field] = {'value': args.icon_path, 'type': BinaryDataType.STRING.value}
    obj.write(args.object_file)
    print(f"Set {args.object_id}.{args.field} = {args.icon_path!r} in {args.object_file}")


def _cmd_dump(args):
    fmt = FORMATS[args.format]
    data = fmt.dump(args.input, options={'width': getattr(args, 'width', None),
                                         'height': getattr(args, 'height', None)})
    _write_json(data, args.output)
    if args.output != '-':
        print(f"Wrote {args.output}")


def _cmd_build(args):
    fmt = FORMATS[args.format]
    data = _read_json(args.input)
    fmt.build(data, args.output, validate=args.validate)
    print(f"Wrote {args.output}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wc3files',
        description='Read/write Warcraft III map, campaign, and replay files.',
    )
    parser.add_argument('-v', '--verbose', action='store_true',
                         help='Show this package\'s own debug/info logging (quiet by default).')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_unpack = subparsers.add_parser('unpack', help='Extract a .w3x/.w3m/.w3n archive to loose files.')
    p_unpack.add_argument('src', help='Path to the .w3x/.w3m/.w3n archive.')
    p_unpack.add_argument('out_dir', help='Directory to extract into (files land under out_dir/<map name>/).')
    p_unpack.set_defaults(func=_cmd_unpack)

    p_pack = subparsers.add_parser('pack', help='Repack loose files (as produced by unpack) into an archive.',
        description="Repack loose files into an archive. Note: since this round-trips through "
        "the OS filesystem, an archive whose member paths differ only by case (a real thing "
        "some protected/edited maps have, e.g. both 'war3map.w3i' and 'War3map.w3i') loses the "
        "duplicate on unpack on a case-insensitive filesystem (Windows/default macOS) - only "
        "one survives. wc3files.w3x.W3xFile.build_from_files() operates on files in memory and "
        "doesn't have this limitation, if that matters for your use case.")
    p_pack.add_argument('src_dir', help="Directory of loose files, e.g. out_dir/<map name>/ from unpack.")
    p_pack.add_argument('dst', help='Path to write the .w3x/.w3m/.w3n archive to.')
    p_pack.set_defaults(func=_cmd_pack)

    p_obj_slk = subparsers.add_parser('obj-to-slk', help=(
        "Flatten an object file (war3map.w3u/w3t/w3a/w3h/w3q/w3b/w3d) into readable SLK "
        "table(s) - what the map actually customizes, not Blizzard's full default stat "
        "tables (wc3files doesn't bundle those). Recognized fields get their real name from "
        "the bundled Blizzard *MetaData.slk schema; unrecognized ones keep their raw 4-char "
        "rawcode as the column name rather than being dropped."))
    p_obj_slk.add_argument('input', help='Path to the object file to read.')
    p_obj_slk.add_argument('output_prefix', help=(
        "Prefix for the output file(s): writes '<prefix>.original.slk' and/or "
        "'<prefix>.custom.slk', skipping either if that table is empty."))
    p_obj_slk.add_argument('--id-column', default=None,
                            help="Row-key column name in the output SLK (default: 'id').")
    p_obj_slk.add_argument('--with-base-data', action='store_true', help=(
        "Merge each row onto its real base-game object's (or, for custom objects, its "
        "template's) full default stats AND default display strings (Name/Tip/Ubertip/"
        "Hotkey/...) from the bundled Blizzard base data tables, instead of showing only "
        "the map's own diff. Off by default since it noticeably widens every row - see "
        "TODO.md's base-data-merge and base-strings entries for the known caveats."))
    p_obj_slk.add_argument('--validate', action='store_true', help=(
        "Report (to stderr, non-blocking) fields whose value violates the bundled Blizzard "
        "metadata's declared range/emptiness constraints. Only checks numeric range/sign and "
        "emptiness for reliably-typed fields - see TODO.md's validator entry for exact scope "
        "and known false-positive sources (e.g. values intentionally pushed past the World "
        "Editor UI's own slider limits)."))
    p_obj_slk.set_defaults(func=_cmd_obj_to_slk)

    p_obj_override = subparsers.add_parser('obj-to-override', help=(
        "Split an object file into literal, game-loadable default-table overrides "
        "(Units\\UnitData.slk, Units\\UnitBalance.slk, ...) plus whatever genuinely can't "
        "move there (icons/models/tooltips and a few other World-Editor-UI-only fields - "
        "never silently dropped). This is the real technique some maps use to end up with "
        "no war3map.w3u/w3t/w3h/w3q at all - see TODO.md's override-table entry."))
    p_obj_override.add_argument('input', help='Path to the object file to read.')
    p_obj_override.add_argument('output_dir', help=(
        "Directory to write into - tables land at their literal in-game relative path "
        "(e.g. <output_dir>/Units/UnitData.slk), and if anything couldn't move, a smaller "
        "war3map.<ext> is written alongside them."))
    p_obj_override.set_defaults(func=_cmd_obj_to_override)

    p_override_to_obj = subparsers.add_parser('override-to-obj', help=(
        "Reverse of obj-to-override: read a directory of literal-path override tables "
        "(Units\\UnitData.slk, ...) plus an optional remainder war3map.<ext>, and rebuild a "
        "normal object file - i.e. undo the technique so a map converted this way can be "
        "inspected/edited normally again. See TODO.md's override-table entry for exactly "
        "what is and isn't recoverable with certainty (ambiguous shared field names, "
        "custom objects with no remaining template hint, unconfirmed binary storage types)."))
    p_override_to_obj.add_argument('category', choices=[t.value for t in ObjectFileType if t != ObjectFileType.File],
                                    help='Object category to rebuild (matches the output file extension).')
    p_override_to_obj.add_argument('input_dir', help=(
        "Directory containing the literal-path override tables (e.g. <input_dir>/Units/"
        "UnitData.slk) and, if present, the remainder war3map.<category> alongside them."))
    p_override_to_obj.add_argument('output', help='Path to write the rebuilt object file to.')
    p_override_to_obj.add_argument('--remainder', default=None, help=(
        "Explicit path to the remainder binary object file, if it isn't at the default "
        "<input_dir>/war3map.<category> location."))
    p_override_to_obj.set_defaults(func=_cmd_override_to_obj)

    p_img_png = subparsers.add_parser('img-to-png', help=(
        "Decode a .blp (BLP1, Warcraft III's native icon/texture format) or .dds "
        "(DXT1/DXT3/DXT5) file to a .png - format is auto-detected from the file's own "
        "magic bytes, not its extension. Always decodes mip level 0 (full resolution)."))
    p_img_png.add_argument('input', help='Path to the .blp or .dds file to read.')
    p_img_png.add_argument('output', help='Path to write the decoded .png to.')
    p_img_png.set_defaults(func=_cmd_img_to_png)

    p_img_convert = subparsers.add_parser('img-convert', help=(
        "Convert between .blp/.dds/.tga/.png (input format auto-detected, output format "
        "chosen by the output path's extension). There is no BLP1 JPEG or DDS "
        "uncompressed-RGB *encoder* in this package (only decoders) - writing a .blp "
        "always uses BLP1's paletted sub-variant (median-cut quantized if the image has "
        "more than 256 distinct colors), and writing a .dds always DXT1/DXT5-compresses."))
    p_img_convert.add_argument('input', help='Path to the .blp/.dds/.tga/.png file to read.')
    p_img_convert.add_argument('output', help='Path to write - format chosen by extension.')
    p_img_convert.add_argument('--resize', metavar='WIDTHxHEIGHT', default=None,
                                help='Resize (box-filter) before writing, e.g. --resize 64x64.')
    p_img_convert.add_argument('--fourcc', choices=['DXT1', 'DXT3', 'DXT5'], default='DXT5',
                                help='.dds output only: block-compression format (default: DXT5).')
    p_img_convert.add_argument('--mipmaps', dest='mipmaps', action='store_true', default=True,
                                help='.dds output only: generate a full mip chain (default: on).')
    p_img_convert.add_argument('--no-mipmaps', dest='mipmaps', action='store_false',
                                help='.dds output only: write a single mip level.')
    p_img_convert.add_argument('--alpha', choices=['auto', 'yes', 'no'], default='auto',
                                help=".blp output only: include an 8-bit alpha plane - "
                                     "'auto' (default) includes one only if the image has "
                                     "any non-opaque pixel.")
    p_img_convert.set_defaults(func=_cmd_img_convert)

    p_make_icon = subparsers.add_parser('make-icon', help=(
        "The 'make a WC3 icon' workflow from the classic and Reforged icon tutorials: "
        "resize source art to a square icon, optionally composite a border/frame image "
        "on top, and optionally generate the matching darkened/desaturated 'disabled' "
        "(DISBTN) variant - which is always generated from the un-bordered resized "
        "image, matching the real DISBTN convention (no border, darker). Bundled real "
        "border art is available via --bundled-border (not a universal ground truth - "
        "see wc3files.img.borders' own docstring) - pass your own via --border instead "
        "for a specific map/mod's own border convention."))
    p_make_icon.add_argument('source', help='Path to the source image (.blp/.dds/.tga/.png).')
    p_make_icon.add_argument('output', help='Path to write the icon to (.blp/.dds/.tga/.png).')
    p_make_icon.add_argument('--size', type=int, default=64,
                              help='Square icon size in pixels (default: 64).')
    p_make_icon.add_argument('--border', default=None,
                              help='Optional border/frame image to composite on top '
                                   '(resized to --size first if needed). Takes '
                                   'precedence over --bundled-border.')
    p_make_icon.add_argument('--bundled-border', choices=['classic', 'reforged'], default=None,
                              help="Use this package's own bundled active-icon border art "
                                   "instead of supplying --border explicitly (classic=64x64, "
                                   "reforged=256x256, resized to --size first).")
    p_make_icon.add_argument('--disabled-output', default=None,
                              help='Optional path to also write the disabled (DISBTN) variant to.')
    p_make_icon.add_argument('--darken', type=float, default=0.4,
                              help='Disabled-variant darken amount, 0..1 (default: 0.4).')
    p_make_icon.add_argument('--desaturate', type=float, default=0.7,
                              help='Disabled-variant desaturate amount, 0..1 (default: 0.7).')
    p_make_icon.add_argument('--alpha', choices=['auto', 'yes', 'no'], default='auto',
                              help='.blp output only: include an 8-bit alpha plane (default: auto).')
    p_make_icon.set_defaults(func=_cmd_make_icon)

    p_batch = subparsers.add_parser('img-convert-batch', help=(
        "Batch-convert every .blp/.dds/.tga/.png/.bmp/.tiff file found (recursively) "
        "under a directory to one target format, mirroring the input's directory "
        "structure under the output directory - the 'Warcraft Image Lab'-style batch "
        "conversion workflow."))
    p_batch.add_argument('input_dir', help='Directory to scan recursively for image files.')
    p_batch.add_argument('output_dir', help='Directory to write converted files into.')
    p_batch.add_argument('--to', required=True, choices=['blp', 'dds', 'tga', 'png', 'bmp', 'tiff'],
                          help='Target format for every converted file.')
    p_batch.add_argument('--resize', metavar='WIDTHxHEIGHT', default=None,
                          help='Resize (box-filter) every image before writing.')
    p_batch.add_argument('--fourcc', choices=['DXT1', 'DXT3', 'DXT5'], default='DXT5',
                          help='--to dds only: block-compression format (default: DXT5).')
    p_batch.add_argument('--mipmaps', dest='mipmaps', action='store_true', default=True,
                          help='--to dds only: generate a full mip chain (default: on).')
    p_batch.add_argument('--no-mipmaps', dest='mipmaps', action='store_false',
                          help='--to dds only: write a single mip level.')
    p_batch.set_defaults(func=_cmd_img_convert_batch)

    p_icon_set = subparsers.add_parser('make-icon-set', help=(
        "Generate one or more icon-file kinds for an ability/item/unit/upgrade from a "
        "single source image, each at its real in-game relative path and prefix - "
        "confirmed against this repo's own real fixture set: active (BTN, in "
        "CommandButtons/, paired with its own DISBTN disabled icon), passive (PAS, in "
        "its own PassiveButtons/ directory - NOT CommandButtons - paired with DISPAS), "
        "autocast (ATC, in CommandButtons/, paired with DISATC), upgrade (UPG, in "
        "CommandButtons/, no disabled version), attack/attribute (ATT, directly under "
        "ReplaceableTextures/ with no subdirectory, no disabled version)."))
    p_icon_set.add_argument('source', help='Path to the source image.')
    p_icon_set.add_argument('output_dir', help='Map/project root to write ReplaceableTextures/... under.')
    p_icon_set.add_argument('name', help="Icon name suffix, e.g. 'MyAbility' -> BTNMyAbility.<ext>.")
    p_icon_set.add_argument('--kinds', default='active',
                             help="Comma-separated icon kinds to generate: active,passive,autocast,"
                                  "upgrade,attack (default: active).")
    p_icon_set.add_argument('--active-border', default=None, help='Border image for the active (BTN) icon.')
    p_icon_set.add_argument('--passive-border', default=None, help='Border image for the passive (PAS) icon.')
    p_icon_set.add_argument('--autocast-border', default=None,
                             help='Border image for the autocast (ATC) icon.')
    p_icon_set.add_argument('--upgrade-border', default=None, help='Border image for the upgrade (UPG) icon.')
    p_icon_set.add_argument('--attack-border', default=None,
                             help='Border image for the attack/attribute (ATT) icon.')
    p_icon_set.add_argument('--bundled-borders', choices=['classic', 'reforged'], default=None,
                             help="Use this package's own bundled per-kind border art (classic="
                                  "64x64, reforged=256x256) for any kind whose specific "
                                  "--<kind>-border flag above wasn't given - not a universal "
                                  "ground truth, see wc3files.img.borders' own docstring.")
    p_icon_set.add_argument('--size', type=int, default=64, help='Square icon size in pixels (default: 64).')
    p_icon_set.add_argument('--format', default='blp', help="Output file extension, e.g. 'blp' or 'png' "
                             "(default: blp).")
    p_icon_set.add_argument('--darken', type=float, default=0.4, help='Disabled-variant darken amount (0..1).')
    p_icon_set.add_argument('--desaturate', type=float, default=0.7,
                             help='Disabled-variant desaturate amount (0..1).')
    p_icon_set.add_argument('--alpha', choices=['auto', 'yes', 'no'], default='auto',
                             help='.blp output only: include an 8-bit alpha plane (default: auto).')
    p_icon_set.set_defaults(func=_cmd_make_icon_set)

    p_set_icon = subparsers.add_parser('set-object-icon', help=(
        "Wire a generated icon path into an object file's icon field (e.g. set a unit's "
        "'uico' field to the BTN path make-icon-set just wrote) - Units/Items/"
        "Destructables/Buffs only (flat fields), not level-indexed categories."))
    p_set_icon.add_argument('object_file', help='Path to the object file (e.g. war3map.w3u) - '
                             'edited in place.')
    p_set_icon.add_argument('object_id', help='4-character rawcode of the object to edit.')
    p_set_icon.add_argument('field', help="Field rawcode to set, e.g. 'uico' (units) / 'iico' (items).")
    p_set_icon.add_argument('icon_path', help="The icon path to set, e.g. "
                             "'ReplaceableTextures\\\\CommandButtons\\\\BTNMyAbility.blp'.")
    p_set_icon.set_defaults(func=_cmd_set_object_icon)

    p_dump = subparsers.add_parser('dump', help='Read a single map sub-file and dump it as JSON.')
    p_dump.add_argument('format', choices=sorted(FORMATS.keys()), help='File format to read.')
    p_dump.add_argument('input', help='Path to the binary/text file to read.')
    p_dump.add_argument('output', nargs='?', default='-', help="Path to write JSON to (default: stdout, '-').")
    p_dump.add_argument('--width', type=int, default=None,
                        help=('Grid width, for formats that carry no header of their '
                              'own (shd). Take it from the terrain: `dump w3e`.'))
    p_dump.add_argument('--height', type=int, default=None,
                        help='Grid height, as --width.')
    p_dump.set_defaults(func=_cmd_dump)

    p_build = subparsers.add_parser('build', help='Build a map sub-file back from a dump() JSON file.')
    p_build.add_argument('format', choices=sorted(FORMATS.keys()), help='File format to write.')
    p_build.add_argument('input', help="Path to a JSON file from `dump` (or '-' for stdin).")
    p_build.add_argument('output', help='Path to write the resulting file to.')
    p_build.add_argument('--validate', action='store_true', help=(
        "obj format only (silently ignored for every other format): check every field "
        "against the bundled Blizzard metadata schema before writing, and raise instead of "
        "writing if anything fails. See TODO.md's validator entry for exact scope."))
    p_build.set_defaults(func=_cmd_build)

    casc_help = ('Read an installed Blizzard game (CASC storage) - the modern '
                 'replacement for MPQ that Warcraft III Reforged ships in. '
                 'Local-only: never contacts the CDN.')
    p_casc_info = subparsers.add_parser('casc-info', help=casc_help)
    p_casc_info.add_argument('install', help="Game install directory (the one holding .build.info).")
    p_casc_info.set_defaults(func=_cmd_casc_info)

    p_casc_list = subparsers.add_parser('casc-list', help='List every file in a CASC install.')
    p_casc_list.add_argument('install')
    p_casc_list.add_argument('--locale', default=None,
                             help="Only entries with this locale tag (e.g. enUS); '' for locale-less.")
    p_casc_list.add_argument('--limit', type=int, default=0, help='Stop after N entries.')
    p_casc_list.set_defaults(func=_cmd_casc_list)

    p_casc_search = subparsers.add_parser(
        'casc-search',
        help=('Find files in a CASC install by path. A pattern with * or ? is '
              'matched as a glob, anything else as a substring; either way '
              'case-insensitively.'))
    p_casc_search.add_argument('install')
    p_casc_search.add_argument('pattern')
    p_casc_search.add_argument('--locale', default=None)
    p_casc_search.add_argument('--limit', type=int, default=None)
    p_casc_search.add_argument('--long', action='store_true',
                               help='Also show size, locale and whether the blob is installed.')
    p_casc_search.set_defaults(func=_cmd_casc_search)

    p_casc_extract = subparsers.add_parser(
        'casc-extract', help='Extract matching files out of a CASC install.')
    p_casc_extract.add_argument('install')
    p_casc_extract.add_argument('pattern')
    p_casc_extract.add_argument('--out', default=None, help='Destination for a single match.')
    p_casc_extract.add_argument('--out-dir', default=None,
                                help="Extract every match under here, keeping the game's layout.")
    p_casc_extract.add_argument('--locale', default=None)
    p_casc_extract.set_defaults(func=_cmd_casc_extract)

    p_replay_mmd = subparsers.add_parser(
        'replay-mmd',
        help=('Dump the W3MMD map meta data a replay carries - the standard maps '
              'use to report players, winners and per-player statistics.'))
    p_replay_mmd.add_argument('replay', help='Path to a .w3g replay.')
    p_replay_mmd.add_argument('--json', action='store_true',
                              help='Emit JSON instead of a readable summary.')
    p_replay_mmd.add_argument('--out', default='-',
                              help='Where to write JSON (default stdout).')
    p_replay_mmd.add_argument('--max-events', type=int, default=20,
                              help='How many events to show in the summary.')
    p_replay_mmd.set_defaults(func=_cmd_replay_mmd)

    p_replay_actions = subparsers.add_parser(
        'replay-actions',
        help=('Summarize the player actions in a replay - totals, APM, and '
              'optionally a breakdown by action type.'))
    p_replay_actions.add_argument('replay', help='Path to a .w3g replay.')
    p_replay_actions.add_argument('--by-action', type=int, default=0,
                                  metavar='N',
                                  help='Also list the N most common action types.')
    p_replay_actions.set_defaults(func=_cmd_replay_actions)

    p_casc_gamedata = subparsers.add_parser(
        'casc-export-gamedata',
        help=("Extract the data tables this package bundles from an installed "
              "game, so they can be refreshed. Reports which are out of date; "
              "never overwrites the bundled copies itself."))
    p_casc_gamedata.add_argument('install', help='Game install directory.')
    p_casc_gamedata.add_argument('--out-dir', required=True,
                                 help='Where to write the extracted tables.')
    p_casc_gamedata.add_argument('--locale', default='enUS',
                                 help='Locale to take the strings files from.')
    p_casc_gamedata.add_argument('--bundled-dir', action='append', default=None,
                                 help=("Directory of bundled files to compare "
                                       "against (repeatable; defaults to this "
                                       "package's own)."))
    p_casc_gamedata.set_defaults(func=_cmd_casc_export_gamedata)

    p_ui_info = subparsers.add_parser(
        'ui-info',
        help=("Summarise a UI or effect file: .fdf frame definitions, .toc "
              "lists, and the PopcornFX/.wai formats that are only partly "
              "decoded."))
    p_ui_info.add_argument('path', help='File to read.')
    p_ui_info.add_argument('--json', action='store_true',
                           help='Emit JSON instead of a readable summary.')
    p_ui_info.add_argument('--out', help='Write JSON here instead of stdout.')
    p_ui_info.add_argument('--limit', type=int, default=20,
                           help='Cap long lists in the readable summary.')
    p_ui_info.set_defaults(func=_cmd_ui_info)

    p_model = subparsers.add_parser(
        'model-convert',
        help=('Convert a model between MDX (binary) and MDL (text). The '
              "direction comes from the output path's extension."))
    p_model.add_argument('input', help='Model to read (.mdx or .mdl).')
    p_model.add_argument('output', help='Where to write it (.mdl or .mdx).')
    p_model.set_defaults(func=_cmd_model_convert)

    p_model_info = subparsers.add_parser(
        'model-info', help='Summarise a model: version, sequences, textures.')
    p_model_info.add_argument('input', help='Model to read (.mdx or .mdl).')
    p_model_info.add_argument('--json', action='store_true',
                              help='Emit the full model data as JSON.')
    p_model_info.add_argument('--out', help='Write JSON here instead of stdout.')
    p_model_info.add_argument('--limit', type=int, default=20,
                              help='Cap long lists in the readable summary.')
    p_model_info.set_defaults(func=_cmd_model_info)

    p_jass = subparsers.add_parser(
        'jass', help=('Parse a JASS or Lua script and print it back; optionally '
                      'strip unreachable code and obfuscate identifiers.'))
    p_jass.add_argument('input', help='Script to read (.j or .lua).')
    p_jass.add_argument('output', nargs='?', default='-',
                        help="Where to write it (default: stdout, '-').")
    p_jass.add_argument('--obfuscate', action='store_true',
                        help='Rename every identifier that is safe to rename.')
    p_jass.add_argument('--strip', action='store_true',
                        help=('Remove code nothing reachable calls (JASS only). '
                              'Runs before --obfuscate when both are given.'))
    p_jass.add_argument('--entry', action='append', default=None,
                        help=('An entry-point function for reachability (repeatable). '
                              "Defaults to a map script's main and config."))
    p_jass.add_argument('--keep', action='append', default=None,
                        help=('An identifier to leave alone (repeatable). Use for '
                              'names other scripts or the map call into.'))
    p_jass.set_defaults(func=_cmd_jass)

    p_trig_text = subparsers.add_parser(
        'trigger-text',
        help=('Render a war3map.wtg as the text the World Editor shows - '
              'events, conditions and actions per trigger.'))
    p_trig_text.add_argument('input', help='war3map.wtg to read.')
    p_trig_text.add_argument('--name', help='Only triggers whose name contains this.')
    p_trig_text.add_argument('--trigger-data',
                             help=('A TriggerData.txt to use instead of the bundled '
                                   'one - needed for a map with custom GUI entries.'))
    p_trig_text.add_argument('--json', action='store_true',
                             help='Emit JSON instead of readable text.')
    p_trig_text.add_argument('--out', help='Write JSON here instead of stdout.')
    p_trig_text.set_defaults(func=_cmd_trigger_text)

    p_from_script = subparsers.add_parser(
        'script-to-triggers',
        help=('Reconstruct GUI triggers, variables, regions, cameras and sounds '
              'from a map script. Partial by nature - reports its own coverage.'))
    p_from_script.add_argument('input', help='war3map.j to read.')
    p_from_script.add_argument('--json', action='store_true',
                               help='Emit the full reconstruction as JSON.')
    p_from_script.add_argument('--out', help='Write JSON here instead of stdout.')
    p_from_script.add_argument('--limit', type=int, default=20,
                               help='Cap the trigger list in the readable summary.')
    p_from_script.set_defaults(func=_cmd_script_to_triggers)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.verbose:
        logger.remove()
        logger.add(sys.stderr, level='WARNING')

    try:
        args.func(args)
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except struct.error as exc:
        # A parser running off the end of its input. The usual cause is a
        # file that was extracted badly - several locally-extracted maps hold
        # war3map.wtg files padded or cut to a whole 4096-byte sector - not a
        # format this package cannot read.
        source = getattr(args, 'input', None) or ''
        print(f"error: {source}: ran off the end of the file ({exc})",
              file=sys.stderr)
        print("hint: the file may be truncated or padded by whatever "
              "extracted it - a size that is an exact multiple of 4096 is a "
              "good sign of that. Try re-extracting it with `unpack`.",
              file=sys.stderr)
        return 1
    except Exception as exc:
        # A script that will not parse is an ordinary outcome, not a crash:
        # plenty of real map scripts are vJass, which this package
        # preprocesses but does not compile. Report it as an error with the
        # likely cause rather than a traceback.
        from wc3files.scripts import JassLexError, JassParseError
        from wc3files.scripts import LuaLexError, LuaParseError
        if isinstance(exc, (JassLexError, JassParseError, LuaLexError,
                            LuaParseError)):
            print(f"error: {args.input}: {exc}", file=sys.stderr)
            print("hint: if this script uses vJass (struct, method, library, "
                  "scope) it needs JassHelper's compilation first - this "
                  "package preprocesses text macros but does not compile "
                  "vJass. See TODO.md.", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == '__main__':
    sys.exit(main())
