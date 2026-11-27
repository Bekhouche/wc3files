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
- `water-flag`: whether a corner has water, black or white in an 8-bit PNG.

A 16-bit image of `height` or `water` gives back the same values, so export
then import leaves the file byte for byte as it was. `stretch` spreads the
values over the whole gray range so the image can be looked at; the range
it used, given back to the import, restores the values exactly. An image
of other values, such as an 8-bit one made elsewhere, is mapped from black
to white onto a range of values: the one given, or else the layer's lowest
to highest in the terrain it goes into.
"""

LAYERS = ('height', 'water', 'water-flag')

_FIELD = {'height': 'ground_height', 'water': 'water_level', 'water-flag': 'is_water'}
_TOP = {'height': 0xFFFF, 'water': 0x3FFF}


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
