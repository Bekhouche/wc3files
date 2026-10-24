"""MDL (text) load/save for ModelFile.

Covers the common Warcraft III Art Tools MDL blocks used by map-imported models.
"""

import re

from .mdx_io import empty_model

_NUMBER = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$')


class MdlTokenizer:
    def __init__(self, text):
        # Strip // comments while preserving newlines for line tracking.
        cleaned = []
        for line in text.splitlines(True):
            if '//' in line:
                code, _ = line.split('//', 1)
                cleaned.append(code + ('\n' if line.endswith('\n') else ''))
            else:
                cleaned.append(line)
        self._tokens = re.findall(r'"[^"]*"|[{},]|[^\s{},]+', ''.join(cleaned))
        self._i = 0

    def peek(self):
        if self._i >= len(self._tokens):
            return None
        return self._tokens[self._i]

    def next(self):
        token = self.peek()
        if token is None:
            raise ValueError('Unexpected end of MDL')
        self._i += 1
        return token

    def expect(self, value):
        token = self.next()
        if token != value:
            raise ValueError(f'Expected {value!r}, got {token!r}')
        return token

    def remaining(self):
        return self._i < len(self._tokens)


def load_mdl(text):
    tokens = MdlTokenizer(text)
    model = empty_model()
    while tokens.remaining():
        token = tokens.peek()
        if token is None:
            break
        if token == 'Version':
            _parse_version(tokens, model)
        elif token == 'Model':
            _parse_model(tokens, model)
        elif token == 'Sequences':
            model['sequences'] = _parse_sequences(tokens)
        elif token == 'GlobalSequences':
            model['globalSequences'] = _parse_global_sequences(tokens)
        elif token == 'Textures':
            model['textures'] = _parse_textures(tokens)
        elif token == 'Materials':
            model['materials'] = _parse_materials(tokens)
        elif token == 'TextureAnims':
            model['textureAnimations'] = _parse_texture_anims(tokens)
        elif token == 'Geoset':
            model['geosets'].append(_parse_geoset(tokens))
        elif token == 'GeosetAnim':
            model['geosetAnimations'].append(_parse_geoset_anim(tokens))
        elif token == 'Bone':
            model['bones'].append(_parse_bone(tokens))
        elif token == 'Light':
            model['lights'].append(_parse_light(tokens))
        elif token == 'Helper':
            model['helpers'].append(_parse_helper(tokens))
        elif token == 'Attachment':
            model['attachments'].append(_parse_attachment(tokens))
        elif token == 'PivotPoints':
            model['pivotPoints'] = _parse_pivot_points(tokens)
        elif token == 'ParticleEmitter':
            model['particleEmitters'].append(_parse_particle_emitter(tokens))
        elif token == 'ParticleEmitter2':
            model['particleEmitters2'].append(_parse_particle_emitter2(tokens))
        elif token == 'RibbonEmitter':
            model['ribbonEmitters'].append(_parse_ribbon_emitter(tokens))
        elif token == 'EventObject':
            model['eventObjects'].append(_parse_event_object(tokens))
        elif token == 'Camera':
            model['cameras'].append(_parse_camera(tokens))
        elif token == 'CollisionShape':
            model['collisionShapes'].append(_parse_collision_shape(tokens))
        else:
            # Skip unknown top-level blocks conservatively.
            tokens.next()
            if tokens.peek() == '{':
                _skip_block(tokens)
            elif tokens.peek() is not None and _NUMBER.match(tokens.peek() or ''):
                tokens.next()
                if tokens.peek() == '{':
                    _skip_block(tokens)
    return model


def save_mdl(model):
    out = []
    version = model.get('version', 800)
    out.append('Version {')
    out.append(f'\tFormatVersion {version},')
    out.append('}')

    name = model.get('name', '')
    out.append(f'Model "{_escape(name)}" {{')
    if model.get('geosets'):
        out.append(f'\tNumGeosets {len(model["geosets"])},')
    if model.get('geosetAnimations'):
        out.append(f'\tNumGeosetAnims {len(model["geosetAnimations"])},')
    if model.get('helpers'):
        out.append(f'\tNumHelpers {len(model["helpers"])},')
    if model.get('lights'):
        out.append(f'\tNumLights {len(model["lights"])},')
    if model.get('bones'):
        out.append(f'\tNumBones {len(model["bones"])},')
    if model.get('attachments'):
        out.append(f'\tNumAttachments {len(model["attachments"])},')
    if model.get('particleEmitters'):
        out.append(f'\tNumParticleEmitters {len(model["particleEmitters"])},')
    if model.get('particleEmitters2'):
        out.append(f'\tNumParticleEmitters2 {len(model["particleEmitters2"])},')
    if model.get('ribbonEmitters'):
        out.append(f'\tNumRibbonEmitters {len(model["ribbonEmitters"])},')
    if model.get('eventObjects'):
        out.append(f'\tNumEvents {len(model["eventObjects"])},')
    out.append(f'\tBlendTime {model.get("blendTime", 150)},')
    extent = model.get('extent') or {}
    out.append(f'\tMinimumExtent {{ {_fmt_floats(extent.get("minimum") or [0, 0, 0])} }},')
    out.append(f'\tMaximumExtent {{ {_fmt_floats(extent.get("maximum") or [0, 0, 0])} }},')
    out.append(f'\tBoundsRadius { _fmt_num(extent.get("boundsRadius", 0.0)) },')
    out.append('}')

    sequences = model.get('sequences') or []
    if sequences:
        out.append(f'Sequences {len(sequences)} {{')
        for seq in sequences:
            out.extend(_write_sequence_lines(seq))
        out.append('}')

    global_seqs = model.get('globalSequences') or []
    if global_seqs:
        out.append(f'GlobalSequences {len(global_seqs)} {{')
        for duration in global_seqs:
            out.append(f'\tDuration {int(duration)},')
        out.append('}')

    textures = model.get('textures') or []
    if textures:
        out.append(f'Textures {len(textures)} {{')
        for texture in textures:
            out.append('\tBitmap {')
            out.append(f'\t\tImage "{_escape(texture.get("path", ""))}",')
            if texture.get('replaceableId'):
                out.append(f'\t\tReplaceableId {texture["replaceableId"]},')
            flags = texture.get('flags', 0)
            if flags & 1:
                out.append('\t\tWrapWidth,')
            if flags & 2:
                out.append('\t\tWrapHeight,')
            out.append('\t}')
        out.append('}')

    materials = model.get('materials') or []
    if materials:
        out.append(f'Materials {len(materials)} {{')
        for material in materials:
            out.extend(_write_material_lines(material))
        out.append('}')

    for geoset in model.get('geosets') or []:
        out.extend(_write_geoset_lines(geoset))
    for anim in model.get('geosetAnimations') or []:
        out.extend(_write_geoset_anim_lines(anim))
    for bone in model.get('bones') or []:
        out.extend(_write_bone_lines(bone))
    for light in model.get('lights') or []:
        out.extend(_write_light_lines(light))
    for helper in model.get('helpers') or []:
        out.extend(_write_helper_lines(helper))
    for attachment in model.get('attachments') or []:
        out.extend(_write_attachment_lines(attachment))

    pivots = model.get('pivotPoints') or []
    if pivots:
        out.append(f'PivotPoints {len(pivots)} {{')
        for pivot in pivots:
            out.append(f'\t{{ {_fmt_floats(pivot)} }},')
        out.append('}')

    for emitter in model.get('particleEmitters') or []:
        out.extend(_write_particle_emitter_lines(emitter))
    for emitter in model.get('particleEmitters2') or []:
        out.extend(_write_particle_emitter2_lines(emitter))
    for emitter in model.get('ribbonEmitters') or []:
        out.extend(_write_ribbon_emitter_lines(emitter))
    for event in model.get('eventObjects') or []:
        out.extend(_write_event_object_lines(event))
    for camera in model.get('cameras') or []:
        out.extend(_write_camera_lines(camera))
    for shape in model.get('collisionShapes') or []:
        out.extend(_write_collision_shape_lines(shape))

    return '\n'.join(out) + '\n'


def _escape(value):
    return (value or '').replace('\\', '\\\\').replace('"', '\\"')


def _fmt_num(value):
    if isinstance(value, float):
        text = f'{value:.7g}'
        return text
    return str(value)


def _fmt_floats(values):
    return ', '.join(_fmt_num(v) for v in values)


def _parse_number(token):
    if token.endswith(','):
        token = token[:-1]
    if '.' in token or 'e' in token.lower():
        return float(token)
    return int(token)


def _parse_string(token):
    if token.startswith('"') and token.endswith('"'):
        return token[1:-1].replace('\\\\', '\\')
    return token


def _skip_block(tokens):
    tokens.expect('{')
    depth = 1
    while depth:
        token = tokens.next()
        if token == '{':
            depth += 1
        elif token == '}':
            depth -= 1


def _read_vector(tokens):
    tokens.expect('{')
    values = []
    while True:
        token = tokens.next()
        if token == '}':
            break
        if token == ',':
            continue
        values.append(float(_parse_number(token)))
        if tokens.peek() == ',':
            tokens.next()
        if tokens.peek() == '}':
            tokens.next()
            break
    return values


def _parse_version(tokens, model):
    tokens.expect('Version')
    tokens.expect('{')
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'FormatVersion':
            model['version'] = int(_parse_number(tokens.next()))
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}', '{'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')


def _parse_model(tokens, model):
    tokens.expect('Model')
    if tokens.peek() and tokens.peek().startswith('"'):
        model['name'] = _parse_string(tokens.next())
    tokens.expect('{')
    while tokens.peek() != '}':
        key = tokens.next()
        if key in ('MinimumExtent', 'MaximumExtent'):
            vec = _read_vector(tokens)
            model.setdefault('extent', empty_model()['extent'])
            if key == 'MinimumExtent':
                model['extent']['minimum'] = vec
            else:
                model['extent']['maximum'] = vec
        elif key == 'BoundsRadius':
            model.setdefault('extent', empty_model()['extent'])
            model['extent']['boundsRadius'] = float(_parse_number(tokens.next()))
        elif key == 'BlendTime':
            model['blendTime'] = int(_parse_number(tokens.next()))
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')


def _parse_sequences(tokens):
    tokens.expect('Sequences')
    if tokens.peek() != '{':
        tokens.next()  # count
    tokens.expect('{')
    sequences = []
    while tokens.peek() != '}':
        tokens.expect('Anim')
        name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
        tokens.expect('{')
        seq = {
            'name': name,
            'interval': [0, 0],
            'moveSpeed': 0.0,
            'flags': 0,
            'rarity': 0.0,
            'syncPoint': 0,
            'extent': empty_model()['extent'].copy(),
        }
        seq['extent'] = {
            'boundsRadius': 0.0,
            'minimum': [0.0, 0.0, 0.0],
            'maximum': [0.0, 0.0, 0.0],
        }
        while tokens.peek() != '}':
            key = tokens.next()
            if key == 'Interval':
                seq['interval'] = [int(v) for v in _read_vector(tokens)]
            elif key == 'NonLooping':
                seq['flags'] = 1
            elif key == 'MoveSpeed':
                seq['moveSpeed'] = float(_parse_number(tokens.next()))
            elif key == 'Rarity':
                seq['rarity'] = float(_parse_number(tokens.next()))
            elif key == 'MinimumExtent':
                seq['extent']['minimum'] = _read_vector(tokens)
            elif key == 'MaximumExtent':
                seq['extent']['maximum'] = _read_vector(tokens)
            elif key == 'BoundsRadius':
                seq['extent']['boundsRadius'] = float(_parse_number(tokens.next()))
            elif tokens.peek() == '{':
                _skip_block(tokens)
            elif tokens.peek() not in (None, '}'):
                tokens.next()
            if tokens.peek() == ',':
                tokens.next()
        tokens.expect('}')
        sequences.append(seq)
    tokens.expect('}')
    return sequences


def _parse_global_sequences(tokens):
    tokens.expect('GlobalSequences')
    if tokens.peek() != '{':
        tokens.next()
    tokens.expect('{')
    values = []
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Duration':
            values.append(int(_parse_number(tokens.next())))
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return values


def _parse_textures(tokens):
    tokens.expect('Textures')
    if tokens.peek() != '{':
        tokens.next()
    tokens.expect('{')
    textures = []
    while tokens.peek() != '}':
        tokens.expect('Bitmap')
        tokens.expect('{')
        texture = {'replaceableId': 0, 'path': '', 'flags': 0}
        while tokens.peek() != '}':
            key = tokens.next()
            if key == 'Image':
                texture['path'] = _parse_string(tokens.next())
            elif key == 'ReplaceableId':
                texture['replaceableId'] = int(_parse_number(tokens.next()))
            elif key == 'WrapWidth':
                texture['flags'] |= 1
            elif key == 'WrapHeight':
                texture['flags'] |= 2
            elif tokens.peek() == '{':
                _skip_block(tokens)
            elif tokens.peek() not in (None, '}'):
                tokens.next()
            if tokens.peek() == ',':
                tokens.next()
        tokens.expect('}')
        textures.append(texture)
    tokens.expect('}')
    return textures


_FILTER_MODES = {
    'None': 0, 'Transparent': 1, 'Blend': 2, 'Additive': 3,
    'AddAlpha': 4, 'Modulate': 5, 'Modulate2x': 6,
}


def _parse_materials(tokens):
    tokens.expect('Materials')
    if tokens.peek() != '{':
        tokens.next()
    tokens.expect('{')
    materials = []
    while tokens.peek() != '}':
        tokens.expect('Material')
        tokens.expect('{')
        material = {'priorityPlane': 0, 'flags': 0, 'layers': []}
        while tokens.peek() != '}':
            key = tokens.next()
            if key == 'ConstantColor':
                material['flags'] |= 1
            elif key == 'SortPrimsFarZ':
                material['flags'] |= 16
            elif key == 'FullResolution':
                material['flags'] |= 32
            elif key == 'PriorityPlane':
                material['priorityPlane'] = int(_parse_number(tokens.next()))
            elif key == 'Layer':
                material['layers'].append(_parse_layer(tokens))
            elif tokens.peek() == '{':
                _skip_block(tokens)
            elif tokens.peek() not in (None, '}'):
                tokens.next()
            if tokens.peek() == ',':
                tokens.next()
        tokens.expect('}')
        materials.append(material)
    tokens.expect('}')
    return materials


def _parse_layer(tokens):
    # Caller already consumed 'Layer'
    tokens.expect('{')
    layer = {
        'filterMode': 0,
        'shadingFlags': 0,
        'textureId': 0,
        'textureAnimationId': 0xFFFFFFFF,
        'coordId': 0,
        'alpha': 1.0,
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'FilterMode':
            mode = tokens.next()
            layer['filterMode'] = _FILTER_MODES.get(mode, int(_parse_number(mode)) if _NUMBER.match(mode) else 0)
        elif key == 'Unshaded':
            layer['shadingFlags'] |= 1
        elif key == 'SphereEnvMap':
            layer['shadingFlags'] |= 2
        elif key == 'TwoSided':
            layer['shadingFlags'] |= 16
        elif key == 'Unfogged':
            layer['shadingFlags'] |= 32
        elif key == 'NoDepthTest':
            layer['shadingFlags'] |= 64
        elif key == 'NoDepthSet':
            layer['shadingFlags'] |= 128
        elif key == 'static':
            field = tokens.next()
            if field == 'TextureID':
                layer['textureId'] = int(_parse_number(tokens.next()))
            elif field == 'Alpha':
                layer['alpha'] = float(_parse_number(tokens.next()))
            else:
                if tokens.peek() not in (None, '}'):
                    tokens.next()
        elif key == 'Alpha':
            if _is_animated_track(tokens):
                tokens.next()  # keyframe count
                layer['tracks']['KMTA'] = _parse_track_block(tokens, 'f32')
            else:
                layer['alpha'] = float(_parse_number(tokens.next()))
        elif key == 'TextureID':
            if _is_animated_track(tokens):
                tokens.next()
                layer['tracks']['KMTF'] = _parse_track_block(tokens, 'u32')
            else:
                layer['textureId'] = int(_parse_number(tokens.next()))
        elif key == 'TVertexAnimId':
            layer['textureAnimationId'] = int(_parse_number(tokens.next()))
        elif key == 'CoordId':
            layer['coordId'] = int(_parse_number(tokens.next()))
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return layer


def _is_animated_track(tokens):
    """True when next tokens look like ``N { ... }`` rather than a static value."""
    peek = tokens.peek()
    if not peek or not (peek[0].isdigit() or peek[0] in '+-'):
        return False
    # Look ahead one more token without consuming permanently.
    if tokens._i + 1 >= len(tokens._tokens):
        return False
    nxt = tokens._tokens[tokens._i + 1]
    return nxt == '{' or nxt in ('DontInterp', 'Linear', 'Hermite', 'Bezier', 'GlobalSeqId')


def _parse_track_block(tokens, kind):
    # Track header may place flags before '{' (rare) or inside the block (Art Tools).
    interpolation = 0
    global_seq = 0xFFFFFFFF
    while tokens.peek() not in (None, '{'):
        flag = tokens.next()
        if flag == 'DontInterp':
            interpolation = 0
        elif flag == 'Linear':
            interpolation = 1
        elif flag == 'Hermite':
            interpolation = 2
        elif flag == 'Bezier':
            interpolation = 3
        elif flag == 'GlobalSeqId':
            global_seq = int(_parse_number(tokens.next()))
    tokens.expect('{')
    keys = []
    while tokens.peek() != '}':
        token = tokens.next()
        if token in ('DontInterp', 'Linear', 'Hermite', 'Bezier'):
            interpolation = {
                'DontInterp': 0, 'Linear': 1, 'Hermite': 2, 'Bezier': 3,
            }[token]
            if tokens.peek() == ',':
                tokens.next()
            continue
        if token == 'GlobalSeqId':
            global_seq = int(_parse_number(tokens.next()))
            if tokens.peek() == ',':
                tokens.next()
            continue

        if token.endswith(':'):
            frame = int(token[:-1])
        else:
            frame = int(_parse_number(token))
            if tokens.peek() == ':':
                tokens.next()
        value = _parse_track_value(tokens, kind)
        key = {'frame': frame, 'value': value}
        # Art Tools writes:  frame: value, InTan ..., OutTan ...
        if tokens.peek() == ',':
            tokens.next()
        while tokens.peek() in ('InTan', 'OutTan'):
            tan_key = tokens.next()
            tan_val = _parse_track_value(tokens, kind)
            if tan_key == 'InTan':
                key['inTan'] = tan_val
            else:
                key['outTan'] = tan_val
            if tokens.peek() == ',':
                tokens.next()
        keys.append(key)
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    if any('inTan' in k for k in keys) and interpolation <= 1:
        interpolation = 2
    return {
        'interpolationType': interpolation,
        'globalSequenceId': global_seq,
        'keys': keys,
    }


def _parse_track_value(tokens, kind):
    if kind == 'u32':
        return int(_parse_number(tokens.next()))
    if kind == 'f32':
        return float(_parse_number(tokens.next()))
    if kind in ('f32x3', 'f32x4'):
        return _read_vector(tokens)
    raise ValueError(kind)


def _parse_texture_anims(tokens):
    tokens.expect('TextureAnims')
    if tokens.peek() != '{':
        tokens.next()
    tokens.expect('{')
    anims = []
    while tokens.peek() != '}':
        tokens.expect('TVertexAnim')
        tokens.expect('{')
        anim = {'tracks': {}}
        while tokens.peek() != '}':
            key = tokens.next()
            if key in ('Translation', 'Rotation', 'Scaling'):
                if tokens.peek() and tokens.peek()[0].isdigit():
                    tokens.next()
                tag = {'Translation': 'KTAT', 'Rotation': 'KTAR', 'Scaling': 'KTAS'}[key]
                kind = 'f32x4' if key == 'Rotation' else 'f32x3'
                anim['tracks'][tag] = _parse_track_block(tokens, kind)
            elif tokens.peek() == '{':
                _skip_block(tokens)
            if tokens.peek() == ',':
                tokens.next()
        tokens.expect('}')
        anims.append(anim)
    tokens.expect('}')
    return anims


def _parse_float_list_block(tokens):
    tokens.expect('{')
    values = []
    while tokens.peek() != '}':
        token = tokens.next()
        if token in ('{', '}'):
            continue
        if token == ',':
            continue
        # vectors written as { a, b, c },
        if token.startswith('{') or False:
            pass
        values.append(float(_parse_number(token)))
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return values


def _parse_vector_list_block(tokens, width):
    tokens.expect('{')
    values = []
    while tokens.peek() != '}':
        if tokens.peek() == '{':
            values.extend(_read_vector(tokens))
        else:
            # flat list
            values.append(float(_parse_number(tokens.next())))
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    # If flattened incorrectly, still return what we have.
    return values


def _parse_geoset(tokens):
    tokens.expect('Geoset')
    tokens.expect('{')
    geoset = {
        'vertices': [],
        'normals': [],
        'faceTypeGroups': [4],
        'faceGroups': [],
        'faces': [],
        'vertexGroups': [],
        'matrixGroups': [],
        'matrixIndices': [],
        'materialId': 0,
        'selectionGroup': 0,
        'selectionFlags': 0,
        'extent': empty_model()['extent'].copy(),
        'sequenceExtents': [],
        'uvSets': [],
    }
    geoset['extent'] = {
        'boundsRadius': 0.0,
        'minimum': [0.0, 0.0, 0.0],
        'maximum': [0.0, 0.0, 0.0],
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Vertices':
            tokens.next()  # count
            geoset['vertices'] = _parse_vector_list_block(tokens, 3)
        elif key == 'Normals':
            tokens.next()
            geoset['normals'] = _parse_vector_list_block(tokens, 3)
        elif key == 'TVertices':
            tokens.next()
            geoset['uvSets'].append(_parse_vector_list_block(tokens, 2))
        elif key == 'VertexGroup':
            geoset['vertexGroups'] = [int(v) for v in _parse_float_list_block(tokens)]
        elif key == 'Faces':
            tokens.next()  # groups
            tokens.next()  # indexes count sometimes "Faces 1 N"
            # Art Tools: Faces <groups> <count> { Triangles { ... } Triangles { ... } ... }
            # A geoset can have more than one Triangles sub-block (LOD/multi-material
            # splits) - each is its own face group, accumulated in order rather than
            # overwriting, so a real multi-group file doesn't silently lose every group
            # but the last one.
            tokens.expect('{')
            all_faces = []
            face_groups = []
            face_type_groups = []
            while tokens.peek() != '}':
                face_key = tokens.next()
                if face_key == 'Triangles':
                    tokens.expect('{')
                    faces = []
                    while tokens.peek() != '}':
                        if tokens.peek() == '{':
                            faces.extend(int(v) for v in _read_vector(tokens))
                        else:
                            faces.append(int(_parse_number(tokens.next())))
                        if tokens.peek() == ',':
                            tokens.next()
                    tokens.expect('}')
                    all_faces.extend(faces)
                    face_groups.append(len(faces))
                    face_type_groups.append(4)
                elif tokens.peek() == '{':
                    _skip_block(tokens)
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
            geoset['faces'] = all_faces
            geoset['faceGroups'] = face_groups
            geoset['faceTypeGroups'] = face_type_groups
        elif key == 'Groups':
            # Groups <matrixGroups> <matrixIndices> { Matrices { ... } ... }
            tokens.next()
            tokens.next()
            tokens.expect('{')
            matrix_groups = []
            matrix_indices = []
            while tokens.peek() != '}':
                gkey = tokens.next()
                if gkey == 'Matrices':
                    mats = [int(v) for v in _read_vector(tokens)]
                    matrix_groups.append(len(mats))
                    matrix_indices.extend(mats)
                elif tokens.peek() == '{':
                    _skip_block(tokens)
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
            geoset['matrixGroups'] = matrix_groups
            geoset['matrixIndices'] = matrix_indices
        elif key == 'MaterialID':
            geoset['materialId'] = int(_parse_number(tokens.next()))
        elif key == 'SelectionGroup':
            geoset['selectionGroup'] = int(_parse_number(tokens.next()))
        elif key == 'Unselectable':
            geoset['selectionFlags'] |= 4
        elif key == 'MinimumExtent':
            geoset['extent']['minimum'] = _read_vector(tokens)
        elif key == 'MaximumExtent':
            geoset['extent']['maximum'] = _read_vector(tokens)
        elif key == 'BoundsRadius':
            geoset['extent']['boundsRadius'] = float(_parse_number(tokens.next()))
        elif key == 'Anim':
            tokens.expect('{')
            extent = {
                'boundsRadius': 0.0,
                'minimum': [0.0, 0.0, 0.0],
                'maximum': [0.0, 0.0, 0.0],
            }
            while tokens.peek() != '}':
                akey = tokens.next()
                if akey == 'MinimumExtent':
                    extent['minimum'] = _read_vector(tokens)
                elif akey == 'MaximumExtent':
                    extent['maximum'] = _read_vector(tokens)
                elif akey == 'BoundsRadius':
                    extent['boundsRadius'] = float(_parse_number(tokens.next()))
                elif tokens.peek() == '{':
                    _skip_block(tokens)
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
            geoset['sequenceExtents'].append(extent)
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return geoset


def _parse_geoset_anim(tokens):
    tokens.expect('GeosetAnim')
    tokens.expect('{')
    anim = {
        'alpha': 1.0,
        'flags': 0,
        'color': [1.0, 1.0, 1.0],
        'geosetId': 0,
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        static = False
        if key == 'static':
            static = True
            key = tokens.next()
        if key == 'Alpha':
            if (not static) and _is_animated_track(tokens):
                tokens.next()  # keyframe count
                anim['tracks']['KGAO'] = _parse_track_block(tokens, 'f32')
            else:
                anim['alpha'] = float(_parse_number(tokens.next()))
        elif key == 'Color':
            if (not static) and _is_animated_track(tokens):
                tokens.next()
                anim['tracks']['KGAC'] = _parse_track_block(tokens, 'f32x3')
            else:
                anim['color'] = _read_vector(tokens)
                anim['flags'] |= 1
        elif key == 'GeosetId':
            anim['geosetId'] = int(_parse_number(tokens.next()))
        elif key == 'DropShadow':
            anim['flags'] |= 1
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return anim


def _parse_node_body(tokens, node):
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'ObjectId':
            node['objectId'] = int(_parse_number(tokens.next()))
        elif key == 'Parent':
            node['parentId'] = int(_parse_number(tokens.next()))
        elif key == 'Billboarded':
            node['flags'] |= 0x8
        elif key == 'BillboardedLockX':
            node['flags'] |= 0x10
        elif key == 'BillboardedLockY':
            node['flags'] |= 0x20
        elif key == 'BillboardedLockZ':
            node['flags'] |= 0x40
        elif key == 'CameraAnchored':
            node['flags'] |= 0x80
        elif key == 'DontInherit':
            tokens.expect('{')
            while tokens.peek() != '}':
                flag = tokens.next()
                if flag == 'Translation':
                    node['flags'] |= 0x1
                elif flag == 'Rotation':
                    node['flags'] |= 0x2
                elif flag == 'Scaling':
                    node['flags'] |= 0x4
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
        elif key == 'Translation':
            tokens.next()
            node.setdefault('tracks', {})['KGTR'] = _parse_track_block(tokens, 'f32x3')
        elif key == 'Rotation':
            tokens.next()
            node.setdefault('tracks', {})['KGRT'] = _parse_track_block(tokens, 'f32x4')
        elif key == 'Scaling':
            tokens.next()
            node.setdefault('tracks', {})['KGSC'] = _parse_track_block(tokens, 'f32x3')
        elif key == 'Visibility':
            # Handled by callers that care; skip animated visibility blocks here.
            if tokens.peek() and tokens.peek()[0].isdigit():
                tokens.next()
                _skip_block(tokens)
            else:
                tokens.next()
        elif tokens.peek() == '{':
            return key  # let caller handle typed fields
        elif tokens.peek() not in (None, '}'):
            # typed field without block - return to caller
            return key
        if tokens.peek() == ',':
            tokens.next()
    return None


def _new_node(name='', flags=0):
    return {
        'name': name,
        'objectId': 0,
        'parentId': 0xFFFFFFFF,
        'flags': flags,
        'tracks': {},
    }


def _parse_bone(tokens):
    tokens.expect('Bone')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    bone = {
        'node': _new_node(name, 0x100),
        'geosetId': 0xFFFFFFFF,
        'geosetAnimationId': 0xFFFFFFFF,
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'GeosetId':
            val = tokens.next()
            bone['geosetId'] = 0xFFFFFFFF if val == 'Multiple' else int(_parse_number(val))
        elif key == 'GeosetAnimId':
            val = tokens.next()
            bone['geosetAnimationId'] = 0xFFFFFFFF if val == 'None' else int(_parse_number(val))
        else:
            # reprocess as node field
            tokens._i -= 1
            leftover = _parse_node_body(tokens, bone['node'])
            if leftover:
                if leftover == 'GeosetId':
                    val = tokens.next()
                    bone['geosetId'] = 0xFFFFFFFF if val == 'Multiple' else int(_parse_number(val))
                elif leftover == 'GeosetAnimId':
                    val = tokens.next()
                    bone['geosetAnimationId'] = 0xFFFFFFFF if val == 'None' else int(_parse_number(val))
                elif tokens.peek() == '{':
                    _skip_block(tokens)
                elif tokens.peek() not in (None, '}'):
                    tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return bone


def _parse_helper(tokens):
    tokens.expect('Helper')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    helper = {'node': _new_node(name, 0)}
    while tokens.peek() != '}':
        leftover = _parse_node_body(tokens, helper['node'])
        if leftover:
            if tokens.peek() == '{':
                _skip_block(tokens)
            elif tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return helper


def _parse_light(tokens):
    tokens.expect('Light')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    light = {
        'node': _new_node(name, 0x200),
        'type': 0,
        'attenuationStart': 0.0,
        'attenuationEnd': 0.0,
        'color': [1.0, 1.0, 1.0],
        'intensity': 0.0,
        'ambientColor': [0.0, 0.0, 0.0],
        'ambientIntensity': 0.0,
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Omnidirectional':
            light['type'] = 0
        elif key == 'Directional':
            light['type'] = 1
        elif key == 'Ambient':
            light['type'] = 2
        elif key == 'AttenuationStart':
            light['attenuationStart'] = float(_parse_number(tokens.next()))
        elif key == 'AttenuationEnd':
            light['attenuationEnd'] = float(_parse_number(tokens.next()))
        elif key == 'Intensity':
            light['intensity'] = float(_parse_number(tokens.next()))
        elif key == 'Color':
            light['color'] = _read_vector(tokens)
        elif key == 'AmbIntensity':
            light['ambientIntensity'] = float(_parse_number(tokens.next()))
        elif key == 'AmbColor':
            light['ambientColor'] = _read_vector(tokens)
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, light['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return light


def _parse_attachment(tokens):
    tokens.expect('Attachment')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    attachment = {
        'node': _new_node(name, 0x800),
        'path': '',
        'attachmentId': 0,
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Path':
            attachment['path'] = _parse_string(tokens.next())
        elif key == 'AttachmentID':
            attachment['attachmentId'] = int(_parse_number(tokens.next()))
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, attachment['node'])
            if leftover == 'Visibility' and tokens.peek() and tokens.peek()[0].isdigit():
                tokens.next()
                attachment['tracks']['KATV'] = _parse_track_block(tokens, 'f32')
            elif leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return attachment


def _parse_pivot_points(tokens):
    tokens.expect('PivotPoints')
    if tokens.peek() != '{':
        tokens.next()
    tokens.expect('{')
    points = []
    while tokens.peek() != '}':
        points.append(_read_vector(tokens))
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return points


def _parse_particle_emitter(tokens):
    tokens.expect('ParticleEmitter')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    emitter = {
        'node': _new_node(name, 0x1000),
        'emissionRate': 0.0,
        'gravity': 0.0,
        'longitude': 0.0,
        'latitude': 0.0,
        'spawnModelFileName': '',
        'lifespan': 0.0,
        'initialVelocity': 0.0,
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'EmitterUsesMDL':
            emitter['node']['flags'] |= 0x8000
        elif key == 'EmitterUsesTGA':
            emitter['node']['flags'] |= 0x10000
        elif key == 'static':
            field = tokens.next()
            value = tokens.next()
            mapping = {
                'EmissionRate': 'emissionRate',
                'Gravity': 'gravity',
                'Longitude': 'longitude',
                'Latitude': 'latitude',
                'LifeSpan': 'lifespan',
                'InitVelocity': 'initialVelocity',
            }
            if field in mapping:
                emitter[mapping[field]] = float(_parse_number(value))
            elif field == 'Path':
                emitter['spawnModelFileName'] = _parse_string(value)
        elif key == 'Path':
            emitter['spawnModelFileName'] = _parse_string(tokens.next())
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, emitter['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return emitter


def _parse_particle_emitter2(tokens):
    tokens.expect('ParticleEmitter2')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    emitter = {
        'node': _new_node(name, 0x1000),
        'speed': 0.0, 'variation': 0.0, 'latitude': 0.0, 'gravity': 0.0,
        'lifespan': 0.0, 'emissionRate': 0.0, 'length': 0.0, 'width': 0.0,
        'filterMode': 0, 'rows': 0, 'columns': 0, 'headOrTail': 0,
        'tailLength': 0.0, 'time': 0.0,
        'segmentColor': [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
        'segmentAlpha': [255, 255, 255],
        'segmentScaling': [1.0, 1.0, 1.0],
        'headInterval': [0, 0, 0],
        'headDecayInterval': [0, 0, 0],
        'tailInterval': [0, 0, 0],
        'tailDecayInterval': [0, 0, 0],
        'textureId': 0, 'squirt': 0, 'priorityPlane': 0, 'replaceableId': 0,
        'tracks': {},
    }
    # Keep parser resilient: consume node fields and skip complex nested animation blocks.
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'static':
            field = tokens.next()
            if field in ('Speed', 'Variation', 'Latitude', 'Gravity', 'LifeSpan',
                         'EmissionRate', 'Length', 'Width', 'TailLength', 'Time'):
                mapping = {
                    'Speed': 'speed', 'Variation': 'variation', 'Latitude': 'latitude',
                    'Gravity': 'gravity', 'LifeSpan': 'lifespan', 'EmissionRate': 'emissionRate',
                    'Length': 'length', 'Width': 'width', 'TailLength': 'tailLength', 'Time': 'time',
                }
                emitter[mapping[field]] = float(_parse_number(tokens.next()))
            elif field == 'TextureID':
                emitter['textureId'] = int(_parse_number(tokens.next()))
            else:
                if tokens.peek() == '{':
                    _skip_block(tokens)
                elif tokens.peek() not in (None, '}'):
                    tokens.next()
        elif key == 'Rows':
            emitter['rows'] = int(_parse_number(tokens.next()))
        elif key == 'Columns':
            emitter['columns'] = int(_parse_number(tokens.next()))
        elif key == 'PriorityPlane':
            emitter['priorityPlane'] = int(_parse_number(tokens.next()))
        elif key == 'ReplaceableId':
            emitter['replaceableId'] = int(_parse_number(tokens.next()))
        elif key == 'Squirt':
            emitter['squirt'] = 1
        elif key == 'Head':
            emitter['headOrTail'] = 0
        elif key == 'Tail':
            emitter['headOrTail'] = 1
        elif key == 'Both':
            emitter['headOrTail'] = 2
        elif key == 'Blend':
            emitter['filterMode'] = 0
        elif key == 'Additive':
            emitter['filterMode'] = 1
        elif key == 'Modulate':
            emitter['filterMode'] = 2
        elif key == 'Modulate2x':
            emitter['filterMode'] = 3
        elif key == 'AlphaKey':
            emitter['filterMode'] = 4
        elif key == 'SegmentColor':
            tokens.expect('{')
            colors = []
            while tokens.peek() != '}':
                if tokens.next() == 'Color':
                    colors.append(_read_vector(tokens))
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
            if colors:
                emitter['segmentColor'] = colors
        elif key == 'Alpha':
            emitter['segmentAlpha'] = [int(v) for v in _read_vector(tokens)]
        elif key == 'ParticleScaling':
            emitter['segmentScaling'] = _read_vector(tokens)
        elif key == 'LifeSpanUVAnim':
            emitter['headInterval'] = [int(v) for v in _read_vector(tokens)]
        elif key == 'DecayUVAnim':
            emitter['headDecayInterval'] = [int(v) for v in _read_vector(tokens)]
        elif key == 'TailUVAnim':
            emitter['tailInterval'] = [int(v) for v in _read_vector(tokens)]
        elif key == 'TailDecayUVAnim':
            emitter['tailDecayInterval'] = [int(v) for v in _read_vector(tokens)]
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, emitter['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return emitter


def _parse_ribbon_emitter(tokens):
    tokens.expect('RibbonEmitter')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    emitter = {
        'node': _new_node(name, 0x4000),
        'heightAbove': 0.0, 'heightBelow': 0.0, 'alpha': 1.0,
        'color': [1.0, 1.0, 1.0], 'lifespan': 0.0, 'textureSlot': 0,
        'emissionRate': 0, 'rows': 0, 'columns': 0, 'materialId': 0,
        'gravity': 0.0, 'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'static':
            field = tokens.next()
            mapping = {
                'HeightAbove': 'heightAbove', 'HeightBelow': 'heightBelow',
                'Alpha': 'alpha', 'LifeSpan': 'lifespan', 'Gravity': 'gravity',
            }
            if field in mapping:
                emitter[mapping[field]] = float(_parse_number(tokens.next()))
            elif field == 'Color':
                emitter['color'] = _read_vector(tokens)
            elif field == 'TextureSlot':
                emitter['textureSlot'] = int(_parse_number(tokens.next()))
            else:
                if tokens.peek() == '{':
                    _skip_block(tokens)
                else:
                    tokens.next()
        elif key == 'EmissionRate':
            emitter['emissionRate'] = int(_parse_number(tokens.next()))
        elif key == 'Rows':
            emitter['rows'] = int(_parse_number(tokens.next()))
        elif key == 'Columns':
            emitter['columns'] = int(_parse_number(tokens.next()))
        elif key == 'MaterialID':
            emitter['materialId'] = int(_parse_number(tokens.next()))
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, emitter['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return emitter


def _parse_event_object(tokens):
    tokens.expect('EventObject')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    event = {'node': _new_node(name, 0x400), 'globalSequenceId': 0xFFFFFFFF, 'tracks': []}
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'EventTrack':
            tokens.next()  # count
            tokens.expect('{')
            tracks = []
            while tokens.peek() != '}':
                tracks.append(int(_parse_number(tokens.next())))
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
            event['tracks'] = tracks
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, event['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return event


def _parse_camera(tokens):
    tokens.expect('Camera')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    camera = {
        'name': name,
        'position': [0.0, 0.0, 0.0],
        'fieldOfView': 0.0,
        'farClippingPlane': 0.0,
        'nearClippingPlane': 0.0,
        'targetPosition': [0.0, 0.0, 0.0],
        'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Position':
            camera['position'] = _read_vector(tokens)
        elif key == 'FieldOfView':
            camera['fieldOfView'] = float(_parse_number(tokens.next()))
        elif key == 'FarClip':
            camera['farClippingPlane'] = float(_parse_number(tokens.next()))
        elif key == 'NearClip':
            camera['nearClippingPlane'] = float(_parse_number(tokens.next()))
        elif key == 'Target':
            tokens.expect('{')
            while tokens.peek() != '}':
                tkey = tokens.next()
                if tkey == 'Position':
                    camera['targetPosition'] = _read_vector(tokens)
                elif tokens.peek() == '{':
                    _skip_block(tokens)
                elif tokens.peek() not in (None, '}'):
                    tokens.next()
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return camera


def _parse_collision_shape(tokens):
    tokens.expect('CollisionShape')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    shape = {
        'node': _new_node(name, 0x2000),
        'type': 0,
        'vertices': [],
        'radius': 0.0,
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Box':
            shape['type'] = 0
        elif key == 'Plane':
            shape['type'] = 1
        elif key == 'Sphere':
            shape['type'] = 2
        elif key == 'Cylinder':
            shape['type'] = 3
        elif key == 'Vertices':
            tokens.next()
            shape['vertices'] = []
            tokens.expect('{')
            while tokens.peek() != '}':
                shape['vertices'].append(_read_vector(tokens))
                if tokens.peek() == ',':
                    tokens.next()
            tokens.expect('}')
        elif key == 'BoundsRadius':
            shape['radius'] = float(_parse_number(tokens.next()))
        else:
            tokens._i -= 1
            leftover = _parse_node_body(tokens, shape['node'])
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return shape


def _write_sequence_lines(seq):
    lines = [f'\tAnim "{_escape(seq.get("name", ""))}" {{']
    interval = seq.get('interval') or [0, 0]
    lines.append(f'\t\tInterval {{ {int(interval[0])}, {int(interval[1])} }},')
    if seq.get('flags', 0) & 1:
        lines.append('\t\tNonLooping,')
    if seq.get('moveSpeed'):
        lines.append(f'\t\tMoveSpeed {_fmt_num(seq["moveSpeed"])},')
    if seq.get('rarity'):
        lines.append(f'\t\tRarity {_fmt_num(seq["rarity"])},')
    extent = seq.get('extent') or {}
    lines.append(f'\t\tMinimumExtent {{ {_fmt_floats(extent.get("minimum") or [0, 0, 0])} }},')
    lines.append(f'\t\tMaximumExtent {{ {_fmt_floats(extent.get("maximum") or [0, 0, 0])} }},')
    lines.append(f'\t\tBoundsRadius {_fmt_num(extent.get("boundsRadius", 0.0))},')
    lines.append('\t}')
    return lines


_FILTER_MODE_NAMES = ['None', 'Transparent', 'Blend', 'Additive', 'AddAlpha', 'Modulate', 'Modulate2x']


def _write_material_lines(material):
    lines = ['\tMaterial {']
    flags = material.get('flags', 0)
    if flags & 1:
        lines.append('\t\tConstantColor,')
    if flags & 16:
        lines.append('\t\tSortPrimsFarZ,')
    if flags & 32:
        lines.append('\t\tFullResolution,')
    if material.get('priorityPlane'):
        lines.append(f'\t\tPriorityPlane {material["priorityPlane"]},')
    for layer in material.get('layers') or []:
        lines.append('\t\tLayer {')
        mode = layer.get('filterMode', 0)
        mode_name = _FILTER_MODE_NAMES[mode] if 0 <= mode < len(_FILTER_MODE_NAMES) else 'None'
        lines.append(f'\t\t\tFilterMode {mode_name},')
        shading = layer.get('shadingFlags', 0)
        if shading & 1:
            lines.append('\t\t\tUnshaded,')
        if shading & 2:
            lines.append('\t\t\tSphereEnvMap,')
        if shading & 16:
            lines.append('\t\t\tTwoSided,')
        if shading & 32:
            lines.append('\t\t\tUnfogged,')
        if shading & 64:
            lines.append('\t\t\tNoDepthTest,')
        if shading & 128:
            lines.append('\t\t\tNoDepthSet,')
        tracks = layer.get('tracks') or {}
        if 'KMTF' in tracks:
            lines.extend(_write_track_lines('TextureID', tracks['KMTF'], 'u32', indent=3))
        else:
            lines.append(f'\t\t\tstatic TextureID {layer.get("textureId", 0)},')
        if layer.get('textureAnimationId', 0xFFFFFFFF) != 0xFFFFFFFF:
            lines.append(f'\t\t\tTVertexAnimId {layer["textureAnimationId"]},')
        if layer.get('coordId'):
            lines.append(f'\t\t\tCoordId {layer["coordId"]},')
        if 'KMTA' in tracks:
            lines.extend(_write_track_lines('Alpha', tracks['KMTA'], 'f32', indent=3))
        else:
            lines.append(f'\t\t\tstatic Alpha {_fmt_num(layer.get("alpha", 1.0))},')
        lines.append('\t\t}')
    lines.append('\t}')
    return lines


def _write_track_lines(name, track, kind, indent=1):
    keys = track.get('keys') or []
    pad = '\t' * indent
    interp = track.get('interpolationType', 0)
    interp_name = {0: 'DontInterp', 1: 'Linear', 2: 'Hermite', 3: 'Bezier'}.get(interp, 'Linear')
    lines = [f'{pad}{name} {len(keys)} {{', f'{pad}\t{interp_name},']
    if track.get('globalSequenceId', 0xFFFFFFFF) != 0xFFFFFFFF:
        lines.append(f'{pad}\tGlobalSeqId {track["globalSequenceId"]},')
    for key in keys:
        value = key['value']
        if kind in ('f32x3', 'f32x4'):
            value_text = f'{{ {_fmt_floats(value)} }}'
        else:
            value_text = _fmt_num(value)
        lines.append(f'{pad}\t{key["frame"]}: {value_text},')
        if 'inTan' in key:
            if kind in ('f32x3', 'f32x4'):
                lines.append(f'{pad}\t\tInTan {{ {_fmt_floats(key["inTan"])} }},')
                lines.append(f'{pad}\t\tOutTan {{ {_fmt_floats(key["outTan"])} }},')
            else:
                lines.append(f'{pad}\t\tInTan {_fmt_num(key["inTan"])},')
                lines.append(f'{pad}\t\tOutTan {_fmt_num(key["outTan"])},')
    lines.append(f'{pad}}}')
    return lines


def _write_geoset_lines(geoset):
    lines = ['Geoset {']
    vertices = geoset.get('vertices') or []
    lines.append(f'\tVertices {len(vertices) // 3} {{')
    for i in range(0, len(vertices), 3):
        lines.append(f'\t\t{{ {_fmt_floats(vertices[i:i+3])} }},')
    lines.append('\t}')
    normals = geoset.get('normals') or []
    lines.append(f'\tNormals {len(normals) // 3} {{')
    for i in range(0, len(normals), 3):
        lines.append(f'\t\t{{ {_fmt_floats(normals[i:i+3])} }},')
    lines.append('\t}')
    for uv in geoset.get('uvSets') or []:
        lines.append(f'\tTVertices {len(uv) // 2} {{')
        for i in range(0, len(uv), 2):
            lines.append(f'\t\t{{ {_fmt_floats(uv[i:i+2])} }},')
        lines.append('\t}')
    vertex_groups = geoset.get('vertexGroups') or []
    lines.append('\tVertexGroup {')
    for group in vertex_groups:
        lines.append(f'\t\t{int(group)},')
    lines.append('\t}')
    faces = geoset.get('faces') or []
    face_groups = geoset.get('faceGroups') or ([len(faces)] if faces else [])
    lines.append(f'\tFaces {len(face_groups)} {len(faces)} {{')
    offset = 0
    for count in face_groups:
        chunk = faces[offset:offset + count]
        offset += count
        lines.append('\t\tTriangles {')
        lines.append(f'\t\t\t{{ {", ".join(str(int(f)) for f in chunk)} }},')
        lines.append('\t\t}')
    lines.append('\t}')
    matrix_groups = geoset.get('matrixGroups') or []
    matrix_indices = geoset.get('matrixIndices') or []
    lines.append(f'\tGroups {len(matrix_groups)} {len(matrix_indices)} {{')
    offset = 0
    for count in matrix_groups:
        chunk = matrix_indices[offset:offset + count]
        offset += count
        lines.append(f'\t\tMatrices {{ {", ".join(str(int(v)) for v in chunk)} }},')
    lines.append('\t}')
    extent = geoset.get('extent') or {}
    lines.append(f'\tMinimumExtent {{ {_fmt_floats(extent.get("minimum") or [0, 0, 0])} }},')
    lines.append(f'\tMaximumExtent {{ {_fmt_floats(extent.get("maximum") or [0, 0, 0])} }},')
    lines.append(f'\tBoundsRadius {_fmt_num(extent.get("boundsRadius", 0.0))},')
    for anim_extent in geoset.get('sequenceExtents') or []:
        lines.append('\tAnim {')
        lines.append(f'\t\tMinimumExtent {{ {_fmt_floats(anim_extent.get("minimum") or [0, 0, 0])} }},')
        lines.append(f'\t\tMaximumExtent {{ {_fmt_floats(anim_extent.get("maximum") or [0, 0, 0])} }},')
        lines.append(f'\t\tBoundsRadius {_fmt_num(anim_extent.get("boundsRadius", 0.0))},')
        lines.append('\t}')
    lines.append(f'\tMaterialID {geoset.get("materialId", 0)},')
    lines.append(f'\tSelectionGroup {geoset.get("selectionGroup", 0)},')
    lines.append('}')
    return lines


def _write_geoset_anim_lines(anim):
    lines = ['GeosetAnim {']
    tracks = anim.get('tracks') or {}
    if 'KGAO' in tracks:
        lines.extend(_write_track_lines('Alpha', tracks['KGAO'], 'f32'))
    else:
        lines.append(f'\tstatic Alpha {_fmt_num(anim.get("alpha", 1.0))},')
    if 'KGAC' in tracks:
        lines.extend(_write_track_lines('Color', tracks['KGAC'], 'f32x3'))
    elif anim.get('flags', 0) & 1:
        lines.append(f'\tstatic Color {{ {_fmt_floats(anim.get("color") or [1, 1, 1])} }},')
    lines.append(f'\tGeosetId {anim.get("geosetId", 0)},')
    lines.append('}')
    return lines


def _write_node_lines(node, indent=1):
    pad = '\t' * indent
    lines = []
    lines.append(f'{pad}ObjectId {node.get("objectId", 0)},')
    parent = node.get('parentId', 0xFFFFFFFF)
    if parent != 0xFFFFFFFF:
        lines.append(f'{pad}Parent {parent},')
    flags = node.get('flags', 0)
    inherit = []
    if flags & 1:
        inherit.append('Translation')
    if flags & 2:
        inherit.append('Rotation')
    if flags & 4:
        inherit.append('Scaling')
    if inherit:
        lines.append(f'{pad}DontInherit {{ {", ".join(inherit)} }},')
    if flags & 0x8:
        lines.append(f'{pad}Billboarded,')
    if flags & 0x10:
        lines.append(f'{pad}BillboardedLockX,')
    if flags & 0x20:
        lines.append(f'{pad}BillboardedLockY,')
    if flags & 0x40:
        lines.append(f'{pad}BillboardedLockZ,')
    if flags & 0x80:
        lines.append(f'{pad}CameraAnchored,')
    tracks = node.get('tracks') or {}
    if 'KGTR' in tracks:
        lines.extend(_write_track_lines('Translation', tracks['KGTR'], 'f32x3', indent=indent))
    if 'KGRT' in tracks:
        lines.extend(_write_track_lines('Rotation', tracks['KGRT'], 'f32x4', indent=indent))
    if 'KGSC' in tracks:
        lines.extend(_write_track_lines('Scaling', tracks['KGSC'], 'f32x3', indent=indent))
    return lines


def _write_bone_lines(bone):
    node = bone.get('node') or {}
    lines = [f'Bone "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    geoset_id = bone.get('geosetId', 0xFFFFFFFF)
    lines.append('\tGeosetId Multiple,' if geoset_id == 0xFFFFFFFF else f'\tGeosetId {geoset_id},')
    anim_id = bone.get('geosetAnimationId', 0xFFFFFFFF)
    lines.append('\tGeosetAnimId None,' if anim_id == 0xFFFFFFFF else f'\tGeosetAnimId {anim_id},')
    lines.append('}')
    return lines


def _write_helper_lines(helper):
    node = helper.get('node') or {}
    lines = [f'Helper "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    lines.append('}')
    return lines


def _write_light_lines(light):
    node = light.get('node') or {}
    lines = [f'Light "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    type_name = {0: 'Omnidirectional', 1: 'Directional', 2: 'Ambient'}.get(light.get('type', 0), 'Omnidirectional')
    lines.append(f'\t{type_name},')
    lines.append(f'\tAttenuationStart {_fmt_num(light.get("attenuationStart", 0.0))},')
    lines.append(f'\tAttenuationEnd {_fmt_num(light.get("attenuationEnd", 0.0))},')
    lines.append(f'\tIntensity {_fmt_num(light.get("intensity", 0.0))},')
    lines.append(f'\tColor {{ {_fmt_floats(light.get("color") or [1, 1, 1])} }},')
    lines.append(f'\tAmbIntensity {_fmt_num(light.get("ambientIntensity", 0.0))},')
    lines.append(f'\tAmbColor {{ {_fmt_floats(light.get("ambientColor") or [0, 0, 0])} }},')
    lines.append('}')
    return lines


def _write_attachment_lines(attachment):
    node = attachment.get('node') or {}
    lines = [f'Attachment "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    if attachment.get('path'):
        lines.append(f'\tPath "{_escape(attachment["path"])}",')
    lines.append(f'\tAttachmentID {attachment.get("attachmentId", 0)},')
    tracks = attachment.get('tracks') or {}
    if 'KATV' in tracks:
        lines.extend(_write_track_lines('Visibility', tracks['KATV'], 'f32'))
    lines.append('}')
    return lines


def _write_particle_emitter_lines(emitter):
    node = emitter.get('node') or {}
    lines = [f'ParticleEmitter "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    lines.append(f'\tstatic EmissionRate {_fmt_num(emitter.get("emissionRate", 0.0))},')
    lines.append(f'\tstatic Gravity {_fmt_num(emitter.get("gravity", 0.0))},')
    lines.append(f'\tstatic Longitude {_fmt_num(emitter.get("longitude", 0.0))},')
    lines.append(f'\tstatic Latitude {_fmt_num(emitter.get("latitude", 0.0))},')
    lines.append(f'\tstatic LifeSpan {_fmt_num(emitter.get("lifespan", 0.0))},')
    lines.append(f'\tstatic InitVelocity {_fmt_num(emitter.get("initialVelocity", 0.0))},')
    lines.append(f'\tPath "{_escape(emitter.get("spawnModelFileName", ""))}",')
    lines.append('}')
    return lines


def _write_particle_emitter2_lines(emitter):
    node = emitter.get('node') or {}
    lines = [f'ParticleEmitter2 "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    lines.append(f'\tstatic Speed {_fmt_num(emitter.get("speed", 0.0))},')
    lines.append(f'\tstatic Variation {_fmt_num(emitter.get("variation", 0.0))},')
    lines.append(f'\tstatic Latitude {_fmt_num(emitter.get("latitude", 0.0))},')
    lines.append(f'\tstatic Gravity {_fmt_num(emitter.get("gravity", 0.0))},')
    lines.append(f'\tstatic LifeSpan {_fmt_num(emitter.get("lifespan", 0.0))},')
    lines.append(f'\tstatic EmissionRate {_fmt_num(emitter.get("emissionRate", 0.0))},')
    lines.append(f'\tstatic Length {_fmt_num(emitter.get("length", 0.0))},')
    lines.append(f'\tstatic Width {_fmt_num(emitter.get("width", 0.0))},')
    filter_name = {0: 'Blend', 1: 'Additive', 2: 'Modulate', 3: 'Modulate2x', 4: 'AlphaKey'}.get(
        emitter.get('filterMode', 0), 'Blend'
    )
    lines.append(f'\t{filter_name},')
    lines.append(f'\tRows {emitter.get("rows", 0)},')
    lines.append(f'\tColumns {emitter.get("columns", 0)},')
    head = {0: 'Head', 1: 'Tail', 2: 'Both'}.get(emitter.get('headOrTail', 0), 'Head')
    lines.append(f'\t{head},')
    lines.append(f'\tstatic TailLength {_fmt_num(emitter.get("tailLength", 0.0))},')
    lines.append(f'\tstatic Time {_fmt_num(emitter.get("time", 0.0))},')
    lines.append('\tSegmentColor {')
    for color in emitter.get('segmentColor') or [[1, 1, 1]] * 3:
        lines.append(f'\t\tColor {{ {_fmt_floats(color)} }},')
    lines.append('\t}')
    lines.append(f'\tAlpha {{ {_fmt_floats(emitter.get("segmentAlpha") or [255, 255, 255])} }},')
    lines.append(f'\tParticleScaling {{ {_fmt_floats(emitter.get("segmentScaling") or [1, 1, 1])} }},')
    lines.append(f'\tLifeSpanUVAnim {{ {_fmt_floats(emitter.get("headInterval") or [0, 0, 0])} }},')
    lines.append(f'\tDecayUVAnim {{ {_fmt_floats(emitter.get("headDecayInterval") or [0, 0, 0])} }},')
    lines.append(f'\tTailUVAnim {{ {_fmt_floats(emitter.get("tailInterval") or [0, 0, 0])} }},')
    lines.append(f'\tTailDecayUVAnim {{ {_fmt_floats(emitter.get("tailDecayInterval") or [0, 0, 0])} }},')
    lines.append(f'\tstatic TextureID {emitter.get("textureId", 0)},')
    if emitter.get('squirt'):
        lines.append('\tSquirt,')
    lines.append(f'\tPriorityPlane {emitter.get("priorityPlane", 0)},')
    lines.append(f'\tReplaceableId {emitter.get("replaceableId", 0)},')
    lines.append('}')
    return lines


def _write_ribbon_emitter_lines(emitter):
    node = emitter.get('node') or {}
    lines = [f'RibbonEmitter "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    lines.append(f'\tstatic HeightAbove {_fmt_num(emitter.get("heightAbove", 0.0))},')
    lines.append(f'\tstatic HeightBelow {_fmt_num(emitter.get("heightBelow", 0.0))},')
    lines.append(f'\tstatic Alpha {_fmt_num(emitter.get("alpha", 1.0))},')
    lines.append(f'\tstatic Color {{ {_fmt_floats(emitter.get("color") or [1, 1, 1])} }},')
    lines.append(f'\tstatic LifeSpan {_fmt_num(emitter.get("lifespan", 0.0))},')
    lines.append(f'\tEmissionRate {emitter.get("emissionRate", 0)},')
    lines.append(f'\tRows {emitter.get("rows", 0)},')
    lines.append(f'\tColumns {emitter.get("columns", 0)},')
    lines.append(f'\tMaterialID {emitter.get("materialId", 0)},')
    lines.append(f'\tstatic Gravity {_fmt_num(emitter.get("gravity", 0.0))},')
    lines.append('}')
    return lines


def _write_event_object_lines(event):
    node = event.get('node') or {}
    lines = [f'EventObject "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    tracks = event.get('tracks') or []
    lines.append(f'\tEventTrack {len(tracks)} {{')
    for track in tracks:
        lines.append(f'\t\t{int(track)},')
    lines.append('\t}')
    lines.append('}')
    return lines


def _write_camera_lines(camera):
    lines = [f'Camera "{_escape(camera.get("name", ""))}" {{']
    lines.append(f'\tPosition {{ {_fmt_floats(camera.get("position") or [0, 0, 0])} }},')
    lines.append(f'\tFieldOfView {_fmt_num(camera.get("fieldOfView", 0.0))},')
    lines.append(f'\tFarClip {_fmt_num(camera.get("farClippingPlane", 0.0))},')
    lines.append(f'\tNearClip {_fmt_num(camera.get("nearClippingPlane", 0.0))},')
    lines.append('\tTarget {')
    lines.append(f'\t\tPosition {{ {_fmt_floats(camera.get("targetPosition") or [0, 0, 0])} }},')
    lines.append('\t}')
    lines.append('}')
    return lines


def _write_collision_shape_lines(shape):
    node = shape.get('node') or {}
    lines = [f'CollisionShape "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    type_name = {0: 'Box', 1: 'Plane', 2: 'Sphere', 3: 'Cylinder'}.get(shape.get('type', 0), 'Box')
    lines.append(f'\t{type_name},')
    vertices = shape.get('vertices') or []
    lines.append(f'\tVertices {len(vertices)} {{')
    for vertex in vertices:
        lines.append(f'\t\t{{ {_fmt_floats(vertex)} }},')
    lines.append('\t}')
    if shape.get('type') in (2, 3):
        lines.append(f'\tBoundsRadius {_fmt_num(shape.get("radius", 0.0))},')
    lines.append('}')
    return lines
