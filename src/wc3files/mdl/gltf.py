"""
glTF 2.0 to and from MDX (#245).

`gltf_to_model(source)` reads a `.glb` file, or a `.gltf` file with its
buffers (files beside it or `data:` URIs), and makes a version 800 model:

- every node of the scene becomes a bone, under the same parent. Its MDX
  matrix is the glTF skinning matrix (the node's global matrix times its
  inverse bind matrix), broken down at each frame into the pivot,
  translation, rotation and scaling MDX keeps; a node that is not a joint
  takes the inverse of its rest matrix, so a mesh without a skin follows its
  own node;
- each primitive (triangles, strips and fans) becomes a geoset. A skinned
  vertex keeps the bones that weigh at least `min_weight` (0.2 by default,
  at least its heaviest one), up to 4, in equal parts: version 800 holds
  matrix groups, not weights, which is the main limit of the conversion
  (measured on the samples below). A vertex without a skin follows the bone
  of its node, at its rest place;
- each animation becomes a sequence, the animations one after another 1 s
  apart, with linear keys at the frames of its channels (and every 1/30 s
  on CUBICSPLINE channels); keys that change nothing are then removed
  (`keyframes.py`). A model without animations gets a `Stand` sequence of
  one second in its rest pose;
- each material becomes a material with the base color texture as `.blp`
  (in `texture_dir` when given); `alphaMode` MASK and BLEND give the
  Transparent and Blend filter modes, and `doubleSided` two-sided layers.
  Embedded PNG and JPEG images are converted to BLP and returned, for the
  caller to write next to the model.

Not taken: morph targets and their weight animations, cameras and lights,
textures other than the base color (normal, metallic-roughness and the
like, which version 800 cannot use), vertex colours, a second set of
texture coordinates, and sparse accessors (which raise). They are listed in
the report's `notes`.

glTF has Y up and faces +Z; the game has Z up and faces +X. A point
(x, y, z) becomes (z, x, y) times `scale`, which keeps the model's
handedness, and the inverse on the way back. glTF lengths are metres and the
game's are its own units, so `scale` is often 50 to 100.

`model_to_gltf(model)` writes a model as a `.glb` file (bytes): one mesh
with a primitive per geoset, a skin with a joint per node, and an animation
per sequence. MDX moves a node about its pivot; a joint whose rest place is
its pivot, translated by the pivot's offset from its parent's, does the
same, so the matrices carry over exactly. Keys inside each sequence are
written as LINEAR or STEP, sampled every 1/30 s where the MDX track is
Hermite or Bezier. Each vertex keeps up to 4 bones: the 4 heaviest of a
skin, or the first 4 of a matrix group, in equal parts. glTF has no
global sequences, so a global sequence track is baked into every animation
on its own clock; geoset visibility is not written. Textures are named by their
path, as `.png`; with `textures`, a function that gives an image's bytes
for a path, they are converted and embedded.
"""
import base64
import json
import math
import os
import struct

from .animation import NONE, compose, nodes, sample, vertex_bones
from .batch import model_data

BS = chr(92)
WHITE = 'Textures' + BS + 'White.blp'
GLB_MAGIC = 0x46546C67
COMPONENT = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2), 5123: ('H', 2),
             5125: ('I', 4), 5126: ('f', 4)}
WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
NORMALIZE = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}


# 4x4 matrices, row-major lists of 16.

IDENTITY4 = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _mul(a, b):
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def _inverse(m):
    """The inverse of an affine matrix."""
    a, b, c, d, e, f, g, h, i = m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-20:
        return list(IDENTITY4)
    inv = [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
           (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
           (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]
    t = (m[3], m[7], m[11])
    out = []
    for r in range(3):
        row = inv[r * 3:r * 3 + 3]
        out.extend(row + [-(row[0] * t[0] + row[1] * t[1] + row[2] * t[2])])
    return out + [0.0, 0.0, 0.0, 1.0]


def _from_columns(values):
    """A glTF column-major matrix as row-major."""
    return [values[c * 4 + r] for r in range(4) for c in range(4)]


def _to_columns(m):
    return [m[r * 4 + c] for c in range(4) for r in range(4)]


def _trs(t, q, s):
    m = list(compose((0.0, 0.0, 0.0), t, q, s))
    return m + [0.0, 0.0, 0.0, 1.0]


def _quaternion(r):
    """A unit quaternion (x, y, z, w) from a rotation matrix's 3x3 rows."""
    m00, m01, m02, m10, m11, m12, m20, m21, m22 = r
    trace = m00 + m11 + m22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return [x / n for x in q]


def _decompose(m, pivot):
    """(translation, rotation, scaling) with m = T(pivot) T(t) R S T(-pivot)."""
    cols = [[m[0], m[4], m[8]], [m[1], m[5], m[9]], [m[2], m[6], m[10]]]
    scale = [math.sqrt(sum(x * x for x in col)) for col in cols]
    # A bone scaled to 0 on an axis has no direction there: the axis is
    # made from the other two, or taken as it is when none is left.
    units = [[x / s for x in col] if s > 1e-9 else None for col, s in zip(cols, scale)]
    axes = ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    for k in range(3):
        if units[k] is None:
            a, b = units[(k + 1) % 3], units[(k + 2) % 3]
            if a is not None and b is not None:
                v = cross(a, b)
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                units[k] = [x / n for x in v]
            elif a is not None or b is not None:
                known = a if a is not None else b
                helper = axes[0] if abs(known[0]) < 0.9 else axes[1]
                v = cross(known, helper)
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                units[k] = [x / n for x in v]
            else:
                units[k] = list(axes[k])
    det = (units[0][0] * (units[1][1] * units[2][2] - units[2][1] * units[1][2])
           - units[1][0] * (units[0][1] * units[2][2] - units[2][1] * units[0][2])
           + units[2][0] * (units[0][1] * units[1][2] - units[1][1] * units[0][2]))
    if det < 0:
        scale[0] = -scale[0]
        units[0] = [-x for x in units[0]]
    r = [units[0][0], units[1][0], units[2][0],
         units[0][1], units[1][1], units[2][1],
         units[0][2], units[1][2], units[2][2]]
    rotation = _quaternion(r)
    lp = [m[row * 4] * pivot[0] + m[row * 4 + 1] * pivot[1] + m[row * 4 + 2] * pivot[2]
          for row in range(3)]
    translation = [m[row * 4 + 3] - pivot[row] + lp[row] for row in range(3)]
    return translation, rotation, scale


# Reading glTF.

def _load(source, read_file=None):
    """(json, [buffer bytes], read_file) of a .glb or .gltf path or bytes."""
    base = None
    if isinstance(source, str):
        base = os.path.dirname(os.path.abspath(source))
        with open(source, 'rb') as handle:
            data = handle.read()
        if read_file is None:
            def read_file(uri):
                with open(os.path.join(base, uri.replace('%20', ' ')), 'rb') as handle:
                    return handle.read()
    else:
        data = bytes(source)
    binary = None
    if data[:4] == b'glTF':
        magic, version, length = struct.unpack_from('<III', data, 0)
        if version != 2:
            raise ValueError(f'glTF version {version}; only 2 is read')
        pos, document = 12, None
        while pos + 8 <= min(length, len(data)):
            size, kind = struct.unpack_from('<II', data, pos)
            chunk = data[pos + 8:pos + 8 + size]
            if kind == 0x4E4F534A:
                document = json.loads(chunk.decode('utf-8'))
            elif kind == 0x004E4942:
                binary = chunk
            pos += 8 + size
        if document is None:
            raise ValueError('a .glb file with no JSON chunk')
    else:
        document = json.loads(data.decode('utf-8-sig'))
    if not str(document.get('asset', {}).get('version', '2')).startswith('2'):
        raise ValueError('only glTF 2.0 is read')
    buffers = []
    for buffer in document.get('buffers') or []:
        uri = buffer.get('uri')
        if uri is None:
            buffers.append(binary or b'')
        elif uri.startswith('data:'):
            buffers.append(base64.b64decode(uri.split(',', 1)[1]))
        elif read_file is not None:
            buffers.append(read_file(uri))
        else:
            raise ValueError(f'buffer {uri!r} is a file, and the glTF was given as bytes')
    return document, buffers, read_file


class _Reader:
    def __init__(self, document, buffers, read_file):
        self.doc, self.buffers, self.read_file = document, buffers, read_file
        self.cache = {}

    def view(self, index):
        view = self.doc['bufferViews'][index]
        data = self.buffers[view['buffer']]
        start = view.get('byteOffset', 0)
        return data[start:start + view['byteLength']], view.get('byteStride')

    def accessor(self, index):
        if index not in self.cache:
            self.cache[index] = self._accessor(index)
        return self.cache[index]

    def _accessor(self, index):
        acc = self.doc['accessors'][index]
        if acc.get('sparse'):
            raise ValueError('sparse accessors are not read')
        code, size = COMPONENT[acc['componentType']]
        width = WIDTH[acc['type']]
        count = acc['count']
        if 'bufferView' not in acc:
            return [tuple([0] * width)] * count
        raw, stride = self.view(acc['bufferView'])
        offset = acc.get('byteOffset', 0)
        stride = stride or size * width
        unpack = struct.Struct('<' + code * width).unpack_from
        norm = NORMALIZE.get(acc['componentType']) if acc.get('normalized') else None
        out = []
        for i in range(count):
            values = unpack(raw, offset + i * stride)
            if norm:
                values = tuple(max(v / norm, -1.0) for v in values)
            out.append(values)
        return out

    def image(self, index):
        image = self.doc['images'][index]
        if 'bufferView' in image:
            return self.view(image['bufferView'])[0]
        uri = image.get('uri') or ''
        if uri.startswith('data:'):
            return base64.b64decode(uri.split(',', 1)[1])
        if self.read_file is None:
            return None
        try:
            return self.read_file(uri)
        except OSError:
            return None


def _image_rgba(data):
    """(width, height, rgba) of PNG or JPEG bytes."""
    from wc3files.img import read_png
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return read_png(data)
    if data[:2] == b'\xff\xd8':
        from wc3files.img.jpeg_decoder import decode_planes
        width, height, planes = decode_planes(data)
        out = bytearray()
        if len(planes) >= 3:
            for y, cb, cr in zip(planes[0], planes[1], planes[2]):
                r = y + 1.402 * (cr - 128)
                g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
                b = y + 1.772 * (cb - 128)
                out += bytes((max(0, min(255, int(round(r)))), max(0, min(255, int(round(g)))),
                              max(0, min(255, int(round(b)))), 255))
        else:
            for y in planes[0]:
                out += bytes((y, y, y, 255))
        return width, height, bytes(out)
    raise ValueError('an image that is neither PNG nor JPEG')


def _axes(scale):
    """C, glTF to game: (x, y, z) -> (z, x, y) * scale; and its inverse."""
    c = [0.0, 0.0, scale, 0.0, scale, 0.0, 0.0, 0.0, 0.0, scale, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    return c, _inverse(c)


def _point(m, p):
    return [m[r * 4] * p[0] + m[r * 4 + 1] * p[1] + m[r * 4 + 2] * p[2] + m[r * 4 + 3]
            for r in range(3)]


def _direction(m, n):
    v = [m[r * 4] * n[0] + m[r * 4 + 1] * n[1] + m[r * 4 + 2] * n[2] for r in range(3)]
    length = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / length for x in v]


def _channel_value(reader, sampler, time, path):
    times = [t[0] for t in reader.accessor(sampler['input'])]
    values = reader.accessor(sampler['output'])
    mode = sampler.get('interpolation', 'LINEAR')
    cubic = mode == 'CUBICSPLINE'
    if cubic:
        values = [values[i * 3 + 1] for i in range(len(times))], values
    else:
        values = values, values
    points, raw = values
    # Times are float32 in glTF (0.499 is read as 0.49900001), and MDX
    # frames whole milliseconds: a time within 0.05 ms of a key is on it.
    near = 5e-5
    if time <= times[0] + near:
        return list(points[0])
    if time >= times[-1] - near:
        return list(points[-1])
    for i in range(len(times) - 1):
        if times[i] - near <= time < times[i + 1] - near:
            span = times[i + 1] - times[i]
            u = max(0.0, min(1.0, (time - times[i]) / span)) if span else 0.0
            a, b = points[i], points[i + 1]
            if mode == 'STEP':
                return list(a)
            if cubic:
                out_tan = raw[i * 3 + 2]
                in_tan = raw[(i + 1) * 3]
                u2, u3 = u * u, u * u * u
                v = [(2 * u3 - 3 * u2 + 1) * a[k] + (u3 - 2 * u2 + u) * span * out_tan[k]
                     + (-2 * u3 + 3 * u2) * b[k] + (u3 - u2) * span * in_tan[k]
                     for k in range(len(a))]
            elif path == 'rotation':
                from .animation import _slerp
                v = _slerp(list(a), list(b), u)
            else:
                v = [x + (y - x) * u for x, y in zip(a, b)]
            if path == 'rotation':
                n = math.sqrt(sum(x * x for x in v)) or 1.0
                v = [x / n for x in v]
            return v
    return list(points[-1])


def gltf_to_model(source, scale=1.0, texture_dir=None, min_weight=0.2, read_file=None):
    """(model data, report) from a .glb/.gltf path or bytes. The report
    holds `textures` ({path: BLP bytes} for the images it could convert),
    `notes` (what was not taken) and `geosets`, `sequences` and `bones`."""
    from wc3files.img import BlpFile

    from .extents import compute_extents
    from .keyframes import remove_redundant_keys
    from .mdx_io import empty_model
    document, buffers, read_file = _load(source, read_file)
    reader = _Reader(document, buffers, read_file)
    notes = []
    gnodes = document.get('nodes') or []
    scene = (document.get('scenes') or [{'nodes': list(range(len(gnodes)))}])[document.get('scene', 0)]
    order, parent = [], {}

    def visit(i, up):
        order.append(i)
        parent[i] = up
        for child in gnodes[i].get('children') or []:
            visit(child, i)
    for root in scene.get('nodes') or []:
        visit(root, None)
    object_id = {n: k for k, n in enumerate(order)}

    def rest_trs(i):
        node = gnodes[i]
        return (list(node.get('translation', (0.0, 0.0, 0.0))),
                list(node.get('rotation', (0.0, 0.0, 0.0, 1.0))),
                list(node.get('scale', (1.0, 1.0, 1.0))))

    def local(i, overrides):
        node = gnodes[i]
        if 'matrix' in node and i not in overrides:
            return _from_columns(node['matrix'])
        t, r, s = rest_trs(i)
        o = overrides.get(i, {})
        return _trs(o.get('translation', t), o.get('rotation', r), o.get('scale', s))

    def globals_at(overrides):
        out = {}
        for i in order:
            m = local(i, overrides)
            out[i] = m if parent[i] is None else _mul(out[parent[i]], m)
        return out

    rest = globals_at({})
    inverse_bind = {}
    for skin in document.get('skins') or []:
        joints = skin.get('joints') or []
        if 'inverseBindMatrices' in skin:
            mats = reader.accessor(skin['inverseBindMatrices'])
        else:
            mats = [IDENTITY4] * len(joints)
        for j, m in zip(joints, mats):
            inverse_bind.setdefault(j, _from_columns(list(m)) if len(m) == 16 else list(m))
    for i in order:
        inverse_bind.setdefault(i, _inverse(rest[i]))
    c, c_inv = _axes(scale)
    pivots = {i: _point(c, _point(_inverse(inverse_bind[i]), (0.0, 0.0, 0.0))) for i in order}

    model = empty_model()
    model['version'] = 800
    model['name'] = 'Model'
    model['pivotPoints'] = [pivots[i] for i in order]
    for i in order:
        name = (gnodes[i].get('name') or f'node{i}')[:79]
        model['bones'].append({'node': {'name': name, 'objectId': object_id[i],
                                        'parentId': object_id[parent[i]] if parent[i] is not None else NONE,
                                        'flags': 256, 'tracks': {}},
                               'geosetId': NONE, 'geosetAnimationId': NONE})

    bind = {i: _inverse(inverse_bind[i]) for i in order}

    def mdx_locals(overrides):
        """{node: (t, r, s)} of the MDX local matrices for the glTF locals
        with `overrides`. The MDX world matrix is G * IBM, and G is the
        parent's G times the local matrix, so the MDX local matrix is
        IBM(parent)^-1 * local * IBM: no animated matrix is inverted, which
        a bone scaled to 0 (as models hide parts) would not allow."""
        out = {}
        for i in order:
            m = _mul(local(i, overrides), inverse_bind[i])
            if parent[i] is not None:
                m = _mul(bind[parent[i]], m)
            out[i] = _decompose(_mul(_mul(c, m), c_inv), pivots[i])
        return out

    def add_keys(frame_values, interval):
        for i, (t, r, s) in frame_values:
            tracks = model['bones'][object_id[i]]['node']['tracks']
            for tag, value in (('KGTR', t), ('KGRT', r), ('KGSC', s)):
                track = tracks.setdefault(tag, {'interpolationType': 1,
                                                'globalSequenceId': NONE, 'keys': []})
                keys = track['keys']
                if tag == 'KGRT' and keys and keys[-1]['frame'] >= interval[0]:
                    if sum(a * b for a, b in zip(keys[-1]['value'], value)) < 0:
                        value = [-x for x in value]
                keys.append({'frame': interval[2], 'value': [float(x) for x in value]})

    cursor = 0
    animations = document.get('animations') or []
    for index, animation in enumerate(animations):
        samplers = animation.get('samplers') or []
        channels = [ch for ch in animation.get('channels') or []
                    if ch.get('target', {}).get('node') in object_id]
        if any(ch.get('target', {}).get('path') == 'weights' for ch in animation.get('channels') or []):
            notes.append(f'animation {index}: morph target weights left out')
        times = set()
        for ch in channels:
            sampler = samplers[ch['sampler']]
            stamps = [t[0] for t in reader.accessor(sampler['input'])]
            times.update(stamps)
            if sampler.get('interpolation') == 'STEP':
                # MDX keys here are linear, and a track keeps one
                # interpolation over all its sequences: a key 1 ms before
                # each step, at the value before it, makes the line jump.
                times.update(t - 0.001 for t in stamps[1:] if t - 0.001 > stamps[0])
            if sampler.get('interpolation') == 'CUBICSPLINE' and len(stamps) > 1:
                step = 1 / 30
                t = stamps[0]
                while t < stamps[-1]:
                    times.add(t)
                    t += step
        if not times:
            continue
        start_time = min(times)
        duration = int(round((max(times) - start_time) * 1000))
        interval = [cursor, cursor + duration]
        frames = sorted({int(round((t - start_time) * 1000)) for t in times})
        for frame in frames:
            seconds = start_time + frame / 1000.0
            overrides = {}
            for ch in channels:
                target = ch['target']
                value = _channel_value(reader, samplers[ch['sampler']], seconds, target['path'])
                overrides.setdefault(target['node'], {})[target['path']] = value
            add_keys(mdx_locals(overrides).items(), (interval[0], interval[1], cursor + frame))
        name = animation.get('name') or f'Animation {index + 1}'
        model['sequences'].append({'name': name[:79], 'interval': interval, 'moveSpeed': 0.0,
                                   'flags': 0, 'rarity': 0.0, 'syncPoint': 0,
                                   'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3,
                                              'maximum': [0.0] * 3}})
        cursor = interval[1] + 1000
    if not model['sequences']:
        add_keys(mdx_locals({}).items(), (0, 1000, 0))
        add_keys(mdx_locals({}).items(), (0, 1000, 1000))
        model['sequences'].append({'name': 'Stand', 'interval': [0, 1000], 'moveSpeed': 0.0,
                                   'flags': 0, 'rarity': 0.0, 'syncPoint': 0,
                                   'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3,
                                              'maximum': [0.0] * 3}})

    textures, texture_files, material_index = [], {}, {}

    def texture_for(material):
        info = ((material.get('pbrMetallicRoughness') or {}).get('baseColorTexture') or {})
        if 'index' not in info:
            return WHITE
        texture = (document.get('textures') or [])[info['index']]
        source_index = texture.get('source')
        if source_index is None:
            return WHITE
        image = document['images'][source_index]
        name = image.get('name') or os.path.basename(image.get('uri') or '') or f'texture{source_index}'
        name = name.rsplit('.', 1)[0] if '.' in name else name
        path = (texture_dir.rstrip(BS + '/') + BS if texture_dir else '') + name + '.blp'
        if path not in texture_files:
            data = reader.image(source_index)
            if data is None:
                notes.append(f'image {name}: not found, named only')
            else:
                try:
                    width, height, rgba = _image_rgba(data)
                    texture_files[path] = BlpFile.from_rgba(width, height, rgba).to_bytes()
                except Exception as error:  # reported; the texture is named all the same
                    notes.append(f'image {name}: not converted ({error})')
        for key in ('normalTexture', 'occlusionTexture', 'emissiveTexture'):
            if key in material and f'{key} left out' not in notes:
                notes.append(f'{key} left out')
        return path

    def material_of(index):
        if index not in material_index:
            material = (document.get('materials') or [{}])[index] if index is not None else {}
            path = texture_for(material)
            if path not in [t['path'] for t in textures]:
                textures.append({'replaceableId': 0, 'path': path, 'flags': 0})
            mode = {'MASK': 1, 'BLEND': 2}.get(material.get('alphaMode'), 0)
            shading = 16 if material.get('doubleSided') else 0
            material_index[index] = len(model['materials'])
            model['materials'].append({'priorityPlane': 0, 'flags': 0, 'layers': [{
                'filterMode': mode, 'shadingFlags': shading,
                'textureId': [t['path'] for t in textures].index(path),
                'textureAnimationId': NONE, 'coordId': 0, 'alpha': 1.0, 'tracks': {}}]})
        return material_index[index]

    worst_dropped = 0.0
    for i in order:
        node = gnodes[i]
        if 'mesh' not in node:
            continue
        skin = (document.get('skins') or [])[node['skin']] if 'skin' in node else None
        mesh = document['meshes'][node['mesh']]
        for p, primitive in enumerate(mesh.get('primitives') or []):
            attributes = primitive.get('attributes') or {}
            mode = primitive.get('mode', 4)
            if mode not in (4, 5, 6) or 'POSITION' not in attributes:
                notes.append(f'mesh {node["mesh"]} primitive {p}: mode {mode} left out')
                continue
            if primitive.get('targets'):
                notes.append(f'mesh {node["mesh"]} primitive {p}: morph targets left out')
            positions = reader.accessor(attributes['POSITION'])
            count = len(positions)
            if count > 65535:
                raise ValueError(f'mesh {node["mesh"]} primitive {p} has {count} vertices; '
                                 'a geoset holds 65,535')
            normals = reader.accessor(attributes['NORMAL']) if 'NORMAL' in attributes else None
            uvs = reader.accessor(attributes['TEXCOORD_0']) if 'TEXCOORD_0' in attributes else None
            if 'indices' in primitive:
                indices = [v[0] for v in reader.accessor(primitive['indices'])]
            else:
                indices = list(range(count))
            if mode == 5:
                indices = [x for k in range(len(indices) - 2)
                           for x in ((indices[k], indices[k + 1], indices[k + 2]) if k % 2 == 0
                                     else (indices[k + 1], indices[k], indices[k + 2]))]
            elif mode == 6:
                indices = [x for k in range(1, len(indices) - 1)
                           for x in (indices[0], indices[k], indices[k + 1])]
            if skin is not None:
                placed = c
                joints = reader.accessor(attributes['JOINTS_0']) if 'JOINTS_0' in attributes else None
                weights = reader.accessor(attributes['WEIGHTS_0']) if 'WEIGHTS_0' in attributes else None
            else:
                placed = _mul(c, rest[i])
                joints = weights = None
            vertices, out_normals, groups, group_index, vertex_groups = [], [], [], {}, []
            for k in range(count):
                vertices.extend(_point(placed, positions[k]))
                out_normals.extend(_direction(placed, normals[k]) if normals else (0.0, 0.0, 1.0))
                if joints is not None and weights is not None:
                    pairs = sorted(((w, skin['joints'][j]) for j, w in zip(joints[k], weights[k])
                                    if w > 0 and skin['joints'][j] in object_id), reverse=True)
                    if not pairs:
                        pairs = [(1.0, i)]
                    total = sum(w for w, _ in pairs)
                    kept = [n for w, n in pairs if w / total >= min_weight][:4] or [pairs[0][1]]
                    dropped = sum(w for w, n in pairs if n not in kept) / total
                    worst_dropped = max(worst_dropped, dropped)
                    members = tuple(sorted(object_id[n] for n in set(kept)))
                else:
                    members = (object_id[i],)
                if members not in group_index:
                    group_index[members] = len(groups)
                    groups.append(members)
                vertex_groups.append(group_index[members])
            if len(groups) > 255:
                raise ValueError(f'mesh {node["mesh"]} primitive {p} needs {len(groups)} bone '
                                 'groups; version 800 holds 255 per geoset')
            uv = []
            for k in range(count):
                uv.extend(uvs[k][:2] if uvs else (0.0, 0.0))
            geoset = {
                'vertices': vertices, 'normals': out_normals, 'faceTypeGroups': [4],
                'faceGroups': [len(indices)], 'faces': indices, 'vertexGroups': vertex_groups,
                'matrixGroups': [len(g) for g in groups],
                'matrixIndices': [m for g in groups for m in g],
                'materialId': material_of(primitive.get('material')),
                'selectionGroup': 0, 'selectionFlags': 0,
                'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3, 'maximum': [0.0] * 3},
                'sequenceExtents': [], 'uvSets': [uv]}
            if normals is None:
                from .normals import geoset_normals
                geoset['normals'] = geoset_normals(geoset)
            model['geosets'].append(geoset)
    if not model['geosets']:
        raise ValueError('the glTF scene has no mesh to convert')
    model['textures'] = textures
    remove_redundant_keys(model, tolerance=1e-6)
    compute_extents(model)
    return model, {'textures': texture_files, 'notes': notes, 'geosets': len(model['geosets']),
                   'sequences': [s['name'] for s in model['sequences']],
                   'bones': len(model['bones']), 'largest_weight_dropped': worst_dropped}


# Writing glTF.

class _Writer:
    def __init__(self):
        self.binary = bytearray()
        self.views, self.accessors = [], []

    def add(self, values, kind, component=5126, target=None, minmax=False):
        width = WIDTH[kind]
        code, size = COMPONENT[component]
        while len(self.binary) % 4:
            self.binary.append(0)
        start = len(self.binary)
        flat = [x for v in values for x in (v if isinstance(v, (list, tuple)) else (v,))]
        self.binary += struct.pack('<' + code * len(flat), *flat)
        view = {'buffer': 0, 'byteOffset': start, 'byteLength': len(self.binary) - start}
        if target:
            view['target'] = target
        self.views.append(view)
        accessor = {'bufferView': len(self.views) - 1, 'componentType': component,
                    'count': len(values), 'type': kind}
        if minmax and values:
            accessor['min'] = [min(v[k] for v in values) for k in range(width)]
            accessor['max'] = [max(v[k] for v in values) for k in range(width)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def model_to_gltf(model, scale=1.0, textures=None, curve_ms=33):
    """A .glb file (bytes) of `model` (a ModelFile or its data), and a report."""
    model = model_data(model)
    back = _axes(scale)[1]
    linear = [back[0], back[1], back[2], back[4], back[5], back[6], back[8], back[9], back[10]]
    pivots = model.get('pivotPoints') or []
    all_nodes = sorted((node for _, _, node in nodes(model)), key=lambda n: n.get('objectId', 0))
    index_of = {n['objectId']: k for k, n in enumerate(all_nodes)}

    def pivot(object_id):
        return pivots[object_id] if object_id < len(pivots) else (0.0, 0.0, 0.0)

    def to_gltf(p):
        return _point(back, p)

    writer = _Writer()
    gnodes, roots = [], []
    for node in all_nodes:
        p = to_gltf(pivot(node['objectId']))
        parent = node.get('parentId', NONE)
        if parent in index_of and parent != node['objectId']:
            q = to_gltf(pivot(parent))
            offset = [a - b for a, b in zip(p, q)]
        else:
            offset = p
            roots.append(index_of[node['objectId']])
        gnodes.append({'name': node.get('name') or f"node{node['objectId']}",
                       'translation': offset})
    for node in all_nodes:
        parent = node.get('parentId', NONE)
        if parent in index_of and parent != node['objectId']:
            gnodes[index_of[parent]].setdefault('children', []).append(index_of[node['objectId']])

    notes = []
    materials_out, images_out, textures_out, material_map = [], [], [], {}
    model_textures = model.get('textures') or []

    def material_for(material_id):
        if material_id in material_map:
            return material_map[material_id]
        materials = model.get('materials') or []
        entry = {'name': f'material_{material_id}', 'pbrMetallicRoughness': {
            'metallicFactor': 0.0, 'roughnessFactor': 1.0}}
        if 0 <= material_id < len(materials):
            layers = materials[material_id].get('layers') or []
            if layers:
                layer = layers[0]
                texture_ids = [e['textureId'] for e in layer.get('textures') or []] or [layer.get('textureId')]
                tid = texture_ids[0]
                if layer.get('filterMode') == 1:
                    entry['alphaMode'] = 'MASK'
                elif layer.get('filterMode') in (2, 3, 4, 5, 6):
                    entry['alphaMode'] = 'BLEND'
                if layer.get('shadingFlags', 0) & 16:
                    entry['doubleSided'] = True
                if tid is not None and 0 <= tid < len(model_textures) and model_textures[tid].get('path'):
                    path = model_textures[tid]['path']
                    image = {'name': path.replace(BS, '/').rsplit('/', 1)[-1].rsplit('.', 1)[0]}
                    data = textures(path) if textures else None
                    if data:
                        try:
                            image_bytes = _png_of(data)
                            image['bufferView'] = len(writer.views)
                            while len(writer.binary) % 4:
                                writer.binary.append(0)
                            writer.views.append({'buffer': 0, 'byteOffset': len(writer.binary),
                                                 'byteLength': len(image_bytes)})
                            writer.binary += image_bytes
                            image['mimeType'] = 'image/png'
                        except Exception as error:  # named only, and reported
                            notes.append(f'{path}: not converted ({error})')
                            data = None
                    if not data:
                        image['uri'] = path.replace(BS, '/').rsplit('.', 1)[0] + '.png'
                    images_out.append(image)
                    textures_out.append({'source': len(images_out) - 1})
                    entry['pbrMetallicRoughness']['baseColorTexture'] = {'index': len(textures_out) - 1}
        materials_out.append(entry)
        material_map[material_id] = len(materials_out) - 1
        return material_map[material_id]

    primitives, more_than_four = [], 0
    for geoset in model.get('geosets') or []:
        vertices = geoset.get('vertices') or []
        count = len(vertices) // 3
        if not count:
            continue
        positions = [to_gltf(vertices[k * 3:k * 3 + 3]) for k in range(count)]
        normals = []
        raw_normals = geoset.get('normals') or []
        for k in range(count):
            n = raw_normals[k * 3:k * 3 + 3] or [0.0, 0.0, 1.0]
            v = [linear[r * 3] * n[0] + linear[r * 3 + 1] * n[1] + linear[r * 3 + 2] * n[2]
                 for r in range(3)]
            length = math.sqrt(sum(x * x for x in v)) or 1.0
            normals.append([x / length for x in v])
        uvs = (geoset.get('uvSets') or [[]])[0]
        uv = [(uvs[k * 2], uvs[k * 2 + 1]) if len(uvs) >= k * 2 + 2 else (0.0, 0.0)
              for k in range(count)]
        joints, weights = [], []
        for influences in vertex_bones(geoset):
            influences = sorted(influences, key=lambda x: -x[1])
            if len(influences) > 4:
                more_than_four += 1
                influences = influences[:4]
            total = sum(w for _, w in influences) or 1.0
            ids = [index_of.get(o, 0) for o, _ in influences] + [0] * (4 - len(influences))
            ws = [w / total for _, w in influences] + [0.0] * (4 - len(influences))
            if not influences:
                ids, ws = [0, 0, 0, 0], [1.0, 0.0, 0.0, 0.0]
            joints.append(ids)
            weights.append(ws)
        faces = geoset.get('faces') or []
        attributes = {
            'POSITION': writer.add(positions, 'VEC3', target=34962, minmax=True),
            'NORMAL': writer.add(normals, 'VEC3', target=34962),
            'TEXCOORD_0': writer.add(uv, 'VEC2', target=34962),
            'JOINTS_0': writer.add(joints, 'VEC4', 5123, target=34962),
            'WEIGHTS_0': writer.add(weights, 'VEC4', target=34962)}
        primitive = {'attributes': attributes, 'mode': 4,
                     'indices': writer.add(list(faces), 'SCALAR', 5125, target=34963),
                     'material': material_for(geoset.get('materialId', 0))}
        primitives.append(primitive)
    if more_than_four:
        notes.append(f'{more_than_four} vertex(es) of more than 4 bones kept their first 4')

    skin_joints = list(range(len(all_nodes)))
    inverse = []
    for node in all_nodes:
        p = to_gltf(pivot(node['objectId']))
        m = [1.0, 0.0, 0.0, -p[0], 0.0, 1.0, 0.0, -p[1], 0.0, 0.0, 1.0, -p[2], 0.0, 0.0, 0.0, 1.0]
        inverse.append(_to_columns(m))
    document = {'asset': {'version': '2.0', 'generator': 'wc3files'}, 'scene': 0,
                'nodes': gnodes, 'meshes': [], 'skins': [], 'materials': materials_out,
                'animations': []}
    if primitives:
        mesh_node = len(gnodes)
        gnodes.append({'name': model.get('name') or 'model', 'mesh': 0})
        document['meshes'].append({'primitives': primitives})
        if all_nodes:
            gnodes[mesh_node]['skin'] = 0
            document['skins'].append({'joints': skin_joints,
                                      'inverseBindMatrices': writer.add(inverse, 'MAT4')})
        roots.append(mesh_node)
    document['scenes'] = [{'nodes': roots}]

    global_tracks = set()
    gs = model.get('globalSequences') or []
    for sequence in model.get('sequences') or []:
        start, end = sequence['interval']
        window = (start, end)
        channels, samplers = [], []
        for node in all_nodes:
            k = index_of[node['objectId']]
            for tag, path in (('KGTR', 'translation'), ('KGRT', 'rotation'), ('KGSC', 'scale')):
                track = (node.get('tracks') or {}).get(tag)
                if not track:
                    continue
                gid = track.get('globalSequenceId', NONE)
                if gid not in (None, NONE):
                    # glTF has no global sequences: the track is baked into
                    # each animation, at its keys on its own clock.
                    global_tracks.add((node['objectId'], tag))
                    length = gs[gid] if gid < len(gs) else 0
                    inside = []
                    if length > 0:
                        for key in track.get('keys') or []:
                            first = key['frame'] + ((start - key['frame']) // length) * length
                            inside.extend(f for f in range(first, end + 1, length) if f >= start)
                else:
                    inside = [key['frame'] for key in track.get('keys') or []
                              if start <= key['frame'] <= end]
                if not inside and gid in (None, NONE):
                    continue
                frames = {start, end, *inside}
                if track.get('interpolationType', 0) in (2, 3):
                    frames.update(range(start, end + 1, max(1, int(curve_ms))))
                frames = sorted(frames)
                values = []
                for f in frames:
                    default = {'KGTR': (0.0, 0.0, 0.0), 'KGRT': (0.0, 0.0, 0.0, 1.0),
                               'KGSC': (1.0, 1.0, 1.0)}[tag]
                    v = sample(track, f, window, gs, default, rotation=tag == 'KGRT')
                    if tag == 'KGTR':
                        v = [a + b for a, b in zip(_direction_only(linear, v),
                                                    gnodes[k]['translation'])]
                    elif tag == 'KGRT':
                        v = _rotate_quaternion(linear, v)
                    else:
                        v = _scale_axes(v)
                    values.append([float(x) for x in v])
                if tag == 'KGRT':
                    for a in range(1, len(values)):
                        if sum(x * y for x, y in zip(values[a - 1], values[a])) < 0:
                            values[a] = [-x for x in values[a]]
                times = writer.add([[(f - start) / 1000.0] for f in frames], 'SCALAR', minmax=True)
                out = writer.add(values, 'VEC4' if tag == 'KGRT' else 'VEC3')
                interpolation = 'STEP' if track.get('interpolationType', 0) == 0 else 'LINEAR'
                samplers.append({'input': times, 'output': out, 'interpolation': interpolation})
                channels.append({'sampler': len(samplers) - 1, 'target': {'node': k, 'path': path}})
        if channels:
            document['animations'].append({'name': sequence.get('name') or 'sequence',
                                           'channels': channels, 'samplers': samplers})
    if global_tracks:
        notes.append(f'{len(global_tracks)} global sequence track(s) baked into each animation')
    if model.get('geosetAnimations'):
        notes.append('geoset visibility (geoset animations) not written')
    if images_out:
        document['images'] = images_out
        document['textures'] = textures_out
    for key in ('meshes', 'skins', 'materials', 'animations'):
        if not document[key]:
            del document[key]
    while len(writer.binary) % 4:
        writer.binary.append(0)
    document['buffers'] = [{'byteLength': len(writer.binary)}]
    document['bufferViews'] = writer.views
    document['accessors'] = writer.accessors
    text = json.dumps(document, separators=(',', ':')).encode('utf-8')
    text += b' ' * (-len(text) % 4)
    binary = bytes(writer.binary)
    total = 12 + 8 + len(text) + 8 + len(binary)
    glb = (struct.pack('<III', GLB_MAGIC, 2, total) + struct.pack('<II', len(text), 0x4E4F534A)
           + text + struct.pack('<II', len(binary), 0x004E4942) + binary)
    return glb, {'nodes': len(all_nodes), 'primitives': len(primitives),
                 'animations': len(document.get('animations') or []), 'notes': notes}


def _direction_only(linear, v):
    return [linear[r * 3] * v[0] + linear[r * 3 + 1] * v[1] + linear[r * 3 + 2] * v[2]
            for r in range(3)]


def _rotate_quaternion(linear, q):
    """A game-space rotation as a glTF-space one: the axis turned, the angle kept."""
    axis = _direction_only(linear, q[:3])
    scale = math.sqrt(sum(x * x for x in linear[:3])) or 1.0
    return [axis[0] / scale, axis[1] / scale, axis[2] / scale, q[3]]


def _scale_axes(s):
    """A game-space scaling as a glTF-space one: (sx, sy, sz) of the game's
    axes are glTF's (sy, sz, sx)."""
    return [s[1], s[2], s[0]]


def _png_of(data):
    """PNG bytes of a BLP, DDS, TGA or PNG image."""
    from wc3files.img import BlpFile, DdsFile, TgaFile, write_png
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return bytes(data)
    if data[:4] in (b'BLP1', b'BLP2'):
        image = BlpFile()
        image.read(data)
        return write_png(image.width, image.height, image.get_rgba())
    if data[:4] == b'DDS ':
        image = DdsFile()
        image.read(data)
        return write_png(image.width, image.height, image.get_rgba())
    image = TgaFile()
    image.read(data)
    return write_png(image.width, image.height, image.get_rgba())
