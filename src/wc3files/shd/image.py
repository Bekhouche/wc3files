"""
war3map.shd as an image, and back (#203).

The shadow map has one byte per cell, four cells per terrain tile in each
direction, from the bottom-left corner: 0xFF where the ground is in shadow
and 0 where it is not. All 342 readable corpus maps hold only these two
values, in a file of exactly `shadow_size(terrain)` cells.

An image has one pixel per cell, north up as the World Editor shows the
map: black for shadow, white for none, in an 8-bit grayscale PNG. A pixel
darker than half gray is read as shadow, so a picture painted in any
image editor works.
"""
from .shadow_map_file import ShadowMapFile

SHADOW = 0xFF


def shadow_size(terrain):
    """(width, height) in cells of the shadow map of a terrain (the dict
    TerrainFile.get_data() returns): its tiles times four."""
    return (terrain['width'] - 1) * 4, (terrain['height'] - 1) * 4


def shadow_png(shadow):
    """The shadow map (ShadowMapFile.get_data()) as PNG bytes."""
    from wc3files.img.png_writer import write_png_gray
    width, height, cells = shadow['width'], shadow['height'], shadow['cells']
    values = []
    for row in range(height - 1, -1, -1):
        values.extend(0 if c else 255 for c in cells[row * width:(row + 1) * width])
    return write_png_gray(width, height, values, 8)


def shadow_from_png(png, size=None):
    """A ShadowMapFile from a PNG. `size` (width, height), from the map's
    terrain, is checked against the image's."""
    from wc3files.img.png_reader import read_png_gray
    width, height, values, depth = read_png_gray(png)
    if size is not None and (width, height) != tuple(size):
        raise ValueError(f'the image is {width}x{height} pixels but the map\'s shadow map '
                         f'is {size[0]}x{size[1]} cells (four per tile)')
    half = 1 << (depth - 1)
    cells = bytearray(width * height)
    for row in range(height):
        start = (height - 1 - row) * width
        for x, value in enumerate(values[row * width:(row + 1) * width]):
            if value < half:
                cells[start + x] = SHADOW
    return ShadowMapFile().import_data({'width': width, 'height': height,
                                        'cells': bytes(cells)})
