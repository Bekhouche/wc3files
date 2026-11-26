"""
Sequences removed from a model (#238), as AnimRemover does.

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
