"""MDX (binary) load/save for ModelFile."""

from .stream import BinaryStream
from .tracks import read_extent, write_extent, read_node, write_node, read_tracks, write_tracks


# A geoset's SKIN payload is followed by one of these. Which one is
# present is what tells the reader how long the payload was; see
# _read_geoset.
_GEOSET_AFTER_SKIN = frozenset({'UVAS', 'TANG', 'BIDX', 'UVBS'})

def empty_model():
    return {
        'version': 800,
        'name': '',
        'animationFile': '',
        'extent': {'boundsRadius': 0.0, 'minimum': [0.0, 0.0, 0.0], 'maximum': [0.0, 0.0, 0.0]},
        'blendTime': 0,
        'sequences': [],
        'globalSequences': [],
        'materials': [],
        'textures': [],
        'textureAnimations': [],
        'geosets': [],
        'geosetAnimations': [],
        'bones': [],
        'lights': [],
        'helpers': [],
        'attachments': [],
        'pivotPoints': [],
        'particleEmitters': [],
        'particleEmitters2': [],
        'particleEmittersPopcorn': [],
        'ribbonEmitters': [],
        'cameras': [],
        'eventObjects': [],
        'collisionShapes': [],
        'faceEffects': [],
        'bindPose': [],
        'unknownChunks': [],
    }


def load_mdx(data):
    stream = BinaryStream(data, 'read')
    magic = stream.read_tag()
    if magic != 'MDLX':
        raise ValueError(f'Invalid MDX magic: {magic!r}')

    model = empty_model()
    while stream.remaining() >= 8:
        tag = stream.read_tag()
        size = stream.read_u32()
        start = stream.tell()
        end = start + size

        if tag == 'VERS':
            model['version'] = stream.read_u32()
        elif tag == 'MODL':
            model['name'] = stream.read_fixed_string(80)
            model['animationFile'] = stream.read_fixed_string(260)
            model['extent'] = read_extent(stream)
            model['blendTime'] = stream.read_u32()
        elif tag == 'SEQS':
            for _ in range(size // 132):
                model['sequences'].append(_read_sequence(stream))
        elif tag == 'GLBS':
            model['globalSequences'] = stream.read_u32_array(size // 4)
        elif tag == 'TEXS':
            for _ in range(size // 268):
                model['textures'].append(_read_texture(stream))
        elif tag == 'SNDS':
            # Legacy / unused; preserve as unknown.
            model['unknownChunks'].append({'tag': tag, 'data': stream.read_bytes(size)})
        elif tag == 'MTLS':
            model['materials'] = _read_sized_objects(stream, size, lambda s: _read_material(s, model['version']))
        elif tag == 'TXAN':
            model['textureAnimations'] = _read_sized_objects(stream, size, _read_texture_animation)
        elif tag == 'GEOS':
            model['geosets'] = _read_sized_objects(stream, size, lambda s: _read_geoset(s, model['version']))
        elif tag == 'GEOA':
            model['geosetAnimations'] = _read_sized_objects(stream, size, _read_geoset_animation)
        elif tag == 'BONE':
            model['bones'] = _read_until(stream, size, _read_bone)
        elif tag == 'LITE':
            model['lights'] = _read_sized_objects(stream, size, _read_light)
        elif tag == 'HELP':
            model['helpers'] = _read_until(stream, size, _read_helper)
        elif tag == 'ATCH':
            model['attachments'] = _read_sized_objects(stream, size, _read_attachment)
        elif tag == 'PIVT':
            count = size // 12
            model['pivotPoints'] = [stream.read_float_array(3) for _ in range(count)]
        elif tag == 'PREM':
            model['particleEmitters'] = _read_sized_objects(stream, size, _read_particle_emitter)
        elif tag == 'PRE2':
            model['particleEmitters2'] = _read_sized_objects(stream, size, _read_particle_emitter2)
        elif tag == 'RIBB':
            model['ribbonEmitters'] = _read_sized_objects(stream, size, _read_ribbon_emitter)
        elif tag == 'EVTS':
            model['eventObjects'] = _read_until(stream, size, _read_event_object)
        elif tag == 'CAMS':
            model['cameras'] = _read_sized_objects(stream, size, _read_camera)
        elif tag == 'CLID':
            model['collisionShapes'] = _read_until(stream, size, _read_collision_shape)
        elif tag == 'BPOS':
            count = stream.read_u32()
            model['bindPose'] = [stream.read_float_array(12) for _ in range(count)]
        elif tag == 'FAFX':
            # One entry is an 80-byte target plus a 260-byte path = 340 bytes.
            # The divisor was 380, so entries were silently dropped: a real
            # cinematic model with a 5440-byte chunk (exactly 16 entries)
            # yielded 14, and rewriting those 14 produced a chunk that read
            # back as 12. Confirmed against real chunks - 5440 and 2040 both
            # divide evenly by 340 and not at all by 380, and decoding at a
            # 340 stride gives sensible targets and real .facefx paths.
            for _ in range(size // 340):
                model['faceEffects'].append({
                    'target': stream.read_fixed_string(80),
                    'path': stream.read_fixed_string(260),
                })
        elif tag == 'CORN':
            model['particleEmittersPopcorn'] = _read_sized_objects(stream, size, _read_corn_emitter)
        else:
            model['unknownChunks'].append({'tag': tag, 'data': stream.read_bytes(size)})

        if stream.tell() != end:
            stream.seek(end)
    return model


def save_mdx(model):
    stream = BinaryStream(mode='write')
    stream.write_tag('MDLX')

    def chunk(tag, writer):
        body = BinaryStream(mode='write')
        writer(body)
        payload = body.getvalue()
        if not payload and tag not in ('VERS', 'MODL'):
            return
        stream.write_tag(tag)
        stream.write_u32(len(payload))
        stream.write_bytes(payload)

    version = model.get('version', 800)
    chunk('VERS', lambda s: s.write_u32(version))
    chunk('MODL', lambda s: _write_model_chunk(s, model))

    if model.get('sequences'):
        chunk('SEQS', lambda s: [_write_sequence(s, seq) for seq in model['sequences']])
    if model.get('globalSequences'):
        chunk('GLBS', lambda s: s.write_u32_array(model['globalSequences']))
    if model.get('materials'):
        chunk('MTLS', lambda s: [_write_material(s, m, version) for m in model['materials']])
    if model.get('textures'):
        chunk('TEXS', lambda s: [_write_texture(s, t) for t in model['textures']])
    if model.get('textureAnimations'):
        chunk('TXAN', lambda s: [_write_texture_animation(s, t) for t in model['textureAnimations']])
    if model.get('geosets'):
        chunk('GEOS', lambda s: [_write_geoset(s, g, version) for g in model['geosets']])
    if model.get('geosetAnimations'):
        chunk('GEOA', lambda s: [_write_geoset_animation(s, g) for g in model['geosetAnimations']])
    if model.get('bones'):
        chunk('BONE', lambda s: [_write_bone(s, b) for b in model['bones']])
    if model.get('lights'):
        chunk('LITE', lambda s: [_write_light(s, lit) for lit in model['lights']])
    if model.get('helpers'):
        chunk('HELP', lambda s: [_write_helper(s, h) for h in model['helpers']])
    if model.get('attachments'):
        chunk('ATCH', lambda s: [_write_attachment(s, a) for a in model['attachments']])
    if model.get('pivotPoints'):
        chunk('PIVT', lambda s: [s.write_float_array(p) for p in model['pivotPoints']])
    if model.get('particleEmitters'):
        chunk('PREM', lambda s: [_write_particle_emitter(s, e) for e in model['particleEmitters']])
    if model.get('particleEmitters2'):
        chunk('PRE2', lambda s: [_write_particle_emitter2(s, e) for e in model['particleEmitters2']])
    if model.get('ribbonEmitters'):
        chunk('RIBB', lambda s: [_write_ribbon_emitter(s, e) for e in model['ribbonEmitters']])
    if model.get('eventObjects'):
        chunk('EVTS', lambda s: [_write_event_object(s, e) for e in model['eventObjects']])
    if model.get('cameras'):
        chunk('CAMS', lambda s: [_write_camera(s, c) for c in model['cameras']])
    if model.get('collisionShapes'):
        chunk('CLID', lambda s: [_write_collision_shape(s, c) for c in model['collisionShapes']])
    if version > 800 and model.get('bindPose'):
        def write_bpos(s):
            s.write_u32(len(model['bindPose']))
            for matrix in model['bindPose']:
                s.write_float_array(matrix)
        chunk('BPOS', write_bpos)
    if version > 800 and model.get('faceEffects'):
        def write_fafx(s):
            for face in model['faceEffects']:
                s.write_fixed_string(face.get('target', ''), 80)
                s.write_fixed_string(face.get('path', ''), 260)
        chunk('FAFX', write_fafx)
    if version > 800 and model.get('particleEmittersPopcorn'):
        chunk('CORN', lambda s: [_write_corn_emitter(s, e) for e in model['particleEmittersPopcorn']])

    for unknown in model.get('unknownChunks') or []:
        data = unknown.get('data') or b''
        stream.write_tag(unknown['tag'])
        stream.write_u32(len(data))
        stream.write_bytes(data)

    return stream.getvalue()


def _read_sized_objects(stream, size, reader):
    objects = []
    total = 0
    while total < size:
        start = stream.tell()
        inclusive = stream.read_u32()
        stream.seek(start)
        objects.append(reader(stream))
        total += inclusive
    return objects


def _read_until(stream, size, reader):
    objects = []
    end = stream.tell() + size
    while stream.tell() < end:
        objects.append(reader(stream))
    return objects


def _write_sized(stream, writer):
    body = BinaryStream(mode='write')
    writer(body)
    payload = body.getvalue()
    stream.write_u32(len(payload) + 4)
    stream.write_bytes(payload)


def _read_sequence(stream):
    return {
        'name': stream.read_fixed_string(80),
        'interval': list(stream.read_u32_array(2)),
        'moveSpeed': stream.read_f32(),
        'flags': stream.read_u32(),
        'rarity': stream.read_f32(),
        'syncPoint': stream.read_u32(),
        'extent': read_extent(stream),
    }


def _write_sequence(stream, seq):
    stream.write_fixed_string(seq.get('name', ''), 80)
    stream.write_u32_array(seq.get('interval') or [0, 0])
    stream.write_f32(seq.get('moveSpeed', 0.0))
    stream.write_u32(seq.get('flags', 0))
    stream.write_f32(seq.get('rarity', 0.0))
    stream.write_u32(seq.get('syncPoint', 0))
    write_extent(stream, seq.get('extent'))


def _write_model_chunk(stream, model):
    stream.write_fixed_string(model.get('name', ''), 80)
    stream.write_fixed_string(model.get('animationFile', ''), 260)
    write_extent(stream, model.get('extent'))
    stream.write_u32(model.get('blendTime', 0))


def _read_texture(stream):
    return {
        'replaceableId': stream.read_u32(),
        'path': stream.read_fixed_string(260),
        'flags': stream.read_u32(),
    }


def _write_texture(stream, texture):
    stream.write_u32(texture.get('replaceableId', 0))
    stream.write_fixed_string(texture.get('path', ''), 260)
    stream.write_u32(texture.get('flags', 0))


def _read_material(stream, version):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    material = {
        'priorityPlane': stream.read_u32(),
        'flags': stream.read_u32(),
    }
    # The 80-byte material shader string exists in versions 900 and 1000 and
    # is gone again by 1200. Measured directly: in a version-1000 model LAYS
    # sits 92 bytes into the material - the 12 bytes of inclusive size,
    # priorityPlane and flags, plus the shader - while in version 1200 and in
    # retail's version 1800 models it sits at 12.
    # This was previously version 900 alone, which was right about retail and
    # wrong about 1000, and cost 48 of the models sampled from the benchmark
    # corpus; reading it for every version above 800 is wrong the other way
    # and eats 80 bytes of layer data.
    if version in (900, 1000):
        material['shader'] = stream.read_fixed_string(80)
    tag = stream.read_tag()
    if tag != 'LAYS':
        raise ValueError(f'Expected LAYS, got {tag!r}')
    count = stream.read_u32()
    material['layers'] = [_read_layer(stream, version) for _ in range(count)]
    if stream.tell() != end:
        stream.seek(end)
    return material


def _write_material(stream, material, version):
    def write_body(body):
        body.write_u32(material.get('priorityPlane', 0))
        body.write_u32(material.get('flags', 0))
        if version in (900, 1000):  # see _read_material
            body.write_fixed_string(material.get('shader', ''), 80)
        layers = material.get('layers') or []
        body.write_tag('LAYS')
        body.write_u32(len(layers))
        for layer in layers:
            _write_layer(body, layer, version)
    _write_sized(stream, write_body)


def _read_layer(stream, version):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    layer = {
        'filterMode': stream.read_u32(),
        'shadingFlags': stream.read_u32(),
        'textureId': stream.read_u32(),
        'textureAnimationId': stream.read_u32(),
        'coordId': stream.read_u32(),
        'alpha': stream.read_f32(),
    }
    if version > 800:
        layer['emissiveGain'] = stream.read_f32()
        layer['fresnelColor'] = stream.read_float_array(3)
        layer['fresnelOpacity'] = stream.read_f32()
        layer['fresnelTeamColor'] = stream.read_f32()
    if version >= 1100:
        # Sixteen further bytes sit between the fresnel fields and the track
        # data from version 1100 on - measured across 569 layers in versions
        # 1100, 1200, 1400 and 1800, and absent from the 208 version-1000
        # layers sampled. They were not read at all, so track parsing began
        # in the wrong place and the bytes were then dropped by the seek
        # below, which is why materials came out 16 bytes short per layer.
        layer['unknown_v1100'] = stream.read_u8_array(16)
    layer['tracks'] = read_tracks(stream, end)
    if stream.tell() != end:
        # Anything still unaccounted for is kept rather than discarded, so a
        # layout this reader does not know about still writes back unchanged.
        layer['trailing'] = stream.read_u8_array(end - stream.tell())
    return layer


def _write_layer(stream, layer, version):
    def write_body(body):
        body.write_u32(layer.get('filterMode', 0))
        body.write_u32(layer.get('shadingFlags', 0))
        body.write_u32(layer.get('textureId', 0))
        anim = layer.get('textureAnimationId', 0xFFFFFFFF)
        body.write_u32(0xFFFFFFFF if anim is None else anim)
        body.write_u32(layer.get('coordId', 0))
        body.write_f32(layer.get('alpha', 1.0))
        if version > 800:
            body.write_f32(layer.get('emissiveGain', 1.0))
            body.write_float_array(layer.get('fresnelColor') or [1.0, 1.0, 1.0])
            body.write_f32(layer.get('fresnelOpacity', 0.0))
            body.write_f32(layer.get('fresnelTeamColor', 0.0))
        if version >= 1100:
            body.write_u8_array(layer.get('unknown_v1100') or bytes(16))
        write_tracks(body, layer.get('tracks'))
        if layer.get('trailing'):
            body.write_u8_array(layer['trailing'])
    _write_sized(stream, write_body)


def _read_texture_animation(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    anim = {'tracks': read_tracks(stream, end)}
    if stream.tell() != end:
        stream.seek(end)
    return anim


def _write_texture_animation(stream, anim):
    _write_sized(stream, lambda body: write_tracks(body, anim.get('tracks')))


def _read_geoset(stream, version):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    geoset = {}

    def expect(tag):
        got = stream.read_tag()
        if got != tag:
            raise ValueError(f'Expected {tag}, got {got!r}')

    expect('VRTX')
    count = stream.read_u32()
    geoset['vertices'] = stream.read_float_array(count * 3)

    expect('NRMS')
    count = stream.read_u32()
    geoset['normals'] = stream.read_float_array(count * 3)

    expect('PTYP')
    count = stream.read_u32()
    geoset['faceTypeGroups'] = stream.read_u32_array(count)

    expect('PCNT')
    count = stream.read_u32()
    geoset['faceGroups'] = stream.read_u32_array(count)

    expect('PVTX')
    count = stream.read_u32()
    geoset['faces'] = stream.read_u16_array(count)

    expect('GNDX')
    count = stream.read_u32()
    geoset['vertexGroups'] = stream.read_u8_array(count)

    expect('MTGC')
    count = stream.read_u32()
    geoset['matrixGroups'] = stream.read_u32_array(count)

    expect('MATS')
    count = stream.read_u32()
    geoset['matrixIndices'] = stream.read_u32_array(count)

    geoset['materialId'] = stream.read_u32()
    geoset['selectionGroup'] = stream.read_u32()
    geoset['selectionFlags'] = stream.read_u32()

    if version > 800:
        geoset['levelOfDetail'] = stream.read_u32()
        geoset['name'] = stream.read_fixed_string(80)

    geoset['extent'] = read_extent(stream)
    extents_count = stream.read_u32()
    geoset['sequenceExtents'] = [read_extent(stream) for _ in range(extents_count)]

    if version > 800 and stream.tell() + 4 <= end:
        tag = stream.peek(4).decode('latin-1')
        if tag == 'TANG':
            stream.read_tag()
            count = stream.read_u32()
            geoset['tangents'] = stream.read_float_array(count * 4)
        tag = stream.peek(4).decode('latin-1') if stream.tell() + 4 <= end else ''
        if tag == 'SKIN':
            stream.read_tag()
            # SKIN's count means different things in different model
            # versions, and both readings occur in real files. Measured: 255
            # chunks in the game's own version-1800 models carry count*2
            # bytes (a count of bone influences, two bytes each), while 166
            # chunks in version-1200 models from the benchmark corpus carry
            # exactly count bytes. Nothing was sampled between those two
            # versions, so rather than guess where the rule changes, the
            # payload is chosen by looking for the tag that has to follow it.
            count = stream.read_u32()
            start = stream.tell()
            size = count * 2
            for candidate in (count, count * 2):
                stream.seek(start + candidate)
                try:
                    if stream.read_tag() in _GEOSET_AFTER_SKIN:
                        size = candidate
                        break
                except Exception:
                    pass
            stream.seek(start)
            geoset['skin'] = stream.read_u8_array(size)

    expect('UVAS')
    uv_sets = stream.read_u32()
    geoset['uvSets'] = []
    for _ in range(uv_sets):
        expect('UVBS')
        count = stream.read_u32()
        geoset['uvSets'].append(stream.read_float_array(count * 2))

    if stream.tell() != end:
        stream.seek(end)
    return geoset


def _write_geoset(stream, geoset, version):
    def write_body(body):
        vertices = geoset.get('vertices') or []
        body.write_tag('VRTX')
        body.write_u32(len(vertices) // 3)
        body.write_float_array(vertices)

        normals = geoset.get('normals') or []
        body.write_tag('NRMS')
        body.write_u32(len(normals) // 3)
        body.write_float_array(normals)

        face_types = geoset.get('faceTypeGroups') or []
        body.write_tag('PTYP')
        body.write_u32(len(face_types))
        body.write_u32_array(face_types)

        face_groups = geoset.get('faceGroups') or []
        body.write_tag('PCNT')
        body.write_u32(len(face_groups))
        body.write_u32_array(face_groups)

        faces = geoset.get('faces') or []
        body.write_tag('PVTX')
        body.write_u32(len(faces))
        body.write_u16_array(faces)

        vertex_groups = geoset.get('vertexGroups') or []
        body.write_tag('GNDX')
        body.write_u32(len(vertex_groups))
        body.write_u8_array(vertex_groups)

        matrix_groups = geoset.get('matrixGroups') or []
        body.write_tag('MTGC')
        body.write_u32(len(matrix_groups))
        body.write_u32_array(matrix_groups)

        matrix_indices = geoset.get('matrixIndices') or []
        body.write_tag('MATS')
        body.write_u32(len(matrix_indices))
        body.write_u32_array(matrix_indices)

        body.write_u32(geoset.get('materialId', 0))
        body.write_u32(geoset.get('selectionGroup', 0))
        body.write_u32(geoset.get('selectionFlags', 0))

        if version > 800:
            body.write_u32(geoset.get('levelOfDetail', 0))
            body.write_fixed_string(geoset.get('name', ''), 80)

        write_extent(body, geoset.get('extent'))
        extents = geoset.get('sequenceExtents') or []
        body.write_u32(len(extents))
        for extent in extents:
            write_extent(body, extent)

        if version > 800 and geoset.get('tangents') is not None:
            tangents = geoset['tangents']
            body.write_tag('TANG')
            body.write_u32(len(tangents) // 4)
            body.write_float_array(tangents)
        if version > 800 and geoset.get('skin') is not None:
            skin = geoset['skin']
            body.write_tag('SKIN')
            body.write_u32(len(skin) // 2)  # influences, not bytes - see _read_geoset
            body.write_u8_array(skin)

        uv_sets = geoset.get('uvSets') or []
        body.write_tag('UVAS')
        body.write_u32(len(uv_sets))
        for uv in uv_sets:
            body.write_tag('UVBS')
            body.write_u32(len(uv) // 2)
            body.write_float_array(uv)

    _write_sized(stream, write_body)


def _read_geoset_animation(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    anim = {
        'alpha': stream.read_f32(),
        'flags': stream.read_u32(),
        'color': stream.read_float_array(3),
        'geosetId': stream.read_u32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return anim


def _write_geoset_animation(stream, anim):
    def write_body(body):
        body.write_f32(anim.get('alpha', 1.0))
        body.write_u32(anim.get('flags', 0))
        body.write_float_array(anim.get('color') or [1.0, 1.0, 1.0])
        body.write_u32(anim.get('geosetId', 0))
        write_tracks(body, anim.get('tracks'))
    _write_sized(stream, write_body)


def _read_bone(stream):
    node = read_node(stream)
    return {
        'node': node,
        'geosetId': stream.read_u32(),
        'geosetAnimationId': stream.read_u32(),
    }


def _write_bone(stream, bone):
    write_node(stream, bone.get('node') or {})
    stream.write_u32(bone.get('geosetId', 0xFFFFFFFF))
    stream.write_u32(bone.get('geosetAnimationId', 0xFFFFFFFF))


def _read_helper(stream):
    return {'node': read_node(stream)}


def _write_helper(stream, helper):
    write_node(stream, helper.get('node') or {})


def _read_light(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    # inclusive covers node + light fields + tracks; node has its own inclusive.
    node = read_node(stream)
    light = {
        'node': node,
        'type': stream.read_u32(),
        'attenuationStart': stream.read_f32(),
        'attenuationEnd': stream.read_f32(),
        'color': stream.read_float_array(3),
        'intensity': stream.read_f32(),
        'ambientColor': stream.read_float_array(3),
        'ambientIntensity': stream.read_f32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return light


def _write_light(stream, light):
    def write_body(body):
        write_node(body, light.get('node') or {})
        body.write_u32(light.get('type', 0))
        body.write_f32(light.get('attenuationStart', 0.0))
        body.write_f32(light.get('attenuationEnd', 0.0))
        body.write_float_array(light.get('color') or [1.0, 1.0, 1.0])
        body.write_f32(light.get('intensity', 0.0))
        body.write_float_array(light.get('ambientColor') or [0.0, 0.0, 0.0])
        body.write_f32(light.get('ambientIntensity', 0.0))
        write_tracks(body, light.get('tracks'))
    _write_sized(stream, write_body)


def _read_attachment(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    attachment = {
        'node': read_node(stream),
        'path': stream.read_fixed_string(260),
        'attachmentId': stream.read_u32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return attachment


def _write_attachment(stream, attachment):
    def write_body(body):
        write_node(body, attachment.get('node') or {})
        body.write_fixed_string(attachment.get('path', ''), 260)
        body.write_u32(attachment.get('attachmentId', 0))
        write_tracks(body, attachment.get('tracks'))
    _write_sized(stream, write_body)


def _read_particle_emitter(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    emitter = {
        'node': read_node(stream),
        'emissionRate': stream.read_f32(),
        'gravity': stream.read_f32(),
        'longitude': stream.read_f32(),
        'latitude': stream.read_f32(),
        'spawnModelFileName': stream.read_fixed_string(260),
        'lifespan': stream.read_f32(),
        'initialVelocity': stream.read_f32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return emitter


def _write_particle_emitter(stream, emitter):
    def write_body(body):
        write_node(body, emitter.get('node') or {})
        body.write_f32(emitter.get('emissionRate', 0.0))
        body.write_f32(emitter.get('gravity', 0.0))
        body.write_f32(emitter.get('longitude', 0.0))
        body.write_f32(emitter.get('latitude', 0.0))
        body.write_fixed_string(emitter.get('spawnModelFileName', ''), 260)
        body.write_f32(emitter.get('lifespan', 0.0))
        body.write_f32(emitter.get('initialVelocity', 0.0))
        write_tracks(body, emitter.get('tracks'))
    _write_sized(stream, write_body)


def _read_particle_emitter2(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    emitter = {
        'node': read_node(stream),
        'speed': stream.read_f32(),
        'variation': stream.read_f32(),
        'latitude': stream.read_f32(),
        'gravity': stream.read_f32(),
        'lifespan': stream.read_f32(),
        'emissionRate': stream.read_f32(),
        'length': stream.read_f32(),
        'width': stream.read_f32(),
        'filterMode': stream.read_u32(),
        'rows': stream.read_u32(),
        'columns': stream.read_u32(),
        'headOrTail': stream.read_u32(),
        'tailLength': stream.read_f32(),
        'time': stream.read_f32(),
        'segmentColor': [stream.read_float_array(3) for _ in range(3)],
        'segmentAlpha': stream.read_u8_array(3),
        'segmentScaling': stream.read_float_array(3),
        'headInterval': stream.read_u32_array(3),
        'headDecayInterval': stream.read_u32_array(3),
        'tailInterval': stream.read_u32_array(3),
        'tailDecayInterval': stream.read_u32_array(3),
        'textureId': stream.read_u32(),
        'squirt': stream.read_u32(),
        'priorityPlane': stream.read_u32(),
        'replaceableId': stream.read_u32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return emitter


def _write_particle_emitter2(stream, emitter):
    def write_body(body):
        write_node(body, emitter.get('node') or {})
        body.write_f32(emitter.get('speed', 0.0))
        body.write_f32(emitter.get('variation', 0.0))
        body.write_f32(emitter.get('latitude', 0.0))
        body.write_f32(emitter.get('gravity', 0.0))
        body.write_f32(emitter.get('lifespan', 0.0))
        body.write_f32(emitter.get('emissionRate', 0.0))
        body.write_f32(emitter.get('length', 0.0))
        body.write_f32(emitter.get('width', 0.0))
        body.write_u32(emitter.get('filterMode', 0))
        body.write_u32(emitter.get('rows', 0))
        body.write_u32(emitter.get('columns', 0))
        body.write_u32(emitter.get('headOrTail', 0))
        body.write_f32(emitter.get('tailLength', 0.0))
        body.write_f32(emitter.get('time', 0.0))
        for color in emitter.get('segmentColor') or [[1, 1, 1]] * 3:
            body.write_float_array(color)
        body.write_u8_array(emitter.get('segmentAlpha') or [255, 255, 255])
        body.write_float_array(emitter.get('segmentScaling') or [1.0, 1.0, 1.0])
        body.write_u32_array(emitter.get('headInterval') or [0, 0, 0])
        body.write_u32_array(emitter.get('headDecayInterval') or [0, 0, 0])
        body.write_u32_array(emitter.get('tailInterval') or [0, 0, 0])
        body.write_u32_array(emitter.get('tailDecayInterval') or [0, 0, 0])
        body.write_u32(emitter.get('textureId', 0))
        body.write_u32(emitter.get('squirt', 0))
        body.write_u32(emitter.get('priorityPlane', 0))
        body.write_u32(emitter.get('replaceableId', 0))
        write_tracks(body, emitter.get('tracks'))
    _write_sized(stream, write_body)


def _read_ribbon_emitter(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    emitter = {
        'node': read_node(stream),
        'heightAbove': stream.read_f32(),
        'heightBelow': stream.read_f32(),
        'alpha': stream.read_f32(),
        'color': stream.read_float_array(3),
        'lifespan': stream.read_f32(),
        'textureSlot': stream.read_u32(),
        'emissionRate': stream.read_u32(),
        'rows': stream.read_u32(),
        'columns': stream.read_u32(),
        'materialId': stream.read_u32(),
        'gravity': stream.read_f32(),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return emitter


def _write_ribbon_emitter(stream, emitter):
    def write_body(body):
        write_node(body, emitter.get('node') or {})
        body.write_f32(emitter.get('heightAbove', 0.0))
        body.write_f32(emitter.get('heightBelow', 0.0))
        body.write_f32(emitter.get('alpha', 1.0))
        body.write_float_array(emitter.get('color') or [1.0, 1.0, 1.0])
        body.write_f32(emitter.get('lifespan', 0.0))
        body.write_u32(emitter.get('textureSlot', 0))
        body.write_u32(emitter.get('emissionRate', 0))
        body.write_u32(emitter.get('rows', 0))
        body.write_u32(emitter.get('columns', 0))
        body.write_u32(emitter.get('materialId', 0))
        body.write_f32(emitter.get('gravity', 0.0))
        write_tracks(body, emitter.get('tracks'))
    _write_sized(stream, write_body)


def _read_event_object(stream):
    node = read_node(stream)
    tag = stream.read_tag()
    if tag != 'KEVT':
        raise ValueError(f'Expected KEVT, got {tag!r}')
    count = stream.read_u32()
    global_seq = stream.read_u32()
    tracks = stream.read_u32_array(count)
    return {
        'node': node,
        'globalSequenceId': global_seq,
        'tracks': tracks,
    }


def _write_event_object(stream, event):
    write_node(stream, event.get('node') or {})
    tracks = event.get('tracks') or []
    stream.write_tag('KEVT')
    stream.write_u32(len(tracks))
    stream.write_u32(event.get('globalSequenceId', 0xFFFFFFFF))
    stream.write_u32_array(tracks)


def _read_camera(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    camera = {
        'name': stream.read_fixed_string(80),
        'position': stream.read_float_array(3),
        'fieldOfView': stream.read_f32(),
        'farClippingPlane': stream.read_f32(),
        'nearClippingPlane': stream.read_f32(),
        'targetPosition': stream.read_float_array(3),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return camera


def _write_camera(stream, camera):
    def write_body(body):
        body.write_fixed_string(camera.get('name', ''), 80)
        body.write_float_array(camera.get('position') or [0.0, 0.0, 0.0])
        body.write_f32(camera.get('fieldOfView', 0.0))
        body.write_f32(camera.get('farClippingPlane', 0.0))
        body.write_f32(camera.get('nearClippingPlane', 0.0))
        body.write_float_array(camera.get('targetPosition') or [0.0, 0.0, 0.0])
        write_tracks(body, camera.get('tracks'))
    _write_sized(stream, write_body)


def _read_collision_shape(stream):
    node = read_node(stream)
    shape_type = stream.read_u32()
    if shape_type in (0, 1, 3):
        vertices = [stream.read_float_array(3), stream.read_float_array(3)]
    else:
        vertices = [stream.read_float_array(3)]
    shape = {
        'node': node,
        'type': shape_type,
        'vertices': vertices,
    }
    if shape_type in (2, 3):
        shape['radius'] = stream.read_f32()
    return shape


def _write_collision_shape(stream, shape):
    write_node(stream, shape.get('node') or {})
    shape_type = shape.get('type', 0)
    stream.write_u32(shape_type)
    for vertex in shape.get('vertices') or []:
        stream.write_float_array(vertex)
    if shape_type in (2, 3):
        stream.write_f32(shape.get('radius', 0.0))


def _read_corn_emitter(stream):
    inclusive = stream.read_u32()
    end = stream.tell() + inclusive - 4
    emitter = {
        'node': read_node(stream),
        'lifeSpan': stream.read_f32(),
        'emissionRate': stream.read_f32(),
        'speed': stream.read_f32(),
        'color': stream.read_float_array(4),
        'replaceableId': stream.read_u32(),
        'path': stream.read_fixed_string(260),
        'flags': stream.read_fixed_string(260),
        'tracks': read_tracks(stream, end),
    }
    if stream.tell() != end:
        stream.seek(end)
    return emitter


def _write_corn_emitter(stream, emitter):
    def write_body(body):
        write_node(body, emitter.get('node') or {})
        body.write_f32(emitter.get('lifeSpan', 0.0))
        body.write_f32(emitter.get('emissionRate', 0.0))
        body.write_f32(emitter.get('speed', 0.0))
        body.write_float_array(emitter.get('color') or [1.0, 1.0, 1.0, 1.0])
        body.write_u32(emitter.get('replaceableId', 0))
        body.write_fixed_string(emitter.get('path', ''), 260)
        body.write_fixed_string(emitter.get('flags', ''), 260)
        write_tracks(body, emitter.get('tracks'))
    _write_sized(stream, write_body)
