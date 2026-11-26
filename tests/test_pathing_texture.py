"""wc3files.wpm.texture and `pathing-texture` (#262): pathing textures
read from TGA images built here with the game's colors, written back, and
made from the footprints of small models built here."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.img import TgaFile
from wc3files.wpm.texture import (NO_BUILD, NO_FLY, NO_WALK, SOLID, PathingTexture,
                                  decode_flags, encode_flags, from_model)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _square(x0, y0, x1, y1, z=0.0):
    """A model of one rectangle on the ground, two triangles."""
    return {'geosets': [{'vertices': [x0, y0, z, x1, y0, z, x1, y1, z, x0, y1, z],
                         'faces': [0, 1, 2, 0, 2, 3]}]}


class PathingTextureTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def tga(self, width, height, pixels):
        path = os.path.join(self.tmp, 'in.tga')
        rgba = b''.join(bytes(p) for p in pixels)
        with open(path, 'wb') as handle:
            handle.write(TgaFile.from_rgba(width, height, rgba).to_bytes())
        return path

    def test_flags(self):
        self.assertEqual(encode_flags({'no_walk': True, 'no_build': True}), SOLID)
        self.assertEqual(decode_flags(NO_FLY), {'no_walk': False, 'no_fly': True,
                                                'no_build': False})

    def test_the_games_colors_read_as_flags(self):
        # Blue, magenta, white and black, as the game's Unbuildable, SimpleSolid,
        # Unflyable and open cells; 254 counts as set and 75 as not; alpha is ignored.
        path = self.tga(3, 2, [(0, 0, 255, 0), (255, 0, 255, 255), (255, 255, 255, 255),
                               (0, 0, 0, 255), (254, 0, 254, 0), (75, 0, 255, 255)])
        texture = PathingTexture().read(path)
        self.assertEqual(texture.cells, [[NO_BUILD, SOLID, NO_WALK | NO_FLY | NO_BUILD],
                                         [0, SOLID, NO_BUILD]])
        self.assertEqual(texture.get_data()['rows'], ['457', '054'])

    def test_write_and_read_back(self):
        texture = PathingTexture().import_data({'rows': ['4444', '4554', '4554', '4444']})
        path = os.path.join(self.tmp, 'out.tga')
        texture.write(path)
        self.assertEqual(PathingTexture().read(path).cells, texture.cells)
        image = TgaFile()
        image.read(path)
        self.assertEqual(image.get_rgba()[:8], bytes((0, 0, 255, 255, 0, 0, 255, 255)))

    def test_rows_must_be_digits_of_one_length(self):
        with self.assertRaises(ValueError):
            PathingTexture().import_data({'rows': ['44', '4']})
        with self.assertRaises(ValueError):
            PathingTexture().import_data({'rows': ['48']})

    def test_a_footprint_covers_the_cells_under_the_model(self):
        # 128 x 128 units around the origin: the middle 4 x 4 cells of 8 x 8.
        texture = from_model(_square(-64, -64, 64, 64), 8, 8)
        self.assertEqual(texture.get_data()['rows'],
                         ['00000000', '00000000', '00555500', '00555500',
                          '00555500', '00555500', '00000000', '00000000'])

    def test_north_is_the_first_row(self):
        # The texture's top edge is y = 128: a square from y = 64 to 128 is
        # its first two rows.
        texture = from_model(_square(-32, 64, 32, 128), 4, 8, snap=1)
        self.assertEqual(texture.get_data()['rows'][:2], ['0550', '0550'])
        self.assertEqual(set(texture.get_data()['rows'][2:]), {'0000'})

    def test_snapping_takes_blocks_at_least_half_covered(self):
        # Three cells wide from the left edge: a 2-cell block fully covered,
        # the next only half.
        model = _square(-128, -32, -32, 32)
        self.assertEqual(from_model(model, 8, 2, snap=1).get_data()['rows'],
                         ['55500000', '55500000'])
        self.assertEqual(from_model(model, 8, 2).get_data()['rows'],
                         ['55550000', '55550000'])

    def test_border_and_max_height(self):
        texture = from_model(_square(-32, -32, 32, 32), 6, 6, border=1, snap=1)
        self.assertEqual(texture.get_data()['rows'],
                         ['000000', '044440', '045540', '045540', '044440', '000000'])
        raised = from_model(_square(-32, -32, 32, 32, z=200.0), 6, 6, max_height=64)
        self.assertEqual(set(raised.get_data()['rows']), {'000000'})

    def test_cli_make_show_and_from_model(self):
        rows = os.path.join(self.tmp, 'rows.txt')
        with open(rows, 'w') as handle:
            handle.write('4444\n4554\n4554\n4444\n')
        out = os.path.join(self.tmp, 'made.tga')
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))

        def run(*args):
            return subprocess.run([sys.executable, '-m', 'wc3files.cli', 'pathing-texture',
                                   *args], capture_output=True, check=True, env=env
                                  ).stdout.decode()

        run('make', rows, out)
        self.assertEqual(run('show', out).split(), ['4444', '4554', '4554', '4444'])
        model = os.path.join(self.tmp, 'square.mdl')
        with open(model, 'w') as handle:
            handle.write('Version {\n\tFormatVersion 800,\n}\n'
                         'Model "Square" {\n\tBlendTime 150,\n}\n'
                         'Geoset {\n\tVertices 4 {\n\t\t{ -64, -64, 0 },\n\t\t{ 64, -64, 0 },\n'
                         '\t\t{ 64, 64, 0 },\n\t\t{ -64, 64, 0 },\n\t}\n'
                         '\tFaces 1 6 {\n\t\tTriangles {\n\t\t\t{ 0, 1, 2, 0, 2, 3 },\n'
                         '\t\t}\n\t}\n}\n')
        shown = run('from-model', model, os.path.join(self.tmp, 'fp.tga'), '--size', '8x8')
        self.assertIn('00555500', shown)


if __name__ == '__main__':
    unittest.main()
