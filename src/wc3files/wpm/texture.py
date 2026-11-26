"""
Pathing textures (#262): the small TGA images with which buildings,
doodads and destructables block pathing.

Each pixel is one pathing cell, 32 game units across, the cell of
war3map.wpm, and the image is centered on the object. A color channel at 128
or more blocks one kind of pathing:

    red    no_walk
    green  no_fly
    blue   no_build

This is the game's convention, checked on its own PathTextures: Unbuildable
textures are blue, Unflyable ones white, SimpleSolid ones magenta (no walking
or building, flying allowed), and the Simple ones a magenta core in a blue
ring. Alpha carries nothing: the game's textures have 0 in some files and
255 in others.

`PathingTexture` holds the cells as rows of bit masks, the first row the
image's top (north). `from_model` makes one from a model's footprint.
"""
import math

NO_WALK, NO_FLY, NO_BUILD = 1, 2, 4
FLAG_BITS = {NO_WALK: 'no_walk', NO_FLY: 'no_fly', NO_BUILD: 'no_build'}
CELL = 32.0
# A building's usual cells: no walking or building, and flying allowed.
SOLID = NO_WALK | NO_BUILD


def decode_flags(mask):
    return {name: bool(mask & bit) for bit, name in FLAG_BITS.items()}


def encode_flags(named_flags):
    mask = 0
    for bit, name in FLAG_BITS.items():
        if named_flags.get(name):
            mask |= bit
    return mask


class PathingTexture:
    """A pathing texture: `width` x `height` cells, `cells[row][column]` a
    mask of NO_WALK, NO_FLY and NO_BUILD."""

    def __init__(self, width=0, height=0, cells=None):
        self.width = width
        self.height = height
        self.cells = cells if cells is not None else [[0] * width for _ in range(height)]

    def read(self, file_path):
        from wc3files.img import TgaFile
        image = TgaFile()
        image.read(file_path)
        rgba = image.get_rgba()
        self.width, self.height = image.width, image.height
        self.cells = []
        for row in range(self.height):
            line = []
            for column in range(self.width):
                r, g, b = rgba[(row * self.width + column) * 4:(row * self.width + column) * 4 + 3]
                line.append((NO_WALK if r >= 128 else 0) | (NO_FLY if g >= 128 else 0)
                            | (NO_BUILD if b >= 128 else 0))
            self.cells.append(line)
        return self

    def to_rgba(self):
        out = bytearray()
        for line in self.cells:
            for mask in line:
                out += bytes((255 if mask & NO_WALK else 0, 255 if mask & NO_FLY else 0,
                              255 if mask & NO_BUILD else 0, 255))
        return bytes(out)

    def to_bytes(self):
        from wc3files.img import TgaFile
        return TgaFile.from_rgba(self.width, self.height, self.to_rgba()).to_bytes()

    def write(self, file_path):
        with open(file_path, 'wb') as handle:
            handle.write(self.to_bytes())

    def get_data(self):
        """The cells as text, one line per row and one digit (the mask, 0 to
        7) per cell: 5 is a building's cell, 4 unbuildable, 7 blocked."""
        return {'width': self.width, 'height': self.height,
                'rows': [''.join(str(mask) for mask in line) for line in self.cells]}

    def import_data(self, data):
        rows = data['rows']
        widths = {len(row) for row in rows}
        if len(widths) > 1:
            raise ValueError('every row of a pathing texture has the same length')
        if any(ch not in '01234567' for row in rows for ch in row):
            raise ValueError('a pathing texture cell is a digit from 0 to 7')
        self.height = len(rows)
        self.width = widths.pop() if widths else 0
        self.cells = [[int(ch) for ch in row] for row in rows]
        return self


def _triangles(model):
    for geoset in model.get('geosets', []):
        vertices = geoset.get('vertices', [])
        points = [tuple(vertices[i:i + 3]) for i in range(0, len(vertices) - 2, 3)]
        faces = geoset.get('faces', [])
        for i in range(0, len(faces) - 2, 3):
            try:
                yield points[faces[i]], points[faces[i + 1]], points[faces[i + 2]]
            except IndexError:
                continue


def _side(px, py, q, r):
    """Which side of the edge q -> r the point (px, py) is on."""
    return (px - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (py - r[1])


def _inside(px, py, a, b, c):
    """True when (px, py) is in the triangle a, b, c projected on the ground."""
    d1, d2, d3 = _side(px, py, a, b), _side(px, py, b, c), _side(px, py, c, a)
    negative = d1 < 0 or d2 < 0 or d3 < 0
    positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (negative and positive)


def from_model(model, width, height, max_height=None, mask=SOLID, border=0, snap=2):
    """A `width` x `height` pathing texture of a model's footprint, seen from
    above. The cells under the model's triangles take `mask`; with
    `max_height`, only triangles that come that close to the ground count.
    With `snap`, the cells go in `snap` x `snap` blocks, as the game's own
    textures are drawn on its 64-unit build grid: a block is blocked when at
    least half its cells are. Then `border` rings of cells around them take
    NO_BUILD, as the game's Simple textures have. `model` is a ModelFile or
    its get_data() dict, centered on its origin.

    Against the 196 game buildings that have a model and a pathing texture
    with no_walk cells, the no_walk cells of the default footprint and of
    the game's texture overlap with a median intersection over union of
    0.80 (0.75 without snapping; 0.61 with only the triangles within 64
    units of the ground)."""
    if hasattr(model, 'get_data'):
        model = model.get_data()
    left, top = -width * CELL / 2, height * CELL / 2
    texture = PathingTexture(width, height)
    samples = (0.25, 0.75)
    for a, b, c in _triangles(model):
        if max_height is not None and min(a[2], b[2], c[2]) > max_height:
            continue
        xs, ys = (a[0], b[0], c[0]), (a[1], b[1], c[1])
        first_col = max(0, int(math.floor((min(xs) - left) / CELL)))
        last_col = min(width - 1, int(math.floor((max(xs) - left) / CELL)))
        first_row = max(0, int(math.floor((top - max(ys)) / CELL)))
        last_row = min(height - 1, int(math.floor((top - min(ys)) / CELL)))
        for row in range(first_row, last_row + 1):
            for column in range(first_col, last_col + 1):
                if texture.cells[row][column] & mask == mask:
                    continue
                x0, y0 = left + column * CELL, top - (row + 1) * CELL
                hit = any(_inside(x0 + sx * CELL, y0 + sy * CELL, a, b, c)
                          for sx in samples for sy in samples)
                # A vertex inside the cell, for a triangle too thin to cover a
                # sample point; one on the cell's edge only touches it.
                hit = hit or any(x0 < p[0] < x0 + CELL and y0 < p[1] < y0 + CELL
                                 for p in (a, b, c))
                if hit:
                    texture.cells[row][column] |= mask
    if snap and snap > 1:
        snapped = [[0] * width for _ in range(height)]
        for row in range(0, height, snap):
            for column in range(0, width, snap):
                block = [(r, c) for r in range(row, min(row + snap, height))
                         for c in range(column, min(column + snap, width))]
                covered = sum(1 for r, c in block if texture.cells[r][c])
                if covered * 2 >= len(block):
                    for r, c in block:
                        snapped[r][c] = mask
        texture.cells = snapped
    for _ in range(border):
        grown = [line[:] for line in texture.cells]
        for row in range(height):
            for column in range(width):
                if texture.cells[row][column]:
                    continue
                if any(0 <= row + dr < height and 0 <= column + dc < width
                       and texture.cells[row + dr][column + dc]
                       for dr in (-1, 0, 1) for dc in (-1, 0, 1)):
                    grown[row][column] = NO_BUILD
        texture.cells = grown
    return texture
