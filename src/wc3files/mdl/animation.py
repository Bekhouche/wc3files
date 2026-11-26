"""
A model's pose at a given frame: keyframe tracks sampled, node matrices
built from them, and geoset vertices moved by their bones.

This is what the model tools need to look at an animated model: extents
(#236), keyframe removal (#230), curved tracks made linear (#231) and a
model exported at one frame (#244). It follows the usual reading of MDX
animation, as model viewers do:

- a track on a sequence uses only its keyframes inside the sequence's
  interval: before the first it holds the first value, after the last it
  holds the last, and with none the node's default (no translation, no
  rotation, scale 1);
- a track with a global sequence runs on that sequence's own clock;
- interpolation 0 holds each value until the next key, 1 is linear (a
  rotation is a quaternion slerp), 2 is Hermite and 3 Bezier, each with the
  key's in and out tangents. A Hermite or Bezier rotation is a squad, as
  model viewers take it: slerp(slerp(a, b, t), slerp(a.out, b.in, t),
  2t(1 - t)), each slerp the short way. A cubic on the quaternion's
  components would swing far from both keys when they have opposite signs,
  which the game's models have (q and -q are the same rotation);
- a node's matrix is its pivot, translation, rotation, scaling and pivot
  back again, after its parent's;
- a classic (version 800) vertex takes the mean of the matrices of its
  matrix group; a vertex with a skin (version 900 and later) takes the
  weighted sum of up to four bones.

Not modelled: billboarded nodes (they turn to the camera, which a model
alone does not have) and the "don't inherit" node flags. Both change only
how a node is turned or scaled at play time, rarely its place.
"""
import math

NONE = 0xFFFFFFFF

# Every list of the model whose entries are nodes, as {'node': {...}}.
NODE_LISTS = ('bones', 'helpers', 'lights', 'attachments', 'particleEmitters',
              'particleEmitters2', 'particleEmittersPopcorn', 'ribbonEmitters',
              'eventObjects', 'collisionShapes')

IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0)


def nodes(model):
    """Every node of the model, as (list name, entry, node)."""
    for key in NODE_LISTS:
        for entry in model.get(key) or []:
            node = entry.get('node')
            if node is not None:
                yield key, entry, node


def _lerp(a, b, t):
    if isinstance(a, (int, float)):
        return a + (b - a) * t
    return [x + (y - x) * t for x, y in zip(a, b)]


def _slerp(a, b, t):
    dot = sum(x * y for x, y in zip(a, b))
    if dot < 0:
        b, dot = [-y for y in b], -dot
    if dot > 0.9995:
        out = [x + (y - x) * t for x, y in zip(a, b)]
    else:
        theta = math.acos(min(1.0, dot))
        s = math.sin(theta)
        wa, wb = math.sin((1 - t) * theta) / s, math.sin(t * theta) / s
        out = [x * wa + y * wb for x, y in zip(a, b)]
    return _normalize(out)


def _normalize(q):
    n = math.sqrt(sum(x * x for x in q)) or 1.0
    return [x / n for x in q]


def _cubic(p0, out_tan, in_tan, p1, t, interpolation):
    if interpolation == 2:
        t2, t3 = t * t, t * t * t
        f = (2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t, t3 - t2, -2 * t3 + 3 * t2)
    else:
        u = 1 - t
        f = (u * u * u, 3 * t * u * u, 3 * t * t * u, t * t * t)
    if isinstance(p0, (int, float)):
        return f[0] * p0 + f[1] * out_tan + f[2] * in_tan + f[3] * p1
    return [f[0] * a + f[1] * b + f[2] * c + f[3] * d
            for a, b, c, d in zip(p0, out_tan, in_tan, p1)]


def interpolate(left, right, frame, interpolation, rotation=False):
    """The value between two keys at `frame`."""
    span = right['frame'] - left['frame']
    if interpolation == 0 or span <= 0:
        return left['value']
    t = (frame - left['frame']) / span
    if interpolation == 1:
        return _slerp(left['value'], right['value'], t) if rotation \
            else _lerp(left['value'], right['value'], t)
    if rotation:
        outer = _slerp(left['value'], right['value'], t)
        inner = _slerp(left.get('outTan', left['value']), right.get('inTan', right['value']), t)
        return _slerp(outer, inner, 2 * t * (1 - t))
    return _cubic(left['value'], left.get('outTan', left['value']),
                  right.get('inTan', right['value']), right['value'], t, interpolation)


def sample(track, frame, interval=None, global_sequences=(), default=None,
           rotation=False):
    """The value of `track` at `frame` of the sequence `interval` (start,
    end); `default` when the track has no key there."""
    if not track:
        return default
    keys = track.get('keys') or []
    gid = track.get('globalSequenceId', NONE)
    if gid not in (None, NONE) and gid < len(global_sequences):
        length = global_sequences[gid]
        frame = frame % length if length else 0
        interval = (0, length)
    if interval is not None:
        start, end = interval
        keys = [k for k in keys if start <= k['frame'] <= end]
    if not keys:
        return default
    if frame <= keys[0]['frame']:
        return keys[0]['value']
    if frame >= keys[-1]['frame']:
        return keys[-1]['value']
    for left, right in zip(keys, keys[1:]):
        if left['frame'] <= frame <= right['frame']:
            return interpolate(left, right, frame, track.get('interpolationType', 0), rotation)
    return keys[-1]['value']


def _multiply(a, b):
    """3x4 row-major matrices: a after b."""
    out = []
    for r in range(3):
        row = a[r * 4:r * 4 + 4]
        for c in range(4):
            v = row[0] * b[c] + row[1] * b[4 + c] + row[2] * b[8 + c]
            if c == 3:
                v += row[3]
            out.append(v)
    return tuple(out)


def compose(pivot, translation, rotation, scaling):
    """The matrix of pivot + translation, rotation and scaling about the pivot."""
    x, y, z, w = rotation
    sx, sy, sz = scaling
    r = (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
         2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
         2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))
    m = []
    for row in range(3):
        rs = (r[row * 3] * sx, r[row * 3 + 1] * sy, r[row * 3 + 2] * sz)
        offset = pivot[row] + translation[row] - (rs[0] * pivot[0] + rs[1] * pivot[1]
                                                  + rs[2] * pivot[2])
        m.extend((rs[0], rs[1], rs[2], offset))
    return tuple(m)


def transform(m, v):
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2] + m[3],
            m[4] * v[0] + m[5] * v[1] + m[6] * v[2] + m[7],
            m[8] * v[0] + m[9] * v[1] + m[10] * v[2] + m[11])


def node_matrices(model, frame=0, interval=None):
    """{objectId: 3x4 matrix} of every node at `frame` of `interval`."""
    pivots = model.get('pivotPoints') or []
    gs = model.get('globalSequences') or []
    by_id = {node.get('objectId'): node for _, _, node in nodes(model)}
    matrices = {}

    def world(object_id, depth=0):
        if object_id in matrices:
            return matrices[object_id]
        node = by_id.get(object_id)
        if node is None or depth > 256:
            return IDENTITY
        tracks = node.get('tracks') or {}
        pivot = pivots[object_id] if object_id < len(pivots) else (0.0, 0.0, 0.0)
        local = compose(pivot,
                        sample(tracks.get('KGTR'), frame, interval, gs, (0.0, 0.0, 0.0)),
                        sample(tracks.get('KGRT'), frame, interval, gs, (0.0, 0.0, 0.0, 1.0),
                               rotation=True),
                        sample(tracks.get('KGSC'), frame, interval, gs, (1.0, 1.0, 1.0)))
        parent = node.get('parentId', NONE)
        if parent not in (None, NONE, object_id) and parent in by_id:
            local = _multiply(world(parent, depth + 1), local)
        matrices[object_id] = local
        return local

    for object_id in by_id:
        world(object_id)
    return matrices


def vertex_bones(geoset):
    """For each vertex, its [(objectId, weight)]: from the skin when the
    geoset has one, else the mean over its matrix group."""
    count = len(geoset.get('vertices') or []) // 3
    indices = geoset.get('matrixIndices') or []
    skin = geoset.get('skin')
    if skin:
        wide = len(skin) >= count * 16
        out = []
        for i in range(count):
            if wide:
                raw = skin[i * 16:i * 16 + 16]
                bones = [raw[j] | raw[j + 1] << 8 for j in range(0, 8, 2)]
                weights = [raw[j] | raw[j + 1] << 8 for j in range(8, 16, 2)]
            else:
                raw = skin[i * 8:i * 8 + 8]
                bones, weights = list(raw[:4]), list(raw[4:])
            total = sum(weights) or 1
            out.append([(indices[b] if b < len(indices) else b, w / total)
                        for b, w in zip(bones, weights) if w])
        return out
    groups, start = [], 0
    for size in geoset.get('matrixGroups') or []:
        members = indices[start:start + size]
        groups.append([(m, 1.0 / len(members)) for m in members] if members else [])
        start += size
    vgroups = geoset.get('vertexGroups') or []
    return [groups[vgroups[i]] if i < len(vgroups) and vgroups[i] < len(groups) else []
            for i in range(count)]


def posed_vertices(geoset, matrices, bones=None):
    """The geoset's vertices moved by `matrices` (from node_matrices). The
    matrices of a vertex's bones are blended by weight first, which moves
    the vertex exactly as blending the moved points would, once per
    distinct set of bones."""
    vertices = geoset.get('vertices') or []
    bones = vertex_bones(geoset) if bones is None else bones
    blended = {}
    out = []
    for i in range(len(vertices) // 3):
        v = vertices[i * 3:i * 3 + 3]
        influences = tuple(bones[i]) if i < len(bones) else ()
        if not influences:
            out.append(tuple(v))
            continue
        m = blended.get(influences)
        if m is None:
            m = [0.0] * 12
            for object_id, weight in influences:
                for k, x in enumerate(matrices.get(object_id, IDENTITY)):
                    m[k] += x * weight
            blended[influences] = m
        out.append(transform(m, v))
    return out


def geoset_alpha(model, geoset_index, frame=0, interval=None):
    """The geoset's visibility at `frame`: its geoset animation's alpha, or 1."""
    gs = model.get('globalSequences') or []
    for anim in model.get('geosetAnimations') or []:
        if anim.get('geosetId') == geoset_index:
            static = anim.get('alpha', 1.0)
            return sample((anim.get('tracks') or {}).get('KGAO'), frame, interval, gs, static)
    return 1.0
