"""
Keyframes of a model removed when they change nothing (#230), as MDX/M3
Optimizer and Model Optimizer do.

`tracks(model)` walks every keyframe track of a model: nodes, layers and
their textures, texture and geoset animations, emitters, ribbons, lights,
attachments and cameras.

`remove_redundant_keys(model, tolerance=0.0)` removes a key when the track
gives the same values without it, at every frame, within `tolerance`:

- on a step track (interpolation 0), a key that repeats the one before;
- on a linear track, a key that its two neighbours interpolate to (a
  rotation by slerp, `q` and `-q` being the same rotation);
- on a Hermite or Bezier track, a key whose two curve segments are flat at
  its own value: its neighbours have that value and every tangent between
  them is flat (0 for Hermite, the value itself for Bezier).

A key at the start or end of a sequence, or at 0 or the length of a global
sequence, is never removed, and neighbours are taken only inside the same
sequence. With the default tolerance of 0 the values must be equal.

With `dead=True`, a key that no sequence plays is removed as well, on a
track that does not run on a global sequence. That rests on the reading of
sequences above, which model viewers follow but which was not checked in
the game, so it is not done by default: models imported into maps hold
many such keys (7,684 of 45,900 on 50 sampled corpus imports, against 17 of
19,606 on 35 game models).
"""
from .animation import interpolate
from .batch import model_data

NONE = 0xFFFFFFFF
ROTATIONS = ('KGRT', 'KTAR')


def tracks(model):
    """(tag, track) for every keyframe track of `model`'s data."""
    def walk(value):
        if isinstance(value, dict):
            found = value.get('tracks')
            if isinstance(found, dict):
                for tag, track in found.items():
                    if isinstance(track, dict) and 'keys' in track:
                        yield tag, track
            for key, item in value.items():
                if key != 'tracks':
                    yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    yield from walk(item)
    for key, value in model.items():
        if key not in ('geosets', 'pivotPoints', 'bindPose', 'unknownChunks'):
            yield from walk(value)


def _windows(model, track):
    """The (start, end) frame ranges the track is played in."""
    gid = track.get('globalSequenceId', NONE)
    lengths = model.get('globalSequences') or []
    if gid not in (None, NONE):
        if gid < len(lengths):
            return [(0, lengths[gid])], True
        return [], True
    return [tuple(s.get('interval') or (0, 0)) for s in model.get('sequences') or []], False


def _close(a, b, tolerance, rotation=False):
    if isinstance(a, (int, float)):
        return abs(a - b) <= tolerance
    diff = max(abs(x - y) for x, y in zip(a, b))
    if rotation and diff > tolerance:
        diff = max(abs(x + y) for x, y in zip(a, b))
    return diff <= tolerance


def _flat(key, value, interpolation, side, tolerance):
    tangent = key.get(side)
    if tangent is None:
        return True
    if interpolation == 2:
        zero = 0.0 if isinstance(value, (int, float)) else [0.0] * len(value)
        return _close(tangent, zero, tolerance)
    return _close(tangent, value, tolerance)


def _redundant(prev, key, nxt, interpolation, tolerance, rotation):
    if interpolation == 0:
        return _close(key['value'], prev['value'], tolerance, rotation)
    if interpolation == 1:
        between = interpolate(prev, nxt, key['frame'], 1, rotation)
        return _close(key['value'], between, tolerance, rotation)
    value = key['value']
    return (_close(prev['value'], value, tolerance, rotation)
            and _close(nxt['value'], value, tolerance, rotation)
            and _flat(prev, value, interpolation, 'outTan', tolerance)
            and _flat(key, value, interpolation, 'inTan', tolerance)
            and _flat(key, value, interpolation, 'outTan', tolerance)
            and _flat(nxt, value, interpolation, 'inTan', tolerance))


def _prune(model, tag, track, tolerance, dead_keys=False):
    keys = track.get('keys') or []
    if not keys:
        return 0, 0
    windows, is_global = _windows(model, track)
    interpolation = track.get('interpolationType', 0)
    rotation = tag in ROTATIONS
    bounds = {frame for window in windows for frame in window}
    in_windows = [sum(1 for s, e in windows if s <= k['frame'] <= e) for k in keys]
    drop = set()
    for start, end in windows:
        inside = [i for i, k in enumerate(keys) if start <= k['frame'] <= end]
        last_kept, pending = None, []
        for position, i in enumerate(inside):
            key = keys[i]
            final = position == len(inside) - 1
            if (last_kept is not None and not final and key['frame'] not in bounds
                    and in_windows[i] == 1):
                nxt = keys[inside[position + 1]]
                # Every key dropped since the last one kept must still lie on
                # the new segment, so errors do not add up.
                if all(_redundant(keys[last_kept], keys[j], nxt, interpolation, tolerance,
                                  rotation) for j in pending + [i]):
                    pending.append(i)
                    continue
            drop.update(pending)
            pending = []
            last_kept = i
        drop.update(pending)
    dead = set()
    if dead_keys and not is_global and windows:
        dead = {i for i, count in enumerate(in_windows) if count == 0}
    gone = drop | dead
    if gone:
        track['keys'] = [k for i, k in enumerate(keys) if i not in gone]
    return len(drop), len(dead)


def remove_redundant_keys(model, tolerance=0.0, dead=False):
    """Remove the keys of `model` (a ModelFile or its data) that change
    nothing. Returns the number of keys before, removed as redundant and
    removed as never played."""
    model = model_data(model)
    before = redundant = never = 0
    for tag, track in tracks(model):
        before += len(track.get('keys') or [])
        r, d = _prune(model, tag, track, tolerance, dead)
        redundant += r
        never += d
    return {'keys': before, 'redundant': redundant, 'dead': never}
