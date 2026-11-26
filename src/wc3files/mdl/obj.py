"""
Wavefront OBJ to and from MDX (#244), as OBJ2MDL does for static models.

`obj_to_model(text, materials=None, up='y', scale=1.0, texture_dir=None)`
makes a static model from an OBJ file and its MTL materials:

- one geoset per group (`o` or `g`) and material (`usemtl`) used together;
- polygons cut into triangles as fans, which is right for the convex faces
  modelling tools write;
- a vertex for each distinct position, texture coordinate and normal used
  together, as MDX keeps one of each per vertex; a missing texture
  coordinate is (0, 0), and a geoset some of whose corners have no normal
  gets normals recomputed from its faces (`normals.py`);
- a material per MTL material, its texture `map_Kd` with the extension
  `.blp` (in `texture_dir` when given); a material with no texture uses the
  game's `Textures\\White.blp`;
- one bone, `Root`, that every vertex follows, a `Stand` sequence of one
  second, and extents from the vertices.

OBJ files are most often made with Y up, and Warcraft III has Z up: with
`up='y'` (the default) a point (x, y, z) becomes (x, -z, y); `up='z'` keeps
it. Texture coordinates have V upwards in OBJ and downwards in MDX, so V
becomes 1 - V. Indices may be negative, counted back from the last vertex,
as the format allows; `l` and `p` (lines and points) and smoothing groups
are left out.

`model_to_obj(model, sequence=None, frame=None, up='y')` writes a model as
OBJ and MTL text: its vertices posed at `frame` of `sequence` (by name; the
rest pose without one), normals turned with them, one group per geoset
shown at that frame, and the texture of each material's first layer.
"""
import math

from .animation import geoset_alpha, node_matrices, posed_vertices, vertex_bones
from .batch import model_data

BS = chr(92)
WHITE = 'Textures' + BS + 'White.blp'
NONE = 0xFFFFFFFF


def _to_game(p, up):
    return (p[0], -p[2], p[1]) if up == 'y' else tuple(p)


def _from_game(p, up):
    return (p[0], p[2], -p[1]) if up == 'y' else tuple(p)


def read_mtl(text):
    """{material name: texture path or None} from MTL text."""
    found, current = {}, None
    for raw in text.splitlines():
        parts = raw.strip().split(None, 1)
        if not parts or parts[0].startswith('#'):
            continue
        if parts[0] == 'newmtl' and len(parts) > 1:
            current = parts[1].strip()
            found[current] = None
        elif parts[0] == 'map_Kd' and current is not None and len(parts) > 1:
            # Options such as -s 1 1 1 come before the file name.
            found[current] = parts[1].split()[-1]
    return found


def read_obj(text):
    """The parts of an OBJ file: positions, texture coordinates, normals,
    MTL file names, and faces as (group, material, [(v, vt, vn)]), indices
    from 0 and None where absent."""
    positions, uvs, normals, libraries, faces = [], [], [], [], []
    group, material = 'default', None

    def index(value, count):
        if not value:
            return None
        i = int(value)
        return i - 1 if i > 0 else count + i

    lines = text.replace(BS + '\r\n', ' ').replace(BS + '\n', ' ').splitlines()
    for raw in lines:
        parts = raw.split('#', 1)[0].split()
        if not parts:
            continue
        head, args = parts[0], parts[1:]
        if head == 'v' and len(args) >= 3:
            positions.append(tuple(float(a) for a in args[:3]))
        elif head == 'vt' and args:
            uvs.append((float(args[0]), float(args[1]) if len(args) > 1 else 0.0))
        elif head == 'vn' and len(args) >= 3:
            normals.append(tuple(float(a) for a in args[:3]))
        elif head == 'f' and len(args) >= 3:
            corners = []
            for corner in args:
                bits = (corner.split('/') + ['', ''])[:3]
                corners.append((index(bits[0], len(positions)), index(bits[1], len(uvs)),
                                index(bits[2], len(normals))))
            faces.append((group, material, corners))
        elif head in ('o', 'g'):
            group = ' '.join(args) or 'default'
        elif head == 'usemtl':
            material = ' '.join(args) or None
        elif head == 'mtllib':
            libraries.extend(args)
    return {'positions': positions, 'uvs': uvs, 'normals': normals,
            'libraries': libraries, 'faces': faces}


def _texture_path(path, texture_dir):
    if not path:
        return WHITE
    name = path.replace('/', BS).rsplit(BS, 1)[-1]
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    folder = path.replace('/', BS).rsplit(BS, 1)[0] + BS if BS in path.replace('/', BS) else ''
    if texture_dir is not None:
        folder = texture_dir.rstrip(BS + '/') + BS if texture_dir else ''
    return folder + stem + '.blp'


def obj_to_model(text, materials=None, up='y', scale=1.0, texture_dir=None):
    """A static model's data from OBJ text. `materials` is {name: texture
    path or None}, as read_mtl gives. Returns the data; see the module."""
    from .extents import compute_extents
    from .mdx_io import empty_model
    from .normals import geoset_normals
    if up not in ('y', 'z'):
        raise ValueError(f"up is 'y' or 'z', not {up!r}")
    obj = read_obj(text)
    if not obj['faces']:
        raise ValueError('the OBJ file has no faces')
    materials = dict(materials or {})
    model = empty_model()
    model['version'] = 800
    model['name'] = 'Model'
    textures, material_index = [], {}

    def material_of(name):
        if name not in material_index:
            path = _texture_path(materials.get(name), texture_dir)
            if path not in [t['path'] for t in textures]:
                textures.append({'replaceableId': 0, 'path': path, 'flags': 0})
            texture_id = [t['path'] for t in textures].index(path)
            material_index[name] = len(model['materials'])
            model['materials'].append({'priorityPlane': 0, 'flags': 0, 'layers': [{
                'filterMode': 0, 'shadingFlags': 0, 'textureId': texture_id,
                'textureAnimationId': NONE, 'coordId': 0, 'alpha': 1.0, 'tracks': {}}]})
        return material_index[name]

    groups = {}
    for group, material, corners in obj['faces']:
        groups.setdefault((group, material), []).append(corners)
    for (group, material), polygons in groups.items():
        has_normals = True
        lookup, vertices, normals, uvs, faces = {}, [], [], [], []
        for corners in polygons:
            ids = []
            for v, vt, vn in corners:
                if v is None or not 0 <= v < len(obj['positions']):
                    raise ValueError(f'a face of group {group!r} uses a vertex that does not exist')
                key = (v, vt, vn)
                if key not in lookup:
                    lookup[key] = len(lookup)
                    p = _to_game(obj['positions'][v], up)
                    vertices.extend(c * scale for c in p)
                    if vn is not None and 0 <= vn < len(obj['normals']):
                        n = _to_game(obj['normals'][vn], up)
                        length = math.sqrt(sum(c * c for c in n)) or 1.0
                        normals.extend(c / length for c in n)
                    else:
                        has_normals = False
                        normals.extend((0.0, 0.0, 1.0))
                    u, w = obj['uvs'][vt] if vt is not None and 0 <= vt < len(obj['uvs']) else (0.0, 0.0)
                    uvs.extend((u, 1.0 - w))
                ids.append(lookup[key])
            for i in range(1, len(ids) - 1):
                faces.extend((ids[0], ids[i], ids[i + 1]))
        if len(lookup) > 65535:
            raise ValueError(f'group {group!r} has {len(lookup)} vertices; MDX holds 65,535')
        count = len(lookup)
        model['geosets'].append({
            'vertices': vertices, 'normals': normals, 'faceTypeGroups': [4],
            'faceGroups': [len(faces)], 'faces': faces, 'vertexGroups': [0] * count,
            'matrixGroups': [1], 'matrixIndices': [0], 'materialId': material_of(material),
            'selectionGroup': 0, 'selectionFlags': 0,
            'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3, 'maximum': [0.0] * 3},
            'sequenceExtents': [], 'uvSets': [uvs]})
        if not has_normals:
            geoset = model['geosets'][-1]
            geoset['normals'] = geoset_normals(geoset)
    model['textures'] = textures
    model['bones'] = [{'node': {'name': 'Root', 'objectId': 0, 'parentId': NONE,
                                'flags': 256, 'tracks': {}},
                       'geosetId': NONE, 'geosetAnimationId': NONE}]
    model['pivotPoints'] = [[0.0, 0.0, 0.0]]
    model['sequences'] = [{'name': 'Stand', 'interval': [0, 1000], 'moveSpeed': 0.0,
                           'flags': 0, 'rarity': 0.0, 'syncPoint': 0,
                           'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3,
                                      'maximum': [0.0] * 3}}]
    compute_extents(model, frames=1)
    return model


def _turn(m, n):
    """A normal turned by the rotation and scale of matrix `m`, unit length."""
    x = m[0] * n[0] + m[1] * n[1] + m[2] * n[2]
    y = m[4] * n[0] + m[5] * n[1] + m[6] * n[2]
    z = m[8] * n[0] + m[9] * n[1] + m[10] * n[2]
    length = math.sqrt(x * x + y * y + z * z) or 1.0
    return (x / length, y / length, z / length)


def _format(values):
    return ' '.join(f'{v:.6f}'.rstrip('0').rstrip('.') if v else '0' for v in values)


def model_to_obj(model, sequence=None, frame=None, up='y', mtl_name='model.mtl'):
    """(OBJ text, MTL text) of `model` (a ModelFile or its data), posed at
    `frame` of the sequence named `sequence`, or at its first frame; in the
    rest pose with no sequence."""
    if up not in ('y', 'z'):
        raise ValueError(f"up is 'y' or 'z', not {up!r}")
    model = model_data(model)
    interval, matrices = None, {}
    if sequence is not None:
        found = [s for s in model.get('sequences') or []
                 if (s.get('name') or '').lower() == sequence.lower()]
        if not found:
            raise ValueError(f'the model has no sequence {sequence!r}')
        interval = tuple(found[0]['interval'])
        frame = interval[0] if frame is None else frame
        if not interval[0] <= frame <= interval[1]:
            raise ValueError(f'frame {frame} is outside {sequence} {list(interval)}')
        matrices = node_matrices(model, frame, interval)
    textures = model.get('textures') or []
    lines = ['# Written by wc3files', f'mtllib {mtl_name}']
    mtl = ['# Written by wc3files']
    base = 1
    for index, geoset in enumerate(model.get('geosets') or []):
        if interval is not None and geoset_alpha(model, index, frame, interval) <= 0:
            continue
        bones = vertex_bones(geoset)
        points = posed_vertices(geoset, matrices, bones) if interval else \
            posed_vertices(geoset, {}, [])
        raw_normals = geoset.get('normals') or []
        uvs = (geoset.get('uvSets') or [[]])[0]
        name = f'geoset_{index}'
        lines.append(f'o {name}')
        material = f'material_{geoset.get("materialId", 0)}'
        lines.append(f'usemtl {material}')
        for p in points:
            lines.append('v ' + _format(_from_game(p, up)))
        for i in range(len(points)):
            u, v = (uvs[i * 2], uvs[i * 2 + 1]) if len(uvs) >= i * 2 + 2 else (0.0, 0.0)
            lines.append('vt ' + _format((u, 1.0 - v)))
        for i in range(len(points)):
            n = raw_normals[i * 3:i * 3 + 3] or [0.0, 0.0, 1.0]
            if interval and bones[i]:
                blended = [0.0] * 12
                for object_id, weight in bones[i]:
                    for k, x in enumerate(matrices.get(object_id, (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0))):
                        blended[k] += x * weight
                n = _turn(blended, n)
            lines.append('vn ' + _format(_from_game(n, up)))
        faces = geoset.get('faces') or []
        for i in range(0, len(faces) - 2, 3):
            a, b, c = (base + faces[i + k] for k in range(3))
            lines.append(f'f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}')
        base += len(points)
    for index, material in enumerate(model.get('materials') or []):
        mtl.append(f'newmtl material_{index}')
        layers = material.get('layers') or []
        texture_id = None
        if layers:
            entries = layers[0].get('textures')
            texture_id = entries[0]['textureId'] if entries else layers[0].get('textureId')
        if texture_id is not None and 0 <= texture_id < len(textures) and textures[texture_id].get('path'):
            mtl.append('map_Kd ' + textures[texture_id]['path'].replace(BS, '/'))
    return '\n'.join(lines) + '\n', '\n'.join(mtl) + '\n'
