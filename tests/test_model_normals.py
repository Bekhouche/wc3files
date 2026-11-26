"""wc3files.mdl.normals (#237): vertex normals recomputed from the faces,
with seams welded and hard edges kept, on small geosets built here."""
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.normals import geoset_normals, recompute_normals

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def geoset(vertices, faces, normals=None):
    return {'vertices': [float(v) for v in vertices], 'faces': faces,
            'normals': normals or [0.0, 0.0, 0.0] * (len(vertices) // 3)}


def rounded(normals):
    return [round(v, 6) for v in normals]


class NormalsTest(unittest.TestCase):

    def test_a_flat_square_faces_up(self):
        g = geoset([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], [0, 1, 2, 0, 2, 3])
        self.assertEqual(rounded(geoset_normals(g)), [0.0, 0.0, 1.0] * 4)

    def test_a_shared_vertex_averages_by_area(self):
        # A roof: two slopes at 45 degrees share the ridge (vertices 0 and 1);
        # the left slope is twice as long, so it weighs twice as much.
        g = geoset([0, 0, 1, 0, 1, 1, -2, 0, -1, -2, 1, -1, 1, 0, 0, 1, 1, 0],
                   [0, 3, 2, 0, 1, 3, 0, 4, 5, 0, 5, 1])
        n = geoset_normals(g)[:3]
        # Each left triangle adds (-2, 0, 2), each right one (1, 0, 1): their
        # lengths are twice their areas.
        expected = (-1, 0, 3)
        length = math.sqrt(sum(v * v for v in expected))
        for a, b in zip(n, expected):
            self.assertAlmostEqual(a, b / length)

    def test_weld_joins_a_seam_and_keeps_a_hard_edge(self):
        # Two squares meeting at x = 1, each with its own copy of the edge.
        flat = geoset([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
                       1, 0, 0, 2, 0, 0.2, 2, 1, 0.2, 1, 1, 0],
                      [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7])
        apart = geoset_normals(flat)
        welded = geoset_normals(flat, angle=30.0, weld=True)
        # Without weld, the copies of the edge differ; welded, they match.
        self.assertNotEqual(rounded(apart[3:6]), rounded(apart[12:15]))
        self.assertEqual(rounded(welded[3:6]), rounded(welded[12:15]))
        # At a 90-degree fold, a 30-degree weld keeps the edge hard.
        fold = geoset([0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0,
                       1, 0, 0, 1, 0, 1, 1, 1, 1, 1, 1, 0],
                      [0, 1, 2, 0, 2, 3, 4, 7, 6, 4, 6, 5])
        hard = geoset_normals(fold, angle=30.0, weld=True)
        self.assertEqual(rounded(hard[3:6]), [0.0, 0.0, 1.0])
        self.assertEqual(rounded(hard[12:15]), [1.0, 0.0, 0.0])

    def test_a_vertex_in_no_face_keeps_its_normal(self):
        g = geoset([0, 0, 0, 1, 0, 0, 1, 1, 0, 5, 5, 5], [0, 1, 2],
                   [0.0, 0.0, 0.0] * 3 + [0.0, 1.0, 0.0])
        self.assertEqual(geoset_normals(g)[9:], [0.0, 1.0, 0.0])
        model = {'geosets': [g]}
        self.assertEqual(recompute_normals(model), {'vertices': 3, 'kept': 1})

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'cube.mdx')
        model = walking_cube()
        g = model['geosets'][0]
        # Two faces of the cube's bottom (z = -10): vertices 0, 2, 4, 6.
        g['faces'] = [0, 2, 6, 0, 6, 4]
        g['faceGroups'] = [6]
        ModelFile().import_data(model).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-normals', path,
                                 '--in-place'], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        model = ModelFile()
        model.read(path)
        normals = model.get_data()['geosets'][0]['normals']
        self.assertEqual(rounded(normals[0:3]), [0.0, 0.0, -1.0])


if __name__ == '__main__':
    unittest.main()
