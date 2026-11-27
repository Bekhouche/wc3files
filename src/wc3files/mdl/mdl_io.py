"""MDL (text) load/save for ModelFile.

Covers the common Warcraft III Art Tools MDL blocks used by map-imported models.
"""

import re
import struct

from .mdx_io import empty_model

_NUMBER = re.compile(r'^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$'
                     r'|^[+-]?(?:nan|inf(?:inity)?)$', re.IGNORECASE)
# What save_mdl writes for a NaN or an infinite float (Python's own spelling).
_SPECIAL_FLOATS = ('nan', 'inf', 'infinity')


_TOKEN = re.compile(
    # A string, which may itself contain // and, escaped, a quote: save_mdl
    # writes " as \" and \ as \\, and a string used to end at the escaped
    # quote (#380). A lone backslash, as paths in real MDL files have it,
    # stays part of the string.
    r'("(?:[^"\\]|\\.)*"'
    r'|[{},]'
    r'|(?:(?!//)[^\s{},"])+)'   # anything else, up to a comment
    r'|//[^\n]*')               # a comment, to the end of the line
# A comment is outside the group, so findall gives '' for it, and the
# tokenizer drops those with filter() instead of testing every token (#177).
# A string still matches first, so a // inside one stays part of it.


# Data MDL has no syntax for is written in a comment that starts with this,
# such as `// wc3files: UnnamedFlags 24,`. Other MDL readers skip it as a
# comment; this one reads what follows as ordinary MDL (#358).
EXTENSION_COMMENT = '// wc3files: '


class MdlTokenizer:
    def __init__(self, text):
        # Comments are matched by the same pattern as strings, so a `//`
        # inside a string stays part of it. They used to be cut from each
        # line before tokenizing: an attachment path holding a URL lost its
        # closing quote, and the parser read on into the next block.
        if EXTENSION_COMMENT in text:
            text = text.replace(EXTENSION_COMMENT, '')
        self._tokens = list(filter(None, _TOKEN.findall(text)))
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
            model['materials'] = _parse_materials(tokens, model.get('version', 800))
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
        elif token == 'BindPose':
            model['bindPose'] = _parse_bind_pose(tokens)
        elif token == 'ParticleEmitterPopcorn':
            model['particleEmittersPopcorn'].append(_parse_popcorn_emitter(tokens))
        elif token == 'FaceFX':
            model['faceEffects'].append(_parse_face_effect(tokens))
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
    # The order of the MDX chunks, so that the MDX written from this MDL has
    # it too: tools differ (some put CAMS before EVTS, some keep an empty
    # PREM), and it was lost through MDL (#363). A tag that is not four
    # letters or digits could not be read back as one token, so then the
    # order is left out and the default order is used.
    order = model.get('chunkOrder') or []
    if order and all(_CHUNK_TAG.match(tag) for tag in order):
        out.append(f'\t{EXTENSION_COMMENT}ChunkOrder {" ".join(order)},')
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

    # The reader knew TextureAnims, but they were never written, so every
    # texture animation was lost through MDL (#360). Tracks are written in
    # the order the model has them, which is the order MDX writes them in.
    texture_anims = model.get('textureAnimations') or []
    if texture_anims:
        out.append(f'TextureAnims {len(texture_anims)} {{')
        for anim in texture_anims:
            out.append('\tTVertexAnim {')
            for tag, track in (anim.get('tracks') or {}).items():
                if tag in _TEXTURE_ANIM_TRACKS:
                    name, kind = _TEXTURE_ANIM_TRACKS[tag]
                    out.extend(_write_track_lines(name, track, kind, indent=2))
            out.append('\t}')
        out.append('}')

    for geoset in model.get('geosets') or []:
        out.extend(_write_geoset_lines(geoset, version))
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
    for emitter in model.get('particleEmittersPopcorn') or []:
        out.extend(_write_popcorn_emitter_lines(emitter))
    # Face effects (FAFX) of version 900 and later, as mdx-m3-viewer writes
    # them: `FaceFX "<target>" { Path "...", }`. They were left out (#377).
    for effect in model.get('faceEffects') or []:
        out.append(f'FaceFX "{_escape(effect.get("target", ""))}" {{')
        out.append(f'\tPath "{_escape(effect.get("path", ""))}",')
        out.append('}')
    for event in model.get('eventObjects') or []:
        out.extend(_write_event_object_lines(event))
    for camera in model.get('cameras') or []:
        out.extend(_write_camera_lines(camera))
    for shape in model.get('collisionShapes') or []:
        out.extend(_write_collision_shape_lines(shape))

    # The bind pose of a version 900 and later model, one 4x3 matrix per
    # node, as mdx-m3-viewer writes it; it was left out (#374).
    bind_pose = model.get('bindPose') or []
    if bind_pose:
        out.append('BindPose {')
        out.append(f'\tMatrices {len(bind_pose)} {{')
        for matrix in bind_pose:
            out.append(f'\t\t{{ {_fmt_floats(matrix)} }},')
        out.append('\t}')
        out.append('}')

    return '\n'.join(out) + '\n'


def _escape(value):
    return (value or '').replace('\\', '\\\\').replace('"', '\\"')


_F32 = struct.Struct('<f')


def _fmt_num(value):
    """A float as the shortest of 7, 8 or 9 significant digits that reads back
    to the same float32, which every MDL float becomes in an MDX.

    Seven digits alone, as this used to write, is not always enough: 263.29132
    came back as 263.29129, so MDX to MDL to MDX changed almost every model.
    Seven is tried first, so values that need no more print as before."""
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return f'{value:.7g}'          # nan, inf, -inf
        try:
            target = _F32.pack(value)
        except OverflowError:
            return repr(value)
        for digits in (7, 8):
            text = f'{value:.{digits}g}'
            if _F32.pack(float(text)) == target:
                return text
        return f'{value:.9g}'
    return str(value)


def _fmt_floats(values):
    return ', '.join(_fmt_num(v) for v in values)


def _parse_number(token):
    if token.endswith(','):
        token = token[:-1]
    # A NaN or infinite float is written as nan, inf or -inf (some real
    # models carry one, often in a portrait camera). Without this the token
    # went to int() and the whole model failed to read back.
    if token.lower().lstrip('+-') in _SPECIAL_FLOATS:
        return float(token)
    if '.' in token or 'e' in token.lower():
        return float(token)
    # The writer prints a negative zero float as -0. int('-0') is 0, which
    # lost the sign, so MDX -> MDL -> MDX changed the float's bits (#168).
    # Every caller converts the result with int() or float(), and int(-0.0)
    # is 0, so an integer field reads the same as before.
    if token == '-0':
        return -0.0
    return int(token)


def _parse_string(token):
    if token.startswith('"') and token.endswith('"'):
        # \\ and \" back to \ and "; any other backslash is kept
        return _UNESCAPE.sub(r'\1', token[1:-1])
    return token


_UNESCAPE = re.compile(r'\\(["\\])')


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
    """`{ a, b, c }` as a list of floats.

    It walks the token list directly rather than through peek() and next(),
    which were most of the time of reading an MDL (#177). float(token) is
    float(_parse_number(token)) for every token here: a token never holds a
    comma, and float() reads nan, inf and -0 as _parse_number does."""
    toks = tokens._tokens
    n = len(toks)
    i = tokens._i
    if i >= n:
        raise ValueError('Unexpected end of MDL')
    if toks[i] != '{':
        tokens._i = i + 1
        raise ValueError(f"Expected '{{', got {toks[i]!r}")
    i += 1
    values = []
    while True:
        if i >= n:
            tokens._i = i
            raise ValueError('Unexpected end of MDL')
        token = toks[i]
        i += 1
        if token == '}':
            break
        if token == ',':
            continue
        values.append(float(token))
        if i < n and toks[i] == ',':
            i += 1
        if i < n and toks[i] == '}':
            i += 1
            break
    tokens._i = i
    return values


def _parse_version(tokens, model):
    tokens.expect('Version')
    tokens.expect('{')
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'FormatVersion':
            model['version'] = int(_parse_number(tokens.next()))
        elif key == 'ChunkOrder':
            order = []
            while tokens.peek() not in (None, ',', '}'):
                order.append(tokens.next())
            model['chunkOrder'] = order
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}', '{'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')


_CHUNK_TAG = re.compile(r'^[A-Z0-9]{4}$')


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


def _parse_materials(tokens, version=800):
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
            if key in _MATERIAL_FLAGS:
                material['flags'] |= _MATERIAL_FLAGS[key]
            elif key == 'PriorityPlane':
                material['priorityPlane'] = int(_parse_number(tokens.next()))
            elif key == 'Shader':
                material['shader'] = _parse_string(tokens.next())
            elif key == 'Layer':
                material['layers'].append(_parse_layer(tokens, version >= 1100))
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


def _parse_layer(tokens, texture_list=False):
    """A layer; with `texture_list` (version 1100 and later), its textures
    are read into the list of #302."""
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
    entries = {}          # texture type -> entry, in the order they come
    own_texture = None    # the layer's own texture field, when written apart
    while tokens.peek() != '}':
        key = tokens.next()
        if texture_list and _parse_hd_texture(tokens, key, layer, entries):
            continue
        if texture_list and key == 'LayerTextureID':
            own_texture = int(_parse_number(tokens.next()))
        elif _parse_field(tokens, key, layer, _LAYER_FIELDS):
            pass
        elif key == 'FilterMode':
            mode = tokens.next()
            layer['filterMode'] = _FILTER_MODES.get(mode, int(_parse_number(mode)) if _NUMBER.match(mode) else 0)
        elif key in _LAYER_FLAGS:
            layer['shadingFlags'] |= _LAYER_FLAGS[key]
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
    if texture_list:
        layer.setdefault('hd', 0)
        layer['textures'] = list(entries.values())
        diffuse = entries[0]['textureId'] if 0 in entries else 0
        layer['textureId'] = diffuse if own_texture is None else own_texture
    return layer


# The texture list of a layer of version 1100 and later (#302), in the
# syntax of Retera's Model Studio (craft3data .../mdl/Layer.java): the HD
# flag as `ShaderTypeId`, and each texture as `static <Type>TextureID n`,
# or an animated one as a `<Type>TextureID` track, where the type is the
# texture's slot. Diffuse has no prefix.
HD_TEXTURE_TYPES = ('', 'Normal', 'ORM', 'Emissive', 'TeamColor', 'Reflections')
_HD_TEXTURE_KEYS = {name + 'TextureID': kind for kind, name in enumerate(HD_TEXTURE_TYPES)}
_HD_SLOT_KEYS = {name + 'TextureSlot': kind for kind, name in enumerate(HD_TEXTURE_TYPES)}


def _parse_hd_texture(tokens, key, layer, entries):
    """One entry of a version 1100 layer's texture list (see
    _write_hd_texture_lines), or False when `key` is not one; it takes its
    own trailing comma."""
    static = key == 'static' and tokens.peek() in _HD_TEXTURE_KEYS
    if static:
        key = tokens.next()
    if key == 'ShaderTypeId':
        layer['hd'] = int(_parse_number(tokens.next()))
    elif key in _HD_SLOT_KEYS:
        entry = entries.setdefault(_HD_SLOT_KEYS[key], {'textureId': 0, 'slot': _HD_SLOT_KEYS[key], 'tracks': {}})
        entry['slot'] = int(_parse_number(tokens.next()))
    elif key in _HD_TEXTURE_KEYS:
        kind = _HD_TEXTURE_KEYS[key]
        entry = entries.setdefault(kind, {'textureId': 0, 'slot': kind, 'tracks': {}})
        if not static and _is_animated_track(tokens):
            tokens.next()  # keyframe count
            entry['tracks']['KMTF'] = _parse_track_block(tokens, 'u32')
        else:
            entry['textureId'] = int(_parse_number(tokens.next()))
    else:
        return False
    if tokens.peek() == ',':
        tokens.next()
    return True


# Material and layer flags by MDL name, in the order they are written. The
# names are mdx-m3-viewer's; TwoSided on a material and Unlit on a layer
# exist from version 900 on, and SortPrimsNearZ was missing (#362).
_MATERIAL_FLAGS = {
    'ConstantColor': 0x1,
    'TwoSided': 0x2,
    'SortPrimsNearZ': 0x8,
    'SortPrimsFarZ': 0x10,
    'FullResolution': 0x20,
}
_LAYER_FLAGS = {
    'Unshaded': 0x1,
    'SphereEnvMap': 0x2,
    'TwoSided': 0x10,
    'Unfogged': 0x20,
    'NoDepthTest': 0x40,
    'NoDepthSet': 0x80,
    'Unlit': 0x100,
}
# Layer fields of version 900 and later, static or animated, in the syntax
# mdx-m3-viewer reads and writes (#362).
_LAYER_FIELDS = {
    'EmissiveGain': ('KMTE', 'f32', 'emissiveGain'),
    'FresnelColor': ('KFC3', 'f32x3', 'fresnelColor'),
    'FresnelOpacity': ('KFCA', 'f32', 'fresnelOpacity'),
    'FresnelTeamColor': ('KFTC', 'f32', 'fresnelTeamColor'),
}


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
    # The keys are read from the token list directly, as _read_vector does
    # (#177); each step is the one the peek()/next() version took.
    toks = tokens._tokens
    n = len(toks)
    i = tokens._i
    while True:
        if i >= n:
            tokens._i = i
            raise ValueError('Unexpected end of MDL')
        token = toks[i]
        if token == '}':
            break
        i += 1
        if token in _INTERPOLATION:
            interpolation = _INTERPOLATION[token]
            if i < n and toks[i] == ',':
                i += 1
            continue
        if token == 'GlobalSeqId':
            if i >= n:
                tokens._i = i
                raise ValueError('Unexpected end of MDL')
            global_seq = int(_parse_number(toks[i]))
            i += 1
            if i < n and toks[i] == ',':
                i += 1
            continue

        if token.endswith(':'):
            frame = int(token[:-1])
        else:
            frame = int(_parse_number(token))
            if i < n and toks[i] == ':':
                i += 1
        tokens._i = i
        value = _parse_track_value(tokens, kind)
        i = tokens._i
        key = {'frame': frame, 'value': value}
        # Art Tools writes:  frame: value, InTan ..., OutTan ...
        if i < n and toks[i] == ',':
            i += 1
        while i < n and toks[i] in ('InTan', 'OutTan'):
            tan_key = toks[i]
            tokens._i = i + 1
            tan_val = _parse_track_value(tokens, kind)
            i = tokens._i
            if tan_key == 'InTan':
                key['inTan'] = tan_val
            else:
                key['outTan'] = tan_val
            if i < n and toks[i] == ',':
                i += 1
        keys.append(key)
        if i < n and toks[i] == ',':
            i += 1
    tokens._i = i
    tokens.expect('}')
    if any('inTan' in k for k in keys) and interpolation <= 1:
        interpolation = 2
    return {
        'interpolationType': interpolation,
        'globalSequenceId': global_seq,
        'keys': keys,
    }


_INTERPOLATION = {'DontInterp': 0, 'Linear': 1, 'Hermite': 2, 'Bezier': 3}


def _parse_track_value(tokens, kind):
    if kind == 'f32':
        # float(token) is float(_parse_number(token)); see _read_vector.
        return float(tokens.next())
    if kind == 'u32':
        return int(_parse_number(tokens.next()))
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


_TEXTURE_ANIM_TRACKS = {
    'KTAT': ('Translation', 'f32x3'),
    'KTAR': ('Rotation', 'f32x4'),
    'KTAS': ('Scaling', 'f32x3'),
}


def _parse_float_list_block(tokens):
    tokens.expect('{')
    values = []
    toks = tokens._tokens
    n = len(toks)
    i = tokens._i
    while True:
        if i >= n:
            tokens._i = i
            raise ValueError('Unexpected end of MDL')
        token = toks[i]
        if token == '}':
            break
        i += 1
        # vectors written as { a, b, c }, are read as a flat list
        if token == '{' or token == ',':
            continue
        values.append(float(token))
        if i < n and toks[i] == ',':
            i += 1
    tokens._i = i
    tokens.expect('}')
    return values


def _parse_vector_list_block(tokens, width):
    tokens.expect('{')
    values = []
    toks = tokens._tokens
    n = len(toks)
    while True:
        i = tokens._i
        if i >= n:
            raise ValueError('Unexpected end of MDL')
        token = toks[i]
        if token == '}':
            break
        if token == '{':
            values.extend(_read_vector(tokens))
            i = tokens._i
        else:
            # flat list
            values.append(float(token))
            i += 1
        if i < n and toks[i] == ',':
            i += 1
        tokens._i = i
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
        elif key == 'Tangents':
            tokens.next()
            geoset['tangents'] = _parse_vector_list_block(tokens, 4)
        elif key == 'SkinWeights':
            tokens.next()
            geoset['skin'] = [int(v) for v in _parse_float_list_block(tokens)]
        elif key == 'SkinCountUnit':
            geoset['skinCountUnit'] = int(_parse_number(tokens.next()))
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
        elif key == 'UnnamedSelectionFlags':
            geoset['selectionFlags'] |= int(_parse_number(tokens.next()))
        elif key == 'LevelOfDetail':
            geoset['levelOfDetail'] = int(_parse_number(tokens.next()))
        elif key == 'Name':
            geoset['name'] = _parse_string(tokens.next())
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
            anim['flags'] |= 2
        elif key == 'GeosetId':
            anim['geosetId'] = int(_parse_number(tokens.next()))
        elif key == 'DropShadow':
            anim['flags'] |= 1
        elif key == 'UnnamedFlags':
            anim['flags'] |= int(_parse_number(tokens.next()))
        elif tokens.peek() == '{':
            _skip_block(tokens)
        elif tokens.peek() not in (None, '}'):
            tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return anim


def _parse_node_body(tokens, node, visibility=None):
    """Parse one node entry (ObjectId, Parent, a flag, a node track) into
    `node`, or consume a key it does not know and return it for the caller.

    `visibility` is (tracks, tag) for an object with a visibility track: an
    animated `Visibility` goes there. Without it the track is skipped, as on
    a bone. An attachment's visibility used to be skipped here too, so the
    attachment never saw it (#355).

    One entry per call: every caller already calls this once per key from its
    own loop. It used to go on reading entries after the first, so the key
    after a node entry was consumed here, returned as "leftover", and its
    value dropped by the caller: an attachment's Path or a light's type,
    written after its ObjectId, read back empty."""
    if tokens.peek() != '}':
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
            if tokens.peek() and tokens.peek()[0].isdigit():
                tokens.next()
                if visibility is not None:
                    tracks, tag = visibility
                    tracks[tag] = _parse_track_block(tokens, 'f32')
                else:
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
        if key == 'static':
            # `static AttenuationStart 80,`, as Reforged's own MDL files
            # write a field that could be animated but is not. It used to
            # fall through to the node parser, and the value read as 0.
            key = tokens.next()
        if key in _LIGHT_TYPES:
            light['type'] = _LIGHT_TYPES[key]
        elif key in _LIGHT_FIELDS:
            tag, kind, field = _LIGHT_FIELDS[key]
            if _is_animated_track(tokens):
                # `Intensity 6 { Linear, 3367: 0.7, ... }`: a track of 6
                # keys, which used to be read as a static intensity of 6.
                tokens.next()
                light['tracks'][tag] = _parse_track_block(tokens, kind)
            elif field is None:
                tokens.next()
            elif kind == 'f32x3':
                light[field] = _read_vector(tokens)
            else:
                light[field] = float(_parse_number(tokens.next()))
        elif key == 'ShadowIntensity':
            light['shadowIntensity'] = float(_parse_number(tokens.next()))
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


_LIGHT_TYPES = {'Omnidirectional': 0, 'Directional': 1, 'Ambient': 2}
# MDL name -> (MDX track tag, value kind, static field). Visibility has no
# static field.
_LIGHT_FIELDS = {
    'AttenuationStart': ('KLAS', 'f32', 'attenuationStart'),
    'AttenuationEnd': ('KLAE', 'f32', 'attenuationEnd'),
    'Intensity': ('KLAI', 'f32', 'intensity'),
    'Color': ('KLAC', 'f32x3', 'color'),
    'AmbIntensity': ('KLBI', 'f32', 'ambientIntensity'),
    'AmbColor': ('KLBC', 'f32x3', 'ambientColor'),
    'Visibility': ('KLAV', 'f32', None),
}


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
            leftover = _parse_node_body(tokens, attachment['node'],
                                        (attachment['tracks'], 'KATV'))
            if leftover and tokens.peek() == '{':
                _skip_block(tokens)
            elif leftover and tokens.peek() not in (None, '}'):
                tokens.next()
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return attachment


# PopcornFX emitters (CORN) of version 900 and later, in the syntax of
# mdx-m3-viewer's ParticleEmitterPopcorn; they were left out (#375). The
# colour and alpha are one 4-float `color` in the MDX data, and the MDX
# field named `flags` there is the animation visibility guide string.
_POPCORN_FLAGS = {'Unshaded': 0x8000, 'SortPrimsFarZ': 0x10000, 'Unfogged': 0x40000}
_POPCORN_FIELDS = {
    'LifeSpan': ('KPPL', 'f32', 'lifeSpan'),
    'EmissionRate': ('KPPE', 'f32', 'emissionRate'),
    'Speed': ('KPPS', 'f32', 'speed'),
    'Color': ('KPPC', 'f32x3', '_rgb'),
    'Alpha': ('KPPA', 'f32', '_alpha'),
    'Visibility': ('KPPV', 'f32', None),
}
# Node bits save_mdl writes by name: DontInherit, Billboarded and the rest.
_NAMED_NODE_BITS = 0xFF


def _write_popcorn_emitter_lines(emitter):
    node = emitter.get('node') or {}
    color = list(emitter.get('color') or [1.0, 1.0, 1.0, 1.0])
    view = dict(emitter, _rgb=color[:3], _alpha=color[3] if len(color) > 3 else 1.0)
    lines = [f'ParticleEmitterPopcorn "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    flags = node.get('flags', 0)
    lines.extend(_write_flag_lines(flags, _POPCORN_FLAGS))
    unnamed = flags & ~_NAMED_NODE_BITS & ~sum(_POPCORN_FLAGS.values())
    if unnamed:
        lines.append(f'\t{EXTENSION_COMMENT}UnnamedFlags {unnamed},')
    lines.extend(_write_field_lines(view, _POPCORN_FIELDS))
    if emitter.get('replaceableId'):
        lines.append(f'\tReplaceableId {emitter["replaceableId"]},')
    if emitter.get('path'):
        lines.append(f'\tPath "{_escape(emitter["path"])}",')
    if emitter.get('flags'):
        lines.append(f'\tAnimVisibilityGuide "{_escape(emitter["flags"])}",')
    lines.append('}')
    return lines


def _parse_popcorn_emitter(tokens):
    tokens.expect('ParticleEmitterPopcorn')
    name = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    emitter = {
        'node': _new_node(name, 0),
        'lifeSpan': 0.0, 'emissionRate': 0.0, 'speed': 0.0,
        '_rgb': [1.0, 1.0, 1.0], '_alpha': 1.0,
        'replaceableId': 0, 'path': '', 'flags': '', 'tracks': {},
    }
    while tokens.peek() != '}':
        key = tokens.next()
        if _parse_field(tokens, key, emitter, _POPCORN_FIELDS):
            pass
        elif key in _POPCORN_FLAGS:
            emitter['node']['flags'] |= _POPCORN_FLAGS[key]
        elif key == 'UnnamedFlags':
            emitter['node']['flags'] |= int(_parse_number(tokens.next()))
        elif key == 'ReplaceableId':
            emitter['replaceableId'] = int(_parse_number(tokens.next()))
        elif key == 'Path':
            emitter['path'] = _parse_string(tokens.next())
        elif key == 'AnimVisibilityGuide':
            emitter['flags'] = _parse_string(tokens.next())
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
    emitter['color'] = list(emitter.pop('_rgb')) + [emitter.pop('_alpha')]
    return emitter


def _parse_face_effect(tokens):
    tokens.expect('FaceFX')
    target = _parse_string(tokens.next()) if tokens.peek().startswith('"') else ''
    tokens.expect('{')
    effect = {'target': target, 'path': ''}
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Path':
            effect['path'] = _parse_string(tokens.next())
        elif tokens.peek() == '{':
            _skip_block(tokens)
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return effect


def _parse_bind_pose(tokens):
    """`BindPose { Matrices N { { 12 floats }, ... } }`, as save_mdl writes
    it: a list of 12-float matrices."""
    tokens.expect('BindPose')
    tokens.expect('{')
    matrices = []
    while tokens.peek() != '}':
        key = tokens.next()
        if key == 'Matrices':
            tokens.next()  # count
            values = _parse_vector_list_block(tokens, 12)
            if len(values) % 12:
                raise ValueError(f'BindPose has {len(values)} values, not 12 per matrix')
            matrices = [values[i:i + 12] for i in range(0, len(values), 12)]
        elif tokens.peek() == '{':
            _skip_block(tokens)
        if tokens.peek() == ',':
            tokens.next()
    tokens.expect('}')
    return matrices


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
        if _parse_field(tokens, key, emitter, _EMITTER_FIELDS):
            pass
        elif key == 'EmitterUsesMDL':
            emitter['node']['flags'] |= 0x8000
        elif key == 'EmitterUsesTGA':
            emitter['node']['flags'] |= 0x10000
        elif key == 'static':
            field = tokens.next()
            value = tokens.next()
            if field == 'Path':
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
        if _parse_field(tokens, key, emitter, _EMITTER2_FIELDS):
            pass
        elif key == 'static':
            field = tokens.next()
            if field == 'TextureID':
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
        elif key in _EMITTER2_FLAGS:
            emitter['node']['flags'] |= _EMITTER2_FLAGS[key]
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


# ParticleEmitter2 node flags by MDL name; written in this order (#356).
_EMITTER2_FLAGS = {
    'Unshaded': 0x8000,
    'SortPrimsFarZ': 0x10000,
    'LineEmitter': 0x20000,
    'Unfogged': 0x40000,
    'ModelSpace': 0x80000,
    'XYQuad': 0x100000,
}
# ParticleEmitter node flags by MDL name.
_EMITTER_FLAGS = {
    'EmitterUsesMDL': 0x8000,
    'EmitterUsesTGA': 0x10000,
}


# Emitter fields that can be animated: MDL name -> (MDX track tag, value
# kind, static field), in the order they are written. Visibility has no
# static field. They were written as their static value only, so every
# emitter track was lost through MDL (#357).
_EMITTER_FIELDS = {
    'EmissionRate': ('KPEE', 'f32', 'emissionRate'),
    'Gravity': ('KPEG', 'f32', 'gravity'),
    'Longitude': ('KPLN', 'f32', 'longitude'),
    'Latitude': ('KPLT', 'f32', 'latitude'),
    'LifeSpan': ('KPEL', 'f32', 'lifespan'),
    'InitVelocity': ('KPES', 'f32', 'initialVelocity'),
    'Visibility': ('KPEV', 'f32', None),
}
_EMITTER2_FIELDS = {
    'Speed': ('KP2S', 'f32', 'speed'),
    'Variation': ('KP2R', 'f32', 'variation'),
    'Latitude': ('KP2L', 'f32', 'latitude'),
    'Gravity': ('KP2G', 'f32', 'gravity'),
    'LifeSpan': (None, 'f32', 'lifespan'),
    'EmissionRate': ('KP2E', 'f32', 'emissionRate'),
    'Length': ('KP2N', 'f32', 'length'),
    'Width': ('KP2W', 'f32', 'width'),
    'Visibility': ('KP2V', 'f32', None),
    # Written later, after the filter mode and the head or tail.
    'TailLength': (None, 'f32', 'tailLength'),
    'Time': (None, 'f32', 'time'),
}
_RIBBON_FIELDS = {
    'HeightAbove': ('KRHA', 'f32', 'heightAbove'),
    'HeightBelow': ('KRHB', 'f32', 'heightBelow'),
    'Alpha': ('KRAL', 'f32', 'alpha'),
    'Color': ('KRCO', 'f32x3', 'color'),
    'LifeSpan': (None, 'f32', 'lifespan'),
    'TextureSlot': ('KRTX', 'u32', 'textureSlot'),
    'Visibility': ('KRVS', 'f32', None),
    # Written last, after the material.
    'Gravity': (None, 'f32', 'gravity'),
}
_STATIC_DEFAULTS = {'alpha': 1.0, 'color': [1.0, 1.0, 1.0], 'textureSlot': 0}


def _parse_field(tokens, key, obj, fields):
    """Read the field `key` of `fields` into `obj`, `static` or animated;
    False when `key` is not one of them (nothing is consumed then)."""
    static = key == 'static'
    if static:
        if tokens.peek() not in fields:
            return False
        key = tokens.next()
    if key not in fields:
        return False
    tag, kind, field = fields[key]
    if not static and tag and _is_animated_track(tokens):
        tokens.next()  # keyframe count
        obj.setdefault('tracks', {})[tag] = _parse_track_block(tokens, kind)
    elif field is None:
        tokens.next()
    elif kind == 'f32x3':
        obj[field] = _read_vector(tokens)
    elif kind == 'u32':
        obj[field] = int(_parse_number(tokens.next()))
    else:
        obj[field] = float(_parse_number(tokens.next()))
    return True


def _write_field_lines(obj, fields, names=None, indent=1, present_only=False):
    """Each field of `fields` (or the `names` among them) as its track when
    `obj` has one, else as its static value; Visibility only as a track.
    With `present_only`, a static value `obj` does not have is left out."""
    tracks = obj.get('tracks') or {}
    pad = '\t' * indent
    lines = []
    for name in names or fields:
        tag, kind, field = fields[name]
        if tag in tracks:
            lines.extend(_write_track_lines(name, tracks[tag], kind, indent=indent))
        elif field is None or present_only and field not in obj:
            continue
        elif kind == 'f32x3':
            value = obj.get(field) or _STATIC_DEFAULTS.get(field, [0.0, 0.0, 0.0])
            lines.append(f'{pad}static {name} {{ {_fmt_floats(value)} }},')
        else:
            lines.append(f'{pad}static {name} {_fmt_num(obj.get(field, _STATIC_DEFAULTS.get(field, 0.0)))},')
    return lines


def _write_flag_lines(flags, names):
    return [f'\t{name},' for name, bit in names.items() if flags & bit]


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
        if _parse_field(tokens, key, emitter, _RIBBON_FIELDS):
            pass
        elif key == 'static':
            tokens.next()  # a static field this reader does not know
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
        elif key == 'Translation' and _is_animated_track(tokens):
            tokens.next()
            camera['tracks']['KCTR'] = _parse_track_block(tokens, 'f32x3')
        elif key == 'Rotation' and _is_animated_track(tokens):
            tokens.next()
            camera['tracks']['KCRL'] = _parse_track_block(tokens, 'f32')
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
                elif tkey == 'Translation' and _is_animated_track(tokens):
                    tokens.next()
                    camera['tracks']['KTTR'] = _parse_track_block(tokens, 'f32x3')
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
    lines.extend(f'\t\t{name},' for name, bit in _MATERIAL_FLAGS.items() if flags & bit)
    if material.get('priorityPlane'):
        lines.append(f'\t\tPriorityPlane {material["priorityPlane"]},')
    if 'shader' in material:
        lines.append(f'\t\tShader "{_escape(material["shader"])}",')
    for layer in material.get('layers') or []:
        lines.append('\t\tLayer {')
        mode = layer.get('filterMode', 0)
        mode_name = _FILTER_MODE_NAMES[mode] if 0 <= mode < len(_FILTER_MODE_NAMES) else 'None'
        lines.append(f'\t\t\tFilterMode {mode_name},')
        shading = layer.get('shadingFlags', 0)
        lines.extend(f'\t\t\t{name},' for name, bit in _LAYER_FLAGS.items() if shading & bit)
        tracks = layer.get('tracks') or {}
        if layer.get('textures') is not None:
            lines.extend(_write_hd_texture_lines(layer))
        elif 'KMTF' in tracks:
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
        lines.extend(_write_field_lines(layer, _LAYER_FIELDS, indent=3, present_only=True))
        lines.append('\t\t}')
    lines.append('\t}')
    return lines


def _hd_texture_type(entry, index):
    """The type an entry of a layer's texture list is written under. An
    animated entry stores a slot that is not its type in the game's own
    models (0 for a normal map, on 5 of 1,714 layers of 600 game models);
    Retera's Model Studio takes its position in the list as the type, and
    so does this."""
    return index if (entry.get('tracks') or {}).get('KMTF') else entry.get('slot', 0)


def _write_hd_texture_lines(layer):
    pad = '\t\t\t'
    textures = layer.get('textures') or []
    lines = [f'{pad}ShaderTypeId {layer.get("hd", 0)},']
    types = [_hd_texture_type(entry, index) for index, entry in enumerate(textures)]
    if len(set(types)) != len(types) or any(not 0 <= t < len(HD_TEXTURE_TYPES) for t in types):
        raise ValueError(f'a layer\'s texture list has slots MDL cannot name: '
                         f'{[entry.get("slot") for entry in textures]}')
    for entry, kind in zip(textures, types):
        name = HD_TEXTURE_TYPES[kind] + 'TextureID'
        lines.append(f'{pad}static {name} {entry.get("textureId", 0)},')
        track = (entry.get('tracks') or {}).get('KMTF')
        if track:
            lines.extend(_write_track_lines(name, track, 'u32', indent=3))
        if entry.get('slot', 0) != kind:
            lines.append(f'{pad}{EXTENSION_COMMENT}{HD_TEXTURE_TYPES[kind]}TextureSlot {entry.get("slot", 0)},')
    # The layer's own texture field: Retera's Model Studio sets it to the
    # diffuse texture, but the game's HD layers have 0 there (864 of 1,714).
    diffuse = textures[types.index(0)].get('textureId', 0) if 0 in types else 0
    if layer.get('textureId', 0) != diffuse:
        lines.append(f'{pad}{EXTENSION_COMMENT}LayerTextureID {layer.get("textureId", 0)},')
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


def _default_skin_unit(version):
    """The unit save_mdx gives a skin count when none is recorded."""
    return 2 if version >= 1800 else 1


def _write_geoset_lines(geoset, version=800):
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
    # The tangents and skin weights of a version 900 and later geoset, as
    # mdx-m3-viewer writes them; they were left out, so an HD model lost
    # them through MDL (#373).
    tangents = geoset.get('tangents')
    if tangents is not None:
        lines.append(f'\tTangents {len(tangents) // 4} {{')
        for i in range(0, len(tangents), 4):
            lines.append(f'\t\t{{ {_fmt_floats(tangents[i:i+4])} }},')
        lines.append('\t}')
    vertex_groups = geoset.get('vertexGroups') or []
    lines.append('\tVertexGroup {')
    for group in vertex_groups:
        lines.append(f'\t\t{int(group)},')
    lines.append('\t}')
    skin = geoset.get('skin')
    if skin is not None:
        # Per vertex, four bone indices and four weights, one byte each.
        if len(skin) % 8:
            raise ValueError(f'a geoset\'s skin has {len(skin)} bytes, not 8 per vertex')
        lines.append(f'\tSkinWeights {len(skin) // 8} {{')
        for i in range(0, len(skin), 8):
            lines.append(f'\t\t{", ".join(str(int(v)) for v in skin[i:i+8])},')
        lines.append('\t}')
        # What the MDX count of the skin counts, bytes (1) or pairs of bytes
        # (2): it differs between versions, and MDL has no field for it.
        unit = geoset.get('skinCountUnit')
        if unit and unit != _default_skin_unit(version):
            lines.append(f'\t{EXTENSION_COMMENT}SkinCountUnit {unit},')
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
    # Unselectable was read but never written (#359). Version 900 and later
    # geosets have a level of detail and a name, written as mdx-m3-viewer
    # writes them.
    selection = geoset.get('selectionFlags', 0)
    if selection & 4:
        lines.append('\tUnselectable,')
    if selection & ~4:
        lines.append(f'\t{EXTENSION_COMMENT}UnnamedSelectionFlags {selection & ~4},')
    if 'levelOfDetail' in geoset:
        lines.append(f'\tLevelOfDetail {geoset["levelOfDetail"]},')
    if 'name' in geoset:
        lines.append(f'\tName "{_escape(geoset["name"])}",')
    lines.append('}')
    return lines


def _write_geoset_anim_lines(anim):
    lines = ['GeosetAnim {']
    tracks = anim.get('tracks') or {}
    flags = anim.get('flags', 0)
    # Bit 1 is DropShadow and bit 2 the colour. A static colour used to be
    # written on bit 1, so it was lost on most models and DropShadow was
    # never written (#358).
    if flags & 1:
        lines.append('\tDropShadow,')
    if 'KGAO' in tracks:
        lines.extend(_write_track_lines('Alpha', tracks['KGAO'], 'f32'))
    else:
        lines.append(f'\tstatic Alpha {_fmt_num(anim.get("alpha", 1.0))},')
    if 'KGAC' in tracks:
        lines.extend(_write_track_lines('Color', tracks['KGAC'], 'f32x3'))
    elif flags & 2:
        lines.append(f'\tstatic Color {{ {_fmt_floats(anim.get("color") or [1, 1, 1])} }},')
    # Bits 8 and 16 are set on 1,063 geoset animations of 300 corpus models
    # (always together, 26 = 2 + 8 + 16) and have no MDL name.
    if flags & ~3:
        lines.append(f'\t{EXTENSION_COMMENT}UnnamedFlags {flags & ~3},')
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
    # A field with a track is written as the track. It used to be written as
    # its static value only, so every animated light lost its animation.
    tracks = light.get('tracks') or {}
    defaults = {'attenuationStart': 0.0, 'attenuationEnd': 0.0, 'intensity': 0.0,
                'color': [1, 1, 1], 'ambientIntensity': 0.0, 'ambientColor': [0, 0, 0]}
    for name, (tag, kind, field) in _LIGHT_FIELDS.items():
        if tag in tracks:
            lines.extend(_write_track_lines(name, tracks[tag], kind))
        elif field is None:
            continue
        elif kind == 'f32x3':
            lines.append(f'\t{name} {{ {_fmt_floats(light.get(field) or defaults[field])} }},')
        else:
            lines.append(f'\t{name} {_fmt_num(light.get(field, defaults[field]))},')
    if 'shadowIntensity' in light:
        lines.append(f'\tShadowIntensity {_fmt_num(light["shadowIntensity"])},')
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
    lines.extend(_write_flag_lines(node.get('flags', 0), _EMITTER_FLAGS))
    lines.extend(_write_field_lines(emitter, _EMITTER_FIELDS))
    lines.append(f'\tPath "{_escape(emitter.get("spawnModelFileName", ""))}",')
    lines.append('}')
    return lines


def _write_particle_emitter2_lines(emitter):
    node = emitter.get('node') or {}
    lines = [f'ParticleEmitter2 "{_escape(node.get("name", ""))}" {{']
    lines.extend(_write_node_lines(node))
    lines.extend(_write_flag_lines(node.get('flags', 0), _EMITTER2_FLAGS))
    lines.extend(_write_field_lines(emitter, _EMITTER2_FIELDS, (
        'Speed', 'Variation', 'Latitude', 'Gravity', 'LifeSpan', 'EmissionRate',
        'Length', 'Width', 'Visibility')))
    filter_name = {0: 'Blend', 1: 'Additive', 2: 'Modulate', 3: 'Modulate2x', 4: 'AlphaKey'}.get(
        emitter.get('filterMode', 0), 'Blend'
    )
    lines.append(f'\t{filter_name},')
    lines.append(f'\tRows {emitter.get("rows", 0)},')
    lines.append(f'\tColumns {emitter.get("columns", 0)},')
    head = {0: 'Head', 1: 'Tail', 2: 'Both'}.get(emitter.get('headOrTail', 0), 'Head')
    lines.append(f'\t{head},')
    lines.extend(_write_field_lines(emitter, _EMITTER2_FIELDS, ('TailLength', 'Time')))
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
    lines.extend(_write_field_lines(emitter, _RIBBON_FIELDS, (
        'HeightAbove', 'HeightBelow', 'Alpha', 'Color', 'LifeSpan', 'TextureSlot', 'Visibility')))
    lines.append(f'\tEmissionRate {emitter.get("emissionRate", 0)},')
    lines.append(f'\tRows {emitter.get("rows", 0)},')
    lines.append(f'\tColumns {emitter.get("columns", 0)},')
    lines.append(f'\tMaterialID {emitter.get("materialId", 0)},')
    lines.extend(_write_field_lines(emitter, _RIBBON_FIELDS, ('Gravity',)))
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
    # The tracks are written as mdx-m3-viewer writes them; they used to be
    # left out (#361).
    tracks = camera.get('tracks') or {}
    lines.append(f'\tPosition {{ {_fmt_floats(camera.get("position") or [0, 0, 0])} }},')
    if 'KCTR' in tracks:
        lines.extend(_write_track_lines('Translation', tracks['KCTR'], 'f32x3'))
    if 'KCRL' in tracks:
        lines.extend(_write_track_lines('Rotation', tracks['KCRL'], 'f32'))
    lines.append(f'\tFieldOfView {_fmt_num(camera.get("fieldOfView", 0.0))},')
    lines.append(f'\tFarClip {_fmt_num(camera.get("farClippingPlane", 0.0))},')
    lines.append(f'\tNearClip {_fmt_num(camera.get("nearClippingPlane", 0.0))},')
    lines.append('\tTarget {')
    lines.append(f'\t\tPosition {{ {_fmt_floats(camera.get("targetPosition") or [0, 0, 0])} }},')
    if 'KTTR' in tracks:
        lines.extend(_write_track_lines('Translation', tracks['KTTR'], 'f32x3', indent=2))
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
