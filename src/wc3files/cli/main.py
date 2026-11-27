import argparse
import base64
import contextlib
import json
import os
import struct
import sys
import tempfile

from loguru import logger

from wc3files import __version__
from wc3files.files.binary_file import TruncatedDataError
from wc3files.w3x import W3xFile
from wc3files.w3o import ObjectFile, SlkFile, BaseObjectData, BaseObjectStrings, CombinedBaseData
from wc3files.w3o.object_metadata import ObjectMetaData
from wc3files.w3o.object_override import literal_table_paths
from wc3files.data import ObjectFileType, BinaryDataType
from wc3files.img import (BlpFile, DdsFile, TgaFile, BmpFile, TiffFile, read_png, write_png,
                           resize_box, composite_over, make_disabled_variant, border_path)
from .formats import FORMATS, FORMAT_FILES, format_for_name

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



# Pages that explain what the help can only summarise (#169).
_DOCS = 'https://github.com/Bekhouche/wc3files/blob/main/docs/cli.md'

def _bytes_decode_hook(obj):
    if set(obj) == {BYTES_TAG}:
        return base64.b64decode(obj[BYTES_TAG])
    return obj


def _open_map(path, deep, members=None):
    """Read a map for a command that writes something from it: a protected
    map is refused unless --deep (wc3files.w3x.read_map), and a recovered one
    is reported as such. `members` reads only those files."""
    from wc3files.w3x.w3x_file import read_map, protection_reason
    # A deep read takes minutes, so the command shows its progress (if tqdm
    # is installed); the library itself prints nothing.
    archive = read_map(path, deep=deep, progress=deep, members=members)
    if archive.protected and members is None:
        print(f"note: {path} is protected ({protection_reason(archive)}); "
              f"{sum(1 for v in archive.files.values() if v is not None)} files were "
              f"recovered by scanning for names, and some may still be missing.",
              file=sys.stderr)
    return archive


_MAP_EXTENSIONS = ('.w3x', '.w3m')


@contextlib.contextmanager
def _member_or_file(path, members, deep=False):
    """`(file path, label)` for a command that reads one map member: a plain
    file as it is, or, for a .w3x/.w3m, the first of `members` it holds,
    copied to a temporary file. `label` names it in messages
    (`MyMap.w3x:war3map.j`)."""
    if not path.lower().endswith(_MAP_EXTENSIONS):
        yield path, path
        return
    # Only the members asked for are read: decompressing the whole of a
    # large map took longer than the command itself.
    archive = _open_map(path, deep, members=members)
    found = {name.lower(): (name, data) for name, data in archive.files.items()
             if data is not None}
    for member in members:
        if member.lower() in found:
            name, data = found[member.lower()]
            break
    else:
        raise ValueError(f'{path} has no {" or ".join(members)}')
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, name.replace('\\', '/').rsplit('/', 1)[-1])
        with open(target, 'wb') as handle:
            handle.write(data)
        yield target, f'{path}:{name}'


def _cmd_unpack(args):
    w3x = _open_map(args.src, args.deep)
    os.makedirs(args.out_dir, exist_ok=True)
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


def _add_model_batch_args(parser):
    """The inputs and outputs every model edit command takes (#234)."""
    parser.add_argument('inputs', nargs='+',
                        help='Models (.mdx, .mdl), or folders searched with their subfolders.')
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('-o', '--out-dir', help='Write the edited models here, as they were laid out.')
    where.add_argument('--in-place', action='store_true', help='Overwrite the models.')


def _run_model_batch(args, edit, describe):
    """Run `edit` over the models and print a line per model; 1 if any failed."""
    from wc3files.mdl.batch import edit_models
    failed = done = 0
    for source, target, result in edit_models(args.inputs, edit, args.out_dir, args.in_place):
        if target is None:
            failed += 1
            print(f'{source}: not edited: {result}', file=sys.stderr)
            continue
        done += 1
        print(f'{target}: {describe(result)}')
    print(f'{done} model(s) edited' + (f', {failed} failed' if failed else ''))
    return 1 if failed else 0


def _cmd_model_extents(args):
    """Recompute the extents of models from their posed vertices (#236)."""
    from wc3files.mdl.extents import compute_extents

    def describe(report):
        if report.get('unchanged'):
            return 'no vertices, extents kept'
        new = report['new']
        return ('extent ' + ' '.join(f'{v:.1f}' for v in new['minimum'] + new['maximum'])
                + f", radius {new['boundsRadius']:.1f}")
    return _run_model_batch(
        args, lambda data: compute_extents(data, frames=args.frames, radius=args.radius),
        describe)


def _cmd_model_transform(args):
    """Scale and move whole models (#235)."""
    from wc3files.mdl.transform import scale_model, translate_model
    if args.scale is None and args.translate is None:
        raise ValueError('model-transform: give --scale, --translate or both')
    offset = None
    if args.translate is not None:
        try:
            offset = [float(v) for v in args.translate.split(',')]
        except ValueError:
            offset = []
        if len(offset) != 3:
            raise ValueError('model-transform: --translate takes X,Y,Z, as 0,0,10')

    def edit(data):
        done = []
        if args.scale is not None:
            report = scale_model(data, args.scale)
            done.append(f'scaled by {args.scale:g}')
            if report['popcorn']:
                done.append(f"{report['popcorn']} popcorn emitter(s) left as they were")
        if offset is not None:
            translate_model(data, offset)
            done.append('moved by ' + ','.join(f'{v:g}' for v in offset))
        return ', '.join(done)
    return _run_model_batch(args, edit, str)


def _cmd_model_normals(args):
    """Recompute the vertex normals of models from their faces (#237)."""
    from wc3files.mdl.normals import recompute_normals

    def describe(report):
        text = f"{report['vertices']} normal(s) set"
        return text + (f", {report['kept']} vertex(es) in no face kept" if report['kept'] else '')
    return _run_model_batch(
        args, lambda data: recompute_normals(data, args.angle, args.weld), describe)


def _cmd_model_remove_unused(args):
    """Remove the unused materials, textures and texture animations (#232)."""
    from wc3files.mdl.cleanup import remove_unused

    def describe(report):
        return (f"removed {report['materials']} material(s), {report['textures']} "
                f"texture(s), {report['textureAnimations']} texture animation(s)")
    return _run_model_batch(args, remove_unused, describe)


def _cmd_model_team_glow(args):
    """Make the team glow of models transparent, or remove it (#240)."""
    from wc3files.mdl.teamglow import remove_team_glow

    def describe(report):
        if not report['layers']:
            return 'no team glow'
        if args.mode == 'transparent':
            return f"{report['layers']} team glow layer(s) made transparent"
        return (f"{report['layers']} team glow layer(s) removed, with {report['geosets']} "
                f"geoset(s), {report['materials']} material(s) and {report['textures']} "
                'texture(s) no longer used')
    return _run_model_batch(args, lambda data: remove_team_glow(data, args.mode), describe)


class _PathRule(argparse.Action):
    """--replace, --prefix and --pattern, kept in the order given (#241)."""

    def __call__(self, parser, namespace, values, option_string=None):
        rules = getattr(namespace, 'rules', None) or []
        rules.append((self.const, values[0], values[1]))
        namespace.rules = rules


def _cmd_model_texture_paths(args):
    """Rewrite the texture paths of models (#241)."""
    from wc3files.mdl.paths import compile_rules, rewrite_texture_paths
    if not args.rules:
        raise ValueError('model-texture-paths: give --replace, --prefix or --pattern')
    rules = compile_rules(args.rules)
    changes = {'changed': 0, 'refused': 0}

    def describe(report):
        changes['changed'] += len(report['changed'])
        changes['refused'] += len(report['refused'])
        lines = [f'{len(report["changed"])} path(s) rewritten']
        lines += [f'  {old} -> {new}' for old, new in report['changed']]
        lines += [f'  not rewritten, longer than 259 bytes: {old} -> {new}'
                  for old, new in report['refused']]
        return '\n'.join(lines)
    status = _run_model_batch(args, lambda data: rewrite_texture_paths(data, rules), describe)
    print(f"{changes['changed']} path(s) rewritten, {changes['refused']} too long")
    return status


def _cmd_model_textures(args):
    """List the textures of models, or of the models of a map (#242)."""
    from wc3files.mdl.batch import model_paths
    from wc3files.mdl.model_file import ModelFile
    from wc3files.mdl.texture_list import game_file_names, list_textures, map_textures
    game_names = None
    if args.game:
        from wc3files.casc import CascStorage
        storage = CascStorage()
        storage.open(args.game)
        game_names = game_file_names(storage)
    maps = [p for p in args.inputs if p.lower().endswith(('.w3x', '.w3m'))]
    others = [p for p in args.inputs if p not in maps]
    out = {}
    if others:
        models, failed = [], []
        for path, _ in model_paths(others):
            try:
                models.append((path, ModelFile().read(path)))
            except Exception as error:  # reported, and the others go on
                failed.append((path, str(error)))
        out['models'] = {'textures': list_textures(models), 'unreadable': failed}
    for path in maps:
        from wc3files.w3x.modes import archive_files
        out[path] = map_textures(archive_files(_open_map(path, args.deep)), game_names)
    if args.json:
        _write_json(out, '-')
        return 0
    for label, report in out.items():
        print(f'== {label}')
        for key in sorted(report['textures']):
            entry = report['textures'][key]
            print(f"{entry['path']}  ({len(entry['models'])} model(s))")
            for model in entry['models']:
                print(f'    {model}')
        for name, error in report['unreadable']:
            print(f'unreadable: {name}: {error}')
        if label == 'models':
            continue
        print(f"{report['models']} model(s), {len(report['textures'])} texture(s), "
              f"{report['imported_images']} imported image(s)")
        print(f"unused, named by no model and no other file: {len(report['unused'])}")
        for name in report['unused']:
            print(f'    {name}')
        print(f"named only by other files: {len(report['named_elsewhere'])}")
        for name, users in report['named_elsewhere'].items():
            print(f"    {name}  ({', '.join(users)})")
        where = 'the map or the game' if report['checked_game'] else 'the map (give --game to check the game too)'
        print(f"used by models but not in {where}: {len(report['missing'])}")
        for entry in report['missing']:
            print(f"    {entry['path']}  ({', '.join(entry['models'])})")
        for used, imported in report['other_extension']:
            print(f'note: {used} is used and {imported} is imported, with another extension')
    return 0


def _cmd_model_keyframes(args):
    """Remove the keyframes that change nothing (#230)."""
    from wc3files.mdl.keyframes import remove_redundant_keys

    def describe(report):
        text = f"{report['redundant']} of {report['keys']} key(s) removed"
        return text + (f", and {report['dead']} no sequence plays" if args.dead else '')
    return _run_model_batch(
        args, lambda data: remove_redundant_keys(data, args.tolerance, args.dead), describe)


def _cmd_model_linear(args):
    """Turn the Hermite and Bezier tracks of models into linear ones (#231)."""
    from wc3files.mdl.keyframes import linearize
    from wc3files.mdl.mdx_io import save_mdx

    def edit(data):
        before = len(save_mdx(data))
        report = linearize(data, args.tolerance)
        report['before'], report['after'] = before, len(save_mdx(data))
        return report

    def describe(report):
        text = (f"{report['tracks']} track(s) made linear, as MDX {report['before']:,} "
                f"-> {report['after']:,} bytes")
        return text + (f", {report['added']} key(s) added" if args.tolerance is not None else '')
    return _run_model_batch(args, edit, describe)


def _cmd_model_remove_sequences(args):
    """Remove sequences by name, with the keys only they used (#238)."""
    from wc3files.mdl.sequences import remove_sequences

    def describe(report):
        parts = [f"removed {', '.join(report['removed']) or 'nothing'}",
                 f"{report['keys']} key(s) and event frame(s) gone"]
        if report['missing']:
            parts.append(f"not found: {', '.join(report['missing'])}")
        return '; '.join(parts)
    return _run_model_batch(
        args, lambda data: remove_sequences(data, args.sequence, args.pack), describe)


def _cmd_model_transfer_sequences(args):
    """Copy sequences from one model into others, by node name (#239)."""
    from wc3files.mdl.model_file import ModelFile
    from wc3files.mdl.sequences import transfer_sequences
    source = ModelFile().read(args.source).get_data()

    def describe(report):
        parts = [f"copied {', '.join(report['sequences']) or 'nothing'} "
                 f"({report['keys']} key(s) and event frame(s))"]
        if report['missing']:
            parts.append(f"not in the source: {', '.join(report['missing'])}")
        if report['already_named']:
            parts.append(f"the target already had: {', '.join(report['already_named'])}")
        if report['unmatched_nodes']:
            parts.append(f"{len(report['unmatched_nodes'])} source node(s) with no match: "
                         + ', '.join(report['unmatched_nodes'][:10])
                         + (' ...' if len(report['unmatched_nodes']) > 10 else ''))
        if report['converted']:
            parts.append(f"{report['converted']} track(s) converted to the target's "
                         'interpolation, losing their shape')
        if report['geoset_animations']:
            parts.append(f"{report['geoset_animations']} geoset animation(s) not copied")
        return '; '.join(parts)
    return _run_model_batch(
        args, lambda data: transfer_sequences(data, source, args.sequence, args.gap), describe)


def _cmd_model_obj(args):
    """OBJ to MDX/MDL, or MDX/MDL to OBJ and MTL (#244)."""
    from wc3files.mdl.model_file import ModelFile
    from wc3files.mdl.obj import model_to_obj, obj_to_model, read_mtl, read_obj
    if args.input.lower().endswith('.obj'):
        with open(args.input, encoding='utf-8', errors='replace') as handle:
            text = handle.read()
        materials = {}
        for name in read_obj(text)['libraries']:
            path = os.path.join(os.path.dirname(args.input), name)
            if os.path.exists(path):
                with open(path, encoding='utf-8', errors='replace') as handle:
                    materials.update(read_mtl(handle.read()))
            else:
                print(f'not found: {path}; its materials take Textures\\White.blp',
                      file=sys.stderr)
        model = obj_to_model(text, materials, args.up, args.scale, args.texture_dir)
        ModelFile().import_data(model).write(args.output)
        print(f"Wrote {args.output}: {len(model['geosets'])} geoset(s), "
              f"{sum(len(g['faces']) // 3 for g in model['geosets'])} triangle(s), "
              f"textures {', '.join(t['path'] for t in model['textures'])}")
        return 0
    if not args.output.lower().endswith('.obj'):
        raise ValueError('model-obj: one of the two paths is an .obj file')
    model = ModelFile().read(args.input)
    mtl_path = os.path.splitext(args.output)[0] + '.mtl'
    obj_text, mtl_text = model_to_obj(model, args.sequence, args.frame, args.up,
                                      os.path.basename(mtl_path))
    for path, text in ((args.output, obj_text), (mtl_path, mtl_text)):
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
    print(f'Wrote {args.output} and {mtl_path}')
    return 0


def _cmd_model_gltf(args):
    """glTF 2.0 to MDX/MDL, or MDX/MDL to .glb (#245)."""
    from wc3files.mdl.gltf import gltf_to_model, model_to_gltf
    from wc3files.mdl.model_file import ModelFile
    if args.input.lower().endswith(('.glb', '.gltf')):
        model, report = gltf_to_model(args.input, args.scale, args.texture_dir, args.min_weight)
        ModelFile().import_data(model).write(args.output)
        folder = os.path.dirname(os.path.abspath(args.output))
        for path, data in report['textures'].items():
            target = os.path.join(folder, *path.split(chr(92)))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as handle:
                handle.write(data)
            print(f'Wrote {target}')
        print(f"Wrote {args.output}: {report['geosets']} geoset(s), {report['bones']} bone(s), "
              f"sequences {', '.join(report['sequences'])}")
        if report['largest_weight_dropped']:
            print(f"bone weights: up to {report['largest_weight_dropped']:.0%} of a vertex's "
                  'weight was on bones below --min-weight, and the bones kept weigh the same')
        for note in report['notes']:
            print(f'not taken: {note}')
        return 0
    if not args.output.lower().endswith('.glb'):
        raise ValueError('model-gltf: give a .glb or .gltf to read, or a .glb to write')
    def read_texture(path):
        stem = os.path.join(args.textures, *path.split(chr(92))).rsplit('.', 1)[0]
        for ext in ('.blp', '.dds', '.tga', '.png', '.BLP'):
            if os.path.exists(stem + ext):
                with open(stem + ext, 'rb') as handle:
                    return handle.read()
        return None
    textures = read_texture if args.textures else None
    glb, report = model_to_gltf(ModelFile().read(args.input), args.scale, textures,
                                args.curve_ms)
    with open(args.output, 'wb') as handle:
        handle.write(glb)
    print(f"Wrote {args.output}: {report['nodes']} node(s), {report['primitives']} "
          f"primitive(s), {report['animations']} animation(s)")
    for note in report['notes']:
        print(f'not written: {note}')
    return 0


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
    with _member_or_file(args.input, ('war3map.lua', 'war3map.j', 'scripts\\war3map.lua',
                                      'scripts\\war3map.j'), args.deep) as (path, label):
        script.read(path)
    program = script.get_data()
    is_lua = script.get_file_type().value == 'lua'

    if args.check:
        findings = script.validate()
        if not is_lua and not args.no_leaks:
            from wc3files.scripts.leaks import find_leaks
            findings = findings + find_leaks(program)
            findings.sort(key=lambda f: (f.get('line') is None, f.get('line') or 0))
        return _report_script_check(label, findings)
    if args.to_lua:
        if is_lua:
            raise ValueError('--to-lua takes a JASS script')
        from wc3files.scripts.jass_to_lua import jass_to_lua
        _write_or_print(jass_to_lua(program), args.output)
        return

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


def _report_script_check(path, findings):
    """Print `path:line: severity: message` for each finding; 1 on an issue."""
    for finding in findings:
        line = finding.get('line')
        where = f'{path}:{line}' if line else path
        print(f"{where}: {finding['severity']}: {finding['message']}")
    issues = sum(1 for f in findings if f['severity'] == 'issue')
    warnings = sum(1 for f in findings if f['severity'] == 'warning')
    notes = len(findings) - issues - warnings
    summary = f'{issues} issue(s), {notes} note(s)'
    if warnings:
        summary += f', {warnings} warning(s)'
    print(summary, file=sys.stderr)
    return 1 if issues else 0


def _cmd_gamedata_extract(args):
    """Extract a game-data profile from an install."""
    from wc3files import gamedata
    profile = gamedata.extract(args.install, args.output, locale=args.locale, name=args.name)
    print(f"{profile['source']}: {len(profile['files'])} table(s) written to {args.output}")
    if profile['missing']:
        print(f"not in this version ({len(profile['missing'])}, the bundled copy is used): "
              + ', '.join(os.path.basename(m) for m in profile['missing']))
    for relative, why in profile['refused'].items():
        print(f'refused {relative}: {why}', file=sys.stderr)
    return 1 if profile['refused'] else 0


def _cmd_slk_mode(args):
    """Whole-map Slk mode (wc3files.w3x.modes.map_to_slk)."""
    from wc3files.w3x.modes import map_to_slk, archive_files
    files = archive_files(_open_map(args.input, args.deep))
    report = map_to_slk(args.input, args.output, files)
    for name, outcome in report.items():
        print(f'{name}: {outcome}')
    print(f'Wrote {args.output}')
    return 0


def _cmd_obj_mode(args):
    """Whole-map Obj mode (wc3files.w3x.modes.map_to_obj)."""
    from wc3files.w3x.modes import map_to_obj, archive_files
    files = archive_files(_open_map(args.input, args.deep))
    report = map_to_obj(args.input, args.output, files)
    if not report:
        print('no object-data tables in this map; nothing to do')
    for name, outcome in report.items():
        print(f'{name}: {outcome}')
    print(f'Wrote {args.output}')
    return 0


def _cmd_optimize(args):
    """Run the optimizer passes the options name (wc3files.w3x.optimize)."""
    from wc3files.w3x.modes import archive_files, _write_map, map_to_slk
    from wc3files.w3x.optimize import (remove_unused_objects, remove_we_only,
                                       squeeze_models, computed_text)
    if not (args.remove_unused_objects or args.remove_we_only or args.slk or
            args.mdx_squeeze or args.computed_text):
        raise ValueError('choose at least one of --remove-unused-objects, '
                         '--remove-we-only, --mdx-squeeze, --computed-text, --slk')
    files = archive_files(_open_map(args.input, args.deep))
    if args.remove_unused_objects:
        files, report = remove_unused_objects(files)
        if report.get('skipped'):
            print(f"unused objects: skipped ({report['skipped']})")
        else:
            count = sum(len(v) for v in report['removed'].values())
            print(f"unused objects: removed {count}, kept {report['kept']}")
    if args.computed_text:
        files, report = computed_text(files)
        print(f"computed text: {report['replaced']} reference(s) written in, "
              f"{report['left']} left as they were")
    if args.mdx_squeeze:
        files, report = squeeze_models(files)
        print(f"models: {len(report['squeezed'])} squeezed")
    if args.remove_we_only:
        files, report = remove_we_only(files)
        print('editor-only files: removed ' + (', '.join(report['removed']) or 'none'))
    if args.slk:
        for name, outcome in map_to_slk(None, args.output, files).items():
            print(f'{name}: {outcome}')
    else:
        _write_map(files, args.output)
    print(f'Wrote {args.output}')
    return 0


def _cmd_objects_merge(args):
    """Merge two object files of one type (#217)."""
    from wc3files.w3o.merge import merge_objects
    ext = args.first.rsplit('.', 1)[-1].lower()
    if args.second.rsplit('.', 1)[-1].lower() != ext \
            or args.output.rsplit('.', 1)[-1].lower() != ext:
        raise ValueError('objects-merge: the three files must be of one type '
                         '(war3map.w3u, war3map.w3a, ...)')
    sides = []
    for path in (args.first, args.second):
        handler = ObjectFile()
        handler.read(path)
        sides.append(handler.export_data())
    merged, report = merge_objects(*sides, on_conflict=args.on_conflict,
                                   on_edit_conflict=args.on_edit_conflict)
    for old, new in report['table'].items():
        print(f'remapped: {old} -> {new}')
    for label in ('kept_first', 'kept_second'):
        for _, object_id in report[label]:
            print(f"{label.replace('_', ' ')}: {object_id}")
    print(f"{report['added']} added, {len(report['remapped'])} remapped, "
          f"{len(report['kept_first'])} kept from the first, {len(report['kept_second'])} "
          f"taken from the second; {report['references_rewritten']} references rewritten")
    if report['strings']:
        print(f"{len(report['strings'])} TRIGSTR_ references of the second file point into "
              f"its own war3map.wts")
    handler = ObjectFile()
    handler.import_data({ext: merged[ext]})
    handler.write(args.output)
    if args.table:
        _write_json(report['table'], args.table)
    print(f'Wrote {args.output}')
    return 0


def _doodad_source(path, deep):
    """(war3map.doo bytes, the gg_dest_ variables its map uses, the map's
    members or None) for a war3map.doo file or a map."""
    from wc3files.doo.merge import referenced_destructables
    from wc3files.w3x.modes import archive_files
    if not path.lower().endswith(_MAP_EXTENSIONS):
        with open(path, 'rb') as handle:
            return handle.read(), None, None
    files = archive_files(_open_map(path, deep))
    lower = {name.lower(): name for name in files}
    if 'war3map.doo' not in lower:
        raise ValueError(f'{path} has no war3map.doo')
    texts = [files[lower[name]] for name in ('war3map.j', 'scripts\\war3map.j', 'war3map.lua',
                                             'war3map.wtg', 'war3map.wct') if name in lower]
    return files[lower['war3map.doo']], referenced_destructables(*texts), files


def _cmd_doodads_merge(args):
    """Merge the war3map.doo files of several maps or files (#219)."""
    from wc3files.doo.merge import merge_doodads
    from wc3files.w3x.modes import _write_map
    sources = [_doodad_source(path, args.deep) for path in args.inputs]
    references = [refs if refs is not None else set() for _, refs, _ in sources]
    merged, report = merge_doodads([raw for raw, _, _ in sources], references=references,
                                   renumber_all=args.renumber_all)
    for path, changed in zip(args.inputs, report['renumbered']):
        print(f'{path}: {len(changed)} editor ids changed')
    for item in report['broken_references']:
        print(f"breaks: gg_dest_{item['id']}_{item['old']:04d} of {args.inputs[item['source']]} "
              f"is now gg_dest_{item['id']}_{item['new']:04d}")
    for item in report['item_tables']:
        print(f"item table: record {item['index']} ({item['id']}) of "
              f"{args.inputs[item['source']]} points to item table {item['pointer']} of its "
              f"own map")
    if report['specials_dropped']:
        print(f"{report['specials_dropped']} duplicate special doodads left out")
    if report['duplicate_ids']:
        print(f"{args.inputs[0]} itself repeats editor ids {report['duplicate_ids']}; "
              f"--renumber-all gives every record its own")
    data = merged.get_data()
    print(f"{len(data['doodads'])} doodads, format {report['version'][0]}/"
          f"{report['version'][1]}")
    if args.output.lower().endswith(_MAP_EXTENSIONS):
        files = sources[0][2]
        if files is None:
            raise ValueError('to write a map, the first input must be a map')
        files = dict(files)
        key = next(name for name in files if name.lower() == 'war3map.doo')
        files[key] = merged.to_bytes()
        _write_map(files, args.output)
    else:
        merged.write(args.output)
    print(f'Wrote {args.output}')
    return 1 if report['broken_references'] else 0


def _cmd_import_folder(args):
    """Add a folder tree to a map and list it in war3map.imp (#215)."""
    from wc3files.w3x.imports import import_folder
    from wc3files.w3x.modes import archive_files, _write_map
    files = archive_files(_open_map(args.input, args.deep))
    files, report = import_folder(files, args.folder, prefix=args.prefix,
                                  replace=not args.keep_existing)
    for label in ('added', 'replaced', 'kept', 'skipped'):
        for name in report[label]:
            print(f'{label}: {name}')
    print(f"{len(report['added'])} added, {len(report['replaced'])} replaced, "
          f"{len(report['kept'])} kept, {len(report['skipped'])} skipped as map data; "
          f"{report['listed']} new war3map.imp entries")
    _write_map(files, args.output)
    print(f'Wrote {args.output}')
    return 0


def _cmd_import_move(args):
    """Move imported files and rewrite the references to them (#214)."""
    from wc3files.w3x.imports import (move_imports, multi_dot_members, strip_prefix_moves,
                                      undot_moves)
    from wc3files.w3x.modes import archive_files, _write_map
    if not (args.move or args.strip_prefix or args.fix_dots):
        raise ValueError('give --move OLD NEW, --strip-prefix or --fix-dots')
    if args.output is None and not args.report_only:
        raise ValueError('give the map to write, or --report-only')
    files = archive_files(_open_map(args.input, args.deep))
    moves = {old: new for old, new in args.move or ()}
    if args.strip_prefix:
        stripped, skipped = strip_prefix_moves(files, names=args.only or None)
        for old, new in skipped:
            print(f'skipped: {old} (the map already has {new})')
        moves.update({old: new for old, new in stripped.items() if old not in moves})
    if args.fix_dots:
        dotted = multi_dot_members(files)
        print(f'{len(dotted)} file names with more than one dot')
        for old in dotted:
            moves.setdefault(old, old)
        moves, skipped = undot_moves(files, moves)
        for old, new in skipped:
            print(f'skipped: {old} (the map already has {new})')
    if not moves:
        print('nothing to move')
        return 0
    files, report = move_imports(files, moves, portraits=not args.no_portraits)
    for old, new in report['moved']:
        print(f'moved: {old} -> {new}')
    for member, count in sorted(report['updated'].items()):
        print(f'updated: {member} ({count})')
    for item in report['unresolved']:
        text = f": {item['text']}" if item['text'] else ''
        print(f"not updated: {item['member']}{text} ({item['reason']})")
    print(f"{len(report['moved'])} moved, {sum(report['updated'].values())} references "
          f"in {len(report['updated'])} files updated, {len(report['unresolved'])} not updated")
    if args.report_only:
        return 0
    _write_map(files, args.output)
    print(f'Wrote {args.output}')
    return 0


def _cmd_transfer(args):
    """Copy parts of one map into other maps (#218)."""
    from wc3files.w3x.modes import archive_files, _write_map
    from wc3files.w3x.transfer import PARTS, transfer
    parts = PARTS if not args.parts else tuple(p.strip() for p in args.parts.split(','))
    source = archive_files(_open_map(args.source, args.deep))
    os.makedirs(args.out_dir, exist_ok=True)
    failed = 0
    for target_path in args.targets:
        output = os.path.join(args.out_dir, os.path.basename(target_path))
        if os.path.normcase(os.path.abspath(output)) == \
                os.path.normcase(os.path.abspath(target_path)):
            raise ValueError(f'{output} would overwrite its target; choose another --out-dir')
        target = archive_files(_open_map(target_path, args.deep))
        try:
            files, report = transfer(source, target, parts=parts,
                                     replace_imports=not args.keep_imports)
        except ValueError as error:
            print(f'{target_path}: not written: {error}', file=sys.stderr)
            failed += 1
            continue
        _write_map(files, output)
        script = report['script'] or {}
        renumbered = sum(1 for old, new in report['strings'].items() if old != new)
        print(f"{target_path}: {len(report['copied'])} files copied, "
              f"{len(report['strings'])} strings ({renumbered} renumbered)")
        for name in script.get('missing_placement', []):
            print(f'  not in the target: {name} (declared as null)')
        for name, replaced in report['objects'].items():
            if replaced:
                print(f'  {name}: {len(replaced)} target objects replaced by the source\'s')
        for note in report['notes']:
            print(f'  note: {note}')
        print(f'Wrote {output}')
    return 1 if failed else 0


def _cmd_campaign_split(args):
    """Write each map of a campaign as a standalone map (#249)."""
    from wc3files.w3x.campaign import PARTS, split_campaign
    from wc3files.w3x.modes import archive_files, _write_map
    parts = PARTS if not args.parts else tuple(p.strip() for p in args.parts.split(','))
    members = archive_files(_open_map(args.campaign, args.deep))
    os.makedirs(args.out_dir, exist_ok=True)
    for name, files, report in split_campaign(members, parts=parts,
                                              rename=not args.keep_names):
        output = os.path.join(args.out_dir, name)
        _write_map(files, output)
        renumbered = sum(1 for old, new in report['strings'].items() if old != new)
        print(f"{report['member']}: {len(report['copied'])} files copied, "
              f"{len(report['strings'])} campaign strings ({renumbered} renumbered)"
              + ('' if report['renamed'] or args.keep_names
                 else '; war3map.w3i kept its name, since it does not read'))
        print(f'Wrote {output}')
    return 0


def _cmd_pathing_texture(args):
    """Pathing textures: show, make from text, or make from a model (#262)."""
    from wc3files.wpm.texture import PathingTexture, from_model
    if args.action == 'show':
        texture = PathingTexture().read(args.input)
        for row in texture.get_data()['rows']:
            print(row)
        return 0
    if args.action == 'make':
        with open(args.input, encoding='utf-8') as handle:
            rows = [line.strip() for line in handle if line.strip()]
        texture = PathingTexture().import_data({'rows': rows})
    else:
        import re
        from wc3files.mdl import ModelFile
        match = re.fullmatch(r'(\d+)x(\d+)', args.size or '')
        if not match:
            raise ValueError('from-model: give --size as WIDTHxHEIGHT in cells, as 8x8')
        model = ModelFile()
        model.read(args.input)
        texture = from_model(model, int(match.group(1)), int(match.group(2)),
                             max_height=args.max_height, border=args.border,
                             snap=args.snap)
    texture.write(args.output)
    for row in texture.get_data()['rows']:
        print(row)
    print(f'Wrote {args.output} ({texture.width}x{texture.height})')
    return 0


def _read_member(path, name, deep=False):
    """(map files or None, member key, bytes) of the member `name` of a map,
    or of `path` itself when it is not a map, for a command that edits it."""
    if not path.lower().endswith(_MAP_EXTENSIONS):
        with open(path, 'rb') as handle:
            return None, None, handle.read()
    from wc3files.w3x.modes import archive_files, _find
    files = archive_files(_open_map(path, deep))
    key = _find(files, name)
    if key is None:
        raise ValueError(f'{path} has no {name}')
    return files, key, files[key]


def _write_member(files, key, data, output):
    """Write a member edited after _read_member: into a copy of the map, or
    as a file of its own."""
    if files is None or not output.lower().endswith(_MAP_EXTENSIONS):
        with open(output, 'wb') as handle:
            handle.write(data)
        return
    from wc3files.w3x.modes import _write_map
    files = dict(files)
    files[key] = data
    _write_map(files, output)


def _cmd_lua_bundle(args):
    """A Lua project bundled into one war3map.lua, alone or in a map (#256)."""
    from wc3files.scripts.lua_bundle import bundle, insert_bundle
    from wc3files.scripts.lua_checker import check_chunk
    from wc3files.scripts.lua_parser import parse
    root = args.root
    if root is None:
        root = (os.path.dirname(os.path.abspath(args.entry))
                if args.entry.lower().endswith('.lua') else os.getcwd())
    text, report = bundle(args.entry, root, run=args.map is None)
    files = key = None
    if args.map is not None:
        if args.output in (None, '-'):
            raise ValueError('--map needs -o, the map or .lua file to write')
        files, key, data = _read_member(args.map, 'war3map.lua', args.deep)
        text = insert_bundle(data.decode('utf-8', 'replace'), text, report['entry'], at=args.at)
    print(f"bundled {len(report['modules'])} module(s) from {os.path.abspath(root)}: "
          + ', '.join(report['modules']), file=sys.stderr)
    if report['dynamic requires']:
        print(f"note: {report['dynamic requires']} require call(s) with a name that is not "
              'a literal string were not followed', file=sys.stderr)
    if args.output in (None, '-'):
        sys.stdout.write(text)
    else:
        _write_member(files, key, text.encode('utf-8'), args.output)
        print(f'Wrote {args.output}', file=sys.stderr)
    if args.no_check:
        return 0
    findings = check_chunk(parse(text))
    issues = [f for f in findings if f['severity'] == 'issue']
    for finding in issues:
        print(f"check: line {finding.get('line')}: {finding['message']}", file=sys.stderr)
    return 1 if issues else 0


def _cmd_terrain_image(args):
    """Terrain layers of war3map.w3e as grayscale PNG, and back (#205)."""
    from wc3files.w3e import TerrainFile
    from wc3files.w3e.images import TILE_LAYERS, export_layer, import_layer
    files, key, data = _read_member(args.input, 'war3map.w3e', args.deep)
    terrain = TerrainFile()
    terrain.read(data)
    d = terrain.get_data()
    size = f"{d['width']}x{d['height']}"
    if args.layer in TILE_LAYERS:
        return _terrain_tiles(args, files, key, terrain, size)
    if args.action == 'export':
        png, value_range = export_layer(d, args.layer, stretch=args.stretch)
        with open(args.image, 'wb') as handle:
            handle.write(png)
        note = (f'; values {value_range[0]} to {value_range[1]}, give --range '
                f'{value_range[0]} {value_range[1]} to import it back' if value_range else '')
        print(f'Wrote {args.image} ({size}, {args.layer}{note})')
        return 0
    if not args.output:
        raise ValueError('terrain-image import: give the map or war3map.w3e to write')
    changed = import_layer(d, args.layer, args.image,
                           tuple(args.range) if args.range else None)
    _write_member(files, key, terrain.to_bytes(), args.output)
    print(f'{changed} of {len(d["tiles"])} tile corners changed; wrote {args.output}')
    return 0


def _terrain_tiles(args, files, key, terrain, size):
    """terrain-image for the ground and cliff-type layers, with a legend (#206)."""
    from wc3files.w3e.images import export_tiles, import_tiles
    d = terrain.get_data()
    legend_path = args.legend or os.path.splitext(args.image)[0] + '.json'
    if args.action == 'export':
        png, legend = export_tiles(d, args.layer)
        with open(args.image, 'wb') as handle:
            handle.write(png)
        with open(legend_path, 'w', encoding='utf-8') as handle:
            json.dump(legend, handle, indent=1)
            handle.write('\n')
        for colour, code in legend.items():
            print(f'{colour} {code}')
        print(f'Wrote {args.image} ({size}, {args.layer}) and its legend {legend_path}')
        return 0
    if not args.output:
        raise ValueError('terrain-image import: give the map or war3map.w3e to write')
    legend = None
    if args.legend or os.path.exists(legend_path):
        with open(legend_path, encoding='utf-8') as handle:
            legend = json.load(handle)
    report = import_tiles(d, args.layer, args.image, legend, nearest=args.nearest,
                          variation=args.variation, seed=args.seed)
    for code in report['added']:
        print(f'added to the {args.layer} list: {code}')
    _write_member(files, key, terrain.to_bytes(), args.output)
    print(f"{report['changed']} of {len(d['tiles'])} tile corners changed; "
          f"wrote {args.output}")
    return 0


def _cmd_shadow_image(args):
    """war3map.shd as a PNG, and back (#203)."""
    from wc3files.shd import ShadowMapFile
    from wc3files.shd.image import shadow_from_png, shadow_png, shadow_size
    from wc3files.w3e import TerrainFile
    from wc3files.w3x.modes import archive_files, _find, _write_map
    files = size = None
    if args.input.lower().endswith(_MAP_EXTENSIONS):
        files = archive_files(_open_map(args.input, args.deep))
        key = _find(files, 'war3map.w3e')
        if key is None:
            raise ValueError(f'{args.input} has no war3map.w3e to take the size from')
        terrain = TerrainFile()
        terrain.read(files[key])
        size = shadow_size(terrain.get_data())
    elif args.input.lower().endswith('.w3e'):
        terrain = TerrainFile()
        terrain.read(args.input)
        size = shadow_size(terrain.get_data())
    elif args.size:
        size = tuple(int(n) for n in args.size.lower().split('x'))
    if args.action == 'export':
        if files is not None:
            key = _find(files, 'war3map.shd')
            if key is None:
                raise ValueError(f'{args.input} has no war3map.shd')
            data = files[key]
        elif args.input.lower().endswith('.w3e'):
            raise ValueError('export: give the map or the war3map.shd, with --size')
        else:
            if size is None:
                raise ValueError('export: a war3map.shd has no size of its own; give '
                                 '--size WIDTHxHEIGHT in cells, or the map')
            with open(args.input, 'rb') as handle:
                data = handle.read()
        shadow = ShadowMapFile()
        shadow.read(data, *size)
        with open(args.image, 'wb') as handle:
            handle.write(shadow_png(shadow.get_data()))
        cells = shadow.get_data()['cells']
        print(f'Wrote {args.image} ({size[0]}x{size[1]}, '
              f'{sum(1 for c in cells if c)} cells in shadow)')
        return 0
    if not args.output:
        raise ValueError('shadow-image import: give the map or war3map.shd to write')
    shadow = shadow_from_png(args.image, size)
    data = shadow.to_bytes()
    if files is not None and args.output.lower().endswith(_MAP_EXTENSIONS):
        files[_find(files, 'war3map.shd') or 'war3map.shd'] = data
        _write_map(files, args.output)
    else:
        with open(args.output, 'wb') as handle:
            handle.write(data)
    print(f'{sum(1 for c in data if c)} of {len(data)} cells in shadow; wrote {args.output}')
    return 0


def _cmd_shadow_compute(args):
    """war3map.shd computed from the terrain and doodads (#202)."""
    import tempfile as _tempfile
    from wc3files.doo import MapDoodadsFile
    from wc3files.shd.compute import compute_shadow_map, game_casters
    from wc3files.w3e import TerrainFile
    from wc3files.w3x.modes import archive_files, _find, _write_map
    files = archive_files(_open_map(args.input, args.deep))
    key = _find(files, 'war3map.w3e')
    if key is None:
        raise ValueError(f'{args.input} has no war3map.w3e')
    terrain = TerrainFile()
    terrain.read(files[key])
    doodads, casters = [], None
    if args.game:
        from wc3files.casc import CascStorage
        if not os.path.isdir(os.path.join(args.game, 'Data')):
            raise ValueError(f'{args.game} is not a CASC install (Warcraft III 1.32 or '
                             f'later); classic installs are not read for this yet')
        doo_key = _find(files, 'war3map.doo')
        if doo_key is not None:
            doo = MapDoodadsFile()
            doo.read(files[doo_key])
            doodads = doo.get_data()['doodads']
        object_data = None
        w3d_key = _find(files, 'war3map.w3d')
        if w3d_key is not None:
            from wc3files.w3o import ObjectFile
            with _tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'war3map.w3d')
                with open(path, 'wb') as handle:
                    handle.write(files[w3d_key])
                objects = ObjectFile()
                objects.read(path)
                object_data = objects.get_data().get('w3d')
        casters = game_casters(CascStorage().open(args.game), files, object_data)
    else:
        print('note: without --game only the terrain casts shadows; doodads need the '
              "game's doodad table and models", file=sys.stderr)
    shadow = compute_shadow_map(terrain.get_data(), doodads, casters,
                                azimuth=args.azimuth, elevation=args.elevation)
    cells = shadow.get_data()['cells']
    if casters is not None:
        for what, count in sorted(casters.counts.items()):
            print(f'{count} doodads: {what}')
        for path in sorted(casters.missing_models):
            print(f'model not found: {path}')
    files[_find(files, 'war3map.shd') or 'war3map.shd'] = shadow.to_bytes()
    _write_map(files, args.output)
    print(f'{sum(1 for c in cells if c)} of {len(cells)} cells in shadow; wrote {args.output}')
    return 0


def _custom_bases(files, names):
    """{custom id: base id} from the map's object data files `names`."""
    import tempfile as _tempfile
    from wc3files.w3o import ObjectFile
    from wc3files.w3x.modes import _find
    out = {}
    for name in names:
        key = _find(files, name)
        if key is None:
            continue
        with _tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            with open(path, 'wb') as handle:
                handle.write(files[key])
            objects = ObjectFile()
            try:
                objects.read(path)
            except Exception:
                continue
            for kind in (objects.get_data() or {}).values():
                for custom, entry in ((kind or {}).get('custom') or {}).items():
                    if entry.get('ID'):
                        out[custom] = entry['ID']
    return out


def _cmd_minimap(args):
    """war3mapMap.blp rendered from the terrain (#209)."""
    from wc3files.casc import CascStorage
    from wc3files.doo import MapDoodadsFile
    from wc3files.w3e import TerrainFile
    from wc3files.w3e.minimap import (GameMinimapColours, minimap_blp, object_colours,
                                      render_minimap)
    from wc3files.w3i import W3iFile
    from wc3files.w3x.modes import archive_files, _find, _write_map
    files = archive_files(_open_map(args.input, args.deep))
    parts = {}
    for name in ('war3map.w3e', 'war3map.w3i'):
        key = _find(files, name)
        if key is None:
            raise ValueError(f'{args.input} has no {name}')
        parts[name] = files[key]
    terrain = TerrainFile()
    terrain.read(parts['war3map.w3e'])
    info = W3iFile()
    info.read(parts['war3map.w3i'])
    d = terrain.get_data()
    doodads = []
    doo_key = _find(files, 'war3map.doo')
    if doo_key is not None:
        doo = MapDoodadsFile()
        doo.read(files[doo_key])
        doodads = doo.get_data()['doodads']
    if not os.path.isdir(os.path.join(args.game, 'Data')):
        raise ValueError(f'{args.game} is not a CASC install (Warcraft III 1.32 or later)')
    game = GameMinimapColours(CascStorage().open(args.game))
    colours = object_colours()
    for custom, base in _custom_bases(files, ('war3map.w3b', 'war3map.w3d')).items():
        if base in colours and custom not in colours:
            colours[custom] = colours[base]
    rgba = render_minimap(d, info.get_data(), doodads, game.tile_colours(d['ground_tilesets']),
                          game.water_tint(d['main_tileset']), colours)
    for code in sorted(game.missing):
        print(f'no texture for ground tileset {code}; drawn grey')
    lower = args.output.lower()
    if lower.endswith('.png'):
        from wc3files.img import write_png
        data = write_png(256, 256, rgba)
    else:
        data = minimap_blp(rgba, quality=args.quality)
    if lower.endswith(_MAP_EXTENSIONS):
        files[_find(files, 'war3mapMap.blp') or 'war3mapMap.blp'] = data
        _write_map(files, args.output)
    else:
        with open(args.output, 'wb') as handle:
            handle.write(data)
    print(f'Wrote {args.output}')
    return 0


def _cmd_map_preview(args):
    """war3mapPreview.tga from the minimap and its icons, or a picture (#210)."""
    from wc3files.w3x.modes import archive_files, _find, _write_map
    from wc3files.w3x.preview import SIZE, draw_icons, fit, game_icons, preview_tga
    files = archive_files(_open_map(args.input, args.deep))
    storage = None
    if args.game:
        from wc3files.casc import CascStorage
        if not os.path.isdir(os.path.join(args.game, 'Data')):
            raise ValueError(f'{args.game} is not a CASC install (Warcraft III 1.32 or later)')
        storage = CascStorage().open(args.game)
    if args.image:
        width, height, rgba = _read_any_image(args.image)
        rgba = fit(rgba, width, height)
        source = args.image
    else:
        key = _find(files, 'war3mapMap.blp')
        if key is not None:
            minimap = BlpFile()
            minimap.read(files[key])
            rgba = fit(minimap.get_rgba(0), minimap.width, minimap.height)
            source = f'{args.input}:{key}'
        elif storage is not None:
            from wc3files.doo import MapDoodadsFile
            from wc3files.w3e import TerrainFile
            from wc3files.w3e.minimap import (GameMinimapColours, object_colours,
                                              render_minimap)
            from wc3files.w3i import W3iFile
            terrain, info = TerrainFile(), W3iFile()
            terrain.read(files[_find(files, 'war3map.w3e')])
            info.read(files[_find(files, 'war3map.w3i')])
            doodads = []
            if _find(files, 'war3map.doo'):
                doo = MapDoodadsFile()
                doo.read(files[_find(files, 'war3map.doo')])
                doodads = doo.get_data()['doodads']
            d = terrain.get_data()
            colours = GameMinimapColours(storage)
            rgba = render_minimap(d, info.get_data(), doodads,
                                  colours.tile_colours(d['ground_tilesets']),
                                  colours.water_tint(d['main_tileset']), object_colours())
            source = 'a minimap rendered from the terrain'
        else:
            raise ValueError(f'{args.input} has no war3mapMap.blp: give --game to render one, '
                             f'or --image')
        mmp_key = _find(files, 'war3map.mmp')
        if mmp_key is not None and not args.no_icons:
            from wc3files.mmp import PreviewIconsFile
            icons_file = PreviewIconsFile()
            icons_file.read(files[mmp_key])
            icons = list(icons_file.get_data().values())
            gold, neutral = game_icons(storage) if storage is not None else (None, None)
            rgba = draw_icons(rgba, icons, gold, neutral)
            source += f' with {len(icons)} icons'
    lower = args.output.lower()
    if lower.endswith('.png'):
        data = write_png(SIZE, SIZE, rgba)
    else:
        data = preview_tga(rgba)
    if lower.endswith(_MAP_EXTENSIONS):
        files[_find(files, 'war3mapPreview.tga') or 'war3mapPreview.tga'] = data
        _write_map(files, args.output)
    else:
        with open(args.output, 'wb') as handle:
            handle.write(data)
    print(f'Wrote {args.output} from {source}')
    return 0


def _cmd_loading_screen(args):
    """A custom loading screen from one picture (#211)."""
    from wc3files.w3x.loading_screen import add_loading_screen
    from wc3files.w3x.modes import archive_files, _write_map
    files = archive_files(_open_map(args.input, args.deep))
    width, height, rgba = _read_any_image(args.image)
    files, report = add_loading_screen(files, rgba, width, height, layout=args.layout,
                                       prefix=args.prefix, quality=args.quality)
    for label in ('added', 'replaced'):
        for name in report[label]:
            print(f'{label}: {name}')
    _write_map(files, args.output)
    print(f"Wrote {args.output}; its loading screen is {report['model']} ({args.layout})")
    return 0


def _cmd_placed_code(args):
    """A map's placed objects as JASS or Lua (#251)."""
    from wc3files.doo import MapDoodadsFile, MapUnitsFile
    from wc3files.w3r import RegionFile
    from wc3files.w3x.modes import archive_files, _find
    from wc3files.w3x.placed_code import placed_code
    files = archive_files(_open_map(args.input, args.deep))
    parts = set(args.parts.split(',')) if args.parts else \
        {'units', 'destructables', 'regions'}
    unknown = parts - {'units', 'destructables', 'regions'}
    if unknown:
        raise ValueError(f'unknown parts {sorted(unknown)}: units, destructables, regions')
    units, doodads, regions = [], [], []
    key = _find(files, 'war3mapUnits.doo')
    if 'units' in parts and key is not None:
        reader = MapUnitsFile()
        reader.read(files[key])
        units = reader.get_data()['units']
    key = _find(files, 'war3map.doo')
    if 'destructables' in parts and key is not None:
        reader = MapDoodadsFile()
        reader.read(files[key])
        doodads = reader.get_data()['doodads']
    key = _find(files, 'war3map.w3r')
    if 'regions' in parts and key is not None:
        reader = RegionFile()
        reader.read(files[key])
        regions = list(reader.get_data().values())
    bases = _custom_bases(files, ('war3map.w3u', 'war3map.w3t', 'war3map.w3b'))
    code, report = placed_code(units, doodads, regions,
                               language='lua' if args.lua else 'jass',
                               function=args.function, skins=not args.classic,
                               custom_bases=bases)
    _write_or_print(code, args.output)
    print(f"{report['units']} units, {report['items']} items, "
          f"{report['destructables']} destructables, {report['regions']} regions",
          file=sys.stderr)
    for what, count in sorted(report['not created'].items()):
        print(f'not created: {count} {what}', file=sys.stderr)
    return 0


def _cmd_map_description(args):
    """A Hive Workshop or GitHub description of a map (#253)."""
    from wc3files.w3x.description import TEMPLATES, map_summary, render
    from wc3files.w3x.modes import archive_files
    if args.print_template:
        sys.stdout.write(TEMPLATES[args.format] + '\n')
        return 0
    if not args.input:
        raise ValueError('map-description: give the map')
    template = None
    if args.template:
        with open(args.template, encoding='utf-8') as handle:
            template = handle.read()
    files = archive_files(_open_map(args.input, args.deep))
    text = render(map_summary(files), args.format, template)
    if args.output:
        with open(args.output, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(f'Wrote {args.output}')
    else:
        sys.stdout.buffer.write(text.encode('utf-8'))
    return 0


def _cmd_trigger_ui(args):
    """TriggerData.txt <-> YDWE ui folder (wc3files.wtg.ydwe_ui)."""
    from wc3files.wtg import ydwe_ui
    *sources, target = args.inputs
    if not sources:
        raise ValueError('give the input(s) and then the output')
    if all(os.path.isdir(src) for src in sources):
        merged = ydwe_ui.merge(*(ydwe_ui.read_ui(src, args.lang) for src in sources))
        data, strings = ydwe_ui.write_trigger_data(merged)
        os.makedirs(target, exist_ok=True)
        for name, text in (('TriggerData.txt', data), ('TriggerStrings.txt', strings)):
            with open(os.path.join(target, name), 'w', encoding='utf-8', newline='\r\n') as h:
                h.write(text)
        count = sum(len(v) for v in merged.functions.values())
        print(f'Wrote TriggerData.txt and TriggerStrings.txt to {target} ({count} functions)')
        return 0
    if len(sources) > 2 or not all(os.path.isfile(src) for src in sources):
        raise ValueError('give TriggerData.txt [TriggerStrings.txt] or ui folders, '
                         'then the output')
    texts = []
    for src in sources:
        with open(src, encoding='utf-8-sig', errors='replace') as h:
            texts.append(h.read())
    ui = ydwe_ui.read_trigger_data(texts[0], texts[1] if len(texts) > 1 else '')
    ydwe_ui.write_ui(ui, target)
    count = sum(len(v) for v in ui.functions.values())
    print(f'Wrote the ui folder {target} ({count} functions)')
    return 0


def _cmd_lni(args):
    """Map -> Lni folder, or Lni folder -> map (by what the input is)."""
    from collections import Counter
    from wc3files.lni import map_to_lni, lni_to_map, MANIFEST
    if os.path.isdir(args.input):
        if not os.path.isfile(os.path.join(args.input, MANIFEST)):
            raise ValueError(f'{args.input} is not an Lni folder (no {MANIFEST})')
        files = lni_to_map(args.input, args.output)
        print(f'Built {args.output} ({len(files)} files)')
        return 0
    archive = _open_map(args.input, args.deep)
    manifest = map_to_lni(args.input, args.output, files=archive.files or {})
    kinds = Counter(entry['kind'] for entry in manifest['files'])
    print(f"Wrote {args.output}: {kinds.get('lni', 0)} object file(s) as Lni, "
          f"{kinds.get('json', 0)} as JSON, {kinds.get('raw', 0)} as they are")
    return 0


def _cmd_run_map(args):
    """Run a map script in wc3files.scripts.wc3_runtime and report what failed."""
    from wc3files.scripts.wc3_runtime import MapRuntime, load_map_script
    source, chunkname, natives = load_map_script(args.input, deep=args.deep)
    runtime = MapRuntime(players=args.players, max_steps=args.max_steps or None)
    runtime.declare_natives(natives)
    report = runtime.run(source, chunkname, seconds=args.seconds)
    if args.messages:
        for text in report.messages:
            print(text)
    for error in report.errors:
        print(f'error: {error}')
    for warning in report.warnings:
        print(f'warning: {warning}')
    print(f'{len(report.errors)} error(s), {len(report.warnings)} warning(s), '
          f'{report.callbacks} callback(s), {report.game_time:.2f} s of game time',
          file=sys.stderr)
    return 1 if report.errors else 0


def _cmd_vjass(args):
    """Compile vJass/Zinc to JASS - a script, several, or a whole map.

    A map (.w3x/.w3m, or a folder map) has its war3map.j compiled and is
    written back with the result, as the World Editor does when saving with
    JassHelper on. Anything else is read as source; several inputs compile as
    one script, in the order given.
    """
    from wc3files.vjass import VJassCompiler, CompileOptions, VJassError
    from wc3files.vjass.preprocessor import Preprocessor, _read_text
    from wc3files.vjass.zinc import translate_zinc

    overrides = dict(debug=args.debug, optimize=args.optimize,
                     strict=args.strict,
                     auto_method_evaluate=not args.force_method_evaluate,
                     implicit_this=not args.no_implicit_this)
    if args.max_array_size:
        overrides['max_array_size'] = args.max_array_size
    if args.common:
        overrides['common_j'] = args.common
    if args.blizzard:
        overrides['blizzard_j'] = args.blizzard
    if args.conf:
        options = CompileOptions.from_conf(args.conf, **overrides)
    else:
        options = CompileOptions(**overrides)
    options.lookup_folders.extend(args.lookup or [])
    for define in args.define or []:
        name, _, value = define.partition('=')
        options.constant_overrides[name] = value.lower() not in ('0', 'false',
                                                                  'no')
    compiler = VJassCompiler(options)

    inputs = args.input
    first = inputs[0]
    is_map = len(inputs) == 1 and (
        first.lower().endswith(('.w3x', '.w3m')) or
        (os.path.isdir(first) and os.path.isfile(os.path.join(first,
                                                               'war3map.j'))))

    if args.preprocess:
        # Show what the parser sees: imports, macros, debug lines and Zinc
        # (translated to vJass) all applied.
        if len(inputs) != 1 or is_map:
            raise ValueError('--preprocess takes one source file')
        pre = Preprocessor(options).run(_read_text(first), os.path.abspath(first))
        lines = []
        for kind, segment in pre.segments:
            if kind == 'zinc':
                segment = translate_zinc(segment, options)
            lines.extend(line.text for line in segment)
        text = '\n'.join(lines) + '\n'
        _write_or_print(text, args.output)
        return

    mode = args.externals
    try:
        if is_map:
            result = compiler.compile_map(first, args.output,
                                          externals=(mode == 'run'),
                                          workspace=args.workspace)
            target = args.output or first
        elif len(inputs) == 1:
            result = compiler.compile_file(first)
            target = None
        else:
            result = compiler.compile_files(inputs)
            target = None
    except VJassError as exc:
        raise ValueError(f'vJass: {exc}') from None

    shown = 0
    for warning in result.warnings:
        if args.quiet:
            break
        if shown == args.max_warnings:
            print(f'... and {len(result.warnings) - shown} more warnings',
                  file=sys.stderr)
            break
        print(warning, file=sys.stderr)
        shown += 1
    if mode == 'note':
        for location, tool, arguments in result.externals:
            if not args.quiet:
                print(f'{location}: note: external tool {tool!r} not run '
                      '(use --externals check or run)', file=sys.stderr)
    elif result.externals:
        done = getattr(result, 'externals_result', None)
        if done is None:
            map_files = None
            if is_map:
                from wc3files.vjass.api import _read_map_files
                map_files = _read_map_files(first)
            done = result.run_externals(map_files, workspace=args.workspace,
                                        stop_on_error=(mode == 'run'))
        _report_externals(done, args.quiet)
        if done.errors:
            raise ValueError(f'{len(done.errors)} external tool call(s) failed')
        if mode == 'run' and not is_map:
            folder = args.externals_dir or (os.path.dirname(os.path.abspath(args.output))
                                            if args.output and args.output != '-'
                                            else os.getcwd())
            for name, data in done.changed_files().items():
                destination = os.path.join(folder, *name.split('\\'))
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with open(destination, 'wb') as handle:
                    handle.write(data)
                print(f'Wrote {destination}', file=sys.stderr)

    if args.check:
        issues = [f for f in result.validate() if f['severity'] == 'issue']
        for finding in issues:
            # back to the vJass source when the compiler knows where it was
            loc = result.source_location(finding['line']) if finding.get('line') else None
            if loc is not None:
                where = f'{loc}: '
            elif finding.get('line'):
                where = f"compiled line {finding['line']}: "
            else:
                where = ''
            print(f"check: {where}{finding['message']}", file=sys.stderr)
        if issues:
            raise ValueError(f'{len(issues)} issue(s) in the compiled script')

    if target is not None:
        print(f'Compiled {first} -> {target}')
        return
    _write_or_print(result.jass, args.output)


def _report_externals(done, quiet=False):
    """Print what running the external tool calls did."""
    for error in done.errors:
        print(f'error: {error}', file=sys.stderr)
        for position, function in error.traceback[1:6]:
            print(f'    in {function} at {position}', file=sys.stderr)
    if quiet:
        return
    if done.output.strip():
        for line in done.output.rstrip('\n').split('\n'):
            print(f'lua: {line}', file=sys.stderr)
    for operation, what in done.denied:
        print(f'note: sandbox refused to {operation} {what}', file=sys.stderr)
    ran = sum(1 for _, status in done.ran if status == 'ok')
    skipped = sum(1 for _, status in done.ran if status == 'skipped')
    summary = f'externals: {ran} ran, {len(done.errors)} failed'
    if skipped:
        summary += f', {skipped} skipped'
    if done.changed:
        summary += '; changed ' + ', '.join(done.changed)
    print(summary, file=sys.stderr)


def _cmd_lua(args):
    """Run a Lua script: plain Lua in a sandbox, or with the functions of a
    Grim Extension Pack tool (ObjectMerger, ...) against a map."""
    from wc3files.scripts.lua_vm import LuaVM, LuaError, run_with_stack
    from wc3files.scripts.lua_stdlib import LuaHost, LuaExit

    with open(args.script, 'rb') as handle:
        source = handle.read().decode('utf-8', 'replace')
    if args.tool is None:
        root = os.path.abspath(args.root or os.path.dirname(os.path.abspath(args.script)))
        host = LuaHost(root=root, writable=args.allow_write, output=sys.stdout.write)
        vm = LuaVM(version=args.lua_version, host=host,
                   max_steps=args.max_steps or None)
        try:
            run_with_stack(vm.execute, source, args.script, None, args.args)
        except LuaError as error:
            sys.stdout.flush()
            print(f'lua: {error}', file=sys.stderr)
            for position, function in error.traceback[1:6]:
                print(f'    in {function} at {position}', file=sys.stderr)
            raise SystemExit(1)
        except LuaExit as exit_:
            raise SystemExit(exit_.code)
        for operation, what in host.denied:
            print(f'note: sandbox refused to {operation} {what}', file=sys.stderr)
        return

    from wc3files.grimex import ExternalRunner
    map_files = None
    if args.map:
        from wc3files.vjass.api import _read_map_files
        map_files = _read_map_files(args.map)
    with ExternalRunner(map_files, workspace=args.workspace,
                        lookup_folders=[os.path.dirname(os.path.abspath(args.script))]
                        ) as runner:
        done = runner.run_script(source, tool=args.tool, chunkname=args.script)
    _report_externals(done)
    if done.errors:
        raise SystemExit(1)
    if not done.changed:
        return
    if args.output is None:
        if args.map is None:
            return
        args.output = args.map
    if args.output.lower().endswith(('.w3x', '.w3m')):
        from wc3files.w3x import W3xFile
        W3xFile().build_from_files(done.files, args.output)
        print(f'Wrote {args.output}')
        return
    for name, data in done.changed_files().items():
        destination = os.path.join(args.output, *name.split('\\'))
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, 'wb') as handle:
            handle.write(data)
        print(f'Wrote {destination}')


def _write_or_print(text, output):
    if output and output != '-':
        with open(output, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        print(f'Wrote {output}')
    else:
        sys.stdout.write(text)


def _cmd_trigger_text(args):
    """Render a war3map.wtg as the text the World Editor would show."""
    from wc3files.wtg import MapTriggersFile
    from wc3files.wtg.trigger_text import TriggerText, describe_trigger

    triggers_file = MapTriggersFile()
    with _member_or_file(args.input, ('war3map.wtg',), args.deep) as (path, _label):
        triggers_file.read(path)
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
    from wc3files.wtg.from_script import reconstruct_from_jass, object_ids_from_files

    object_ids = None
    scripts = ('war3map.j', 'scripts\\war3map.j')
    if args.input.lower().endswith(_MAP_EXTENSIONS):
        # A map gives its object data too: its custom units and upgrades
        # tell SetPlayerUnitMaxAllowed from SetPlayerTechMaxAllowedSwap.
        # One read of just these files; the map used to be read whole, twice.
        files = _open_map(args.input, args.deep,
                          members=scripts + ('war3map.w3u', 'war3map.w3q')).files
        object_ids = object_ids_from_files(files)
        found = {name.lower(): data for name, data in files.items() if data is not None}
        data = next((found[name] for name in scripts if name in found), None)
        if data is None:
            raise ValueError(f'{args.input} has no {" or ".join(scripts)}')
        # Decoded as the file read below is: bare CR runs in real map
        # scripts otherwise wreck every line number.
        source = bytes(data).decode('utf-8', errors='replace')
    else:
        # newline='' for the same reason ScriptFile.read uses it: bare CR
        # runs in real map scripts otherwise wreck every line number.
        with open(args.input, encoding='utf-8', errors='replace', newline='') as handle:
            source = handle.read()
    result = reconstruct_from_jass(source, object_ids=object_ids)

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


def _cmd_ui_build(args):
    """Write a UI layout's frames as JASS, Lua or .fdf, or add them to a map
    as a custom-text trigger (#172)."""
    from wc3files import ui

    if args.input.lower().endswith('.fdf'):
        layout, left_out = ui.from_fdf(args.input)
        if left_out:
            print(f"note: left out of the layout: {', '.join(left_out)}", file=sys.stderr)
    else:
        with open(args.input, encoding='utf-8') as handle:
            layout = ui.Layout.from_json(handle.read())
    for warning in layout.check():
        print(f'warning: {warning}', file=sys.stderr)

    wrote = False
    for path, make in ((args.jass, ui.to_jass), (args.lua, ui.to_lua)):
        if path:
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(make(layout, function=args.function, toc=args.toc))
            print(f'Wrote {path}', file=sys.stderr)
            wrote = True
    if args.fdf:
        fdf, warnings = ui.to_fdf(layout)
        for warning in warnings:
            print(f'warning: {warning}', file=sys.stderr)
        fdf.write(args.fdf)
        print(f'Wrote {args.fdf}', file=sys.stderr)
        wrote = True
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            handle.write(layout.to_json() + '\n')
        print(f'Wrote {args.json}', file=sys.stderr)
        wrote = True
    if args.map:
        if not args.out:
            raise ValueError('ui-build --map needs --out, the map to write')
        _ui_into_map(layout, args)
        wrote = True
    if not wrote:
        sys.stdout.write(ui.to_jass(layout, function=args.function, toc=args.toc))


def _ui_into_map(layout, args):
    from wc3files import ui
    from wc3files.w3i import W3iFile
    from wc3files.wtg import MapTriggersFile, MapCustomTextTriggersFile
    from wc3files.w3x.w3x_file import read_map

    archive = read_map(args.map)
    files = archive.files
    found = {name.lower(): name for name, data in files.items() if data is not None}
    for needed in ('war3map.wtg', 'war3map.wct'):
        if needed not in found:
            raise ValueError(f'{args.map} has no {needed}, so there is no trigger editor '
                             f'data to add to')
    language = 'jass'
    if 'war3map.w3i' in found:
        info = W3iFile()
        info.read(files[found['war3map.w3i']])
        language = 'lua' if info.get_data().get('script_type') == 'lua' else 'jass'
    triggers = MapTriggersFile()
    triggers.read(files[found['war3map.wtg']])
    texts = MapCustomTextTriggersFile()
    texts.read(files[found['war3map.wct']])
    wtg, wct = triggers.get_data(), texts.get_data()
    ui.add_ui_trigger(wtg, wct, layout, trigger_name=args.trigger_name,
                      language=language, function=args.function, toc=args.toc)
    files[found['war3map.wtg']] = triggers.import_data(wtg).to_bytes()
    files[found['war3map.wct']] = texts.import_data(wct).to_bytes()
    W3xFile().build_from_files(files, args.out)
    print(f"Wrote {args.out} (trigger {args.trigger_name!r}, {language}); it runs once the "
          f"map is saved in the World Editor", file=sys.stderr)


def _clock(ms):
    seconds = int((ms or 0) // 1000)
    return f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def _cmd_replay_info(args):
    """Summarize a replay: game, players, chat, leaves and W3MMD results."""
    from wc3files import ReplayFile
    from wc3files.w3g.summary import summarize

    replay = ReplayFile()
    replay.read(args.replay)
    summary = summarize(replay.get_data(), chat=not args.no_chat)
    if args.json:
        _write_json(summary, args.out)
        return

    print(f"game     : {summary['game_name']}")
    print(f"map      : {summary['map']}")
    print(f"host     : {summary['host']}")
    length = _clock(summary['length_ms'])
    if summary['unfinished']:
        length += ' (unfinished replay: up to the last recorded time slot)'
    print(f"length   : {length}")
    print(f"version  : {summary['version']} build {summary['build']}"
          + (' (Reforged)' if summary['reforged'] else ''))
    print()
    print(f"{'slot':>4}  {'player':28} {'team':>4}  {'colour':11} {'race':10} handicap")
    for player in summary['players']:
        team = 'obs' if player['observer'] else str(player['team'] + 1)
        print(f"{player['slot'] + 1:>4}  {str(player['name'])[:28]:28} {team:>4}  "
              f"{str(player['color_name'])[:11]:11} {str(player['race'])[:10]:10} "
              f"{player['handicap']}%" + ('  host' if player['host'] else ''))
    if summary['leaves']:
        print()
        print('left:')
        for leave in summary['leaves']:
            print(f"  {_clock(leave['time_ms'])}  {leave['name']}"
                  f"  ({leave['reason'] or 'reason ' + str(leave['reason_code'])}, "
                  f"result 0x{leave['result_code']:02X})")
    if 'w3mmd' in summary:
        mmd = summary['w3mmd']
        print()
        print('W3MMD winners: ' + (', '.join(str(mmd['players'].get(str(p), mmd['players'].get(p, p)))
                                            for p in mmd['winners']) or 'none'))
    messages = summary.get('chat') or []
    if messages:
        print()
        print(f'chat ({len(messages)}):')
        for message in messages[:args.max_chat]:
            to = message['to'] if message['to'] != 'player' else f"slot {message['to_slot'] + 1}"
            print(f"  {_clock(message['time_ms'])}  [{to}] {message['name']}: {message['text']}")
        if len(messages) > args.max_chat:
            print(f'  ... {len(messages) - args.max_chat} more (--max-chat, or --json for all)')


def _cmd_map_strings(args):
    """List a map's strings by object and field, or check its tooltips."""
    from wc3files.w3x.strings import check_strings, map_strings

    files = _open_map(args.map, args.deep).files
    files = {name: data for name, data in files.items() if data is not None}
    if args.check:
        findings = check_strings(files)
        if args.json:
            _write_json(findings, args.out)
        else:
            for f in findings:
                level = f' level {f["level"]}' if f['level'] else ''
                print(f"{f['file']}: {f['id']} {f['field']}{level}: {f['severity']}: "
                      f"{f['kind']}: {f['message']}")
        issues = sum(1 for f in findings if f['severity'] == 'issue')
        print(f'{issues} issue(s), {len(findings) - issues} note(s)', file=sys.stderr)
        return 1 if issues else 0

    listing = map_strings(files)
    if args.json:
        _write_json(listing, args.out)
        return 0
    current = None
    for entry in listing['objects']:
        key = (entry['file'], entry['id'])
        if key != current:
            current = key
            origin = (f"custom, from {entry['base']}" if entry['custom']
                      else 'changed game object')
            print(f"\n{entry['file']} {entry['id']} ({origin})")
        level = f" {entry['level']}" if entry['level'] else ''
        where = f"TRIGSTR_{entry['string_id']:03d}" if entry['string_id'] is not None else 'inline'
        text = entry['text'] if entry['text'] is not None else '(missing from war3map.wts)'
        print(f"  {entry['field'] + level:18} {where:12} {text}")
    if listing['references']:
        print('\nother files that use war3map.wts:')
        for reference in listing['references']:
            ids = reference['string_ids']
            print(f"  {reference['file']}: {len(ids)} string(s)")
    print(f"\n{listing['strings']} string(s) in war3map.wts, "
          f"{len(listing['unused'])} not used by any file")
    return 0


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
    unfinished = getattr(replay, 'unfinished', False)
    if unfinished:
        # The game never wrote this replay's header (a crash or a hang),
        # so its length is the sum of the recorded time slots.
        minutes = sum(block.get('time_increment') or 0 for block in blocks) / 60000.0

    print(f"actions      : {sum(totals.values())}")
    print(f"length       : {minutes:.1f} min"
          + (' (unfinished replay: up to the last recorded time slot)' if unfinished else ''))
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
                  f"('note' = usually intentional, e.g. a value below a declared minVal "
                  f"of 0; 'issue' = more likely a real problem, e.g. an unrecognized enum "
                  f"value; what is checked: {_DOCS}#checking-object-data):", file=sys.stderr)
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
          f"icons, models, tooltips and some editor-only fields have no override "
          f"table): {sorted(stuck_field_names)}")


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
            print(f"  {len(report[key])} {label} - see {_DOCS}#override-tables",
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


def _write_any_image(path, width, height, rgba, fourcc=b'DXT5', mipmaps=True, alpha='auto',
                     blp_jpeg=None):
    """Write (width, height, rgba_bytes) to a .blp/.dds/.tga/.png/.bmp/.tiff file,
    format chosen by the output path's extension. `blp_jpeg`, a quality from 1
    to 100, writes a .blp as JPEG rather than paletted (#261)."""
    lower = path.lower()
    if alpha in ('yes', 'no'):
        alpha = alpha == 'yes'
    if lower.endswith('.blp'):
        if blp_jpeg is not None:
            BlpFile.from_rgba(width, height, rgba, alpha=alpha, compression='jpeg',
                              quality=blp_jpeg, mipmaps=mipmaps).write(path)
        else:
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
    _write_any_image(args.output, w, h, rgba, fourcc=fourcc, mipmaps=args.mipmaps, alpha=args.alpha,
                     blp_jpeg=args.blp_jpeg)
    print(f"Wrote {args.output} ({w}x{h})")


def _cmd_make_icon(args):
    w, h, rgba = _read_any_image(args.source)
    size = args.size
    rgba = resize_box(rgba, w, h, size, size) if (w, h) != (size, size) else rgba

    if args.disabled_output:
        disabled = make_disabled_variant(rgba, size, size, darken=args.darken,
                                          desaturate=args.desaturate)
        _write_any_image(args.disabled_output, size, size, disabled, alpha=args.alpha,
                         blp_jpeg=args.blp_jpeg)
        print(f"Wrote {args.disabled_output} ({size}x{size}, disabled variant)")

    border = args.border or (border_path('active', reforged=(args.bundled_border == 'reforged'))
                              if args.bundled_border else None)
    if border:
        bw, bh, border_rgba = _read_any_image(border)
        rgba = composite_over(rgba, size, size, border_rgba, bw, bh)

    _write_any_image(args.output, size, size, rgba, alpha=args.alpha, blp_jpeg=args.blp_jpeg)
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
                _write_any_image(out_path, w, h, rgba, fourcc=fourcc, mipmaps=args.mipmaps,
                                 blp_jpeg=args.blp_jpeg)
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
        _write_any_image(out_path, size, size, icon, alpha=args.alpha, blp_jpeg=args.blp_jpeg)
        written.append(out_path)

        if spec['disabled_prefix']:
            dis_dir = os.path.join(rt_dir, _DISABLED_DIR)
            os.makedirs(dis_dir, exist_ok=True)
            dis_path = os.path.join(dis_dir, f"{spec['disabled_prefix']}{args.name}{ext}")
            _write_any_image(dis_path, size, size, disabled, alpha=args.alpha,
                             blp_jpeg=args.blp_jpeg)
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


def _format_and_paths(command, values, name_from):
    """Split `[format] path [path]`: the format is optional when the file
    named by `values[name_from]` (once the format is taken off) says it."""
    values = list(values)
    if values and values[0] in FORMATS:
        fmt = values.pop(0)
    else:
        named = values[name_from] if len(values) > name_from else None
        fmt = format_for_name(named) if named else None
        if fmt is None:
            raise ValueError(
                f'{command}: give the format first ({", ".join(sorted(FORMATS))}); '
                f'it cannot be told from the name {named!r}')
    return fmt, values


def _cmd_dump(args):
    fmt, paths = _format_and_paths('dump', args.paths, 0)
    if not 1 <= len(paths) <= 2:
        raise ValueError('dump: give [format] input [output]')
    source, output = paths[0], (paths[1] if len(paths) > 1 else '-')
    data = FORMATS[fmt].dump(source, options={'width': getattr(args, 'width', None),
                                              'height': getattr(args, 'height', None)})
    _write_json(data, output)
    if output != '-':
        print(f"Wrote {output}")


def _cmd_textconv(args):
    from .textconv import textconv
    text = textconv(args.path)
    out = sys.stdout.buffer
    out.write(text.encode('utf-8'))
    out.flush()


def _cmd_build(args):
    fmt, paths = _format_and_paths('build', args.paths, 1)
    if len(paths) != 2:
        raise ValueError('build: give [format] input.json output')
    data = _read_json(paths[0])
    FORMATS[fmt].build(data, paths[1], validate=args.validate)
    print(f"Wrote {paths[1]}")


def _format_help():
    return '; '.join(f'{name}: {FORMAT_FILES.get(name, name)}' for name in sorted(FORMATS))


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wc3files',
        description='Read/write Warcraft III map, campaign, and replay files.',
    )
    parser.add_argument('--game-data', metavar='PROFILE', default=None,
                        help=('Read game tables (object data, natives, GUI triggers) from '
                              'a profile made by gamedata-extract instead of the bundled '
                              'Reforged ones.'))
    parser.add_argument('--version', action='version', version=f'wc3files {__version__}')
    parser.add_argument('-v', '--verbose', action='store_true',
                         help='Show this package\'s own debug/info logging (quiet by default).')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_unpack = subparsers.add_parser('unpack', help='Extract a .w3x/.w3m/.w3n archive to loose files.')
    p_unpack.add_argument('src', help='Path to the .w3x/.w3m/.w3n archive.')
    p_unpack.add_argument('out_dir', help='Directory to extract into (files land under out_dir/<map name>/).')
    p_unpack.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
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
        "the map's own diff. Off by default since it noticeably widens every row. A base "
        "table's column can sit beside the override's own name for the same field "
        "(DataA1 next to Data1); see " + _DOCS + "#base-game-data."))
    p_obj_slk.add_argument('--validate', action='store_true', help=(
        "Report (to stderr, non-blocking) fields whose value violates the bundled Blizzard "
        "metadata's declared range/emptiness constraints. Only checks numeric range/sign and "
        "emptiness for reliably-typed fields, and enum values. A finding can be intentional: "
        "maxVal is the World Editor's slider limit, which maps often pass on purpose. See "
        + _DOCS + "#checking-object-data."))
    p_obj_slk.set_defaults(func=_cmd_obj_to_slk)

    p_obj_override = subparsers.add_parser('obj-to-override', help=(
        "Split an object file into literal, game-loadable default-table overrides "
        "(Units\\UnitData.slk, Units\\UnitBalance.slk, ...) plus whatever genuinely can't "
        "move there (icons/models/tooltips and a few other World-Editor-UI-only fields - "
        "never silently dropped). This is the real technique some maps use to end up with "
        "no war3map.w3u/w3t/w3h/w3q at all. See " + _DOCS + "#override-tables."))
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
        "inspected/edited normally again. Not everything is recoverable with certainty "
        "(ambiguous shared field names, custom objects with no remaining template hint, "
        "unconfirmed binary storage types); each is reported. See " + _DOCS + "#override-tables."))
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
        "Decode an image (.blp, .dds, .tga, .bmp, .tiff or .png) to a .png - the format "
        "is detected from the file's own magic bytes, not its extension. DDS covers DXT1 "
        "to DXT5, BC4, BC5 and uncompressed images. Always decodes mip level 0 (full "
        "resolution)."))
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
    p_img_convert.add_argument('--blp-jpeg', type=int, metavar='QUALITY', default=None, help=(
        "Write a .blp as JPEG at this quality (1 to 100; the game's own icons are about 70), "
        'with mipmaps, rather than paletted.'))
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
    p_make_icon.add_argument('--blp-jpeg', type=int, metavar='QUALITY', default=None, help=(
        "Write a .blp as JPEG at this quality (1 to 100; the game's own icons are about 70), "
        'with mipmaps, rather than paletted.'))
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
    p_batch.add_argument('--blp-jpeg', type=int, metavar='QUALITY', default=None, help=(
        "Write a .blp as JPEG at this quality (1 to 100; the game's own icons are about 70), "
        'with mipmaps, rather than paletted.'))
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
    p_icon_set.add_argument('--blp-jpeg', type=int, metavar='QUALITY', default=None, help=(
        "Write a .blp as JPEG at this quality (1 to 100; the game's own icons are about 70), "
        'with mipmaps, rather than paletted.'))
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

    p_dump = subparsers.add_parser(
        'dump', help='Read a single map sub-file and dump it as JSON.',
        description=('wc3files dump [format] input [output]. The format can be left out '
                     'when the file name says it (war3mapUnits.doo, war3map.doo, '
                     'war3map.w3i, ...). Formats: ' + _format_help() + '.'))
    p_dump.add_argument('paths', nargs='+', metavar='[format] input [output]',
                        help=("The format (optional), the file to read, and where to "
                              "write the JSON (default: stdout, '-')."))
    p_dump.add_argument('--width', type=int, default=None,
                        help=('Grid width, for formats that carry no header of their '
                              'own (shd). By default it is read from a war3map.wpm or '
                              'war3map.w3e next to the file.'))
    p_dump.add_argument('--height', type=int, default=None,
                        help='Grid height, as --width.')
    p_dump.set_defaults(func=_cmd_dump)

    p_textconv = subparsers.add_parser(
        'textconv', help='Print a map file as text for git diff.',
        description=('Print a map file as text that git diff can compare: the dump '
                     'JSON with one record per line, scripts and other text files as '
                     'they are, an MDX model as MDL, and any other file as its size '
                     'and SHA-256. The format comes from the file name. See '
                     + _DOCS + '#maps-in-git.'))
    p_textconv.add_argument('path', help='The file to print (git passes it).')
    p_textconv.set_defaults(func=_cmd_textconv)

    p_build = subparsers.add_parser(
        'build', help='Build a map sub-file back from a dump() JSON file.',
        description=('wc3files build [format] input.json output. The format can be left '
                     'out when the output name says it. Formats: ' + _format_help() + '.'))
    p_build.add_argument('paths', nargs='+', metavar='[format] input output',
                         help=("The format (optional), a JSON file from `dump` (or '-' for "
                               "stdin), and the file to write."))
    p_build.add_argument('--validate', action='store_true', help=(
        "obj format only (silently ignored for every other format): check every field "
        "against the bundled Blizzard metadata schema before writing, and raise instead of "
        "writing if anything fails. See " + _DOCS + "#checking-object-data."))
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

    p_replay_info = subparsers.add_parser(
        'replay-info',
        help=('Summarize a replay: game, length, players with team, colour and '
              'race, who left and when, chat with timestamps, and the W3MMD '
              'winners when the map records them.'))
    p_replay_info.add_argument('replay', help='Path to a .w3g replay.')
    p_replay_info.add_argument('--json', action='store_true',
                               help='Emit JSON instead of a readable summary.')
    p_replay_info.add_argument('--out', default='-',
                               help='Where to write JSON (default stdout).')
    p_replay_info.add_argument('--no-chat', action='store_true', help='Leave the chat out.')
    p_replay_info.add_argument('--max-chat', type=int, default=50,
                               help='How many chat messages the summary shows (default 50).')
    p_replay_info.set_defaults(func=_cmd_replay_info)

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

    p_ui_build = subparsers.add_parser(
        'ui-build',
        help=("Write the frames of a UI layout (a .json layout, or an .fdf to read "
              "one from) as the JASS or Lua that creates them, as an .fdf, or as a "
              "custom-text trigger added to a map. With no output named, the JASS "
              "goes to stdout. See " + _DOCS.replace('cli.md', 'ui.md') + "."))
    p_ui_build.add_argument('input', help='The layout: a .json file, or an .fdf.')
    p_ui_build.add_argument('--jass', metavar='OUT.j', help='Write the JASS here.')
    p_ui_build.add_argument('--lua', metavar='OUT.lua', help='Write the Lua here.')
    p_ui_build.add_argument('--fdf', metavar='OUT.fdf', help='Write frame definitions here.')
    p_ui_build.add_argument('--json', metavar='OUT.json',
                            help='Write the layout as JSON (for example one read from an .fdf).')
    p_ui_build.add_argument('--map', help='A map to add the frames to, as a trigger.')
    p_ui_build.add_argument('--out', help='The map to write, with --map.')
    p_ui_build.add_argument('--trigger-name', default='UI',
                            help="The trigger's name, with --map (default UI).")
    p_ui_build.add_argument('--function', default='CreateUI',
                            help='The name of the function that creates the frames.')
    p_ui_build.add_argument('--toc', help='A .toc the code loads first, for templates it defines.')
    p_ui_build.set_defaults(func=_cmd_ui_build)

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

    p_extents = subparsers.add_parser(
        'model-extents', help='Recompute the extents of models from their vertices.',
        description=('Set the extents of each geoset, sequence and model from the '
                     'vertices, posed by the bones at frames spread over each sequence. '
                     'A geoset counts only while its geoset animation shows it. See '
                     + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_extents)
    p_extents.add_argument('--frames', type=int, default=24,
                           help='Frames posed per sequence (default 24; 1 for the first only).')
    p_extents.add_argument('--radius', choices=('center', 'diagonal'), default='center',
                           help=("The bounds radius: the farthest vertex from the box's "
                                 'center (default, as SD models), or half the diagonal '
                                 '(as HD models).'))
    p_extents.set_defaults(func=_cmd_model_extents)

    p_transform = subparsers.add_parser(
        'model-transform', help='Scale and move whole models.',
        description=('Scale a model about its origin, then move it, in one step: vertices, '
                     'pivots, translation keys, extents, cameras, collision shapes, '
                     'particle and ribbon sizes, lights and move speeds. See '
                     + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_transform)
    p_transform.add_argument('--scale', type=float, help='The factor, as 1.5.')
    p_transform.add_argument('--translate', metavar='X,Y,Z', help='The offset, as 0,0,10.')
    p_transform.set_defaults(func=_cmd_model_transform)

    p_normals = subparsers.add_parser(
        'model-normals', help='Recompute the vertex normals of models from their faces.',
        description=("Set each vertex normal to the sum of its triangles' normals, "
                     'weighted by area. --weld also joins the vertices at one position '
                     'whose normals are within --angle degrees, as on a UV seam. See '
                     + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_normals)
    p_normals.add_argument('--weld', action='store_true',
                           help='Join vertices at one position, as across a UV seam.')
    p_normals.add_argument('--angle', type=float, default=60.0,
                           help='With --weld: the widest angle joined (default 60); '
                                'a sharper edge stays hard.')
    p_normals.set_defaults(func=_cmd_model_normals)

    p_unused = subparsers.add_parser(
        'model-remove-unused',
        help='Remove the materials, textures and texture animations nothing uses.',
        description=('Remove the materials no geoset or ribbon uses, then the textures and '
                     'texture animations no kept layer or particle emitter uses, and '
                     'renumber the references. Nothing drawn changes. See '
                     + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_unused)
    p_unused.set_defaults(func=_cmd_model_remove_unused)

    p_glow = subparsers.add_parser(
        'model-team-glow', help='Make the team glow of models transparent, or remove it.',
        description=('Find the layers that show the team glow (replaceable texture 2). '
                     'transparent sets their alpha to 0; remove takes them out, with the '
                     'geosets that drew only the glow and what nothing uses any more. See '
                     + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_glow)
    p_glow.add_argument('--mode', choices=('transparent', 'remove'), default='transparent',
                        help='transparent (default) or remove.')
    p_glow.set_defaults(func=_cmd_model_team_glow)

    p_paths = subparsers.add_parser(
        'model-texture-paths', help='Rewrite the texture paths of models.',
        description=('Rewrite texture paths by whole path, prefix or regular expression. '
                     'The rules are tried in the order given, and the first that matches '
                     'a path rewrites it. Paths compare without regard to case or to \\ '
                     'against /. See ' + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_paths)
    p_paths.add_argument('--replace', nargs=2, metavar=('OLD', 'NEW'), action=_PathRule,
                         const='exact', dest='rules', help='Replace the path OLD with NEW.')
    p_paths.add_argument('--prefix', nargs=2, metavar=('OLD', 'NEW'), action=_PathRule,
                         const='prefix', dest='rules', help='Replace the start OLD with NEW.')
    p_paths.add_argument('--pattern', nargs=2, metavar=('REGEX', 'REPLACEMENT'),
                         action=_PathRule, const='pattern', dest='rules',
                         help='Replace each match of REGEX, as "\\.blp$" ".dds".')
    p_paths.set_defaults(func=_cmd_model_texture_paths, rules=[])

    p_textures = subparsers.add_parser(
        'model-textures', help='List the textures of models, or of the models of a map.',
        description=('List each texture the models use, with the models that use it. For a '
                     'map, also the imported images nothing uses, those only other files '
                     'name, and the textures that are neither in the map nor, with --game, '
                     'in the game. See ' + _DOCS + '#model-edits.'))
    p_textures.add_argument('inputs', nargs='+',
                            help='Models, folders of models, or .w3x/.w3m maps.')
    p_textures.add_argument('--game', metavar='INSTALL',
                            help="An installed game, whose files count as found.")
    p_textures.add_argument('--json', action='store_true', help='Print the report as JSON.')
    p_textures.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_textures.set_defaults(func=_cmd_model_textures)

    p_keys = subparsers.add_parser(
        'model-keyframes', help='Remove the keyframes of models that change nothing.',
        description=('Remove a keyframe when its track gives the same values without it '
                     'at every frame: a repeated step, a point its neighbours interpolate '
                     'to, or a flat stretch of a curve. A key at the start or end of a '
                     'sequence stays. See ' + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_keys)
    p_keys.add_argument('--tolerance', type=float, default=0.0,
                        help='How far a value may move (default 0: equal values only).')
    p_keys.add_argument('--dead', action='store_true',
                        help='Also remove the keys no sequence plays.')
    p_keys.set_defaults(func=_cmd_model_keyframes)

    p_linear = subparsers.add_parser(
        'model-linear', help='Turn the curved tracks of models into linear ones (lossy).',
        description=('Turn Hermite and Bezier tracks into linear ones and drop their '
                     'tangents, which halves their size. Lossy: the track then moves in '
                     'straight lines. --tolerance adds keys where the line moves away from '
                     'the curve by more than that. See ' + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_linear)
    p_linear.add_argument('--tolerance', type=float, default=None,
                          help='Add keys until the line is this close to the curve.')
    p_linear.set_defaults(func=_cmd_model_linear)

    p_rmseq = subparsers.add_parser(
        'model-remove-sequences', help='Remove sequences from models by name.',
        description=('Remove the named sequences, with their geoset extents, the keys no '
                     'kept sequence plays and their event frames. --pack moves the kept '
                     'sequences up in time. See ' + _DOCS + '#model-edits.'))
    _add_model_batch_args(p_rmseq)
    p_rmseq.add_argument('--sequence', action='append', required=True, metavar='NAME',
                         help='A sequence to remove, without regard to case; repeat it.')
    p_rmseq.add_argument('--pack', action='store_true',
                         help='Move the kept sequences up, with the smallest gap the model had.')
    p_rmseq.set_defaults(func=_cmd_model_remove_sequences)

    p_xseq = subparsers.add_parser(
        'model-transfer-sequences', help='Copy sequences from one model into others.',
        description=('Copy sequences of SOURCE into each target model, matching bones and '
                     'other nodes by name, after the target\'s last sequence. The nodes '
                     'that do not match are reported. See ' + _DOCS + '#model-edits.'))
    p_xseq.add_argument('source', help='The model to copy the sequences from.')
    _add_model_batch_args(p_xseq)
    p_xseq.add_argument('--sequence', action='append', metavar='NAME',
                        help='A sequence to copy (repeat it); all by default.')
    p_xseq.add_argument('--gap', type=int, default=None,
                        help="Milliseconds between sequences (default: the target's smallest gap).")
    p_xseq.set_defaults(func=_cmd_model_transfer_sequences)

    p_obj = subparsers.add_parser(
        'model-obj', help='Convert a Wavefront OBJ model to MDX or MDL, or back.',
        description=('OBJ (with its MTL materials) to a static MDX or MDL model, or a '
                     'model to OBJ and MTL at a frame of a sequence. The direction comes '
                     'from the extensions. See ' + _DOCS + '#model-edits.'))
    p_obj.add_argument('input', help='The .obj, or the .mdx/.mdl model.')
    p_obj.add_argument('output', help='The .mdx/.mdl to write, or the .obj (an .mtl goes beside it).')
    p_obj.add_argument('--up', choices=('y', 'z'), default='y',
                       help="The OBJ file's up axis (default y; the game's is z).")
    p_obj.add_argument('--scale', type=float, default=1.0, help='OBJ to model: a scale factor.')
    p_obj.add_argument('--texture-dir', help='OBJ to model: the folder of the .blp textures.')
    p_obj.add_argument('--sequence', help='Model to OBJ: the sequence to pose (by name).')
    p_obj.add_argument('--frame', type=int, help="Model to OBJ: the frame (default the sequence's first).")
    p_obj.set_defaults(func=_cmd_model_obj)

    p_gltf = subparsers.add_parser(
        'model-gltf', help='Convert a glTF 2.0 model to MDX or MDL, or a model to .glb.',
        description=('A .glb or .gltf (meshes, skins, animations, base color textures) to '
                     'a version 800 MDX or MDL, its textures written beside it as BLP; or a '
                     'model to a .glb with its skin and an animation per sequence. The '
                     'direction comes from the extensions. See ' + _DOCS + '#model-edits.'))
    p_gltf.add_argument('input', help='The .glb/.gltf, or the .mdx/.mdl model.')
    p_gltf.add_argument('output', help='The .mdx/.mdl to write, or the .glb.')
    p_gltf.add_argument('--scale', type=float, default=1.0,
                        help='Game units per glTF metre (default 1; 50 to 100 is common).')
    p_gltf.add_argument('--texture-dir', help='glTF to model: the folder of the .blp textures.')
    p_gltf.add_argument('--min-weight', type=float, default=0.2,
                        help='glTF to model: the lightest bone weight kept (default 0.2).')
    p_gltf.add_argument('--textures', metavar='FOLDER',
                        help='Model to glTF: a folder holding the textures by their path, '
                             'to embed them as PNG.')
    p_gltf.add_argument('--curve-ms', type=int, default=33,
                        help='Model to glTF: how often a Hermite or Bezier track is sampled, '
                             'in milliseconds (default 33).')
    p_gltf.set_defaults(func=_cmd_model_gltf)

    p_jass = subparsers.add_parser(
        'jass', help=('Parse a JASS or Lua script and print it back; optionally '
                      'strip unreachable code and obfuscate identifiers.'))
    p_jass.add_argument('input', help='Script to read (.j or .lua), or a .w3x/.w3m map, '
                                       'whose war3map.lua or war3map.j is read.')
    p_jass.add_argument('--deep', action='store_true',
                        help=argparse.SUPPRESS)
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
    p_jass.add_argument('--to-lua', action='store_true',
                        help=('Translate a JASS script to Lua 5.3, as Reforged runs '
                              'JASS-era code (integer division, string +, arrays).'))
    p_jass.add_argument('--check', action='store_true',
                        help=('Check calls against common.j/blizzard.j (and, for Lua, '
                              'undefined globals and the Lua 5.3 library) and print '
                              'the findings instead of the script. Exits 1 on an issue. '
                              'For JASS, handle leaks are reported too, as warnings.'))
    p_jass.add_argument('--no-leaks', action='store_true',
                        help='With --check, leave out the handle leak warnings.')
    p_jass.set_defaults(func=_cmd_jass)

    p_vjass = subparsers.add_parser(
        'vjass', help=('Compile vJass and Zinc to plain JASS (what JassHelper '
                       'does): a script, several scripts, or a whole map.'))
    p_vjass.add_argument('input', nargs='+',
                         help=('Source file(s), compiled together in order; or '
                               'one map (.w3x/.w3m or a folder map), whose '
                               'war3map.j is compiled and written back.'))
    p_vjass.add_argument('-o', '--output', default=None,
                         help=("Where to write the JASS (default: stdout). For "
                               "a map: the map to write (default: in place)."))
    p_vjass.add_argument('--debug', action='store_true',
                         help='Debug build: keep debug code, DEBUG_MODE=true.')
    p_vjass.add_argument('-O', '--optimize', action='store_true',
                         help='Inline one-line functions (JassHelper\'s '
                              'optimizer).')
    p_vjass.add_argument('--common', help='common.j to compile against '
                                          '(default: the bundled copy).')
    p_vjass.add_argument('--blizzard', help='Blizzard.j to compile against '
                                            '(default: the bundled copy).')
    p_vjass.add_argument('--conf', help="A JassHelper jasshelper.conf to read "
                                        "(lookup folders and options).")
    p_vjass.add_argument('-I', '--lookup', action='append', default=None,
                         help='A folder //! import searches (repeatable).')
    p_vjass.add_argument('-D', '--define', action='append', default=None,
                         help=('Force a constant for static if, NAME or '
                               'NAME=false (repeatable).'))
    p_vjass.add_argument('--max-array-size', type=int, default=None,
                         help=('Size of one JASS array: 8191 (default, matches '
                               'JassHelper) or 32768 (game 1.29+).'))
    p_vjass.add_argument('--strict', action='store_true',
                         help='Make undeclared names an error instead of a '
                              'warning.')
    p_vjass.add_argument('--force-method-evaluate', action='store_true',
                         help=('Calling a method above its declaration is an '
                               'error ([forcemethodevaluate]).'))
    p_vjass.add_argument('--no-implicit-this', action='store_true',
                         help='Bare member names do not mean this.member.')
    p_vjass.add_argument('--check', action='store_true',
                         help='Validate the compiled JASS against the natives.')
    p_vjass.add_argument('--preprocess', action='store_true',
                         help=('Print the source after imports, text macros, '
                               'debug lines and Zinc translation, and stop.'))
    p_vjass.add_argument('-q', '--quiet', action='store_true',
                         help='Do not print warnings.')
    p_vjass.add_argument('--max-warnings', type=int, default=50,
                         help='Warnings to print before summarizing '
                              '(default: 50).')
    p_vjass.add_argument('--externals', choices=('note', 'check', 'run'),
                         default='note',
                         help=('What to do with //! external calls and external '
                               'blocks: note them (default), check them (run them '
                               'in a sandbox and report errors, changing nothing) '
                               'or run them (a map gets the object data they '
                               'make; for a script, the changed files are written '
                               'to --externals-dir).'))
    p_vjass.add_argument('--externals-dir', default=None,
                         help=('Where --externals run writes the files it changes '
                               'when the input is a script (default: next to -o).'))
    p_vjass.add_argument('--workspace', default=None,
                         help=('The external tools\' sandbox folder, kept between '
                               'runs (default: a temporary one).'))
    p_vjass.set_defaults(func=_cmd_vjass)

    p_lua = subparsers.add_parser(
        'lua', help=('Run a Lua script in a sandbox: plain Lua 5.1/5.3, or with a '
                     'Grim Extension Pack tool\'s functions (ObjectMerger, ...) '
                     'against a map.'))
    p_lua.add_argument('script', help='The .lua file to run.')
    p_lua.add_argument('args', nargs='*', help='Arguments for the script (...).')
    p_lua.add_argument('--tool', default=None,
                       choices=('ObjectMerger', 'FileImporter', 'FileExporter',
                                'ConstantMerger'),
                       help='Run it as this tool would (its functions are defined).')
    p_lua.add_argument('--map', help='The map the tool works on (.w3x/.w3m or folder).')
    p_lua.add_argument('-o', '--output',
                       help=('Where to write the result: a .w3x/.w3m map, or a '
                             'folder for the changed files (default: the --map, '
                             'in place).'))
    p_lua.add_argument('--workspace', help='The tool\'s sandbox folder.')
    p_lua.add_argument('--lua-version', choices=('5.1', '5.3'), default='5.1',
                       help='Lua version to run (plain mode; default 5.1).')
    p_lua.add_argument('--root',
                       help='Plain mode: the folder the script may read (default: '
                            'its own).')
    p_lua.add_argument('--allow-write', action='store_true',
                       help='Plain mode: let the script write under --root.')
    p_lua.add_argument('--max-steps', type=int, default=50_000_000,
                       help='Stop the script after this many steps (0: no limit).')
    p_lua.set_defaults(func=_cmd_lua)

    p_gd = subparsers.add_parser(
        'gamedata-extract', help=('Copy a game install\'s data tables (classic MPQ or '
                                  'CASC) into a profile folder for --game-data.'))
    p_gd.add_argument('install', help='The game install folder.')
    p_gd.add_argument('output', help='The profile folder to write.')
    p_gd.add_argument('--locale', default='enUS', help='Locale of the strings (CASC).')
    p_gd.add_argument('--name', help='A name for the profile (default: the folder name).')
    p_gd.set_defaults(func=_cmd_gamedata_extract)

    p_slk = subparsers.add_parser(
        'slk', help=('Slk mode: move a map\'s object data into the tables the game '
                     'reads (Units\\UnitData.slk, ...), for release.'))
    p_slk.add_argument('input', help='The .w3x/.w3m map.')
    p_slk.add_argument('output', help='The map to write.')
    p_slk.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_slk.set_defaults(func=_cmd_slk_mode)

    p_obj = subparsers.add_parser(
        'obj', help=('Obj mode: turn a map\'s object-data tables back into object '
                     'files the World Editor reads.'))
    p_obj.add_argument('input', help='The .w3x/.w3m map.')
    p_obj.add_argument('output', help='The map to write.')
    p_obj.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_obj.set_defaults(func=_cmd_obj_mode)

    p_objects = subparsers.add_parser(
        'objects-merge', help='Merge two object files of one type, remapping rawcodes.',
        description=('Put the objects of SECOND into FIRST. A custom object whose '
                     'rawcode FIRST uses too, and an edit of a default object both '
                     'change, take a policy: remap (the second object takes a free '
                     'rawcode; an edit becomes a new custom object), first, or second. '
                     "The second file's ability, build and train lists follow its "
                     'remapped rawcodes. See ' + _DOCS + '#merging-object-data.'))
    p_objects.add_argument('output', help='The object file to write.')
    p_objects.add_argument('first', help='The object file to merge into.')
    p_objects.add_argument('second', help='The object file to merge from.')
    p_objects.add_argument('--on-conflict', choices=('remap', 'first', 'second'),
                           default='remap', help='For a custom rawcode both use (default remap).')
    p_objects.add_argument('--on-edit-conflict', choices=('remap', 'first', 'second'),
                           default='remap',
                           help='For a default object both edit (default remap).')
    p_objects.add_argument('--table', metavar='JSON',
                           help='Write the old-to-new rawcode table here.')
    p_objects.set_defaults(func=_cmd_objects_merge)

    p_doodads = subparsers.add_parser(
        'doodads-merge', help='Merge the doodads of several maps or war3map.doo files.',
        description=('Put every doodad and destructable of the inputs into one '
                     'war3map.doo. The first input keeps its editor ids and the others '
                     'are numbered on from its highest. A renumbered destructable that '
                     "its map's script or triggers name (gg_dest_...) is reported, and "
                     'the command then exits with 1. See ' + _DOCS + '#merging-doodads.'))
    p_doodads.add_argument('output', help=('The war3map.doo to write, or a map: the first '
                                           'input map with the merged war3map.doo.'))
    p_doodads.add_argument('inputs', nargs='+', help='Maps or war3map.doo files.')
    p_doodads.add_argument('--renumber-all', action='store_true',
                           help='Number every record from 0 in order, the first input too.')
    p_doodads.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_doodads.set_defaults(func=_cmd_doodads_merge)

    p_import = subparsers.add_parser(
        'import-folder', help='Import a folder and its subfolders into a map.',
        description=('Add every file under a folder to a map at the same relative '
                     'path, and list each one in war3map.imp. A file named as map '
                     'data (war3map.w3i, war3map.j, ...) is skipped. See '
                     + _DOCS + '#imported-files.'))
    p_import.add_argument('input', help='The .w3x/.w3m map.')
    p_import.add_argument('folder', help='The folder to import.')
    p_import.add_argument('output', help='The map to write.')
    p_import.add_argument('--prefix', default='', help=(
        "A path put before every name, for example 'war3mapImported'. By default "
        'the files keep their path relative to the folder.'))
    p_import.add_argument('--keep-existing', action='store_true',
                          help='Keep a member the map already has instead of replacing it.')
    p_import.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_import.set_defaults(func=_cmd_import_folder)

    p_transfer = subparsers.add_parser(
        'transfer', help="Copy one map's object data, triggers and more into other maps.",
        description=('Copy parts of SOURCE into each TARGET and keep the target\'s '
                     'terrain, placed units and doodads, regions, cameras and map info. '
                     'The script is merged: the target keeps what the World Editor '
                     'writes from its own map info and placed objects, the source brings '
                     'its triggers and custom code. Each result is written to OUT_DIR '
                     'under its target\'s name. See ' + _DOCS + '#transferring-map-data.'))
    p_transfer.add_argument('source', help='The map to copy from.')
    p_transfer.add_argument('out_dir', help='The folder to write the results to.')
    p_transfer.add_argument('targets', nargs='+', help='The maps to copy into.')
    p_transfer.add_argument('--parts', default='', help=(
        'Comma-separated parts to copy (default all): objects, triggers, interface, '
        'constants, imports, sounds.'))
    p_transfer.add_argument('--keep-imports', action='store_true',
                            help='Keep a target file that the source also imports.')
    p_transfer.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_transfer.set_defaults(func=_cmd_transfer)

    p_campaign = subparsers.add_parser(
        'campaign-split', help='Write each map of a .w3n campaign as a standalone map.',
        description=("Write each map of a campaign to OUT_DIR, with the campaign's "
                     "object data merged under the map's own, the campaign's gameplay "
                     'constants and interface where the map has none, its imported '
                     'files, and the strings those use. Each file is named after its '
                     'mission index and campaign button. See ' + _DOCS
                     + '#splitting-a-campaign.'))
    p_campaign.add_argument('campaign', help='The .w3n campaign.')
    p_campaign.add_argument('out_dir', help='The folder to write the maps to.')
    p_campaign.add_argument('--parts', default='', help=(
        'Comma-separated parts to copy (default all): objects, interface, constants, '
        'imports.'))
    p_campaign.add_argument('--keep-names', action='store_true',
                            help="Keep each map's own name in its war3map.w3i.")
    p_campaign.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_campaign.set_defaults(func=_cmd_campaign_split)

    p_move = subparsers.add_parser(
        'import-move', help='Move imported files and rewrite every reference to them.',
        description=('Rename imported files and rewrite the references to them in '
                     'object data, the map info, sounds, GUI triggers, the script, '
                     'war3map.imp, models, FDF and the other text files. A model is '
                     'matched as .mdl or .mdx, and its _portrait model moves with it. '
                     'The places a name is still found, such as a path the script '
                     'builds while it runs, are reported. See ' + _DOCS
                     + '#imported-files.'))
    p_move.add_argument('input', help='The .w3x/.w3m map.')
    p_move.add_argument('output', nargs='?', help='The map to write.')
    p_move.add_argument('--move', nargs=2, action='append', metavar=('OLD', 'NEW'),
                        help='Move member OLD to NEW. Can be given more than once.')
    p_move.add_argument('--strip-prefix', action='store_true',
                        help='Take war3mapImported\\ off every member under it.')
    p_move.add_argument('--only', nargs='+', metavar='MEMBER',
                        help='With --strip-prefix, only these members.')
    p_move.add_argument('--fix-dots', action='store_true', help=(
        'Turn each dot but the last of a file name into an underscore, as '
        'TownHallElfV1.01.mdx to TownHallElfV1_01.mdx: patch 3.0 takes the extension '
        'from the first dot and does not load such a file. With --report-only, '
        'lists them.'))
    p_move.add_argument('--no-portraits', action='store_true',
                        help="Do not move a model's _portrait model with it.")
    p_move.add_argument('--report-only', action='store_true',
                        help='Print what would change and write nothing.')
    p_move.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_move.set_defaults(func=_cmd_import_move)

    p_pathing = subparsers.add_parser(
        'pathing-texture', help='Show, make, or make from a model a pathing texture (TGA).',
        description=('A pathing texture is a small TGA, one pixel per 32-unit cell, whose '
                     'red, green and blue block walking, flying and building. Cells are '
                     'written as digits: 1 no walk, 2 no fly, 4 no build, added up (5 is '
                     "a building's cell, 4 unbuildable, 7 blocked). show prints a "
                     'texture; make writes one from such rows of digits; from-model '
                     "writes one from a model's footprint. See " + _DOCS
                     + '#pathing-textures.'))
    p_pathing.add_argument('action', choices=('show', 'make', 'from-model'))
    p_pathing.add_argument('input', help='The texture (show), a text file of rows (make), '
                                         'or a model (from-model).')
    p_pathing.add_argument('output', nargs='?', help='The TGA to write (make, from-model).')
    p_pathing.add_argument('--size', help='from-model: the size in cells, as 8x8.')
    p_pathing.add_argument('--border', type=int, default=0,
                           help='from-model: rings of unbuildable cells around the footprint.')
    p_pathing.add_argument('--snap', type=int, default=2, help=(
        'from-model: cells per block side, as the game draws on its 64-unit build grid '
        '(default 2; 1 for none).'))
    p_pathing.add_argument('--max-height', type=float, default=None, help=(
        'from-model: only the triangles that come this close to the ground.'))
    p_pathing.set_defaults(func=_cmd_pathing_texture)

    p_lua_bundle = subparsers.add_parser(
        'lua-bundle', help=('Bundle a Lua project of modules joined by require into one '
                            'war3map.lua, alone or into a map.'))
    p_lua_bundle.add_argument('entry', help=('The entry module: a .lua file, or a module '
                                             'name under --root.'))
    p_lua_bundle.add_argument('--root', default=None,
                              help=('The source folder module names are found in '
                                    '(a.b is a/b.lua or a/b/init.lua). Default: the '
                                    "entry file's folder, or the current one."))
    p_lua_bundle.add_argument('--map', default=None,
                              help=('A map (or its war3map.lua) to put the bundle into, '
                                    'in front of the generated code.'))
    p_lua_bundle.add_argument('-o', '--output', default=None,
                              help="Where to write it: a .lua file, or a map with --map "
                                   "(default: stdout without --map).")
    p_lua_bundle.add_argument('--at', choices=('main', 'load'), default='main',
                              help=('With --map, start the entry module at the end of '
                                    'main (default) or when the script loads.'))
    p_lua_bundle.add_argument('--no-check', action='store_true',
                              help='Do not run the Lua checker on the result.')
    p_lua_bundle.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_lua_bundle.set_defaults(func=_cmd_lua_bundle)

    p_terrain_image = subparsers.add_parser(
        'terrain-image', help='Export or import terrain heights, water, cliffs and ground '
                              'textures as PNG.',
        description=('One pixel per tile corner, north up. height and water are 16-bit '
                     'PNGs of the values as stored, so export then import leaves '
                     'war3map.w3e as it was; water-flag is black or white, cliff-level '
                     'gray steps. An image of other values (8-bit, or with --range) is '
                     'mapped from black to white onto a range of values, by default the '
                     "layer's lowest to highest in the terrain. ground and cliff-type "
                     'are colours, one per entry of the tileset list, with a JSON legend '
                     'of colour to tileset code. See ' + _DOCS + '#terrain-images.'))
    p_terrain_image.add_argument('action', choices=('export', 'import'))
    p_terrain_image.add_argument('input', help='The .w3x/.w3m map, or a war3map.w3e.')
    p_terrain_image.add_argument('layer', choices=('height', 'water', 'water-flag',
                                                   'cliff-level', 'ground', 'cliff-type'))
    p_terrain_image.add_argument('image', help='The PNG to write (export) or read (import).')
    p_terrain_image.add_argument('output', nargs='?',
                                 help='import: the map or war3map.w3e to write.')
    p_terrain_image.add_argument('--stretch', action='store_true', help=(
        'export: spread the values from black to white, to look at; the range used is '
        'printed for --range.'))
    p_terrain_image.add_argument('--range', nargs=2, type=int, metavar=('LOW', 'HIGH'),
                                 help='import: the values black and white stand for.')
    p_terrain_image.add_argument('--legend', help=(
        'ground and cliff-type: the JSON of colour to tileset code, written on export and '
        "read on import (default: the image's name with .json; without one, the colours "
        "export gives the map's own list)."))
    p_terrain_image.add_argument('--nearest', action='store_true', help=(
        'ground and cliff-type import: take the closest legend colour for any other '
        'colour, such as the smoothed edges of a painted image.'))
    p_terrain_image.add_argument('--variation', choices=('keep', 'random', 'zero'),
                                 default='keep', help=(
        'ground import: the variation of a corner whose texture changed: kept (default), '
        'drawn as often as each one is in the corpus, or 0.'))
    p_terrain_image.add_argument('--seed', type=int, default=0,
                                 help='ground import: the seed for --variation random.')
    p_terrain_image.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_terrain_image.set_defaults(func=_cmd_terrain_image)

    p_shadow_image = subparsers.add_parser(
        'shadow-image', help='Export or import the shadow map (war3map.shd) as a PNG.',
        description=('One pixel per shadow cell, four per tile in each direction, north '
                     'up: black for shadow, white for none. On import a pixel darker than '
                     'half gray is shadow, and the size must match the map. See '
                     + _DOCS + '#shadow-maps.'))
    p_shadow_image.add_argument('action', choices=('export', 'import'))
    p_shadow_image.add_argument('input', help=(
        'The .w3x/.w3m map; or a war3map.shd with --size (export), or a war3map.w3e to '
        'check the size against (import).'))
    p_shadow_image.add_argument('image', help='The PNG to write (export) or read (import).')
    p_shadow_image.add_argument('output', nargs='?',
                                help='import: the map or war3map.shd to write.')
    p_shadow_image.add_argument('--size', help='The size in cells, as 256x256, for a bare '
                                               'war3map.shd.')
    p_shadow_image.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_shadow_image.set_defaults(func=_cmd_shadow_image)

    p_shadow_compute = subparsers.add_parser(
        'shadow-compute', help="Compute a map's shadow map (war3map.shd) from its terrain "
                               'and doodads.',
        description=('In place of the World Editor\'s Calculate Shadows: the terrain and '
                     'the doodads whose shadow flag is set cast shadows from a light in '
                     'the south-west. The editor\'s algorithm is not documented; this one '
                     'was fitted to the shadow maps it wrote, and differs from them. See '
                     + _DOCS + '#shadow-maps.'))
    p_shadow_compute.add_argument('input', help='The .w3x/.w3m map.')
    p_shadow_compute.add_argument('output', help='The map to write.')
    p_shadow_compute.add_argument('--game', help=(
        'The Warcraft III install (1.32 or later), for the doodad table and models. '
        'Without it, only the terrain casts.'))
    p_shadow_compute.add_argument('--azimuth', type=float, default=225.0, help=(
        'The direction toward the light, in degrees from east toward north (default 225, '
        'the south-west).'))
    p_shadow_compute.add_argument('--elevation', type=float, default=30.0,
                                  help='The light above the horizon, in degrees (default 30).')
    p_shadow_compute.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_shadow_compute.set_defaults(func=_cmd_shadow_compute)

    p_minimap = subparsers.add_parser(
        'minimap', help="Render a map's minimap (war3mapMap.blp) from its terrain.",
        description=('The playable area, 256 by 256, ground coloured by its tileset '
                     'textures, water tinted, and trees and other objects that show on the '
                     'minimap as dots. The textures come from the installed game. See '
                     + _DOCS + '#minimaps.'))
    p_minimap.add_argument('input', help='The .w3x/.w3m map.')
    p_minimap.add_argument('output', help=(
        'A map to write with the new war3mapMap.blp, or a .blp or .png to write it to.'))
    p_minimap.add_argument('--game', required=True,
                           help='The Warcraft III install (1.32 or later).')
    p_minimap.add_argument('--quality', type=int, default=40, help=(
        'The JPEG quality of the .blp (default 40, as the World Editor writes it).'))
    p_minimap.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_minimap.set_defaults(func=_cmd_minimap)

    p_preview = subparsers.add_parser(
        'map-preview', help='Make the lobby preview (war3mapPreview.tga) of a map.',
        description=("From the map's minimap and the icons of war3map.mmp (gold mines, "
                     'neutral buildings, start locations), or from any picture, 256 by '
                     '256. The game shows the preview whenever the map has one. See '
                     + _DOCS + '#minimaps.'))
    p_preview.add_argument('input', help='The .w3x/.w3m map.')
    p_preview.add_argument('output', help=(
        'A map to write with the preview, or a .tga or .png to write it to.'))
    p_preview.add_argument('--image', help='A picture to use instead of the minimap.')
    p_preview.add_argument('--game', help=(
        "The Warcraft III install (1.32 or later): the game's minimap icons, and the "
        'minimap rendered from the terrain when the map has none.'))
    p_preview.add_argument('--no-icons', action='store_true',
                           help='Leave the icons of war3map.mmp off the minimap.')
    p_preview.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_preview.set_defaults(func=_cmd_map_preview)

    p_loading = subparsers.add_parser(
        'loading-screen', help='Give a map a custom loading screen made from one picture.',
        description=('Cut the picture into the textures of a loading screen, write the '
                     'model that shows them, import them into the map and point its map '
                     "info at the model. classic is the game's own layout (four tiles of a "
                     '1024 by 768 picture); reforged is one full-screen texture. See '
                     + _DOCS + '#loading-screens.'))
    p_loading.add_argument('input', help='The .w3x/.w3m map.')
    p_loading.add_argument('image', help='The picture (.png, .blp, .tga, ...).')
    p_loading.add_argument('output', help='The map to write.')
    p_loading.add_argument('--layout', choices=('classic', 'reforged'), default='classic')
    p_loading.add_argument('--quality', type=int, default=85,
                           help='The JPEG quality of the textures (default 85).')
    p_loading.add_argument('--prefix', default='war3mapImported\\LoadingScreen', help=(
        'The path of the model without .mdx, and the start of the textures\' names '
        '(default war3mapImported\\LoadingScreen).'))
    p_loading.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_loading.set_defaults(func=_cmd_loading_screen)

    p_placed = subparsers.add_parser(
        'placed-code', help="Write a map's placed units, items, destructables and regions "
                            'as JASS or Lua.',
        description=('A function that creates them at run time, with the statements the '
                     'World Editor writes for them. Doodads, start locations, random units '
                     'and item drop tables are not created, and terrain cannot be: the '
                     'game has no native that sets a height, a cliff or water. What is left '
                     'out is listed. See ' + _DOCS + '#placed-objects-as-code.'))
    p_placed.add_argument('input', help='The .w3x/.w3m map.')
    p_placed.add_argument('output', nargs='?', help='The file to write (default: print).')
    p_placed.add_argument('--lua', action='store_true', help='Write Lua instead of JASS.')
    p_placed.add_argument('--function', default='CreatePlacedObjects',
                          help='The function\'s name (default CreatePlacedObjects).')
    p_placed.add_argument('--classic', action='store_true', help=(
        'Use CreateUnit, CreateItem and CreateDestructable rather than the natives with '
        'skins, for patches before 1.32.'))
    p_placed.add_argument('--parts', help=(
        'Which parts, comma-separated: units (with items), destructables, regions '
        '(default all three).'))
    p_placed.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_placed.set_defaults(func=_cmd_placed_code)

    p_description = subparsers.add_parser(
        'map-description', help='Write a Hive Workshop (BBCode) or Markdown map description.',
        description=('Write a description of a map from its own data: name, author, '
                     'players and forces, playable size, tileset and description, and for '
                     'a melee map its start locations and gold mines. --template takes a '
                     'string.Template with $name, $author, $description, '
                     '$players_recommended, $player_count, $players, $forces, $size, '
                     '$tileset, $melee and $editor_version. See ' + _DOCS
                     + '#map-descriptions.'))
    p_description.add_argument('input', nargs='?', help='The .w3x/.w3m map.')
    p_description.add_argument('--format', choices=('bbcode', 'markdown'), default='bbcode',
                               help='BBCode for the Hive Workshop (default) or Markdown.')
    p_description.add_argument('--template', metavar='FILE',
                               help='A template to fill instead of the default one.')
    p_description.add_argument('--print-template', action='store_true',
                               help="Print the format's default template, to start from.")
    p_description.add_argument('-o', '--output', help='Write here instead of to stdout.')
    p_description.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_description.set_defaults(func=_cmd_map_description)

    p_opt = subparsers.add_parser(
        'optimize', help=('Optimize a map for release, as w3x2lni\'s Slk mode does: '
                          'drop unused objects and editor-only files, move object '
                          'data to the game\'s tables.'))
    p_opt.add_argument('input', help='The .w3x/.w3m map.')
    p_opt.add_argument('output', help='The map to write.')
    p_opt.add_argument('--remove-unused-objects', action='store_true',
                       help='Drop custom objects nothing refers to.')
    p_opt.add_argument('--remove-we-only', action='store_true',
                       help='Drop the files only the World Editor reads.')
    p_opt.add_argument('--mdx-squeeze', action='store_true',
                       help='Cut the precision of the models so they compress '
                            'about a third better (lossy, about 0.8%%).')
    p_opt.add_argument('--computed-text', action='store_true',
                       help='Write the values of <A000,DataA1> references into the text.')
    p_opt.add_argument('--slk', action='store_true',
                       help='Then move object data into the tables (as `slk`).')
    p_opt.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_opt.set_defaults(func=_cmd_optimize)

    p_ui = subparsers.add_parser(
        'trigger-ui', help=('Convert GUI trigger definitions between the World Editor\'s '
                            'TriggerData.txt/TriggerStrings.txt and YDWE\'s ui folder; '
                            'several folders are merged in order.'))
    p_ui.add_argument('inputs', nargs='+',
                      help=('TriggerData.txt [TriggerStrings.txt], or one or more ui folders '
                            '(a base, then plugins), then the output: a ui folder, or a '
                            'folder for the two .txt files.'))
    p_ui.add_argument('--lang', help='Language subfolder of the ui folders to read (zhCN, ...).')
    p_ui.set_defaults(func=_cmd_trigger_ui)

    p_lni = subparsers.add_parser(
        'lni', help=('Turn a map into a folder of text files (objects as Lni, '
                     'the rest as JSON or as they are), or such a folder back into a map.'))
    p_lni.add_argument('input', help='A .w3x/.w3m map, or an Lni folder.')
    p_lni.add_argument('output', help='The folder to write, or the map to build.')
    p_lni.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_lni.set_defaults(func=_cmd_lni)

    p_run_map = subparsers.add_parser(
        'run-map', help=('Run a map script without the game (common.j stubs, '
                         'blizzard.j, timers) and report its runtime errors.'))
    p_run_map.add_argument('input', help='A war3map.lua or war3map.j, or a .w3x/.w3m map.')
    p_run_map.add_argument('--seconds', type=float, default=10.0,
                           help='Game time to let pass after main() (default 10).')
    p_run_map.add_argument('--players', type=int, default=1,
                           help='Player slots in use (default 1).')
    p_run_map.add_argument('--messages', action='store_true',
                           help='Also print the text the map displays.')
    p_run_map.add_argument('--max-steps', type=int, default=200_000_000,
                           help='Stop after this many interpreter steps.')
    p_run_map.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_run_map.set_defaults(func=_cmd_run_map)

    p_trig_text = subparsers.add_parser(
        'trigger-text',
        help=('Render a war3map.wtg as the text the World Editor shows - '
              'events, conditions and actions per trigger.'))
    p_trig_text.add_argument('input', help='war3map.wtg to read, or a .w3x/.w3m map.')
    p_trig_text.add_argument('--deep', action='store_true',
                             help=argparse.SUPPRESS)
    p_trig_text.add_argument('--name', help='Only triggers whose name contains this.')
    p_trig_text.add_argument('--trigger-data',
                             help=('A TriggerData.txt to use instead of the bundled '
                                   'one - needed for a map with custom GUI entries.'))
    p_trig_text.add_argument('--json', action='store_true',
                             help='Emit JSON instead of readable text.')
    p_trig_text.add_argument('--out', help='Write JSON here instead of stdout.')
    p_trig_text.set_defaults(func=_cmd_trigger_text)

    p_strings = subparsers.add_parser(
        'map-strings',
        help=('List the strings of a map by object and field (names, tooltips, '
              'descriptions), the other files that use war3map.wts and the strings '
              'nothing uses; --check reports missing strings, <A000,DataA1> '
              'references that do not resolve and tooltips naming another object.'))
    p_strings.add_argument('map', help='The .w3x/.w3m map.')
    p_strings.add_argument('--check', action='store_true',
                           help='Check the tooltips instead of listing the strings.')
    p_strings.add_argument('--json', action='store_true', help='Emit JSON.')
    p_strings.add_argument('--out', default='-', help='Where to write JSON (default stdout).')
    p_strings.add_argument('--deep', action='store_true', help=argparse.SUPPRESS)
    p_strings.set_defaults(func=_cmd_map_strings)

    p_from_script = subparsers.add_parser(
        'script-to-triggers',
        help=('Reconstruct GUI triggers, variables, regions, cameras and sounds '
              'from a map script. Partial by nature - reports its own coverage.'))
    p_from_script.add_argument('input', help=('war3map.j to read, or a .w3x/.w3m map, whose '
                                              'war3map.j and object data are read.'))
    p_from_script.add_argument('--deep', action='store_true',
                               help=argparse.SUPPRESS)
    p_from_script.add_argument('--json', action='store_true',
                               help='Emit the full reconstruction as JSON.')
    p_from_script.add_argument('--out', help='Write JSON here instead of stdout.')
    p_from_script.add_argument('--limit', type=int, default=20,
                               help='Cap the trigger list in the readable summary.')
    p_from_script.set_defaults(func=_cmd_script_to_triggers)

    return parser


def _utf8_output():
    """Write UTF-8 to stdout and stderr when they are a file or a pipe.

    Python writes those in the locale's code page on Windows (cp1252 and the
    like), and map text is often Chinese, Korean or Russian: `trigger-text
    MyMap.w3x > out.txt` stopped halfway with a 'charmap' codec error. A
    console takes Unicode already and is left alone. surrogateescape gives
    back the bytes of a name that was not valid UTF-8 to begin with. An
    encoding set with PYTHONIOENCODING is kept."""
    if os.environ.get('PYTHONIOENCODING'):
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            if stream.isatty():
                continue
            if (stream.encoding or '').lower().replace('-', '') == 'utf8':
                continue
            reconfigure(encoding='utf-8', errors='surrogateescape')
        except (OSError, ValueError):
            pass


def main(argv=None):
    _utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, 'game_data', None):
        from wc3files import gamedata
        gamedata.use(args.game_data)

    # The package keeps its log quiet (wc3files/__init__.py); the command
    # line shows warnings, and everything with --verbose.
    logger.enable('wc3files')
    if not args.verbose:
        logger.remove()
        logger.add(sys.stderr, level='WARNING')

    try:
        # A command returns 1 (or another status) when it failed after
        # reporting why; None means success.
        status = args.func(args)
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (TruncatedDataError, struct.error) as exc:
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
    except (ValueError, FileNotFoundError) as exc:
        # A script that will not parse is an ordinary outcome, not a crash:
        # plenty of real map scripts are vJass, which this package
        # preprocesses but does not compile. Report it with the likely cause.
        # The script errors are ValueErrors (#167), so they are told apart
        # here rather than in a branch of their own.
        from wc3files.scripts import JassLexError, JassParseError
        from wc3files.scripts import LuaLexError, LuaParseError
        if isinstance(exc, (JassLexError, JassParseError, LuaLexError,
                            LuaParseError)):
            print(f"error: {args.input}: {exc}", file=sys.stderr)
            print("hint: if this script uses vJass (struct, method, library, "
                  "scope) or Zinc, compile it to JASS first: "
                  f"wc3files vjass {args.input} -o compiled.j",
                  file=sys.stderr)
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return status if isinstance(status, int) else 0


if __name__ == '__main__':
    sys.exit(main())
