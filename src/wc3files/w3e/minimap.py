"""
war3mapMap.blp, the minimap, rendered from the terrain (#209).

What the World Editor writes, found on the 342 readable corpus maps: a
256 x 256 image of the map's playable area (the map less its camera bounds
complements), scaled to fill the square with its shape kept and centered on
black (the pixels around it are (1, 1, 1) to (2, 2, 2) after JPEG). Nearly
all are JPEG BLP1 of one mip at quality 40.

The editor's renderer is not documented, and this one is simpler:

- the ground at each pixel blends the four corners of its tile, each the
  mean colour of its tileset texture's full tiles (the texture's left
  square);
- a pixel whose corners are mostly under water is tinted with the water's
  minimap colour, at the minimap alpha of `TerrainArt\\Water.slk`;
- destructables and doodads that show on the minimap with a colour of their
  own (`showInMM` and `useMMColor`, trees among them) are a pixel of that
  colour.

Tried and dropped, as they matched the editor's images less well across
tilesets: a stronger water tint, a water colour measured from the editor's
images, and a brightness gain per channel. Cliffs, terrain lighting and
models are not drawn.
"""
from wc3files.img import BlpFile

SIZE = 256
QUALITY = 40


def playable_area(terrain, info):
    """(left, bottom, width, height) of the playable area in tiles, from the
    map info's camera bounds complements (left, right, bottom, top)."""
    left, right, bottom, top = info.get('camera_complements') or (0, 0, 0, 0)
    width = terrain['width'] - 1 - left - right
    height = terrain['height'] - 1 - bottom - top
    if width <= 0 or height <= 0:
        raise ValueError(f'the camera bounds complements {left, right, bottom, top} leave no '
                         f'playable area on a map of {terrain["width"] - 1}x'
                         f'{terrain["height"] - 1} tiles')
    return left, bottom, width, height


def layout(terrain, info, size=SIZE):
    """(left, bottom, width, height, scale, x0, y0): the playable area and
    where it lies in the image."""
    left, bottom, width, height = playable_area(terrain, info)
    scale = size / max(width, height)
    return (left, bottom, width, height, scale,
            (size - width * scale) / 2, (size - height * scale) / 2)


def render_minimap(terrain, info, doodads=(), tile_colours=None, water=None,
                   object_colours=None, size=SIZE):
    """RGBA bytes of the minimap, `size` pixels square.

    `tile_colours` is {ground tileset code: (r, g, b)}; `water` is
    ((r, g, b), alpha from 0 to 1) or None for no tint; `object_colours` is
    {destructable or doodad id: (r, g, b)} for those drawn as a pixel, and
    `doodads` the records of war3map.doo."""
    tile_colours = tile_colours or {}
    left, bottom, width, height, scale, x0, y0 = layout(terrain, info, size)
    corners = terrain['width']
    tiles = terrain['tiles']
    grey = (128, 128, 128)
    by_index = [tile_colours.get(code, grey) for code in terrain['ground_tilesets']]
    colour = [by_index[t['ground_texture']] if t['ground_texture'] < len(by_index) else grey
              for t in tiles]
    wet = []
    for t in tiles:
        ground = (t['ground_height'] - 8192 + (t['layer_height'] - 2) * 512) / 4
        surface = (t['water_level'] - 8192) / 4 - 89.6
        wet.append(1.0 if t['is_water'] and surface > ground else 0.0)
    out = bytearray(b'\x00\x00\x00\xff' * (size * size))
    content_height = height * scale
    for py in range(size):
        fy = (content_height - (py + 0.5 - y0)) / scale
        if not 0 <= fy < height:
            continue
        ty = bottom + fy
        j = int(ty)
        ay = ty - j
        for px in range(size):
            fx = (px + 0.5 - x0) / scale
            if not 0 <= fx < width:
                continue
            tx = left + fx
            i = int(tx)
            ax = tx - i
            k = j * corners + i
            ks = (k, k + 1, k + corners, k + corners + 1)
            ws = ((1 - ax) * (1 - ay), ax * (1 - ay), (1 - ax) * ay, ax * ay)
            r = g = b = 0.0
            wetness = 0.0
            for kk, w in zip(ks, ws):
                c = colour[kk]
                r += c[0] * w
                g += c[1] * w
                b += c[2] * w
                wetness += wet[kk] * w
            if water and wetness > 0.5:
                (wr, wg, wb), alpha = water
                r += (wr - r) * alpha
                g += (wg - g) * alpha
                b += (wb - b) * alpha
            o = (py * size + px) * 4
            out[o] = min(255, max(0, int(r + 0.5)))
            out[o + 1] = min(255, max(0, int(g + 0.5)))
            out[o + 2] = min(255, max(0, int(b + 0.5)))
    for record in doodads or ():
        rgb = (object_colours or {}).get(record['id'])
        if rgb is None:
            continue
        tx = (record['position'][0] - terrain['offset_x']) / 128 - left
        ty = (record['position'][1] - terrain['offset_y']) / 128 - bottom
        if not (0 <= tx < width and 0 <= ty < height):
            continue
        px = int(x0 + tx * scale)
        py = int(y0 + content_height - ty * scale)
        if 0 <= px < size and 0 <= py < size:
            o = (py * size + px) * 4
            out[o:o + 3] = bytes(rgb)
    return bytes(out)


def minimap_blp(rgba, size=SIZE, quality=QUALITY):
    """The minimap as war3mapMap.blp bytes: JPEG, one mip, as the editor writes."""
    return BlpFile.from_rgba(size, size, rgba, alpha=False, compression='jpeg',
                             quality=quality, mipmaps=False).to_bytes()


# -- colours from the game and the bundled tables ----------------------------------

def object_colours():
    """{id: (r, g, b)} of the destructables and doodads that show on the
    minimap in a colour of their own, from the bundled tables."""
    from wc3files import gamedata
    from wc3files.table.slk_file import SlkFile
    out = {}
    for name in ('DestructableData.slk', 'Doodads.slk'):
        table = SlkFile()
        table.read(gamedata.path('w3o/gamedata/' + name))
        for key, row in table.export_data().items():
            if row.get('showInMM') and row.get('useMMColor'):
                out[key] = (int(row.get('MMRed') or 0), int(row.get('MMGreen') or 0),
                            int(row.get('MMBlue') or 0))
    return out


def _slk(data):
    import os
    import tempfile
    from wc3files.table.slk_file import SlkFile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'table.slk')
        with open(path, 'wb') as handle:
            handle.write(data)
        table = SlkFile()
        table.read(path)
        return table.export_data()


def texture_colour(rgba, width, height):
    """The mean colour of a ground texture's full tiles: its left square."""
    side = min(width, height)
    total = [0, 0, 0]
    for y in range(side):
        row = y * width * 4
        for x in range(side):
            o = row + x * 4
            total[0] += rgba[o]
            total[1] += rgba[o + 1]
            total[2] += rgba[o + 2]
    n = side * side or 1
    return tuple(v / n for v in total)


class GameMinimapColours:
    """The tile and water colours of an installed game (a CascStorage),
    each texture read and averaged once."""

    def __init__(self, storage):
        self.storage = storage
        self.terrain = _slk(storage.read('War3.w3mod:TerrainArt/Terrain.slk'))
        self.water = _slk(storage.read('War3.w3mod:TerrainArt/Water.slk'))
        self._tiles = {}
        self.missing = set()

    def tile(self, code):
        if code in self._tiles:
            return self._tiles[code]
        from wc3files.img import DdsFile
        row = self.terrain.get(code)
        colour = None
        if row:
            stem = 'War3.w3mod:' + (row['dir'] + '/' + row['file']).replace('\\', '/')
            for ext in ('.dds', '.blp'):
                try:
                    data = self.storage.read(stem + ext)
                except Exception:
                    continue
                image = DdsFile() if ext == '.dds' else BlpFile()
                image.read(data)
                colour = texture_colour(image.get_rgba(), image.width, image.height)
                break
        if colour is None:
            self.missing.add(code)
        self._tiles[code] = colour
        return colour

    def tile_colours(self, codes):
        return {code: c for code in codes if (c := self.tile(code)) is not None}

    def water_tint(self, tileset):
        row = self.water.get(f'{tileset}Sha')
        if not row:
            return None
        return ((int(row['mmRed']), int(row['mmGreen']), int(row['mmBlue'])),
                int(row['mmAlpha']) / 255)
