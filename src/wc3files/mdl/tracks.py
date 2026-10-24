"""Keyframe track codecs shared by MDX nodes and animated objects."""

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
    # Corn emitter
    'KPPA': ('f32', 4),
    'KPPC': ('f32x3', 12),
    'KPPE': ('f32', 4),
    'KPPL': ('f32', 4),
    'KPPS': ('f32', 4),
    'KPPV': ('f32', 4),
}


def _read_value(stream, kind):
    if kind == 'u32':
        return stream.read_u32()
    if kind == 'f32':
        return stream.read_f32()
    if kind == 'f32x3':
        return stream.read_float_array(3)
    if kind == 'f32x4':
        return stream.read_float_array(4)
    raise ValueError(f'Unknown track value kind: {kind}')


def _write_value(stream, kind, value):
    if kind == 'u32':
        stream.write_u32(int(value))
    elif kind == 'f32':
        stream.write_f32(value)
    elif kind in ('f32x3', 'f32x4'):
        stream.write_float_array(value)
    else:
        raise ValueError(f'Unknown track value kind: {kind}')


def read_tracks(stream, end=None):
    """Read optional keyframe tracks until ``end`` (absolute offset) or EOF."""
    tracks = {}
    while True:
        if end is not None and stream.tell() >= end:
            break
        if stream.remaining() < 8:
            break
        tag = stream.peek(4).decode('latin-1')
        if tag not in TRACK_VALUE_SIZE:
            break
        stream.read_tag()
        kind, _ = TRACK_VALUE_SIZE[tag]
        count = stream.read_u32()
        interpolation = stream.read_u32()
        global_seq = stream.read_u32()
        keys = []
        for _ in range(count):
            frame = stream.read_i32()
            value = _read_value(stream, kind)
            key = {'frame': frame, 'value': value}
            if interpolation > 1:
                key['inTan'] = _read_value(stream, kind)
                key['outTan'] = _read_value(stream, kind)
            keys.append(key)
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
