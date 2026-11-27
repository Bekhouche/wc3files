"""
war3mapPreview.tga, the picture of a map in the game lobby (#210).

The game shows war3mapPreview.tga when a map has one; no map info flag goes
with it. Over 342 readable corpus maps, the 50 with a preview set no flag bit
the 292 without do not, and its size is most often 256 x 256 (36 of the 50),
else 128 x 128.

A preview is made here from either:

- the minimap (war3mapMap.blp, the map's own or one rendered by
  w3e.minimap) with the icons of war3map.mmp on it. Those icons are placed
  in minimap pixels: on five corpus maps, a gold mine's icon lies within a
  pixel of where the minimap layout puts the mine. Gold mines and neutral
  buildings are drawn with the game's minimap icons when given, else as a
  plain shape; a start location is a dot in its player's colour;
- any picture, fitted into the square with its shape kept, on black.

The icon sizes are chosen here, not measured from the game.
"""
from wc3files.img import TgaFile
from wc3files.img.transform import resize_box

SIZE = 256
GOLD, NEUTRAL, START = 0, 1, 2
ICON_SIZE = 12
START_SIZE = 9


def fit(rgba, width, height, size=SIZE):
    """`rgba` scaled to fit a `size` square with its shape kept, centered on
    black."""
    scale = size / max(width, height)
    new_w, new_h = max(1, round(width * scale)), max(1, round(height * scale))
    scaled = resize_box(rgba, width, height, new_w, new_h)
    out = bytearray(b'\x00\x00\x00\xff' * (size * size))
    x0, y0 = (size - new_w) // 2, (size - new_h) // 2
    for y in range(new_h):
        start = ((y0 + y) * size + x0) * 4
        out[start:start + new_w * 4] = scaled[y * new_w * 4:(y + 1) * new_w * 4]
    return bytes(out)


def _blend(out, size, x, y, rgb, alpha):
    if 0 <= x < size and 0 <= y < size and alpha > 0:
        o = (y * size + x) * 4
        for c in range(3):
            out[o + c] = int(out[o + c] + (rgb[c] - out[o + c]) * alpha + 0.5)


def _draw_image(out, size, cx, cy, icon, side):
    rgba, width, height = icon
    small = resize_box(rgba, width, height, side, side)
    left, top = cx - side // 2, cy - side // 2
    for y in range(side):
        for x in range(side):
            o = (y * side + x) * 4
            _blend(out, size, left + x, top + y, small[o:o + 3], small[o + 3] / 255)


def _draw_dot(out, size, cx, cy, rgb, side, outline=(0, 0, 0)):
    r = side / 2
    for y in range(-int(r) - 1, int(r) + 2):
        for x in range(-int(r) - 1, int(r) + 2):
            d = (x * x + y * y) ** 0.5
            if d <= r - 1:
                _blend(out, size, cx + x, cy + y, rgb, 1.0)
            elif d <= r:
                _blend(out, size, cx + x, cy + y, outline, 1.0)


def _draw_square(out, size, cx, cy, rgb, side):
    half = side // 2
    for y in range(-half, half + 1):
        for x in range(-half, half + 1):
            edge = abs(x) == half or abs(y) == half
            _blend(out, size, cx + x, cy + y, (0, 0, 0) if edge else rgb, 1.0)


def draw_icons(rgba, icons, gold_icon=None, neutral_icon=None, size=SIZE):
    """The minimap `rgba` (size x size) with the war3map.mmp `icons`
    (PreviewIcon objects or dicts of icon_type, x, y and color) drawn on it.
    `gold_icon` and `neutral_icon` are (rgba, width, height) of the game's
    minimap icons, or None for a plain square."""
    out = bytearray(rgba)
    # Start locations last, so they stay on top as in the game.
    ordered = sorted(icons, key=lambda icon: _field(icon, 'icon_type') == START)
    for icon in ordered:
        kind = _field(icon, 'icon_type')
        x, y = int(_field(icon, 'x')), int(_field(icon, 'y'))
        if kind == START:
            r, g, b, _ = _field(icon, 'color')
            _draw_dot(out, size, x, y, (r, g, b), START_SIZE)
        elif kind == GOLD:
            if gold_icon:
                _draw_image(out, size, x, y, gold_icon, ICON_SIZE)
            else:
                _draw_square(out, size, x, y, (255, 215, 0), ICON_SIZE - 4)
        elif kind == NEUTRAL:
            if neutral_icon:
                _draw_image(out, size, x, y, neutral_icon, ICON_SIZE)
            else:
                _draw_square(out, size, x, y, (255, 255, 255), ICON_SIZE - 4)
    return bytes(out)


def _field(icon, name):
    return icon[name] if isinstance(icon, dict) else getattr(icon, name)


def preview_tga(rgba, size=SIZE):
    """war3mapPreview.tga bytes of a `size` square image."""
    return TgaFile.from_rgba(size, size, rgba).to_bytes()


GOLD_ICON = 'War3.w3mod:UI/MiniMap/Minimap-Gold.dds'
NEUTRAL_ICON = 'War3.w3mod:UI/MiniMap/MiniMap-NeutralBuilding.dds'


def game_icons(storage):
    """(gold_icon, neutral_icon) as (rgba, width, height) from a CascStorage,
    each None when the game does not have it."""
    from wc3files.img import DdsFile
    out = []
    for path in (GOLD_ICON, NEUTRAL_ICON):
        try:
            image = DdsFile()
            image.read(storage.read(path))
            out.append((image.get_rgba(), image.width, image.height))
        except Exception:
            out.append(None)
    return tuple(out)
