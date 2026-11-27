"""
Model extents recomputed from the vertices (#236).

A model holds bounds at three levels, each a box and a radius:

- each geoset's extent, and one per sequence (`sequenceExtents`);
- each sequence's extent;
- the model's extent.

`compute_extents` sets all of them from the geometry. A sequence's extents
come from the vertices posed by the bones (`animation.py`) at frames spread
evenly over the sequence, and a geoset counts only at the frames where its
geoset animation leaves it visible. The rules follow the game's own models:

- the radius is the largest distance of a vertex from the box's center,
  as the game's SD models have it; its HD models use half the box's
  diagonal, which `radius='diagonal'` gives (never smaller);
- a geoset that never shows during a sequence gets the empty box the game's
  models use: minimum +3.40282e38, maximum -3.40282e38, radius 0;
- a sequence's extent is the union of its geosets', a geoset's the union of
  its sequences', and the model's the union of all of them;
- a geoset, a sequence or the model that no visible pose went into takes
  the rest pose, and a model with no vertex (particles only) keeps the
  extents it has, since an empty box would hide it.

The game's stored extents were not all made by one rule (a geoset's extent
equals the union of its own sequence extents in about two thirds of its SD
models), so they are matched only roughly. Over 49 of the game's SD models,
the box of a geoset in a sequence is within 2% of the game's in 60% of cases
and within 10% in 77%; over 23 HD models, whose geosets store no extents, a
sequence's box is within 2% in 83% and within 10% in 98%. Where they differ,
the box made here is mostly the larger one, since it holds every posed
vertex, which is the safe side: a box that is too small is what makes a
model vanish at the edge of the screen.
"""
import math

from .animation import geoset_alpha, node_matrices, posed_vertices, vertex_bones
from .batch import model_data

EMPTY = 3.402820018375656e+38


def empty_extent():
    return {'boundsRadius': 0.0, 'minimum': [EMPTY] * 3, 'maximum': [-EMPTY] * 3}


def is_empty(extent):
    return not extent or any(lo > hi for lo, hi in zip(extent['minimum'], extent['maximum']))


class _Box:
    def __init__(self):
        self.lo = [math.inf] * 3
        self.hi = [-math.inf] * 3
        self.radius = 0.0

    def add(self, points):
        lo, hi = self.lo, self.hi
        for p in points:
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]

    def merge(self, other):
        self.lo = [min(a, b) for a, b in zip(self.lo, other.lo)]
        self.hi = [max(a, b) for a, b in zip(self.hi, other.hi)]

    def empty(self):
        return self.lo[0] > self.hi[0]

    def center(self):
        return [(a + b) / 2 for a, b in zip(self.lo, self.hi)]

    def reach(self, points, center):
        for p in points:
            d = math.dist(p, center)
            if d > self.radius:
                self.radius = d

    def extent(self, diagonal=False):
        if self.empty():
            return empty_extent()
        radius = math.dist(self.lo, self.hi) / 2 if diagonal else self.radius
        return {'boundsRadius': radius, 'minimum': list(self.lo), 'maximum': list(self.hi)}


def _rest_box(point_lists):
    box = _Box()
    for points in point_lists:
        box.add(points)
    center = box.center()
    for points in point_lists:
        box.reach(points, center)
    return box


def _frames(interval, count):
    start, end = interval
    if end <= start or count <= 1:
        return [start]
    step = (end - start) / (count - 1)
    return sorted({int(round(start + i * step)) for i in range(count)})


def compute_extents(model, frames=24, radius='center'):
    """Set every extent of `model` (a ModelFile or its data) from its
    vertices, posing each sequence at `frames` frames (1: its first frame
    only). `radius` is 'center' (the farthest vertex from the box's center)
    or 'diagonal' (half the box's diagonal). Returns the old and the new
    model extent."""
    if radius not in ('center', 'diagonal'):
        raise ValueError(f"radius is 'center' or 'diagonal', not {radius!r}")
    model = model_data(model)
    diagonal = radius == 'diagonal'
    geosets = model.get('geosets') or []
    sequences = model.get('sequences') or []
    old = model.get('extent')
    if not any(g.get('vertices') for g in geosets):
        # Nothing to measure, as on a model of particles only: its extents
        # stay as they are, since an empty one would hide it.
        return {'old': old, 'new': old, 'unchanged': True}
    bones = [vertex_bones(g) for g in geosets]
    rest = [posed_vertices(g, {}, []) for g in geosets]

    def poses():
        """(sequence index, geoset index, points) of every visible pose."""
        for si, sequence in enumerate(sequences):
            interval = tuple(sequence.get('interval') or (0, 0))
            for frame in _frames(interval, frames):
                matrices = node_matrices(model, frame, interval)
                for gi, geoset in enumerate(geosets):
                    if geoset_alpha(model, gi, frame, interval) > 0:
                        yield si, gi, posed_vertices(geoset, matrices, bones[gi])

    cells = {(si, gi): _Box() for si in range(len(sequences)) for gi in range(len(geosets))}
    per_geoset = [_Box() for _ in geosets]
    per_sequence = [_Box() for _ in sequences]
    whole = _Box()
    # Boxes first, then the radii from the boxes' centers.
    for si, gi, points in poses():
        cells[si, gi].add(points)
    for (si, gi), box in cells.items():
        per_geoset[gi].merge(box)
        per_sequence[si].merge(box)
    for box in per_geoset:
        whole.merge(box)
    centers = {key: box.center() for key, box in cells.items()}
    geoset_centers = [box.center() for box in per_geoset]
    sequence_centers = [box.center() for box in per_sequence]
    whole_center = whole.center()
    for si, gi, points in poses():
        cells[si, gi].reach(points, centers[si, gi])
        per_sequence[si].reach(points, sequence_centers[si])
        per_geoset[gi].reach(points, geoset_centers[gi])
        whole.reach(points, whole_center)
    # A box nothing visible went into, but a geoset, a sequence or the model
    # with vertices, takes the rest pose: an effect whose geosets are always
    # hidden still needs bounds for its particles to be drawn.
    for gi, box in enumerate(per_geoset):
        if box.empty():
            per_geoset[gi] = _rest_box([rest[gi]])
    for si, box in enumerate(per_sequence):
        if box.empty():
            per_sequence[si] = _rest_box(rest)
    if whole.empty():
        whole = _rest_box(rest)

    for gi, geoset in enumerate(geosets):
        geoset['sequenceExtents'] = [cells[si, gi].extent(diagonal)
                                     for si in range(len(sequences))]
        geoset['extent'] = per_geoset[gi].extent(diagonal)
    for si, sequence in enumerate(sequences):
        sequence['extent'] = per_sequence[si].extent(diagonal)
    model['extent'] = whole.extent(diagonal)
    return {'old': old, 'new': model['extent']}
