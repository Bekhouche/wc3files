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
