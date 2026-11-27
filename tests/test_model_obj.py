"""wc3files.mdl.obj (#244): OBJ and MTL to a static MDX, and a model to OBJ
at a frame of a sequence, read back with ModelFile."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.mdx_io import load_mdx, save_mdx
from wc3files.mdl.obj import model_to_obj, obj_to_model, read_mtl, read_obj

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = chr(92)

# A unit square (a quad) in the XZ plane facing up (Y), and a triangle
# above it with negative indices and its own material; no normals.
OBJ = """# test
mtllib box.mtl
v 0 0 0
v 1 0 0
v 1 0 1
v 0 0 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
o floor
usemtl Ground
f 1/1 4/4 3/3 2/2
v 0 2 0
v 1 2 0
v 0 2 1
o roof
usemtl Roof
f -3 -2 -1
"""
MTL = """newmtl Ground
Kd 1 1 1
map_Kd -s 1 1 1 textures/ground.png
newmtl Roof
"""


class ObjTest(unittest.TestCase):

    def test_read(self):
        obj = read_obj(OBJ)
        self.assertEqual(len(obj['positions']), 7)
        self.assertEqual(obj['faces'][1][2], [(4, None, None), (5, None, None), (6, None, None)])
        self.assertEqual(obj['libraries'], ['box.mtl'])
        self.assertEqual(read_mtl(MTL), {'Ground': 'textures/ground.png', 'Roof': None})

    def test_to_model(self):
        model = obj_to_model(OBJ, read_mtl(MTL))
        floor, roof = model['geosets']
        # The quad is two triangles, as a fan from its first corner.
        self.assertEqual(floor['faces'], [0, 1, 2, 0, 2, 3])
        # Y up becomes Z up: (1, 0, 1) is (1, -1, 0).
        self.assertEqual(floor['vertices'][6:9], [1.0, -1.0, 0.0])
        self.assertEqual(roof['vertices'][2], 2.0)
        # V flips; the roof had no texture coordinates.
        self.assertEqual(floor['uvSets'][0][4:6], [1.0, 0.0])
        self.assertEqual(roof['uvSets'][0], [0.0, 1.0] * 3)
        # No normals were given: they come from the faces, the floor up.
        self.assertEqual([round(v, 6) for v in floor['normals'][:3]], [0.0, 0.0, 1.0])
        self.assertEqual([t['path'] for t in model['textures']],
                         ['textures' + BS + 'ground.blp', 'Textures' + BS + 'White.blp'])
        self.assertEqual(model['extent']['maximum'], [1.0, 0.0, 2.0])
        self.assertEqual(model['sequences'][0]['name'], 'Stand')
        read = load_mdx(save_mdx(model))
        self.assertEqual(len(read['geosets']), 2)

    def test_options_and_errors(self):
        model = obj_to_model(OBJ, up='z', scale=2.0, texture_dir='war3mapImported')
        self.assertEqual(model['geosets'][0]['vertices'][6:9], [2.0, 0.0, 2.0])
        self.assertEqual(model['textures'][0]['path'], 'Textures' + BS + 'White.blp')
        model = obj_to_model(OBJ, read_mtl(MTL), texture_dir='war3mapImported')
        self.assertEqual(model['textures'][0]['path'], 'war3mapImported' + BS + 'ground.blp')
        for text in ('v 0 0 0\n', 'v 0 0 0\nf 1 2 3\n'):
            with self.assertRaises(ValueError):
                obj_to_model(text)

    def test_model_to_obj_at_a_frame(self):
        model = walking_cube()
        rest, _ = model_to_obj(model, up='z')
        walked, mtl = model_to_obj(model, 'walk', 1000, up='z')
        self.assertIn('v -10 -10 -10', rest)
        # At the end of Walk the bone has moved 100 along x.
        self.assertIn('v 90 -10 -10', walked)
        # The cube has no material, so the MTL has none either.
        self.assertNotIn('newmtl', mtl)
        with self.assertRaises(ValueError):
            model_to_obj(model, 'Fly')
        back = obj_to_model(rest, up='z')
        self.assertEqual(len(back['geosets'][0]['faces']), len(model['geosets'][0]['faces']))

    def test_cli_both_ways(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(os.path.join(tmp, 'box.obj'), 'w') as handle:
            handle.write(OBJ)
        with open(os.path.join(tmp, 'box.mtl'), 'w') as handle:
            handle.write(MTL)
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))
        for args in (['box.obj', 'box.mdx'], ['box.mdx', 'again.obj']):
            result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-obj',
                                     *[os.path.join(tmp, a) for a in args]],
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        model = ModelFile()
        model.read(os.path.join(tmp, 'box.mdx'))
        self.assertEqual(len(model.get_data()['geosets']), 2)
        self.assertTrue(os.path.exists(os.path.join(tmp, 'again.mtl')))


if __name__ == '__main__':
    unittest.main()
