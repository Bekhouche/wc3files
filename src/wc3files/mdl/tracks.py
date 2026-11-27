"""Keyframe track codecs shared by MDX nodes and animated objects."""

import struct

from .stream import BinaryStream

TRACK_VALUE_SIZE = {
    # Node
    'KGTR': ('f32x3', 12),
    'KGRT': ('f32x4', 16),
    'KGSC': ('f32x3', 12),
    # Layer
    'KMTF': ('u32', 4),
    'KMTA': ('f32', 4),
    'KMTE': ('f32', 4),
    'KFC3': ('f32x3', 12),
    'KFCA': ('f32', 4),
    'KFTC': ('f32', 4),
    # Texture animation
    'KTAT': ('f32x3', 12),
    'KTAR': ('f32x4', 16),
    'KTAS': ('f32x3', 12),
    # Geoset animation
    'KGAO': ('f32', 4),
    'KGAC': ('f32x3', 12),
    # Light
    'KLAS': ('f32', 4),
    'KLAE': ('f32', 4),
    'KLAC': ('f32x3', 12),
    'KLAI': ('f32', 4),
    'KLBI': ('f32', 4),
    'KLBC': ('f32x3', 12),
    'KLAV': ('f32', 4),
    # Light, version 1300 and later (#376): shadow casting start and end,
    # quadratic and linear falloff, damping
    'KLSS': ('f32', 4),
    'KLSE': ('f32', 4),
    'KLQF': ('f32', 4),
    'KLLF': ('f32', 4),
    'KLDA': ('f32', 4),
    # Attachment
    'KATV': ('f32', 4),
    # Particle emitter
    'KPEE': ('f32', 4),
    'KPEG': ('f32', 4),
    'KPLN': ('f32', 4),
    'KPLT': ('f32', 4),
    'KPEL': ('f32', 4),
    'KPES': ('f32', 4),
    'KPEV': ('f32', 4),
    # Particle emitter 2
    'KP2E': ('f32', 4),
    'KP2G': ('f32', 4),
    'KP2L': ('f32', 4),
    'KP2S': ('f32', 4),
    'KP2V': ('f32', 4),
    'KP2R': ('f32', 4),
    'KP2N': ('f32', 4),
    'KP2W': ('f32', 4),
    # Ribbon emitter
    'KRVS': ('f32', 4),
    'KRHA': ('f32', 4),
    'KRHB': ('f32', 4),
    'KRAL': ('f32', 4),
    'KRCO': ('f32x3', 12),
    'KRTX': ('u32', 4),
    # Camera
    'KCTR': ('f32x3', 12),
    'KCRL': ('f32', 4),
    'KTTR': ('f32x3', 12),
    # Reforged cinematic cameras (the game's own Cinematics/ models): one
    # float per key each; the tags read back to front are FUDI, FALE and
    # FSTP, perhaps depth-of-field settings - unconfirmed, so the raw tags
    # are kept.
    'IDUF': ('f32', 4),
    'ELAF': ('f32', 4),
    'PTSF': ('f32', 4),
    # Corn emitter
    'KPPA': ('f32', 4),
    'KPPC': ('f32x3', 12),
    'KPPE': ('f32', 4),
    'KPPL': ('f32', 4),
    'KPPS': ('f32', 4),
    'KPPV': ('f32', 4),
}


def _write_value(stream, kind, value):
    if kind == 'u32':
        stream.write_u32(int(value))
    elif kind == 'f32':
        stream.write_f32(value)
    elif kind in ('f32x3', 'f32x4'):
        stream.write_float_array(value)
    else:
        raise ValueError(f'Unknown track value kind: {kind}')


# struct code and value width of each track value kind.
_KEY_LAYOUT = {'u32': ('I', 1), 'f32': ('f', 1), 'f32x3': ('3f', 3), 'f32x4': ('4f', 4)}
_KEY_STRUCTS = {}


def _key_struct(kind, tangents):
    key = (kind, tangents)
    found = _KEY_STRUCTS.get(key)
    if found is None:
        code, _ = _KEY_LAYOUT[kind]
        found = _KEY_STRUCTS[key] = struct.Struct('<i' + code * (3 if tangents else 1))
    return found


def _read_keys(stream, kind, count, tangents):
    """The `count` keys of one track, read in one pass (#176): one read for
    the whole track and one unpack per key, instead of a read per frame,
    value and tangent. The keys are the same as before: an int frame, a
    float or int value (a list for f32x3 and f32x4), and inTan and outTan
    when the track interpolates with tangents."""
    if kind not in _KEY_LAYOUT:
        raise ValueError(f'Unknown track value kind: {kind}')
    layout = _key_struct(kind, tangents)
    raw = stream.read_bytes(layout.size * count)
    width = _KEY_LAYOUT[kind][1]
    keys = []
    append = keys.append
    if width == 1:
        if tangents:
            for frame, value, in_tan, out_tan in layout.iter_unpack(raw):
                append({'frame': frame, 'value': value, 'inTan': in_tan, 'outTan': out_tan})
        else:
            for frame, value in layout.iter_unpack(raw):
                append({'frame': frame, 'value': value})
    elif tangents:
        a, b = 1 + width, 1 + 2 * width
        for v in layout.iter_unpack(raw):
            append({'frame': v[0], 'value': list(v[1:a]),
                    'inTan': list(v[a:b]), 'outTan': list(v[b:])})
    else:
        for v in layout.iter_unpack(raw):
            append({'frame': v[0], 'value': list(v[1:])})
    return keys


def read_tracks(stream, end=None, only=None):
    """Read optional keyframe tracks until ``end`` (absolute offset) or EOF.
    With ``only``, stop at the first track whose tag is not in it."""
    tracks = {}
    while True:
        if end is not None and stream.tell() >= end:
            break
        if stream.remaining() < 8:
            break
        tag = stream.peek(4).decode('latin-1')
        if tag not in TRACK_VALUE_SIZE or (only is not None and tag not in only):
            break
        if tag in tracks and only is not None:
            break
        stream.read_tag()
        kind, _ = TRACK_VALUE_SIZE[tag]
        count = stream.read_u32()
        interpolation = stream.read_u32()
        global_seq = stream.read_u32()
        keys = _read_keys(stream, kind, count, interpolation > 1)
        tracks[tag] = {
            'interpolationType': interpolation,
            'globalSequenceId': global_seq,
            'keys': keys,
        }
    return tracks


def write_tracks(stream, tracks):
    if not tracks:
        return
    for tag, track in tracks.items():
        if tag not in TRACK_VALUE_SIZE:
            raise ValueError(f'Unsupported track tag: {tag}')
        kind, _ = TRACK_VALUE_SIZE[tag]
        keys = track.get('keys') or []
        interpolation = track.get('interpolationType', 0)
        stream.write_tag(tag)
        stream.write_u32(len(keys))
        stream.write_u32(interpolation)
        stream.write_u32(track.get('globalSequenceId', 0xFFFFFFFF))
        for key in keys:
            stream.write_i32(key['frame'])
            _write_value(stream, kind, key['value'])
            if interpolation > 1:
                _write_value(stream, kind, key['inTan'])
                _write_value(stream, kind, key['outTan'])


def read_extent(stream):
    return {
        'boundsRadius': stream.read_f32(),
        'minimum': stream.read_float_array(3),
        'maximum': stream.read_float_array(3),
    }


def write_extent(stream, extent):
    extent = extent or {}
    stream.write_f32(extent.get('boundsRadius', 0.0))
    stream.write_float_array(extent.get('minimum') or [0.0, 0.0, 0.0])
    stream.write_float_array(extent.get('maximum') or [0.0, 0.0, 0.0])


def read_node(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    node = {
        'name': stream.read_fixed_string(80),
        'objectId': stream.read_u32(),
        'parentId': stream.read_u32(),
        'flags': stream.read_u32(),
    }
    node['tracks'] = read_tracks(stream, end)
    if stream.tell() != end:
        stream.seek(end)
    node['inclusiveSize'] = inclusive
    return node


def write_node(stream, node):
    body = BinaryStream(mode='write')
    body.write_fixed_string(node.get('name', ''), 80)
    body.write_u32(node.get('objectId', 0))
    parent = node.get('parentId', 0xFFFFFFFF)
    body.write_u32(0xFFFFFFFF if parent is None else parent)
    body.write_u32(node.get('flags', 0))
    write_tracks(body, node.get('tracks'))
    payload = body.getvalue()
    stream.write_u32(len(payload) + 4)
    stream.write_bytes(payload)
