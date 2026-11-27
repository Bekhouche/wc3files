"""
Sequences removed from a model (#238), as AnimRemover does, and copied
from one model to another (#239), as the Animation Transferer does: see
`transfer_sequences` below.

`remove_sequences(model, names, pack=False)` removes the sequences whose
names are in `names` (compared without regard to case), with what only
they used:

- each geoset's extent for them (`sequenceExtents` goes by sequence);
- the keyframes no kept sequence plays, on every track that does not run on
  a global sequence: a key inside a removed sequence's interval and inside
  no kept one's goes;
- the frames of event objects (sounds, footprints, spawned effects) inside
  removed intervals only.

Keys outside every interval, which no sequence played before either, stay
unless `pack` is set. Global sequence tracks are not changed: they run on
their own clock, whatever sequence plays.

With `pack`, the kept sequences are moved up in time, in the order they
start, so that each starts after the one before with the smallest gap the
model had between two sequences (at least 1 ms). Their keys and event
frames move with them, and keys outside every kept interval go, since
nothing could play them any more.
"""
from .animation import NONE
from .batch import model_data
from .keyframes import tracks


def _inside(frame, windows):
    return any(start <= frame <= end for start, end in windows)


def _event_objects(model):
    return model.get('eventObjects') or []


def remove_sequences(model, names, pack=False):
    """Remove the named sequences of `model` (a ModelFile or its data).
    Returns the names removed, the names not found and the keys removed."""
    model = model_data(model)
    wanted = {name.lower() for name in names}
    sequences = model.get('sequences') or []
    gone = [i for i, s in enumerate(sequences) if (s.get('name') or '').lower() in wanted]
    found = {sequences[i].get('name', '').lower() for i in gone}
    missing = [name for name in names if name.lower() not in found]
    kept = [i for i in range(len(sequences)) if i not in gone]
    removed_windows = [tuple(sequences[i]['interval']) for i in gone]
    kept_windows = [tuple(sequences[i]['interval']) for i in kept]

    # How each kept window moves when packed.
    shift = {}
    if pack and kept_windows:
        starts = sorted(set(kept_windows))
        all_windows = sorted({tuple(s['interval']) for s in sequences})
        gaps = [b[0] - a[1] for a, b in zip(all_windows, all_windows[1:]) if b[0] - a[1] > 0]
        gap = max(1, min(gaps)) if gaps else 1
        cursor = None
        for start, end in starts:
            # The first keeps its start; each other follows the one before.
            new_start = start if cursor is None else cursor + gap
            shift[(start, end)] = new_start - start
            cursor = new_start + (end - start)

    def moved(frame):
        for window, delta in shift.items():
            if window[0] <= frame <= window[1]:
                return frame + delta
        return None

    keys_removed = 0
    for _, track in tracks(model):
        if track.get('globalSequenceId', NONE) not in (None, NONE):
            continue
        keys = track.get('keys') or []
        out = []
        for key in keys:
            frame = key['frame']
            if pack:
                new = moved(frame)
                if new is None:
                    continue
                key['frame'] = new
            elif _inside(frame, removed_windows) and not _inside(frame, kept_windows):
                continue
            out.append(key)
        keys_removed += len(keys) - len(out)
        track['keys'] = out
    for event in _event_objects(model):
        if event.get('globalSequenceId', NONE) not in (None, NONE):
            continue
        frames = event.get('tracks')
        if not isinstance(frames, list):
            continue
        out = []
        for frame in frames:
            if pack:
                new = moved(frame)
                if new is not None:
                    out.append(new)
            elif not (_inside(frame, removed_windows) and not _inside(frame, kept_windows)):
                out.append(frame)
        keys_removed += len(frames) - len(out)
        event['tracks'] = out

    for geoset in model.get('geosets') or []:
        extents = geoset.get('sequenceExtents') or []
        if len(extents) == len(sequences):
            geoset['sequenceExtents'] = [extents[i] for i in kept]
    model['sequences'] = [sequences[i] for i in kept]
    if pack:
        for sequence in model['sequences']:
            delta = shift[tuple(sequence['interval'])]
            sequence['interval'] = [sequence['interval'][0] + delta,
                                    sequence['interval'][1] + delta]
    return {'removed': [sequences[i]['name'] for i in gone], 'missing': missing,
            'keys': keys_removed}


# Sequences copied between models (#239).

def _node_name(name):
    return (name or '').strip().lower()


def _named_nodes(model):
    """{(name, n): node}: models imported into maps repeat names (two 'jaw'
    bones, two particle emitters 'BlizParticle01'), so the nth node of a
    name in one model matches the nth of that name in the other."""
    from .animation import nodes
    found, seen = {}, {}
    for _, _, node in nodes(model):
        name = _node_name(node.get('name'))
        seen[name] = seen.get(name, -1) + 1
        found[(name, seen[name])] = node
    return found


def _named_events(model):
    found, seen = {}, {}
    for event in model.get('eventObjects') or []:
        name = _node_name((event.get('node') or {}).get('name'))
        seen[name] = seen.get(name, -1) + 1
        found[(name, seen[name])] = event
    return found


def _lerp(a, b, t):
    if isinstance(a, (int, float)):
        return a + (b - a) * t
    return [x + (y - x) * t for x, y in zip(a, b)]


def _minus(a, b):
    if isinstance(a, (int, float)):
        return a - b
    return [x - y for x, y in zip(a, b)]


def _as_linear_tangents(keys, interpolation):
    """Tangents that make a Hermite (2) or Bezier (3) track run straight
    from each key to the next, as a linear one does."""
    out = []
    for i, key in enumerate(keys):
        value = key['value']
        prev = keys[i - 1]['value'] if i else value
        nxt = keys[i + 1]['value'] if i + 1 < len(keys) else value
        if interpolation == 2:
            out_tan, in_tan = _minus(nxt, value), _minus(value, prev)
        else:
            out_tan, in_tan = _lerp(value, nxt, 1 / 3), _lerp(value, prev, 1 / 3)
        out.append({'frame': key['frame'], 'value': value, 'inTan': in_tan, 'outTan': out_tan})
    return out


def _converted(keys, source, target):
    """`keys` of a track with interpolation `source`, made fit for a track
    with `target`, and whether that changed how they play."""
    if source == target:
        return keys, False
    plain = [{'frame': k['frame'], 'value': k['value']} for k in keys]
    if target in (2, 3):
        # Exact from a linear source; a step source's jumps become lines.
        return _as_linear_tangents(plain, target), source != 1
    return plain, True


def _last_frame(model):
    last = max([s['interval'][1] for s in model.get('sequences') or []] + [0])
    for _, track in tracks(model):
        if track.get('globalSequenceId', NONE) in (None, NONE):
            last = max([last] + [k['frame'] for k in track.get('keys') or []])
    for event in model.get('eventObjects') or []:
        if isinstance(event.get('tracks'), list):
            last = max([last] + event['tracks'])
    return last


def transfer_sequences(target, source, names=None, gap=None):
    """Copy sequences of `source` into `target` (ModelFiles or their data),
    matching nodes and event objects by name, without regard to case or to
    spaces at either end. `names` picks the sequences (all by default). Each
    goes after the target's last sequence and last key, `gap` ms apart (by
    default the target's smallest gap between two sequences, else 100).

    Each keyframe track of a matched node that does not run on a global
    sequence brings its keys inside the sequence, moved to the new
    interval. Where the target's track has another interpolation, the keys
    are made fit for it: from linear to Hermite or Bezier exactly, with
    tangents that keep each segment straight; the other ways lose the
    curve's shape or the steps, and are counted in `converted`. Geoset
    animations are not copied, since geosets have no names to match. The
    new sequence takes the source's extent, and each target geoset its own
    extent for it. Returns what was copied and what did not match."""
    target, source = model_data(target), model_data(source)
    wanted = None if names is None else {n.lower() for n in names}
    picked = [s for s in source.get('sequences') or []
              if wanted is None or (s.get('name') or '').lower() in wanted]
    found = {(s.get('name') or '').lower() for s in picked}
    missing = [] if names is None else [n for n in names if n.lower() not in found]
    existing = {(s.get('name') or '').lower() for s in target.get('sequences') or []}

    windows = sorted(tuple(s['interval']) for s in target.get('sequences') or [])
    if gap is None:
        gaps = [b[0] - a[1] for a, b in zip(windows, windows[1:]) if b[0] - a[1] > 0]
        gap = min(gaps) if gaps else 100
    cursor = _last_frame(target) + gap

    target_nodes = _named_nodes(target)
    source_nodes = _named_nodes(source)
    report = {'sequences': [], 'missing': missing, 'already_named': [], 'keys': 0,
              'converted': 0, 'global_skipped': 0,
              'unmatched_nodes': sorted(n for n, _ in source_nodes if (n, _) not in target_nodes),
              'target_only_nodes': sorted(n for n, _ in target_nodes if (n, _) not in source_nodes),
              'geoset_animations': len(source.get('geosetAnimations') or [])}
    target_events = _named_events(target)
    for sequence in picked:
        start, end = sequence['interval']
        delta = cursor - start
        new = dict(sequence)
        new['interval'] = [start + delta, end + delta]
        new['extent'] = dict(sequence.get('extent') or {})
        for name, node in source_nodes.items():
            other = target_nodes.get(name)
            if other is None:
                continue
            for tag, track in (node.get('tracks') or {}).items():
                if track.get('globalSequenceId', NONE) not in (None, NONE):
                    report['global_skipped'] += 1
                    continue
                keys = [dict(k, frame=k['frame'] + delta) for k in track.get('keys') or []
                        if start <= k['frame'] <= end]
                if not keys:
                    continue
                into = other.setdefault('tracks', {}).get(tag)
                if into is None:
                    into = other['tracks'][tag] = {
                        'interpolationType': track.get('interpolationType', 0),
                        'globalSequenceId': NONE, 'keys': []}
                if into.get('globalSequenceId', NONE) not in (None, NONE):
                    report['global_skipped'] += 1
                    continue
                keys, lossy = _converted(keys, track.get('interpolationType', 0),
                                         into.get('interpolationType', 0))
                report['converted'] += lossy
                into['keys'] = sorted(into.get('keys', []) + keys, key=lambda k: k['frame'])
                report['keys'] += len(keys)
        for key, event in _named_events(source).items():
            other = target_events.get(key)
            if other is None or not isinstance(event.get('tracks'), list):
                continue
            frames = [f + delta for f in event['tracks'] if start <= f <= end]
            other['tracks'] = sorted(other.get('tracks', []) + frames)
            report['keys'] += len(frames)
        target.setdefault('sequences', []).append(new)
        for geoset in target.get('geosets') or []:
            extents = geoset.get('sequenceExtents')
            if extents is not None and len(extents) == len(target['sequences']) - 1:
                extents.append(dict(geoset.get('extent') or {}))
        if (sequence.get('name') or '').lower() in existing:
            report['already_named'].append(sequence.get('name'))
        report['sequences'].append(sequence.get('name'))
        cursor = new['interval'][1] + gap
    return report
