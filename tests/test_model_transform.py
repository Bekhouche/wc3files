"""wc3files.mdl.transform (#235): a whole model scaled or moved, checked on
small models built here by posing them before and after."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import bone, track, walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import node_matrices, posed_vertices
from wc3files.mdl.extents import compute_extents
from wc3files.mdl.transform import scale_model, translate_model

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def poses(model):
    for s in model['sequences']:
        a, b = s['interval']
        for frame in (a, (a + b) // 2, b):
            yield from posed_vertices(model['geosets'][0], node_matrices(model, frame, (a, b)))


def rich_cube():
    """The walking cube with a rotating child bone, a camera, a collision
    sphere, a particle emitter and a light."""
    model = walking_cube()
    half = 0.7071067811865476
    model['bones'].append(bone(1, {'KGRT': track([(0, [0.0, 0.0, 0.0, 1.0]),
                                                  (1000, [0.0, 0.0, half, half])])}, parent=0))
    model['pivotPoints'].append([5.0, 0.0, 0.0])
    model['geosets'][0]['matrixIndices'] = [1]
    model['cameras'] = [{'name': 'c', 'position': [100.0, 0.0, 50.0], 'fieldOfView': 0.8,
                         'farClippingPlane': 1000.0, 'nearClippingPlane': 8.0,
                         'targetPosition': [0.0, 0.0, 20.0], 'tracks': {}}]
    model['collisionShapes'] = [{'node': {'name': 's', 'objectId': 2, 'parentId': 0xFFFFFFFF,
                                          'flags': 8192, 'tracks': {}},
                                 'type': 2, 'vertices': [[0.0, 0.0, 10.0]], 'radius': 30.0}]
    model['pivotPoints'].append([0.0, 0.0, 0.0])
    model['particleEmitters2'] = [{'node': {'name': 'p', 'objectId': 3, 'parentId': 0xFFFFFFFF,
                                            'flags': 0, 'tracks': {}},
                                   'speed': 20.0, 'gravity': 5.0, 'length': 10.0, 'width': 4.0,
                                   'segmentScaling': [1.0, 2.0, 3.0], 'lifespan': 1.5,
                                   'tracks': {'KP2S': track([(0, 20.0), (500, 40.0)])}}]
    model['pivotPoints'].append([0.0, 0.0, 0.0])
    model['lights'] = [{'node': {'name': 'l', 'objectId': 4, 'parentId': 0xFFFFFFFF,
                                 'flags': 512, 'tracks': {}},
                        'attenuationStart': 80.0, 'attenuationEnd': 200.0, 'tracks': {}}]
    model['pivotPoints'].append([0.0, 0.0, 0.0])
    model['sequences'][1]['moveSpeed'] = 270.0
    compute_extents(model)
    return model


class ScaleTest(unittest.TestCase):

    def test_posed_vertices_scale_with_the_model(self):
        model = rich_cube()
        before = list(poses(model))
        scale_model(model, 2.0)
        for p, q in zip(before, poses(model)):
            for a, b in zip(p, q):
                self.assertAlmostEqual(a * 2.0, b, places=9)

    def test_lengths_scale_and_times_do_not(self):
        model = rich_cube()
        before = model['extent']['maximum'][0]
        scale_model(model, 2.0)
        self.assertEqual(model['cameras'][0]['position'], [200.0, 0.0, 100.0])
        self.assertEqual(model['cameras'][0]['nearClippingPlane'], 16.0)
        self.assertEqual(model['collisionShapes'][0]['radius'], 60.0)
        emitter = model['particleEmitters2'][0]
        self.assertEqual((emitter['speed'], emitter['segmentScaling'], emitter['lifespan']),
                         (40.0, [2.0, 4.0, 6.0], 1.5))
        self.assertEqual(emitter['tracks']['KP2S']['keys'][1]['value'], 80.0)
        self.assertEqual(model['lights'][0]['attenuationEnd'], 400.0)
        self.assertEqual(model['sequences'][1]['moveSpeed'], 540.0)
        self.assertEqual(model['extent']['maximum'][0], before * 2.0)
        # Rotation keys are not lengths.
        self.assertEqual(model['bones'][1]['node']['tracks']['KGRT']['keys'][0]['value'],
                         [0.0, 0.0, 0.0, 1.0])

    def test_the_factor_must_be_positive(self):
        for factor in (0, -1.0):
            with self.assertRaises(ValueError):
                scale_model(rich_cube(), factor)


class TranslateTest(unittest.TestCase):

    def test_posed_vertices_move_with_the_model(self):
        model = rich_cube()
        before = list(poses(model))
        lowest = model['extent']['minimum']
        translate_model(model, (100.0, -50.0, 25.0))
        for p, q in zip(before, poses(model)):
            self.assertAlmostEqual(p[0] + 100.0, q[0], places=9)
            self.assertAlmostEqual(p[1] - 50.0, q[1], places=9)
            self.assertAlmostEqual(p[2] + 25.0, q[2], places=9)
        self.assertEqual(model['cameras'][0]['targetPosition'], [100.0, -50.0, 45.0])
        self.assertEqual(model['collisionShapes'][0]['vertices'], [[100.0, -50.0, 35.0]])
        self.assertEqual(model['extent']['minimum'],
                         [lowest[0] + 100.0, lowest[1] - 50.0, lowest[2] + 25.0])
        # Translation keys are offsets from the pivot.
        self.assertEqual(model['bones'][0]['node']['tracks']['KGTR']['keys'][1]['value'],
                         [100.0, 0.0, 0.0])

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'cube.mdl')
        ModelFile().import_data(rich_cube()).write(path)
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))
        for args in (['--scale', '2'], ['--translate', '0,0,10']):
            result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-transform',
                                     path, '--in-place', *args], capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        model = ModelFile()
        model.read(path)
        self.assertAlmostEqual(model.get_data()['extent']['maximum'][2], 30.0, places=4)


if __name__ == '__main__':
    unittest.main()
