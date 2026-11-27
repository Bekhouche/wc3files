"""Real tests for wc3files.mdl: MDL (text) and MDX (binary) both round-trip
through the same shared model IR, cross-checked against each other."""
import os
import tempfile
import unittest

import _helpers
from wc3files import ModelFile
from wc3files.mdl.mdx_io import empty_model, save_mdx, load_mdx
from wc3files.mdl.mdl_io import save_mdl, load_mdl


class TestModelRoundTrip(unittest.TestCase):
    def _load(self, name):
        model = ModelFile()
        model.read(_helpers.sample_path(name))
        return model

    def test_mdl_known_fields(self):
        model = self._load('sample.mdl')
        self.assertEqual(model.name, 'SampleModel')
        self.assertEqual(len(model.sequences), 1)
        self.assertEqual(model.sequences[0]['name'], 'Stand')

    def test_mdx_known_fields(self):
        model = self._load('sample.mdx')
        self.assertEqual(model.name, 'SampleModel')
        self.assertEqual(len(model.sequences), 1)
        self.assertEqual(model.sequences[0]['name'], 'Stand')

    def test_mdl_and_mdx_agree(self):
        mdl_data = self._load('sample.mdl').export_data()
        mdx_data = self._load('sample.mdx').export_data()
        self.assertEqual(mdl_data['name'], mdx_data['name'])
        self.assertEqual(mdl_data['sequences'], mdx_data['sequences'])

    def test_mdl_round_trip(self):
        model = self._load('sample.mdl')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.mdl')
            model.write(out)
            reread = ModelFile()
            reread.read(out)
            self.assertEqual(reread.export_data()['name'], model.export_data()['name'])
            self.assertEqual(reread.export_data()['sequences'], model.export_data()['sequences'])

    def test_mdx_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.mdx'))
        model = self._load('sample.mdx')
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.mdx')
            model.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)


class TestBonesAndMultiGroupGeosets(unittest.TestCase):
    """Regression coverage for two real bugs found during a capability audit
    (see TODO.md): mdl_io.py's text writer dropped BillboardedLockX/Y/Z and
    CameraAnchored node flags (only Billboarded survived), and collapsed a
    geoset's multiple face groups into one on write (and, worse, on read too
    - a real multi-Triangles-block .mdl file lost every group but the last)."""

    def _build_model(self):
        model = empty_model()
        model['name'] = 'FlagTest'
        model['bones'] = [{
            'node': {'name': 'Bone1', 'objectId': 0, 'parentId': 0xFFFFFFFF,
                     'flags': 0x1f8, 'tracks': {}},
            'geosetId': 0xFFFFFFFF, 'geosetAnimationId': 0xFFFFFFFF,
        }]
        model['geosets'] = [{
            'vertices': [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 0, 1],
            'normals': [0, 0, 1] * 6,
            'uvSets': [[0, 0, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0]],
            'vertexGroups': [0, 0, 0, 0, 0, 0],
            'faces': [0, 1, 2, 3, 4, 5],
            'faceGroups': [3, 3],
            'faceTypeGroups': [4, 4],
            'matrixGroups': [1], 'matrixIndices': [0],
            'materialId': 0,
            'extent': {'boundsRadius': 1.0, 'minimum': [0, 0, 0], 'maximum': [1, 1, 1]},
        }]
        return model

    def test_mdl_preserves_all_bone_flag_bits(self):
        model = self._build_model()
        reread = load_mdl(save_mdl(model))
        self.assertEqual(reread['bones'][0]['node']['flags'], 0x1f8)

    def test_mdx_preserves_all_bone_flag_bits(self):
        model = self._build_model()
        reread = load_mdx(save_mdx(model))
        self.assertEqual(reread['bones'][0]['node']['flags'], 0x1f8)

    def test_mdl_preserves_multiple_face_groups(self):
        model = self._build_model()
        reread = load_mdl(save_mdl(model))
        self.assertEqual(reread['geosets'][0]['faceGroups'], [3, 3])
        self.assertEqual(reread['geosets'][0]['faces'], [0, 1, 2, 3, 4, 5])

    def test_mdx_preserves_multiple_face_groups(self):
        model = self._build_model()
        reread = load_mdx(save_mdx(model))
        self.assertEqual(reread['geosets'][0]['faceGroups'], [3, 3])
        self.assertEqual(reread['geosets'][0]['faces'], [0, 1, 2, 3, 4, 5])


def _chunks(data):
    import struct
    out, pos = [], 4
    while pos + 8 <= len(data):
        size = struct.unpack('<I', data[pos + 4:pos + 8])[0]
        out.append((data[pos:pos + 4], data[pos + 8:pos + 8 + size]))
        pos += 8 + size
    return out


def _join(chunks):
    import struct
    return b'MDLX' + b''.join(tag + struct.pack('<I', len(body)) + body for tag, body in chunks)


def _node(name, object_id):
    return {'name': name, 'objectId': object_id, 'parentId': 0xFFFFFFFF, 'flags': 0,
            'tracks': {}}


def _track(*values):
    return {'interpolationType': 1, 'globalSequenceId': 0xFFFFFFFF,
            'keys': [{'frame': i * 100, 'value': v} for i, v in enumerate(values)]}


class TestByteExactRewrites(unittest.TestCase):
    """An unedited model writes back to its own bytes. Each case here broke
    that for real files: the game's 15,090 models and 4,780 from local maps
    (19,870 in all) read and rewrite exactly; before these fixes 1,657 of
    3,756 distinct map models did not, some of them losing data."""

    def assertExact(self, data):
        self.assertEqual(save_mdx(load_mdx(data)), data)

    def test_chunk_order_and_empty_chunks_are_kept(self):
        model = empty_model()
        model['globalSequences'] = [1000]
        model['pivotPoints'] = [[0.0, 0.0, 0.0]]
        model['cameras'] = [{'name': 'Cam', 'position': [0.0] * 3, 'fieldOfView': 0.9,
                             'farClippingPlane': 1000.0, 'nearClippingPlane': 8.0,
                             'targetPosition': [1.0, 0.0, 0.0], 'tracks': {}}]
        model['eventObjects'] = [{'node': _node('SNDxHERO', 0), 'globalSequenceId': 0xFFFFFFFF,
                                  'tracks': [10]}]
        data = save_mdx(model)
        chunks = _chunks(data)
        self.assertGreater(len(chunks), 4)
        # another tool's order, plus an empty PREM chunk (seen in real files)
        reordered = chunks[:2] + chunks[2:][::-1] + [(b'PREM', b'')]
        self.assertExact(_join(reordered))
        # a model built in code still gets the default order
        model = load_mdx(data)
        del model['chunkOrder']
        self.assertEqual([t for t, _ in _chunks(save_mdx(model))], [t for t, _ in chunks])

    def test_light_shadow_intensity(self):
        for version, shadow in ((1000, None), (1000, 0.4), (1200, 0.4)):
            with self.subTest(version=version, shadow=shadow):
                light = {'node': _node('Omni01', 0), 'type': 0, 'attenuationStart': 80.0,
                         'attenuationEnd': 200.0, 'color': [1.0, 1.0, 1.0], 'intensity': 8.0,
                         'ambientColor': [1.0, 1.0, 1.0], 'ambientIntensity': 0.0,
                         'tracks': {'KLAV': _track(1.0, 0.0)}}
                if shadow is not None:
                    light['shadowIntensity'] = shadow
                model = empty_model()
                model['version'] = version
                model['lights'] = [light]
                data = save_mdx(model)
                read = load_mdx(data)['lights'][0]
                # the visibility track used to be lost behind the float
                self.assertIn('KLAV', read['tracks'])
                self.assertEqual('shadowIntensity' in read, shadow is not None)
                self.assertExact(data)

    def test_camera_size_high_byte_and_cinematic_tracks(self):
        import struct
        model = empty_model()
        model['version'] = 1800
        model['cameras'] = [
            {'name': f'CAM_{i}', 'position': [0.0, 0.0, 0.0], 'fieldOfView': 0.9,
             'farClippingPlane': 10000.0, 'nearClippingPlane': 8.0,
             'targetPosition': [1.0, 0.0, 0.0],
             'tracks': {'KCRL': _track(0.0, 1.0), 'IDUF': _track(509.9),
                        'ELAF': _track(35.0), 'PTSF': _track(2.8)}}
            for i in range(2)]
        data = save_mdx(model)
        chunks = _chunks(data)
        index = [t for t, _ in chunks].index(b'CAMS')
        body = bytearray(chunks[index][1])
        # the game's cinematic cameras have 0x03 in each size's high byte
        first = struct.unpack('<I', body[:4])[0]
        body[3] = 0x03
        body[first + 3] = 0x03
        chunks[index] = (b'CAMS', bytes(body))
        patched = _join(chunks)
        cameras = load_mdx(patched)['cameras']
        self.assertEqual([c['name'] for c in cameras], ['CAM_0', 'CAM_1'])
        self.assertEqual(sorted(cameras[1]['tracks']), ['ELAF', 'IDUF', 'KCRL', 'PTSF'])
        self.assertExact(patched)

    def test_skin_count_unit_is_kept(self):
        vertex_count = 3
        skin = [0] * (vertex_count * 16)
        for version, unit in ((1000, 1), (1000, 2), (1800, 2)):
            with self.subTest(version=version, unit=unit):
                model = empty_model()
                model['version'] = version
                model['geosets'] = [{
                    'vertices': [0.0] * (vertex_count * 3), 'normals': [0.0] * (vertex_count * 3),
                    'faceTypeGroups': [4], 'faceGroups': [3], 'faces': [0, 1, 2],
                    'vertexGroups': [0] * vertex_count, 'matrixGroups': [1],
                    'matrixIndices': [0], 'materialId': 0, 'selectionGroup': 0,
                    'selectionFlags': 0, 'levelOfDetail': 0, 'name': 'geo',
                    'extent': {'bounds': 0.0, 'minimum': [0.0] * 3, 'maximum': [0.0] * 3},
                    'sequenceExtents': [], 'skin': skin, 'skinCountUnit': unit,
                    'uvSets': [[0.0] * (vertex_count * 2)]}]
                data = save_mdx(model)
                geoset = load_mdx(data)['geosets'][0]
                self.assertEqual(geoset['skinCountUnit'], unit)
                self.assertEqual(len(geoset['skin']), len(skin))
                self.assertExact(data)

    def test_short_collision_shape_is_kept(self):
        import struct
        # one exporter writes a type-3 shape (needs 28 bytes) with 8
        node = bytearray(96)
        node[0:4] = struct.pack('<I', 96)
        node[4:13] = b'Plane_Col'
        node[84:96] = struct.pack('<IiI', 1, 0, 0x2000)
        clid = bytes(node) + struct.pack('<I', 3) + struct.pack('<2f', 100.0, 100.0)
        model = empty_model()
        chunks = _chunks(save_mdx(model)) + [(b'CLID', clid)]
        data = _join(chunks)
        shape = load_mdx(data)['collisionShapes'][0]
        self.assertEqual(shape['type'], 3)
        self.assertIn('unparsedTail', shape)
        self.assertExact(data)


if __name__ == '__main__':
    unittest.main()


class TestReforgedMdxFields(unittest.TestCase):
    """Fields that only appear in Reforged-era (version > 800) models.

    All three bugs covered here were found by pointing the new CASC reader at
    the retail game's own 14,984 models - nothing had ever fed a v1800 file to
    this parser before, so they sat unnoticed. Each is pinned here with a
    synthetic model so the suite catches a regression without needing a game
    install; the real-install figures are in TODO.md.
    """

    def _round_trip(self, model):
        data = save_mdx(model)
        return load_mdx(data)

    def test_material_shader_is_written_for_versions_900_and_1000(self):
        # The 80-byte shader string exists in v900 (the Reforged beta format)
        # and is gone again by v1200. Measured on real models: in a
        # version-1000 model LAYS sits 92 bytes into the material - 12 bytes
        # of size, priorityPlane and flags, plus the 80-byte shader - while
        # in version 1200 and in retail's version-1800 models it sits at 12.
        # Reading it whenever version > 800 eats 80 bytes of layer data;
        # reading it for 900 alone cost 48 of the models sampled from the
        # benchmark corpus.
        for version, expect_shader in ((900, True), (1000, True), (1200, False), (1800, False)):
            with self.subTest(version=version):
                model = empty_model()
                model['version'] = version
                model['materials'] = [{
                    'priorityPlane': 0, 'flags': 0, 'shader': 'Shader_HD',
                    'layers': [{
                        'filterMode': 0, 'shadingFlags': 0, 'textureId': 0,
                        'textureAnimationId': 0xFFFFFFFF, 'coordId': 0,
                        'alpha': 1.0, 'tracks': {},
                    }],
                }]
                result = self._round_trip(model)
                material = result['materials'][0]
                self.assertEqual(len(material['layers']), 1)
                if expect_shader:
                    self.assertEqual(material['shader'], 'Shader_HD')
                else:
                    self.assertNotIn('shader', material)

    def test_geoset_skin_count_is_influences_not_bytes(self):
        # SKIN's count is a number of bone influences, each contributing two
        # bytes (index + weight): 8 indices then 8 weights per vertex. Reading
        # `count` bytes consumed exactly half the chunk and left the stream
        # mid-data, which is why every cinematic model died on "Expected UVAS".
        vertex_count = 3
        skin = []
        for i in range(vertex_count):
            skin += [i, 0, 0, 0, 0, 0, 0, 0]      # 8 bone indices
            skin += [255, 0, 0, 0, 0, 0, 0, 0]    # 8 weights
        self.assertEqual(len(skin), vertex_count * 16)

        model = empty_model()
        model['version'] = 1800
        model['geosets'] = [{
            'vertices': [0.0] * (vertex_count * 3),
            'normals': [0.0] * (vertex_count * 3),
            'faceTypeGroups': [4], 'faceGroups': [3], 'faces': [0, 1, 2],
            'vertexGroups': [0] * vertex_count,
            'matrixGroups': [1], 'matrixIndices': [0],
            'materialId': 0, 'selectionGroup': 0, 'selectionFlags': 0,
            'levelOfDetail': 0, 'name': 'geo',
            'extent': {'bounds': 0.0, 'minimum': [0.0] * 3, 'maximum': [0.0] * 3},
            'sequenceExtents': [],
            'skin': skin,
            'uvSets': [[0.0] * (vertex_count * 2)],
        }]

        result = self._round_trip(model)
        geoset = result['geosets'][0]
        # The whole payload must survive, and the UV set after it must still
        # be found - that is the part the old reader could not reach.
        self.assertEqual(list(geoset['skin']), skin)
        self.assertEqual(len(geoset['uvSets']), 1)
        self.assertEqual(len(geoset['uvSets'][0]), vertex_count * 2)

    def test_face_effects_entries_are_340_bytes_each(self):
        # An entry is an 80-byte target plus a 260-byte path. The reader
        # divided the chunk size by 380, so entries were silently dropped -
        # a real 5440-byte chunk (16 entries) read back as 14, and rewriting
        # those 14 produced a chunk that read back as 12.
        model = empty_model()
        model['version'] = 1800
        model['faceEffects'] = [
            {'target': 'Node', 'path': f'Units/Human/Unit{i}/Unit{i}.facefx'}
            for i in range(5)
        ]
        result = self._round_trip(model)
        self.assertEqual(len(result['faceEffects']), 5)
        self.assertEqual(result['faceEffects'][3]['target'], 'Node')
        self.assertEqual(result['faceEffects'][3]['path'],
                         'Units/Human/Unit3/Unit3.facefx')

        # And the count must survive a second trip - the old divisor made each
        # rewrite lose more entries than the last.
        again = self._round_trip(result)
        self.assertEqual(len(again['faceEffects']), 5)


class TestCommentMarkersInStrings(unittest.TestCase):
    """The tokenizer cut each line at `//` before looking at quotes, so a
    string holding a URL lost its closing quote and the parser read on into
    the next block ("Unexpected end of MDL"). A real map's model has the
    attachment path "THIS MODEL WAS RIPPED BY INICO. PM? (http://...)"."""

    PATH = 'RIPPED BY SOMEONE (http://example.org/u1)'

    def test_tokens(self):
        from wc3files.mdl.mdl_io import MdlTokenizer
        tokens = MdlTokenizer('Path "see http://x.y/z",\nAttachmentID 8, // a comment\n')._tokens
        self.assertEqual(tokens, ['Path', '"see http://x.y/z"', ',', 'AttachmentID', '8', ','])

    def test_an_attachment_path_with_a_url(self):
        with open(_helpers.sample_path('sample.mdl'), encoding='utf-8') as handle:
            text = handle.read()
        text += ('Attachment "README" {\n\tObjectId 9,\n\tAttachmentID 0,\n'
                 '\tPath "' + self.PATH + '",\n}\n')
        model = load_mdl(text)
        self.assertEqual(model['attachments'][0]['path'], self.PATH)
        again = load_mdl(save_mdl(model))
        self.assertEqual(again['attachments'][0]['path'], self.PATH)
        self.assertEqual(save_mdl(again), save_mdl(model))


class TestNanAndInfinityInMdl(unittest.TestCase):
    """Some real models carry a NaN or infinite float (16 in 10 corpus maps).
    save_mdl writes them as nan and inf, and load_mdl then failed on its own
    output: "invalid literal for int() with base 10: 'nan'"."""

    def test_they_read_back(self):
        import math
        with open(_helpers.sample_path('sample.mdx'), 'rb') as handle:
            model = load_mdx(handle.read())
        model['extent']['minimum'] = [float('-inf'), 0.0, 1.5]
        model['extent']['maximum'] = [float('inf'), float('nan'), 2.0]
        text = save_mdl(model)
        again = load_mdl(text)
        self.assertEqual(again['extent']['minimum'], [float('-inf'), 0.0, 1.5])
        self.assertEqual(again['extent']['maximum'][0], float('inf'))
        self.assertTrue(math.isnan(again['extent']['maximum'][1]))
        self.assertEqual(save_mdl(again), text)
        # An infinity is kept exactly in the binary model too.
        self.assertEqual(load_mdx(save_mdx(again))['extent']['maximum'][0], float('inf'))


class TestMdlFloatPrecision(unittest.TestCase):
    """MDL wrote floats with 7 significant digits. A float32 needs up to 9,
    so MDX -> MDL -> MDX changed almost every real model (263.29132 came
    back as 263.29129)."""

    def test_mdx_through_mdl_is_exact(self):
        import struct
        f32 = lambda x: struct.unpack('<f', struct.pack('<f', x))[0]
        with open(_helpers.sample_path('sample.mdx'), 'rb') as handle:
            model = load_mdx(handle.read())
        model['extent']['minimum'] = [f32(-261.061279296875), f32(-277.02838134765625),
                                      f32(-0.003250477835536003)]
        model['extent']['maximum'] = [f32(263.29132080078125), f32(18.17574691772461),
                                      f32(382.4608459472656)]
        model['extent']['boundsRadius'] = f32(0.1)
        original = save_mdx(model)
        self.assertEqual(save_mdx(load_mdl(save_mdl(model))), original)

    def test_short_values_print_as_before(self):
        from wc3files.mdl.mdl_io import _fmt_num
        self.assertEqual(_fmt_num(0.5), '0.5')
        self.assertEqual(_fmt_num(0.341176), '0.341176')
        self.assertEqual(_fmt_num(3.0), '3')


class TestMdlLights(unittest.TestCase):
    """Lights as Reforged's own MDL files write them. The reader read
    `static AttenuationStart 80` as 0 and an animated `Intensity 6 { ... }`
    as a static 6, and the writer wrote only static values, so a light's
    animation was lost both ways."""

    LIGHT = (
        'Light "Key_Light" {\n'
        '\tDirectional,\n'
        '\tstatic AttenuationStart 80,\n'
        '\tstatic AttenuationEnd 200,\n'
        '\tIntensity 3 {\n\t\tLinear,\n\t\t0: 0.7,\n\t\t500: 0.75,\n\t\t1000: 0.9,\n\t}\n'
        '\tColor 2 {\n\t\tLinear,\n\t\t0: { 0.1, 0.2, 0.3 },\n\t\t1000: { 0.4, 0.5, 0.6 },\n\t}\n'
        '\tstatic AmbIntensity 0.5,\n'
        '\tstatic AmbColor { 1, 1, 1 },\n'
        '}\n')

    def model(self):
        with open(_helpers.sample_path('sample.mdl'), encoding='utf-8') as handle:
            return load_mdl(handle.read() + self.LIGHT)

    def test_a_reforged_light_reads(self):
        light = self.model()['lights'][0]
        self.assertEqual(light['type'], 1)
        self.assertEqual((light['attenuationStart'], light['attenuationEnd']), (80.0, 200.0))
        self.assertEqual(light['ambientIntensity'], 0.5)
        self.assertEqual([key['value'] for key in light['tracks']['KLAI']['keys']], [0.7, 0.75, 0.9])
        self.assertEqual(len(light['tracks']['KLAC']['keys']), 2)

    def test_it_survives_mdl_and_mdx(self):
        model = self.model()
        text = save_mdl(model)
        again = load_mdl(text)
        self.assertEqual(again['lights'][0]['type'], 1)
        self.assertEqual(set(again['lights'][0]['tracks']), {'KLAI', 'KLAC'})
        self.assertEqual(save_mdl(again), text)
        binary = save_mdx(again)
        self.assertEqual(save_mdx(load_mdl(save_mdl(load_mdx(binary)))), binary)


class TestMdlKeepsMdxData(unittest.TestCase):
    """Data an MDX model has that MDL text lost on the way through it
    (#110). Each test adds one object to the sample model and checks that
    MDX -> MDL -> MDX gives the same bytes."""

    def model(self, text):
        with open(_helpers.sample_path('sample.mdl'), encoding='utf-8') as handle:
            return load_mdl(handle.read() + text)

    def assertSurvives(self, model):
        binary = save_mdx(model)
        self.assertEqual(save_mdx(load_mdl(save_mdl(load_mdx(binary)))), binary)

    def test_attachment_visibility(self):
        model = self.model(
            'Attachment "Origin Ref" {\n'
            '\tObjectId 5,\n'
            '\tAttachmentID 0,\n'
            '\tVisibility 2 {\n\t\tDontInterp,\n\t\t0: 1,\n\t\t500: 0,\n\t}\n'
            '}\n')
        keys = model['attachments'][0]['tracks']['KATV']['keys']
        self.assertEqual([(key['frame'], key['value']) for key in keys], [(0, 1.0), (500, 0.0)])
        self.assertSurvives(model)

    def test_particle_emitter2_flags(self):
        model = self.model(
            'ParticleEmitter2 "Glow" {\n'
            '\tObjectId 5,\n'
            '\tUnshaded,\n\tSortPrimsFarZ,\n\tLineEmitter,\n'
            '\tUnfogged,\n\tModelSpace,\n\tXYQuad,\n'
            '\tstatic Speed 10,\n'
            '}\n')
        flags = model['particleEmitters2'][0]['node']['flags']
        self.assertEqual(flags, 0x1000 | 0x8000 | 0x10000 | 0x20000 | 0x40000 | 0x80000 | 0x100000)
        self.assertSurvives(model)
        model['particleEmitters2'][0]['node']['flags'] = 0x1000 | 0x40000
        self.assertEqual(load_mdl(save_mdl(model))['particleEmitters2'][0]['node']['flags'],
                         0x1000 | 0x40000)

    def test_particle_emitter_flags(self):
        model = self.model(
            'ParticleEmitter "Smoke" {\n'
            '\tObjectId 5,\n'
            '\tEmitterUsesMDL,\n'
            '\tPath "Abilities\\\\Weapons\\\\Smoke.mdl",\n'
            '}\n')
        self.assertEqual(model['particleEmitters'][0]['node']['flags'], 0x1000 | 0x8000)
        self.assertSurvives(model)
        model['particleEmitters'][0]['node']['flags'] = 0x1000 | 0x10000
        self.assertEqual(load_mdl(save_mdl(model))['particleEmitters'][0]['node']['flags'],
                         0x1000 | 0x10000)

    def test_geoset_animation_flags_and_colour(self):
        model = self.model(
            'GeosetAnim {\n'
            '\tDropShadow,\n'
            '\tstatic Alpha 0.5,\n'
            '\tstatic Color { 1, 0.5, 0 },\n'
            '\tGeosetId 0,\n'
            '}\n')
        anim = model['geosetAnimations'][-1]
        self.assertEqual((anim['flags'], anim['color']), (3, [1.0, 0.5, 0.0]))
        self.assertSurvives(model)
        # The colour flag alone, as on most real models: the colour was
        # written only when bit 1 (DropShadow) was set.
        anim['flags'] = 2
        again = load_mdl(save_mdl(model))['geosetAnimations'][-1]
        self.assertEqual((again['flags'], again['color']), (2, [1.0, 0.5, 0.0]))
        self.assertSurvives(model)

    def test_geoset_animation_unnamed_flags(self):
        model = self.model('GeosetAnim {\n\tstatic Color { 1, 1, 0 },\n\tGeosetId 0,\n}\n')
        model['geosetAnimations'][-1]['flags'] = 26
        text = save_mdl(model)
        self.assertIn('\t// wc3files: UnnamedFlags 24,\n', text)
        self.assertEqual(load_mdl(text)['geosetAnimations'][-1]['flags'], 26)
        self.assertSurvives(model)

    GEOSET = (
        'Geoset {\n'
        '\tVertices 3 {\n\t\t{ 0, 0, 0 },\n\t\t{ 1, 0, 0 },\n\t\t{ 0, 1, 0 },\n\t}\n'
        '\tNormals 3 {\n\t\t{ 0, 0, 1 },\n\t\t{ 0, 0, 1 },\n\t\t{ 0, 0, 1 },\n\t}\n'
        '\tTVertices 3 {\n\t\t{ 0, 0 },\n\t\t{ 1, 0 },\n\t\t{ 0, 1 },\n\t}\n'
        '\tVertexGroup {\n\t\t0,\n\t\t0,\n\t\t0,\n\t}\n'
        '\tFaces 1 3 {\n\t\tTriangles {\n\t\t\t{ 0, 1, 2 },\n\t\t}\n\t}\n'
        '\tGroups 1 1 {\n\t\tMatrices { 0 },\n\t}\n'
        '\tMaterialID 0,\n'
        '\tSelectionGroup 0,\n'
        '}\n')

    def test_geoset_selection_flags(self):
        model = self.model(self.GEOSET)
        model['geosets'][0]['selectionFlags'] = 4
        self.assertIn('\tUnselectable,\n', save_mdl(model))
        self.assertSurvives(model)
        model['geosets'][0]['selectionFlags'] = 4 | 0x20
        self.assertEqual(load_mdl(save_mdl(model))['geosets'][0]['selectionFlags'], 0x24)

    def test_geoset_level_of_detail_and_name(self):
        model = self.model(self.GEOSET)
        model['version'] = 1000
        model['geosets'][0].update(levelOfDetail=2, name='Body LOD2')
        again = load_mdl(save_mdl(model))['geosets'][0]
        self.assertEqual((again['levelOfDetail'], again['name']), (2, 'Body LOD2'))
        binary = save_mdx(model)
        self.assertEqual(load_mdx(binary)['geosets'][0]['name'], 'Body LOD2')

    def test_texture_animations(self):
        model = self.model(
            'TextureAnims 2 {\n'
            '\tTVertexAnim {\n'
            '\t\tTranslation 2 {\n\t\t\tLinear,\n\t\t\t0: { 0, 0, 0 },\n\t\t\t1000: { 1, 0, 0 },\n\t\t}\n'
            '\t}\n'
            '\tTVertexAnim {\n'
            '\t\tScaling 1 {\n\t\t\tDontInterp,\n\t\t\t0: { 2, 2, 1 },\n\t\t}\n'
            '\t\tRotation 1 {\n\t\t\tLinear,\n\t\t\t0: { 0, 0, 0, 1 },\n\t\t}\n'
            '\t}\n'
            '}\n')
        anims = model['textureAnimations']
        self.assertEqual([list(anim['tracks']) for anim in anims], [['KTAT'], ['KTAS', 'KTAR']])
        self.assertSurvives(model)

    def test_camera_tracks(self):
        model = self.model(
            'Camera "Portrait" {\n'
            '\tPosition { 100, 0, 50 },\n'
            '\tTranslation 2 {\n\t\tLinear,\n\t\t0: { 0, 0, 0 },\n\t\t1000: { 10, 0, 0 },\n\t}\n'
            '\tRotation 2 {\n\t\tLinear,\n\t\t0: 0,\n\t\t1000: 0.5,\n\t}\n'
            '\tFieldOfView 0.7,\n'
            '\tFarClip 1000,\n'
            '\tNearClip 8,\n'
            '\tTarget {\n'
            '\t\tPosition { 0, 0, 50 },\n'
            '\t\tTranslation 1 {\n\t\t\tLinear,\n\t\t\t0: { 0, 0, 5 },\n\t\t}\n'
            '\t}\n'
            '}\n')
        camera = model['cameras'][0]
        self.assertEqual(set(camera['tracks']), {'KCTR', 'KCRL', 'KTTR'})
        self.assertEqual(camera['targetPosition'], [0.0, 0.0, 50.0])
        self.assertSurvives(model)

    def test_material_fields_of_version_900_and_later(self):
        model = self.model(
            'Textures 1 {\n\tBitmap {\n\t\tImage "Textures\\\\Hero.blp",\n\t}\n}\n'
            'Materials 1 {\n'
            '\tMaterial {\n'
            '\t\tTwoSided,\n\t\tSortPrimsNearZ,\n'
            '\t\tShader "Shader_SD_FixedFunction",\n'
            '\t\tLayer {\n'
            '\t\t\tFilterMode Blend,\n'
            '\t\t\tUnlit,\n'
            '\t\t\tstatic TextureID 0,\n'
            '\t\t\tstatic EmissiveGain 0.5,\n'
            '\t\t\tstatic FresnelColor { 1, 0, 0 },\n'
            '\t\t\tFresnelOpacity 2 {\n\t\t\t\tLinear,\n\t\t\t\t0: 0,\n\t\t\t\t100: 1,\n\t\t\t}\n'
            '\t\t\tstatic FresnelTeamColor 0.25,\n'
            '\t\t}\n'
            '\t}\n'
            '}\n')
        model['version'] = 1000
        material = model['materials'][0]
        layer = material['layers'][0]
        self.assertEqual((material['flags'], material['shader']), (0x2 | 0x8, 'Shader_SD_FixedFunction'))
        self.assertEqual(layer['shadingFlags'], 0x100)
        self.assertEqual((layer['emissiveGain'], layer['fresnelColor'], layer['fresnelTeamColor']),
                         (0.5, [1.0, 0.0, 0.0], 0.25))
        self.assertEqual(set(layer['tracks']), {'KFCA'})
        self.assertSurvives(model)

    TRACK = ' 2 {\n\t\tLinear,\n\t\t0: %s,\n\t\t500: %s,\n\t}\n'

    def test_particle_emitter2_tracks(self):
        model = self.model(
            'ParticleEmitter2 "Sparks" {\n'
            '\tObjectId 5,\n'
            '\tSpeed' + self.TRACK % (100, 200) +
            '\tstatic Variation 0.5,\n'
            '\tEmissionRate' + self.TRACK % (10, 0) +
            '\tLength' + self.TRACK % (5, 8) +
            '\tVisibility' + self.TRACK % (1, 0) +
            '\tstatic TailLength 2,\n'
            '}\n')
        emitter = model['particleEmitters2'][0]
        self.assertEqual(set(emitter['tracks']), {'KP2S', 'KP2E', 'KP2N', 'KP2V'})
        self.assertEqual((emitter['variation'], emitter['tailLength']), (0.5, 2.0))
        self.assertSurvives(model)

    def test_particle_emitter_tracks(self):
        model = self.model(
            'ParticleEmitter "Smoke" {\n'
            '\tObjectId 5,\n'
            '\tGravity' + self.TRACK % (1, 2) +
            '\tstatic LifeSpan 3,\n'
            '\tVisibility' + self.TRACK % (1, 0) +
            '\tPath "Smoke.mdl",\n'
            '}\n')
        emitter = model['particleEmitters'][0]
        self.assertEqual(set(emitter['tracks']), {'KPEG', 'KPEV'})
        self.assertEqual(emitter['lifespan'], 3.0)
        self.assertSurvives(model)

    def test_ribbon_emitter_tracks(self):
        model = self.model(
            'RibbonEmitter "Trail" {\n'
            '\tObjectId 5,\n'
            '\tstatic HeightAbove 10,\n'
            '\tAlpha' + self.TRACK % (1, 0.5) +
            '\tColor 1 {\n\t\tDontInterp,\n\t\t0: { 0.1, 0.2, 0.3 },\n\t}\n'
            '\tTextureSlot' + self.TRACK % (0, 3) +
            '\tVisibility' + self.TRACK % (1, 0) +
            '\tEmissionRate 20,\n'
            '\tstatic Gravity 4,\n'
            '}\n')
        emitter = model['ribbonEmitters'][0]
        self.assertEqual(set(emitter['tracks']), {'KRAL', 'KRCO', 'KRTX', 'KRVS'})
        self.assertEqual((emitter['heightAbove'], emitter['emissionRate'], emitter['gravity']),
                         (10.0, 20, 4.0))
        self.assertSurvives(model)
