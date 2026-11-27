"""
A custom loading screen from one picture (#211): textures cut from it, the
model that shows them, the files imported into the map and its map info
pointed at the model.

Two layouts, both taken from models that work:

- `classic`, the game's own: its campaign loading screens
  (UI\\Glues\\Loading\\Backgrounds\\Campaigns\\*Background.mdx) draw the
  screen as four quads in a 0.8 x 0.6 area, the top two 0.4 x 0.4 with
  512 x 512 textures and the bottom two 0.4 x 0.2 with 512 x 256 ones, so a
  1024 x 768 picture cut in four. Three corpus maps' loading screens are
  the same four quads;
- `reforged`: one quad of 0.8 x 0.6 with the whole picture, as 72 of the
  79 corpus maps with a custom loading screen use, at the picture's size
  rounded to powers of two (2048 x 1024 by default).

The model is a version 800 MDX as the corpus ones are: one bone, a `Birth`
sequence from 1000 to 2000 that does not loop, and per quad a material with
one unshaded, two-sided, unfogged layer. A texture coordinate v of 0 is the
top of a quad, as in the game's models. The map info gets loading screen
number -1 and the model's path, as all 79 corpus maps with a custom model
have.
"""
from wc3files.img import BlpFile
from wc3files.img.transform import resize_box

CLASSIC = (  # name, quad (x0, y0, x1, y1), picture area (x, y, w, h) of 1024 x 768
    ('BotLeft', (0.0, 0.0, 0.4, 0.2), (0, 512, 512, 256)),
    ('BotRight', (0.4, 0.0, 0.8, 0.2), (512, 512, 512, 256)),
    ('TopRight', (0.4, 0.2, 0.8, 0.6), (512, 0, 512, 512)),
    ('TopLeft', (0.0, 0.2, 0.4, 0.6), (0, 0, 512, 512)),
)
LAYOUTS = ('classic', 'reforged')


def fill(rgba, width, height, new_width, new_height):
    """`rgba` scaled to cover new_width x new_height with its shape kept,
    the overflow cut evenly from both sides."""
    scale = max(new_width / width, new_height / height)
    scaled_w, scaled_h = max(new_width, round(width * scale)), max(new_height, round(height * scale))
    scaled = resize_box(rgba, width, height, scaled_w, scaled_h)
    x0, y0 = (scaled_w - new_width) // 2, (scaled_h - new_height) // 2
    out = bytearray()
    for y in range(new_height):
        start = ((y0 + y) * scaled_w + x0) * 4
        out += scaled[start:start + new_width * 4]
    return bytes(out)


def crop(rgba, width, x, y, w, h):
    out = bytearray()
    for row in range(y, y + h):
        out += rgba[(row * width + x) * 4:(row * width + x + w) * 4]
    return bytes(out)


def tiles(rgba, width, height, layout='classic', size=(2048, 1024)):
    """[(name, quad, (rgba, w, h))] of the textures a layout needs."""
    if layout == 'classic':
        screen = fill(rgba, width, height, 1024, 768)
        return [(name, quad, (crop(screen, 1024, *area), area[2], area[3]))
                for name, quad, area in CLASSIC]
    if layout == 'reforged':
        w, h = size
        return [('FullScreen', (0.0, 0.0, 0.8, 0.6), (fill(rgba, width, height, w, h), w, h))]
    raise ValueError(f'unknown layout {layout!r}: one of {", ".join(LAYOUTS)}')


def model_mdl(textures):
    """The MDL text of the model: `textures` is [(texture path, quad)]."""
    def num(v):
        return ('%.5f' % v).rstrip('0').rstrip('.') or '0'

    out = ['Version {', '\tFormatVersion 800,', '}',
           'Model "LoadingScreen" {', f'\tNumGeosets {len(textures)},', '\tNumBones 1,',
           '\tBlendTime 150,', '}',
           'Sequences 1 {', '\tAnim "Birth" {', '\t\tInterval { 1000, 2000 },',
           '\t\tNonLooping,', '\t}', '}',
           f'Textures {len(textures)} {{']
    for path, _ in textures:
        out += ['\tBitmap {', '\t\tImage "%s",' % path.replace('\\', '\\\\'), '\t}']
    out += ['}', f'Materials {len(textures)} {{']
    for i in range(len(textures)):
        out += ['\tMaterial {', '\t\tFullResolution,', '\t\tLayer {', '\t\t\tFilterMode None,',
                '\t\t\tUnshaded,',
                '\t\t\tTwoSided,', '\t\t\tUnfogged,', f'\t\t\tstatic TextureID {i},', '\t\t}',
                '\t}']
    out.append('}')
    for i, (_, (x0, y0, x1, y1)) in enumerate(textures):
        corners = ((x1, y0, 1, 1), (x1, y1, 1, 0), (x0, y0, 0, 1), (x0, y1, 0, 0))
        out += ['Geoset {', '\tVertices 4 {']
        out += ['\t\t{ %s, %s, 0 },' % (num(x), num(y)) for x, y, _, _ in corners]
        out += ['\t}', '\tNormals 4 {'] + ['\t\t{ 0, 0, 1 },'] * 4 + ['\t}', '\tTVertices 4 {']
        out += ['\t\t{ %s, %s },' % (u, v) for _, _, u, v in corners]
        out += ['\t}', '\tVertexGroup {'] + ['\t\t0,'] * 4 + ['\t}',
                '\tFaces 1 6 {', '\t\tTriangles {', '\t\t\t{ 3, 2, 1, 0, 1, 2 },', '\t\t}',
                '\t}', '\tGroups 1 1 {', '\t\tMatrices { 0 },', '\t}',
                f'\tMaterialID {i},', '\tSelectionGroup 0,', '}']
    geoset = 'Multiple' if len(textures) > 1 else '0'
    out += ['Bone "LoadingScreenBone" {', '\tObjectId 0,', f'\tGeosetId {geoset},',
            '\tGeosetAnimId None,', '}', 'PivotPoints 1 {', '\t{ 0, 0, 0 },', '}', '']
    return '\n'.join(out)


def build(rgba, width, height, layout='classic', prefix='war3mapImported\\LoadingScreen',
          quality=85, size=(2048, 1024)):
    """{member name: bytes} of the model and its textures (JPEG BLP), and
    the model's member name."""
    from wc3files.mdl.mdl_io import load_mdl
    from wc3files.mdl.mdx_io import save_mdx
    members, textures = {}, []
    for name, quad, (pixels, w, h) in tiles(rgba, width, height, layout, size):
        path = f'{prefix}{name}.blp'
        members[path] = BlpFile.from_rgba(w, h, pixels, alpha=False, compression='jpeg',
                                          quality=quality).to_bytes()
        textures.append((path, quad))
    from wc3files.mdl.extents import compute_extents
    model = f'{prefix}.mdx'
    data = load_mdl(model_mdl(textures))
    compute_extents(data, frames=1)
    members[model] = save_mdx(data)
    return members, model


def add_loading_screen(files, rgba, width, height, layout='classic',
                       prefix='war3mapImported\\LoadingScreen', quality=85,
                       size=(2048, 1024)):
    """(files, report): the map's members with the loading screen added and
    listed in war3map.imp, and war3map.w3i pointed at the model. The report
    lists the members `added` and `replaced`, and the `model`."""
    from wc3files.w3i import W3iFile
    from .imports import find, import_flags, member_name, read_imports, write_imports
    key = find(files, 'war3map.w3i')
    if key is None:
        raise ValueError('the map has no war3map.w3i to point at the loading screen')
    files = dict(files)
    members, model = build(rgba, width, height, layout, prefix, quality, size)
    imp_key, listing = read_imports(files)
    entries = listing['files']
    listed = {member_name(e.get('path', '')).lower() for e in entries}
    flags = import_flags(entries)
    report = {'added': [], 'replaced': [], 'model': model}
    for name, data in members.items():
        existing = find(files, name)
        files[existing or name] = data
        report['replaced' if existing else 'added'].append(existing or name)
        if (existing or name).lower() not in listed:
            entries.append({'flags': flags, 'path': existing or name})
    write_imports(files, imp_key, listing)
    info = W3iFile()
    info.read(files[key])
    data = info.get_data()
    data['loading_screen']['id'] = -1
    data['loading_screen']['path'] = model
    files[key] = info.import_data(data).to_bytes()
    return files, report
