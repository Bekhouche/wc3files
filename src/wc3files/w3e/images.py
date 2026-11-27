"""
Terrain layers of war3map.w3e as grayscale images, and back (#205).

An image has one pixel per tile corner, as the file stores them: `width`
by `height` of the w3e header, which is one more than the map's tiles in
each direction. The file's grid starts at the bottom-left corner and an
image at the top-left, so the rows are flipped, and an image shows the map
the way the World Editor does, north up.

Layers:

- `height`: the ground height as stored, 0 to 65535 (8192 is the ground
  level of layer 0), in a 16-bit PNG;
- `water`: the water level as stored, 0 to 16383, in a 16-bit PNG;
- `water-flag`: whether a corner has water, black or white in an 8-bit PNG;
- `cliff-level`: the cliff level, 0 to 15, as 17 gray steps apart in an 8-bit
  PNG (level 2 is the editor's ground level).

Ground textures and cliff types are colours instead (#206): `export_tiles`
gives each entry of the map's ground or cliff tileset list a colour of its
own and returns the legend, colour to tileset code; `import_tiles` takes a
painted image and a legend back. A code the map's list does not have is
added to it, up to the list's limit. `cliff-type` has a colour for corners
with no cliff type (15, a third of all corners in the corpus).

A 16-bit image of `height` or `water` gives back the same values, so export
then import leaves the file byte for byte as it was. `stretch` spreads the
values over the whole gray range so the image can be looked at; the range
it used, given back to the import, restores the values exactly. An image
of other values, such as an 8-bit one made elsewhere, is mapped from black
to white onto a range of values: the one given, or else the layer's lowest
to highest in the terrain it goes into.
"""

import random

LAYERS = ('height', 'water', 'water-flag', 'cliff-level')
TILE_LAYERS = ('ground', 'cliff-type')

_FIELD = {'height': 'ground_height', 'water': 'water_level', 'water-flag': 'is_water',
          'cliff-level': 'layer_height', 'ground': 'ground_texture',
          'cliff-type': 'cliff_texture'}
_TOP = {'height': 0xFFFF, 'water': 0x3FFF}
_LEVEL_STEP = 17

NO_CLIFF = 15
NONE = 'none'

# Ground variations as the corpus has them: the share of each variation over
# the 10.2 million tile corners of 342 maps, made and painted in the World
# Editor. The editor's own rule is not known; `variation='random'` draws
# from this.
VARIATION_SHARES = (
    (0, 0.1594), (1, 0.0207), (2, 0.0084), (3, 0.0055), (4, 0.1457), (5, 0.0202),
    (6, 0.0080), (7, 0.0019), (8, 0.1447), (9, 0.0180), (10, 0.0068), (11, 0.0019),
    (12, 0.1439), (13, 0.0176), (14, 0.0068), (15, 0.0018), (16, 0.1428), (17, 0.1443))

# Distinct colours, in order, for the entries of a tileset list.
_PALETTE = (
    (230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212),
    (0, 128, 128), (220, 190, 255), (170, 110, 40), (255, 250, 200), (128, 0, 0),
    (170, 255, 195), (128, 128, 0), (255, 215, 180), (0, 0, 128), (128, 128, 128))
_NONE_COLOUR = (0, 0, 0)


def _check_layer(layer):
    if layer not in LAYERS:
        raise ValueError(f'unknown terrain layer {layer!r}: one of {", ".join(LAYERS)}')


def layer_values(terrain, layer):
    """The layer's values, row-major from the top row of the map, as ints
    (`water-flag`: 1 for water, 0 for none)."""
    _check_layer(layer)
    width, height = terrain['width'], terrain['height']
    tiles = terrain['tiles']
    field = _FIELD[layer]
    out = []
    for row in range(height - 1, -1, -1):
        start = row * width
        out.extend(int(tile[field]) for tile in tiles[start:start + width])
    return out


def export_layer(terrain, layer, stretch=False):
    """(png bytes, value range or None). `stretch` maps the layer's lowest
    value to black and its highest to white, and returns that range."""
    from wc3files.img.png_writer import write_png_gray
    values = layer_values(terrain, layer)
    width, height = terrain['width'], terrain['height']
    if layer == 'water-flag':
        return write_png_gray(width, height, [255 if v else 0 for v in values], 8), None
    if layer == 'cliff-level':
        return write_png_gray(width, height, [v * _LEVEL_STEP for v in values], 8), None
    if not stretch:
        return write_png_gray(width, height, values, 16), None
    low, high = min(values), max(values)
    span = high - low
    scaled = [round((v - low) * 0xFFFF / span) if span else 0 for v in values]
    return write_png_gray(width, height, scaled, 16), (low, high)


def import_layer(terrain, layer, png, value_range=None):
    """Set the layer of `terrain` (the dict TerrainFile.get_data() returns)
    from a PNG; returns the number of corners that changed. A 16-bit image
    of `height` or `water` with no `value_range` is taken as the values
    themselves."""
    from wc3files.img.png_reader import read_png_gray
    _check_layer(layer)
    width, height, samples, depth = read_png_gray(png)
    if (width, height) != (terrain['width'], terrain['height']):
        raise ValueError(f'the image is {width}x{height} pixels but the terrain has '
                         f'{terrain["width"]}x{terrain["height"]} tile corners')
    full = (1 << depth) - 1
    if layer == 'water-flag':
        values = [s * 2 > full for s in samples]
    elif layer == 'cliff-level':
        # 16 levels spread from black to white: an 8-bit image as exported
        # comes back exactly, and any other gray takes the nearest level.
        values = [round(s * 15 / full) for s in samples]
    else:
        top = _TOP[layer]
        if value_range is None and depth == 16:
            values = samples
        else:
            if value_range is None:
                current = layer_values(terrain, layer)
                value_range = (min(current), max(current))
                if value_range[0] == value_range[1]:
                    raise ValueError(f'the terrain\'s {layer} is flat, so it gives no range '
                                     f'to map an {depth}-bit image onto: give one')
            low, high = value_range
            values = [round(low + s * (high - low) / full) for s in samples]
        bad = [v for v in values if v < 0 or v > top]
        if bad:
            raise ValueError(f'{layer} values go from 0 to {top}, and the image gives '
                             f'{min(bad) if min(bad) < 0 else max(bad)}')
    field = _FIELD[layer]
    tiles = terrain['tiles']
    changed = 0
    for i, value in enumerate(values):
        row, x = divmod(i, width)
        tile = tiles[(height - 1 - row) * width + x]
        if tile[field] != value:
            tile[field] = value
            changed += 1
    return changed


def _colour(index):
    if index < len(_PALETTE):
        return _PALETTE[index]
    # Past the fixed colours (lists longer than 20 are rare: 15 of 342
    # corpus maps), spread hues by the golden angle.
    import colorsys
    hue = (index * 0.618033988749895) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.55 + 0.4 * (index % 2), 0.95 - 0.3 * (index % 3) / 2)
    return round(r * 255), round(g * 255), round(b * 255)


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(rgb)


def _parse_hex(text):
    text = text.strip().lstrip('#')
    if len(text) != 6:
        raise ValueError(f'a colour is #rrggbb, not {text!r}')
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _tile_list(terrain, layer):
    if layer not in TILE_LAYERS:
        raise ValueError(f'unknown tile layer {layer!r}: one of {", ".join(TILE_LAYERS)}')
    return terrain['ground_tilesets' if layer == 'ground' else 'cliff_tilesets']


def tile_legend(terrain, layer):
    """{'#rrggbb': tileset code} for the map's ground or cliff list, in the
    colours export_tiles paints with; `cliff-type` adds 'none'."""
    legend = {_hex(_colour(i)): code for i, code in enumerate(_tile_list(terrain, layer))}
    if layer == 'cliff-type':
        legend[_hex(_NONE_COLOUR)] = NONE
    return legend


def export_tiles(terrain, layer):
    """(png bytes, legend): the ground textures or cliff types of each tile
    corner, north up, each entry of the map's list in a colour of its own."""
    from wc3files.img.png_writer import write_png
    codes = _tile_list(terrain, layer)
    colours = [_colour(i) for i in range(len(codes))]
    rgba = bytearray()
    for value in layer_values_raw(terrain, layer):
        if value < len(colours):
            rgb = colours[value]
        elif layer == 'cliff-type' and value == NO_CLIFF:
            rgb = _NONE_COLOUR
        else:
            raise ValueError(f'a tile corner has {layer} {value}, past the map\'s list of '
                             f'{len(codes)}')
        rgba += bytes(rgb) + b'\xff'
    return write_png(terrain['width'], terrain['height'], bytes(rgba)), \
        tile_legend(terrain, layer)


def layer_values_raw(terrain, layer):
    """The values of any field layer, row-major from the top row."""
    width, height = terrain['width'], terrain['height']
    field = _FIELD[layer]
    tiles = terrain['tiles']
    out = []
    for row in range(height - 1, -1, -1):
        out.extend(int(t[field]) for t in tiles[row * width:(row + 1) * width])
    return out


def _limit(terrain, layer):
    if layer == 'ground':
        return 64 if terrain.get('version', 11) >= 12 else 16
    return NO_CLIFF  # 4 bits, with 15 meaning no cliff type


def import_tiles(terrain, layer, png, legend=None, nearest=False, variation='keep',
                 seed=0):
    """Set the ground textures or cliff types of `terrain` from a painted
    image; returns {'changed': n, 'added': [codes added to the list]}.

    `legend` is {'#rrggbb': code}, as export_tiles returns, by default the
    map's own. A code the list lacks is added to it. A colour not in the
    legend is an error, or with `nearest` takes the closest legend colour
    (for images whose edges an editor smoothed). `variation` is 'keep', or
    'random' for a ground variation drawn from VARIATION_SHARES on each
    corner whose texture changed, or 'zero'."""
    from wc3files.img.png_reader import read_png
    codes = list(_tile_list(terrain, layer))
    if variation not in ('keep', 'random', 'zero'):
        raise ValueError(f"variation is 'keep', 'random' or 'zero', not {variation!r}")
    width, height, rgba = read_png(png)
    if (width, height) != (terrain['width'], terrain['height']):
        raise ValueError(f'the image is {width}x{height} pixels but the terrain has '
                         f'{terrain["width"]}x{terrain["height"]} tile corners')
    legend = tile_legend(terrain, layer) if legend is None else legend
    by_colour = {}
    added = []
    for colour, code in legend.items():
        rgb = _parse_hex(colour)
        if code == NONE:
            if layer != 'cliff-type':
                raise ValueError("'none' is only a cliff type")
            by_colour[rgb] = NO_CLIFF
            continue
        if code not in codes:
            if len(codes) >= _limit(terrain, layer):
                raise ValueError(f'the {layer} list is full ({len(codes)} entries), so '
                                 f'{code} cannot be added')
            codes.append(code)
            added.append(code)
        # A list can name a code twice (3 corpus maps do): the colour
        # export_tiles gave an entry finds that entry, not the first.
        same = [j for j, c in enumerate(codes) if c == code]
        by_colour[rgb] = next((j for j in same if _colour(j) == rgb), same[0])
    palette = list(by_colour.items())
    cache = {}
    values = []
    for i in range(width * height):
        rgb = tuple(rgba[i * 4:i * 4 + 3])
        value = by_colour.get(rgb)
        if value is None:
            value = cache.get(rgb)
        if value is None:
            if not nearest:
                raise ValueError(f'pixel ({i % width}, {i // width}) is {_hex(rgb)}, which the '
                                 f'legend does not have (use nearest to take the closest)')
            value = min(palette, key=lambda item: sum((a - b) ** 2
                                                       for a, b in zip(item[0], rgb)))[1]
            cache[rgb] = value
        values.append(value)
    terrain['ground_tilesets' if layer == 'ground' else 'cliff_tilesets'] = codes
    field = _FIELD[layer]
    tiles = terrain['tiles']
    rng = random.Random(seed)
    population = [v for v, _ in VARIATION_SHARES]
    weights = [w for _, w in VARIATION_SHARES]
    changed = 0
    for i, value in enumerate(values):
        row, x = divmod(i, width)
        tile = tiles[(height - 1 - row) * width + x]
        if tile[field] == value:
            continue
        tile[field] = value
        changed += 1
        if layer == 'ground' and variation != 'keep':
            tile['ground_variation'] = (rng.choices(population, weights)[0]
                                        if variation == 'random' else 0)
    return {'changed': changed, 'added': added}
