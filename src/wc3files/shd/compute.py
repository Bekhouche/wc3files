"""
war3map.shd computed from the terrain and the doodads that cast shadows
(#202), in place of the World Editor's Calculate Shadows.

The editor's algorithm is not documented, so this is a model of it, fitted
to the shadow maps the editor wrote into corpus maps:

- what casts: the terrain, and the doodads whose `shadow` is set in
  `Doodads\\DoodadSkins.txt` (or in the map's object data). Destructables,
  trees among them, do not: counting them shadowed most of a forested map
  where its own shadow map has a few strokes;
- the ground: each tile corner's height, with the cliff level of the
  nearest corner, so a cliff is a wall in the middle of its tile rather
  than a slope across it (this matched better than a slope);
- the light: from the south-west (azimuth 225 degrees, measured from +x
  toward +y) at 30 degrees above the horizon, the best of a grid of
  directions and elevations;
- a doodad is its model at rest, posed by its position, rotation and
  scale, and taken as solid from the ground up to the model's top in each
  cell it covers.

A cell is in shadow when a line from its ground toward the light passes
under the top of the terrain or a doodad. The grid is swept once, from the
light's side, carrying the highest shadow surface along, so the cost is one
step per cell.
"""
import math

from .shadow_map_file import ShadowMapFile

AZIMUTH = 225.0
ELEVATION = 30.0
CELL = 32.0
SHADOW = 0xFF


def shadow_grid(terrain):
    """(width, height) in cells: four per tile in each direction."""
    return (terrain['width'] - 1) * 4, (terrain['height'] - 1) * 4


def ground_heights(terrain):
    """The ground height at the centre of each shadow cell, row-major from
    the bottom-left: the corners' ground heights blended, plus the cliff
    level of the nearest corner (128 units a level, level 2 at 0)."""
    width, height = shadow_grid(terrain)
    corners = terrain['width']
    tiles = terrain['tiles']
    ground = [(t['ground_height'] - 8192) / 4 for t in tiles]
    level = [(t['layer_height'] - 2) * 128 for t in tiles]
    out = [0.0] * (width * height)
    # A cell's place in its tile is one of 4 x 4, the same in every tile.
    weights = []
    for sub_y in range(4):
        ty = (sub_y + 0.5) / 4
        for sub_x in range(4):
            tx = (sub_x + 0.5) / 4
            w = ((1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty)
            weights.append((w, max(range(4), key=w.__getitem__)))
    for tile_y in range(terrain['height'] - 1):
        for tile_x in range(corners - 1):
            base = tile_y * corners + tile_x
            ks = (base, base + 1, base + corners, base + corners + 1)
            g = [ground[k] for k in ks]
            for sub_y in range(4):
                row = (tile_y * 4 + sub_y) * width + tile_x * 4
                for sub_x in range(4):
                    w, near = weights[sub_y * 4 + sub_x]
                    out[row + sub_x] = (g[0] * w[0] + g[1] * w[1] + g[2] * w[2] + g[3] * w[3]
                                        + level[ks[near]])
    return out


def model_points(model, spacing=12.0):
    """Points over the surface of a model (the dict ModelFile.get_data()
    gives), at rest, every `spacing` units or closer, keeping the highest in
    each 8-unit square seen from above: what a shadow from above needs."""
    top = {}
    for geoset in model.get('geosets') or []:
        v = geoset.get('vertices') or []
        faces = geoset.get('faces') or []
        for i in range(0, len(faces) - 2, 3):
            a = v[faces[i] * 3:faces[i] * 3 + 3]
            b = v[faces[i + 1] * 3:faces[i + 1] * 3 + 3]
            c = v[faces[i + 2] * 3:faces[i + 2] * 3 + 3]
            if len(a) < 3 or len(b) < 3 or len(c) < 3:
                continue
            n = max(1, int(max(math.dist(a, b), math.dist(b, c), math.dist(a, c)) / spacing))
            for p in range(n + 1):
                for q in range(n + 1 - p):
                    r = n - p - q
                    x = (a[0] * p + b[0] * q + c[0] * r) / n
                    y = (a[1] * p + b[1] * q + c[1] * r) / n
                    z = (a[2] * p + b[2] * q + c[2] * r) / n
                    key = (round(x / 8), round(y / 8))
                    if key not in top or z > top[key][2]:
                        top[key] = (x, y, z)
    return list(top.values())


def occluders(terrain, ground, doodads, casters):
    """The top of what casts shadows in each cell: the ground, raised where a
    casting doodad stands. `casters(doodad record)` gives the model points
    of a doodad that casts, or None."""
    width, height = shadow_grid(terrain)
    out = list(ground)
    ox, oy = terrain['offset_x'], terrain['offset_y']
    for record in doodads:
        points = casters(record)
        if not points:
            continue
        x0, y0, z0 = record['position']
        sx, sy, sz = record['scale']
        cos_r, sin_r = math.cos(record['rotation']), math.sin(record['rotation'])
        for x, y, z in points:
            x, y = x * sx, y * sy
            cx = int((x0 + x * cos_r - y * sin_r - ox) // CELL)
            cy = int((y0 + x * sin_r + y * cos_r - oy) // CELL)
            if 0 <= cx < width and 0 <= cy < height:
                k = cy * width + cx
                z = z0 + z * sz
                if z > out[k]:
                    out[k] = z
    return out


def cast(width, height, ground, occluder, azimuth=AZIMUTH, elevation=ELEVATION,
         margin=1.0):
    """The shadow cells (bytes, SHADOW or 0) of a height field lit from
    `azimuth` degrees (the direction toward the light, from +x toward +y)
    at `elevation` degrees.

    Each cell takes the shadow surface of the cell one step toward the
    light: the higher of that cell's occluder and its own surface, lowered
    by the step times the tangent of the elevation. A step is one cell
    along the axis the light is closer to, and the other coordinate falls
    between two cells, whose values are blended. A cell is in shadow when
    the surface is more than `margin` above its ground."""
    lx, ly = math.cos(math.radians(azimuth)), math.sin(math.radians(azimuth))
    big = max(abs(lx), abs(ly))
    sx, sy = lx / big, ly / big
    if abs(sx) < 1e-9:
        sx = 0.0
    if abs(sy) < 1e-9:
        sy = 0.0
    drop = math.hypot(sx, sy) * CELL * math.tan(math.radians(elevation))
    surface = [-1e30] * (width * height)
    out = bytearray(width * height)
    along_x = abs(sx) >= abs(sy)
    # One step toward the light must land on cells already swept.
    xs = list(range(width - 1, -1, -1) if sx > 0 else range(width))
    ys = list(range(height - 1, -1, -1) if sy > 0 else range(height))
    if along_x:
        step, other = int(round(sx)), sy
        for x in xs:
            qx = x + step
            if not 0 <= qx < width:
                continue
            for y in ys:
                qy = y + other
                y0 = math.floor(qy)
                f = qy - y0
                total = weight = 0.0
                if 0 <= y0 < height and f < 1:
                    k = y0 * width + qx
                    s = occluder[k] if occluder[k] > surface[k] else surface[k]
                    total += s * (1 - f)
                    weight += 1 - f
                if 0 <= y0 + 1 < height and f > 0:
                    k = (y0 + 1) * width + qx
                    s = occluder[k] if occluder[k] > surface[k] else surface[k]
                    total += s * f
                    weight += f
                if weight:
                    k = y * width + x
                    s = total / weight - drop
                    surface[k] = s
                    if s > ground[k] + margin:
                        out[k] = SHADOW
    else:
        step, other = int(round(sy)), sx
        for y in ys:
            qy = y + step
            if not 0 <= qy < height:
                continue
            row = qy * width
            for x in xs:
                qx = x + other
                x0 = math.floor(qx)
                f = qx - x0
                total = weight = 0.0
                if 0 <= x0 < width and f < 1:
                    k = row + x0
                    s = occluder[k] if occluder[k] > surface[k] else surface[k]
                    total += s * (1 - f)
                    weight += 1 - f
                if 0 <= x0 + 1 < width and f > 0:
                    k = row + x0 + 1
                    s = occluder[k] if occluder[k] > surface[k] else surface[k]
                    total += s * f
                    weight += f
                if weight:
                    k = y * width + x
                    s = total / weight - drop
                    surface[k] = s
                    if s > ground[k] + margin:
                        out[k] = SHADOW
    return out


def compute_shadow_map(terrain, doodads=(), casters=None, azimuth=AZIMUTH,
                       elevation=ELEVATION):
    """A ShadowMapFile for `terrain` (TerrainFile.get_data()), with the
    doodad records of war3map.doo (`MapDoodadsFile.get_data()['doodads']`)
    whose `casters(record)` gives model points. Without casters, only the
    terrain casts."""
    width, height = shadow_grid(terrain)
    ground = ground_heights(terrain)
    occluder = occluders(terrain, ground, doodads, casters) if casters else ground
    cells = cast(width, height, ground, occluder, azimuth, elevation)
    return ShadowMapFile().import_data({'width': width, 'height': height,
                                        'cells': bytes(cells)})


# -- which doodads cast, and their models ----------------------------------------

def read_profile(text):
    """{section: {key (lower case): value}} of a profile text file such as
    DoodadSkins.txt; the first value of a key wins, as in the game."""
    out, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            current = out.setdefault(line[1:-1], {})
        elif current is not None and '=' in line and not line.startswith('//'):
            key, value = line.split('=', 1)
            current.setdefault(key.strip().lower(), value.strip())
    return out


def _object_field(entry, field):
    found = (entry or {}).get(field)
    if not found:
        return None
    values = found.get('value') or {}
    return next(iter(values.values()), None) if values else None


class DoodadCasters:
    """`casters` for compute_shadow_map: which doodads cast and the points of
    their models.

    `skins` is DoodadSkins.txt read by read_profile; `read_file(path)` gives
    the bytes of a model path (map members first, then the game), or None;
    `object_data` is the map's doodad object data as ObjectFile gives it
    ({'original': {...}, 'custom': {...}}), whose model (dfil), variation
    count (dvar) and shadow (dshd) override the profile's. `counts` tells,
    after a run, how many records cast and why the others did not."""

    def __init__(self, skins, read_file, object_data=None):
        self.skins = skins
        self.read_file = read_file
        self.original = (object_data or {}).get('original') or {}
        self.custom = (object_data or {}).get('custom') or {}
        self._points = {}
        self.counts = {}
        self.missing_models = set()

    def _count(self, what):
        self.counts[what] = self.counts.get(what, 0) + 1

    def _fields(self, type_id):
        entry = self.custom.get(type_id)
        base = entry.get('ID') if entry else type_id
        if base not in self.skins:
            return None
        skin = self.skins[base]
        own = entry or self.original.get(type_id) or {}
        model = _object_field(own, 'dfil') or skin.get('file')
        variations = _object_field(own, 'dvar') or skin.get('numvar') or 1
        shadow = _object_field(own, 'dshd')
        if shadow is None:
            shadow = skin.get('shadow', '0')
        return model, int(variations or 1), str(shadow) not in ('0', '', 'False', 'false')

    def points(self, path):
        if path in self._points:
            return self._points[path]
        points = None
        stem = path[:-4] if path.lower().endswith(('.mdx', '.mdl')) else path
        for name in (stem + '.mdx', stem + '.mdl'):
            data = self.read_file(name)
            if data is None:
                continue
            try:
                if name.endswith('.mdx'):
                    from wc3files.mdl.mdx_io import load_mdx
                    model = load_mdx(data)
                else:
                    from wc3files.mdl.mdl_io import load_mdl
                    model = load_mdl(data.decode('utf-8', 'replace'))
            except Exception:
                continue
            points = model_points(model)
            break
        if points is None:
            self.missing_models.add(path)
        self._points[path] = points
        return points

    def __call__(self, record):
        type_id = record.get('skin') if record.get('skin') else record['id']
        fields = self._fields(type_id) or self._fields(record['id'])
        if fields is None:
            self._count('not a doodad')     # destructables, unknown types
            return None
        model, variations, shadow = fields
        if not shadow:
            self._count('no shadow')
            return None
        if not model:
            self._count('no model')
            return None
        # A doodad of one variation names its model; of several, the model
        # is the name and the variation number (Brazier.mdx, Lords_Rock3.mdx).
        if model.lower().endswith(('.mdx', '.mdl')):
            model = model[:-4]
        if variations > 1:
            points = self.points(f'{model}{record.get("variation", 0) % variations}')
        else:
            points = self.points(model)
        if not points:
            self._count('model not found')
            return None
        self._count('cast')
        return points


SKINS_PATH = 'War3.w3mod:Doodads/DoodadSkins.txt'


def file_reader(map_files=None, storage=None):
    """read_file for DoodadCasters: a path (Doodads\\Props\\X.mdx) from the
    map's members, compared without regard to case or slash direction, then
    from a CascStorage of the installed game."""
    members = {}
    for name, data in (map_files or {}).items():
        if data is not None:
            members[name.replace('/', '\\').lower()] = data

    def read_file(path):
        key = path.replace('/', '\\').lower()
        if key in members:
            return members[key]
        if storage is None:
            return None
        try:
            return storage.read('War3.w3mod:' + path.replace('\\', '/'))
        except Exception:
            return None
    return read_file


def game_casters(storage, map_files=None, object_data=None):
    """DoodadCasters for a map, with the doodad table and models of the game
    open as `storage` (a CascStorage), and the map's own members and doodad
    object data."""
    skins = read_profile(storage.read(SKINS_PATH).decode('utf-8', 'replace'))
    return DoodadCasters(skins, file_reader(map_files, storage), object_data)
