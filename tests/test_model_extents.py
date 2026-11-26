"""wc3files.mdl.animation and wc3files.mdl.extents (#236): tracks sampled
as model viewers do, vertices posed by their bones, and extents recomputed
on small models built here, whose extents are known."""
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import node_matrices, posed_vertices, sample
from wc3files.mdl.extents import EMPTY, compute_extents, is_empty
from wc3files.mdl.mdx_io import empty_model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def cube(half=10.0, offset=(0.0, 0.0, 0.0)):
    vertices = []
    for x in (-half, half):
        for y in (-half, half):
            for z in (-half, half):
                vertices += [x + offset[0], y + offset[1], z + offset[2]]
    return {'vertices': vertices, 'normals': [0.0, 0.0, 1.0] * 8, 'faceTypeGroups': [4],
            'faceGroups': [3], 'faces': [0, 1, 2], 'vertexGroups': [0] * 8,
            'matrixGroups': [1], 'matrixIndices': [0], 'materialId': 0, 'selectionGroup': 0,
            'selectionFlags': 0, 'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3,
                                            'maximum': [0.0] * 3},
            'sequenceExtents': [], 'uvSets': [[0.0, 0.0] * 8]}


def bone(object_id=0, tracks=None, parent=NONE):
    return {'node': {'name': f'b{object_id}', 'objectId': object_id, 'parentId': parent,
                     'flags': 256, 'tracks': tracks or {}},
            'geosetId': NONE, 'geosetAnimationId': NONE}


def track(keys, interpolation=1, global_sequence=NONE):
    return {'interpolationType': interpolation, 'globalSequenceId': global_sequence,
            'keys': [dict(frame=f, value=v) for f, v in keys]}


def sequence(name, start, end):
    return {'name': name, 'interval': [start, end], 'moveSpeed': 0.0, 'flags': 0,
            'rarity': 0.0, 'syncPoint': 0,
            'extent': {'boundsRadius': 0.0, 'minimum': [0.0] * 3, 'maximum': [0.0] * 3}}


def walking_cube():
    """A cube of side 20 on one bone that moves 100 along x in 'Walk'."""
    model = empty_model()
    model['geosets'] = [cube()]
    model['bones'] = [bone(0, {'KGTR': track([(0, [0.0, 0.0, 0.0]), (1000, [100.0, 0.0, 0.0])])})]
    model['pivotPoints'] = [[0.0, 0.0, 0.0]]
    model['sequences'] = [sequence('Stand', 2000, 3000), sequence('Walk', 0, 1000)]
    return model


class SampleTest(unittest.TestCase):

    def test_step_linear_and_out_of_range(self):
        keys = [(0, 0.0), (100, 10.0)]
        self.assertEqual(sample(track(keys, 0), 50), 0.0)
        self.assertEqual(sample(track(keys, 1), 50), 5.0)
        self.assertEqual(sample(track(keys, 1), -5), 0.0)
        self.assertEqual(sample(track(keys, 1), 500), 10.0)

    def test_only_the_keys_inside_the_sequence_count(self):
        t = track([(0, 1.0), (2000, 3.0)])
        self.assertEqual(sample(t, 1200, (1000, 1500), default=7.0), 7.0)
        self.assertEqual(sample(t, 1200, (0, 1500), default=7.0), 1.0)

    def test_global_sequence_runs_on_its_own_clock(self):
        t = track([(0, 0.0), (100, 10.0)], global_sequence=0)
        self.assertAlmostEqual(sample(t, 1050, (1000, 2000), [100]), 5.0)

    def test_hermite_with_flat_tangents(self):
        t = {'interpolationType': 2, 'globalSequenceId': NONE,
             'keys': [{'frame': 0, 'value': 0.0, 'inTan': 0.0, 'outTan': 0.0},
                      {'frame': 100, 'value': 1.0, 'inTan': 0.0, 'outTan': 0.0}]}
        self.assertAlmostEqual(sample(t, 25), 0.15625)

    def test_rotation_slerp(self):
        half = math.sqrt(0.5)
        t = track([(0, [0.0, 0.0, 0.0, 1.0]), (100, [0.0, 0.0, half, half])])
        q = sample(t, 50, rotation=True)
        self.assertAlmostEqual(q[2], math.sin(math.pi / 8))
        self.assertAlmostEqual(q[3], math.cos(math.pi / 8))


class PoseTest(unittest.TestCase):

    def test_a_child_follows_its_parent_about_the_pivot(self):
        half = math.sqrt(0.5)
        model = empty_model()
        model['bones'] = [bone(0, {'KGRT': track([(0, [0.0, 0.0, half, half])])}),
                          bone(1, {'KGTR': track([(0, [0.0, 0.0, 5.0])])}, parent=0)]
        model['pivotPoints'] = [[10.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        matrices = node_matrices(model, 0)
        geoset = {'vertices': [20.0, 0.0, 0.0], 'vertexGroups': [0], 'matrixGroups': [1],
                  'matrixIndices': [1]}
        # 90 degrees about z around (10, 0, 0) takes (20, 0, 0) to (10, 10, 0),
        # after the child's own move of 5 up.
        x, y, z = posed_vertices(geoset, matrices)[0]
        self.assertAlmostEqual(x, 10.0)
        self.assertAlmostEqual(y, 10.0)
        self.assertAlmostEqual(z, 5.0)

    def test_skin_weights(self):
        model = empty_model()
        model['version'] = 1000
        model['bones'] = [bone(0), bone(1, {'KGTR': track([(0, [100.0, 0.0, 0.0])])})]
        model['pivotPoints'] = [[0.0] * 3, [0.0] * 3]
        # One vertex: bones 0 and 1, weights 128 and 127 of 255.
        geoset = {'vertices': [0.0, 0.0, 0.0], 'matrixIndices': [0, 1],
                  'skin': bytes([0, 1, 0, 0, 128, 127, 0, 0])}
        x = posed_vertices(geoset, node_matrices(model, 0))[0][0]
        self.assertAlmostEqual(x, 100.0 * 127 / 255)


class ExtentsTest(unittest.TestCase):

    def test_a_moving_cube(self):
        model = walking_cube()
        compute_extents(model)
        stand, walk = model['sequences']
        self.assertEqual(stand['extent']['minimum'], [-10.0] * 3)
        self.assertEqual(stand['extent']['maximum'], [10.0] * 3)
        self.assertAlmostEqual(stand['extent']['boundsRadius'], math.sqrt(300))
        self.assertEqual(walk['extent']['minimum'], [-10.0] * 3)
        self.assertEqual(walk['extent']['maximum'], [110.0, 10.0, 10.0])
        # The farthest corner from the box's center (50, 0, 0).
        self.assertAlmostEqual(walk['extent']['boundsRadius'], math.sqrt(60 ** 2 + 200))
        geoset = model['geosets'][0]
        self.assertEqual(len(geoset['sequenceExtents']), 2)
        self.assertEqual(geoset['extent']['maximum'], [110.0, 10.0, 10.0])
        self.assertEqual(model['extent']['maximum'], [110.0, 10.0, 10.0])

    def test_diagonal_radius(self):
        model = walking_cube()
        compute_extents(model, radius='diagonal')
        self.assertAlmostEqual(model['sequences'][1]['extent']['boundsRadius'],
                               math.dist([-10.0] * 3, [110.0, 10.0, 10.0]) / 2)
        with self.assertRaises(ValueError):
            compute_extents(model, radius='sphere')

    def test_a_hidden_geoset(self):
        model = walking_cube()
        model['geosets'].append(cube(offset=(0.0, 500.0, 0.0)))
        model['geosetAnimations'] = [{'alpha': 0.0, 'flags': 0, 'color': [1.0] * 3,
                                      'geosetId': 1, 'tracks': {}}]
        compute_extents(model)
        hidden = model['geosets'][1]
        self.assertTrue(all(is_empty(e) for e in hidden['sequenceExtents']))
        self.assertEqual(hidden['sequenceExtents'][0]['minimum'], [EMPTY] * 3)
        # Its own extent takes the rest pose; the model's leaves it out.
        self.assertEqual(hidden['extent']['minimum'], [-10.0, 490.0, -10.0])
        self.assertEqual(model['extent']['maximum'], [110.0, 10.0, 10.0])

    def test_a_model_of_particles_only_keeps_its_extents(self):
        model = empty_model()
        model['extent'] = {'boundsRadius': 50.0, 'minimum': [-5.0] * 3, 'maximum': [5.0] * 3}
        report = compute_extents(model)
        self.assertTrue(report['unchanged'])
        self.assertEqual(model['extent']['boundsRadius'], 50.0)

    def test_cli_over_a_folder(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.makedirs(os.path.join(tmp, 'in', 'sub'))
        source = os.path.join(tmp, 'in', 'sub', 'cube.mdx')
        ModelFile().import_data(walking_cube()).write(source)
        out = os.path.join(tmp, 'out')
        result = subprocess.run(
            [sys.executable, '-m', 'wc3files.cli', 'model-extents', os.path.join(tmp, 'in'),
             '-o', out], capture_output=True, env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        model = ModelFile()
        model.read(os.path.join(out, 'sub', 'cube.mdx'))
        self.assertAlmostEqual(model.get_data()['extent']['maximum'][0], 110.0, places=4)
        self.assertIn(b'1 model(s) edited', result.stdout)

    def test_two_inputs_never_write_the_same_path(self):
        from wc3files.mdl.batch import edit_models
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for folder in ('a', 'b'):
            os.makedirs(os.path.join(tmp, folder))
            ModelFile().import_data(walking_cube()).write(os.path.join(tmp, folder, 'cube.mdx'))
        results = list(edit_models([os.path.join(tmp, 'a'), os.path.join(tmp, 'b')],
                                   compute_extents, os.path.join(tmp, 'out')))
        self.assertIsNotNone(results[0][1])
        self.assertIsNone(results[1][1])
        self.assertIn('already written', str(results[1][2]))


if __name__ == '__main__':
    unittest.main()
