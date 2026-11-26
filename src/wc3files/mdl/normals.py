"""
Vertex normals recomputed from the faces (#237).

A vertex's normal is the sum of the normals of the triangles it belongs to,
each weighted by its area, made unit length. In a model, one point of the
surface is often several vertices: the same position with different texture
coordinates (a UV seam), or with different normals (a hard edge). With
`weld`, the vertices of a geoset at the same position are taken together,
but only those whose own normals are within `angle` degrees of each other:
a seam then gets one smooth normal on both sides, and an edge sharper than
`angle` stays hard.

Welding is off by default, since it moves away from the game's own normals:
the normals recomputed without it are a median of 1.0 degrees from the
stored ones of 49 SD game models and 1.6 degrees from those of 22 HD models,
87% and 89% of them within 15 degrees, against 2.7 and 2.6 degrees with
`weld` at 60 degrees.

A vertex in no triangle keeps the normal it has.
"""
import math

from .batch import model_data


def _triangles(geoset):
    faces = geoset.get('faces') or []
    count = len(geoset.get('vertices') or []) // 3
    for i in range(0, len(faces) - 2, 3):
        a, b, c = faces[i], faces[i + 1], faces[i + 2]
        if a < count and b < count and c < count:
            yield a, b, c


def _face_sums(geoset):
    v = geoset.get('vertices') or []
    sums = [None] * (len(v) // 3)
    for a, b, c in _triangles(geoset):
        ax, ay, az = v[a * 3:a * 3 + 3]
        ux, uy, uz = v[b * 3] - ax, v[b * 3 + 1] - ay, v[b * 3 + 2] - az
        wx, wy, wz = v[c * 3] - ax, v[c * 3 + 1] - ay, v[c * 3 + 2] - az
        # The cross product's length is twice the area: the weight.
        n = (uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx)
        for i in (a, b, c):
            s = sums[i]
            sums[i] = n if s is None else (s[0] + n[0], s[1] + n[1], s[2] + n[2])
    return sums


def _unit(n):
    length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
    return None if length == 0 else (n[0] / length, n[1] / length, n[2] / length)


def geoset_normals(geoset, angle=60.0, weld=False):
    """The recomputed normals of one geoset, as a flat list like 'normals'."""
    vertices = geoset.get('vertices') or []
    count = len(vertices) // 3
    old = list(geoset.get('normals') or [0.0, 0.0, 1.0] * count)
    sums = _face_sums(geoset)
    units = [_unit(s) if s is not None else None for s in sums]
    groups = {}
    if weld:
        for i in range(count):
            key = tuple(round(x, 4) for x in vertices[i * 3:i * 3 + 3])
            groups.setdefault(key, []).append(i)
    limit = math.cos(math.radians(angle))
    out = []
    for i in range(count):
        if units[i] is None:
            out.extend(old[i * 3:i * 3 + 3] if len(old) >= i * 3 + 3 else (0.0, 0.0, 1.0))
            continue
        total = sums[i]
        if weld:
            key = tuple(round(x, 4) for x in vertices[i * 3:i * 3 + 3])
            for j in groups[key]:
                if j == i or units[j] is None:
                    continue
                if sum(p * q for p, q in zip(units[i], units[j])) >= limit:
                    total = (total[0] + sums[j][0], total[1] + sums[j][1], total[2] + sums[j][2])
        out.extend(_unit(total) or units[i])
    return out


def recompute_normals(model, angle=60.0, weld=False):
    """Set the normals of every geoset of `model` (a ModelFile or its data).
    Returns the number of vertices set and of those that kept theirs."""
    if not 0 <= angle <= 180:
        raise ValueError(f'the angle is in degrees, from 0 to 180, not {angle!r}')
    model = model_data(model)
    set_count = kept = 0
    for geoset in model.get('geosets') or []:
        count = len(geoset.get('vertices') or []) // 3
        if not count:
            continue
        sums = _face_sums(geoset)
        kept += sum(1 for s in sums if s is None or _unit(s) is None)
        set_count += count
        geoset['normals'] = geoset_normals(geoset, angle, weld)
    return {'vertices': set_count - kept, 'kept': kept}
