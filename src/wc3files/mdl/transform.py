"""
A whole model scaled or moved in one step (#235), as GoldenEgg's
WC3ModelBatchEditor does.

`scale_model(model, factor)` multiplies every length of the model by a
positive `factor`:

- vertices, pivot points, translation keyframes (with their tangents) and
  every extent, box and radius;
- cameras: position, target, clipping planes and their translation tracks;
- collision shapes: vertices and radius;
- particle emitters: speed, gravity and particle sizes (`segmentScaling`,
  `length`, `width`, and the matching tracks); ribbon heights and gravity;
  light attenuation; the sequences' move speed.

Rotations and scaling keys are left alone: with one factor on every axis,
scaling the pivots and the translations is all a node's matrix needs, and
the normals keep their direction. Particle lifespans and emission rates
are times and counts, not lengths. Popcorn (PopcornFX) emitters are not
changed: their effect files hold their own sizes.

`translate_model(model, offset)` moves the model by `offset`: vertices,
pivot points, extents, cameras and collision shapes. Translation keyframes
are offsets from the pivot, so they stay as they are.
"""
from .animation import nodes
from .batch import model_data

_EMITTER2_TRACKS = ('KP2S', 'KP2G', 'KP2N', 'KP2W')
_EMITTER_TRACKS = ('KPES', 'KPEG')
_RIBBON_TRACKS = ('KRHA', 'KRHB')
_LIGHT_TRACKS = ('KLAS', 'KLAE')


def _scale_list(values, factor, offset=None):
    if offset is None:
        return [v * factor for v in values]
    return [v * factor + offset[i % 3] for i, v in enumerate(values)]


def _scale_value(value, factor):
    if isinstance(value, (int, float)):
        return value * factor
    return [v * factor for v in value]


def _scale_track(track, factor):
    for key in (track or {}).get('keys') or []:
        for field in ('value', 'inTan', 'outTan'):
            if field in key:
                key[field] = _scale_value(key[field], factor)


def _scale_tracks(tracks, tags, factor):
    for tag in tags:
        if tracks and tag in tracks:
            _scale_track(tracks[tag], factor)


def _move_extent(extent, factor=1.0, offset=(0.0, 0.0, 0.0)):
    if not extent:
        return
    lo, hi = extent.get('minimum'), extent.get('maximum')
    if lo is None or hi is None or any(a > b for a, b in zip(lo, hi)):
        return  # an empty box stays empty
    extent['minimum'] = [v * factor + o for v, o in zip(lo, offset)]
    extent['maximum'] = [v * factor + o for v, o in zip(hi, offset)]
    extent['boundsRadius'] = extent.get('boundsRadius', 0.0) * factor


def _extents(model):
    yield model.get('extent')
    for sequence in model.get('sequences') or []:
        yield sequence.get('extent')
    for geoset in model.get('geosets') or []:
        yield geoset.get('extent')
        yield from geoset.get('sequenceExtents') or []


def _apply(model, factor, offset):
    for geoset in model.get('geosets') or []:
        if geoset.get('vertices'):
            geoset['vertices'] = _scale_list(geoset['vertices'], factor, offset)
    model['pivotPoints'] = [[v * factor + o for v, o in zip(p, offset)]
                            for p in model.get('pivotPoints') or []]
    for extent in _extents(model):
        _move_extent(extent, factor, offset)
    for camera in model.get('cameras') or []:
        for field in ('position', 'targetPosition'):
            if camera.get(field) is not None:
                camera[field] = [v * factor + o for v, o in zip(camera[field], offset)]
        if factor != 1.0:
            for field in ('farClippingPlane', 'nearClippingPlane'):
                if field in camera:
                    camera[field] *= factor
            _scale_tracks(camera.get('tracks'), ('KCTR', 'KTTR'), factor)
    for shape in model.get('collisionShapes') or []:
        if shape.get('vertices'):
            shape['vertices'] = [[v * factor + o for v, o in zip(p, offset)]
                                 for p in shape['vertices']]
        if factor != 1.0 and 'radius' in shape:
            shape['radius'] *= factor


def scale_model(model, factor):
    """Scale `model` (a ModelFile or its data) by `factor` about its origin."""
    if not factor > 0:
        raise ValueError(f'the scale factor is a positive number, not {factor!r}')
    model = model_data(model)
    factor = float(factor)
    _apply(model, factor, (0.0, 0.0, 0.0))
    for _, _, node in nodes(model):
        _scale_tracks(node.get('tracks'), ('KGTR',), factor)
    for sequence in model.get('sequences') or []:
        if 'moveSpeed' in sequence:
            sequence['moveSpeed'] *= factor
    for emitter in model.get('particleEmitters2') or []:
        for field in ('speed', 'gravity', 'length', 'width'):
            if field in emitter:
                emitter[field] *= factor
        if emitter.get('segmentScaling'):
            emitter['segmentScaling'] = _scale_list(emitter['segmentScaling'], factor)
        _scale_tracks(emitter.get('tracks'), _EMITTER2_TRACKS, factor)
    for emitter in model.get('particleEmitters') or []:
        for field in ('initialVelocity', 'gravity'):
            if field in emitter:
                emitter[field] *= factor
        _scale_tracks(emitter.get('tracks'), _EMITTER_TRACKS, factor)
    for ribbon in model.get('ribbonEmitters') or []:
        for field in ('heightAbove', 'heightBelow', 'gravity'):
            if field in ribbon:
                ribbon[field] *= factor
        _scale_tracks(ribbon.get('tracks'), _RIBBON_TRACKS, factor)
    for light in model.get('lights') or []:
        for field in ('attenuationStart', 'attenuationEnd'):
            if field in light:
                light[field] *= factor
        _scale_tracks(light.get('tracks'), _LIGHT_TRACKS, factor)
    return {'factor': factor, 'popcorn': len(model.get('particleEmittersPopcorn') or [])}


def translate_model(model, offset):
    """Move `model` (a ModelFile or its data) by `offset` (x, y, z)."""
    offset = [float(v) for v in offset]
    if len(offset) != 3:
        raise ValueError('the offset is three numbers: x, y, z')
    model = model_data(model)
    _apply(model, 1.0, offset)
    return {'offset': offset}
