import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.mdl.mdx_io import save_mdx
from wc3files.mdl.obj import obj_to_model
from wc3files.shd.compute import (DoodadCasters, cast, compute_shadow_map, file_reader,
                                  ground_heights, model_points, read_profile)
from wc3files.w3e import TerrainFile

# A box 64 wide and 64 tall, standing on z = 0.
BOX = """v -32 -32 0
v 32 -32 0
v 32 32 0
v -32 32 0
v -32 -32 64
v 32 -32 64
v 32 32 64
v -32 32 64
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""

SKINS = """[LOrk]
file=Doodads\\Props\\Box
numVar=1
shadow=1
[LOnl]
file=Doodads\\Props\\Box
numVar=1
shadow=0
[LOvr]
file=Doodads\\Props\\Crate
numVar=3
shadow=1
"""


def _terrain(tiles=8, levels=None):
    width = height = tiles + 1
    tiles_out = []
    for y in range(height):
        for x in range(width):
            tiles_out.append({
                'ground_height': 8192, 'water_level': 0, 'is_map_edge': False,
                'ground_texture': 0, 'is_ramp': False, 'is_blight': False, 'is_water': False,
                'is_boundary': False, 'ground_variation': 0, 'cliff_variation': 0,
                'layer_height': levels(x, y) if levels else 2, 'cliff_texture': 15})
    return {'version': 11, 'main_tileset': 'L', 'custom_tileset': False,
            'ground_tilesets': ['Ldrt'], 'cliff_tilesets': ['CLdi'], 'width': width,
            'height': height, 'offset_x': 0.0, 'offset_y': 0.0, 'tiles': tiles_out}


def _box_bytes():
    return save_mdx(obj_to_model(BOX, up='z'))


def _doodad(type_id, x, y, variation=0, scale=1.0):
    return {'id': type_id, 'variation': variation, 'position': [x, y, 0.0], 'rotation': 0.0,
            'scale': [scale, scale, scale], 'skin': None}


def _cells(shadow):
    d = shadow.get_data()
    return {(i % d['width'], i // d['width']) for i, c in enumerate(d['cells']) if c}


class TestCast(unittest.TestCase):
    def test_flat_ground_has_no_shadow(self):
        self.assertEqual(_cells(compute_shadow_map(_terrain())), set())

    def test_column_shadow_length_and_direction(self):
        # One cell 100 units tall in the middle of 32x32 flat cells.
        width = height = 32
        ground = [0.0] * (width * height)
        occ = list(ground)
        occ[16 * width + 16] = 100.0
        for azimuth, step in ((225, (1, 1)), (0, (-1, 0)), (90, (0, -1))):
            out = cast(width, height, ground, occ, azimuth, 30)
            shadowed = {(i % width, i // width) for i, c in enumerate(out) if c}
            # Away from the light; a diagonal step is 45 units long.
            length = 100 / math.tan(math.radians(30))
            per_step = 32 * math.hypot(*step)
            n = int(length // per_step)
            expected = {(16 + step[0] * k, 16 + step[1] * k) for k in range(1, n + 1)}
            self.assertTrue(expected <= shadowed, (azimuth, expected - shadowed))
            back = (16 - step[0], 16 - step[1])
            self.assertNotIn(back, shadowed)

    def test_cliff_shades_the_lower_side_away_from_the_light(self):
        # Level 3 on the west half (x < 4), level 2 east: the light from the
        # south-west, so the wall shades the low ground east of it.
        terrain = _terrain(levels=lambda x, y: 3 if x <= 4 else 2)
        ground = ground_heights(terrain)
        self.assertEqual(max(ground), 128)
        cells = _cells(compute_shadow_map(terrain))
        self.assertTrue(cells)
        self.assertTrue(all(x >= 18 for x, _ in cells))
        # The wall is 128 high; along a diagonal the shadow reaches about
        # 128 / tan(30) = 222 units, about 4.9 cells in x.
        self.assertLessEqual(max(x for x, _ in cells), 18 + 6)
        flat = compute_shadow_map(_terrain(levels=lambda x, y: 3 if x <= 4 else 2),
                                  azimuth=45)
        self.assertTrue(all(x < 18 for x, _ in _cells(flat)) or not _cells(flat))


class TestCasters(unittest.TestCase):
    def setUp(self):
        self.files = {'Doodads\\Props\\Box.mdx': _box_bytes(),
                      'Doodads\\Props\\Crate2.mdx': _box_bytes()}
        self.read = file_reader(self.files)

    def test_model_points_cover_the_top(self):
        points = model_points(obj_to_model(BOX, up='z'))
        self.assertEqual(max(z for _, _, z in points), 64)
        self.assertEqual(round(min(x for x, _, _ in points)), -32)

    def test_which_doodads_cast(self):
        casters = DoodadCasters(read_profile(SKINS), self.read)
        self.assertTrue(casters(_doodad('LOrk', 0, 0)))
        self.assertIsNone(casters(_doodad('LOnl', 0, 0)))
        self.assertIsNone(casters(_doodad('LTlt', 0, 0)))     # a tree, a destructable
        self.assertTrue(casters(_doodad('LOvr', 0, 0, variation=5)))   # Crate2
        self.assertIsNone(casters(_doodad('LOvr', 0, 0, variation=1)))
        self.assertEqual(casters.counts, {'cast': 2, 'no shadow': 1, 'not a doodad': 1,
                                          'model not found': 1})
        self.assertEqual(casters.missing_models, {'Doodads\\Props\\Crate1'})

    def test_object_data_overrides(self):
        object_data = {
            'original': {'LOnl': {'dshd': {'value': {'0': 1}}}},
            'custom': {'D000': {'ID': 'LOnl', 'dshd': {'value': {'0': 1}},
                                'dfil': {'value': {'0': 'war3mapImported\\Tall.mdx'}}}}}
        files = dict(self.files)
        files['war3mapImported\\tall.mdx'] = save_mdx(obj_to_model(BOX, up='z', scale=4.0))
        casters = DoodadCasters(read_profile(SKINS), file_reader(files), object_data)
        self.assertTrue(casters(_doodad('LOnl', 0, 0)))
        tall = casters(_doodad('D000', 0, 0))
        self.assertEqual(max(z for _, _, z in tall), 256)

    def test_doodad_shadow_on_the_map(self):
        terrain = _terrain()
        casters = DoodadCasters(read_profile(SKINS), self.read)
        shadow = compute_shadow_map(terrain, [_doodad('LOrk', 512, 512)], casters)
        cells = _cells(shadow)
        # The box covers cells 15-16; its shadow falls north-east of it.
        self.assertTrue({(17, 17), (18, 18)} <= cells)
        self.assertFalse({(13, 13), (14, 14)} & cells)
        none = compute_shadow_map(terrain, [_doodad('LOnl', 512, 512)], casters)
        self.assertEqual(_cells(none), set())


class TestShadowComputeCli(unittest.TestCase):
    def test_terrain_only(self):
        from wc3files.w3x.modes import _write_map
        from wc3files.w3x.w3x_file import read_map
        terrain = TerrainFile().import_data(_terrain(levels=lambda x, y: 3 if x <= 4 else 2))
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3map.w3e': terrain.to_bytes(), 'war3map.j': b'//'}, source)
            out = os.path.join(tmp, 'out.w3x')
            self.assertEqual(main(['shadow-compute', source, out]), 0)
            files = read_map(out).files
            self.assertEqual(len(files['war3map.shd']), 32 * 32)
            self.assertIn(0xFF, files['war3map.shd'])
            self.assertEqual(main(['shadow-compute', source, out, '--game', tmp]), 1)


if __name__ == '__main__':
    unittest.main()
