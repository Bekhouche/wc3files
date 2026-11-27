import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.img import BlpFile
from wc3files.table.slk_file import SlkFile
from wc3files.w3e.minimap import (GameMinimapColours, layout, minimap_blp, object_colours,
                                  playable_area, render_minimap, texture_colour)


def _terrain(tiles_x=8, tiles_y=4, water=None):
    width, height = tiles_x + 1, tiles_y + 1
    tiles = []
    for y in range(height):
        for x in range(width):
            wet = bool(water and water(x, y))
            tiles.append({'ground_height': 8192, 'water_level': 8192 + 800 if wet else 0,
                          'is_map_edge': False, 'ground_texture': 1 if x >= tiles_x // 2 else 0,
                          'is_ramp': False, 'is_blight': False, 'is_water': wet,
                          'is_boundary': False, 'ground_variation': 0, 'cliff_variation': 0,
                          'layer_height': 2, 'cliff_texture': 15})
    return {'version': 11, 'main_tileset': 'L', 'custom_tileset': False,
            'ground_tilesets': ['Ldrt', 'Lgrs'], 'cliff_tilesets': [], 'width': width,
            'height': height, 'offset_x': -tiles_x * 64.0, 'offset_y': -tiles_y * 64.0,
            'tiles': tiles}


COLOURS = {'Ldrt': (120, 90, 40), 'Lgrs': (40, 140, 40)}


def _pixel(rgba, x, y):
    o = (y * 256 + x) * 4
    return tuple(rgba[o:o + 3])


class TestMinimap(unittest.TestCase):
    def test_playable_area_and_layout(self):
        terrain = _terrain(8, 4)
        info = {'camera_complements': (1, 1, 0, 0)}
        self.assertEqual(playable_area(terrain, info), (1, 0, 6, 4))
        left, bottom, width, height, scale, x0, y0 = layout(terrain, info)
        self.assertEqual((scale, x0), (256 / 6, 0))
        self.assertAlmostEqual(y0, (256 - 4 * 256 / 6) / 2)
        with self.assertRaisesRegex(ValueError, 'no playable area'):
            playable_area(terrain, {'camera_complements': (4, 4, 0, 0)})

    def test_ground_and_black_bars(self):
        rgba = render_minimap(_terrain(8, 4), {'camera_complements': (0, 0, 0, 0)},
                              tile_colours=COLOURS)
        # 8 x 4 tiles: 256 x 128 in the middle, black above and below.
        self.assertEqual(_pixel(rgba, 10, 10), (0, 0, 0))
        self.assertEqual(_pixel(rgba, 10, 250), (0, 0, 0))
        self.assertEqual(_pixel(rgba, 10, 128), COLOURS['Ldrt'])
        self.assertEqual(_pixel(rgba, 245, 128), COLOURS['Lgrs'])
        mid = _pixel(rgba, 124, 128)            # the corners blend near the seam
        self.assertTrue(40 < mid[0] < 120)

    def test_water_and_objects(self):
        terrain = _terrain(8, 4, water=lambda x, y: x <= 2)
        doodad = {'id': 'LTlt', 'position': [0.0, 0.0, 0.0]}
        rgba = render_minimap(terrain, {'camera_complements': (0, 0, 0, 0)}, [doodad],
                              COLOURS, ((0, 0, 192), 0.5), {'LTlt': (32, 90, 32)})
        self.assertEqual(_pixel(rgba, 5, 128), (60, 45, 116))   # dirt half-tinted blue
        self.assertEqual(_pixel(rgba, 128, 128), (32, 90, 32))  # the tree at the centre
        self.assertEqual(object_colours()['LTlt'], (32, 90, 32))

    def test_blp_like_the_editors(self):
        rgba = render_minimap(_terrain(), {'camera_complements': (0, 0, 0, 0)},
                              tile_colours=COLOURS)
        blp = BlpFile()
        blp.read(minimap_blp(rgba))
        self.assertEqual((blp.width, blp.height, blp.compression, blp.mip_count(),
                          blp.alpha_bits), (256, 256, 0, 1, 0))
        back = blp.get_rgba(0)
        self.assertLess(max(abs(a - b) for a, b in zip(back, rgba)), 40)


class _Storage:
    """Stands in for a CascStorage: Terrain.slk, Water.slk and one texture."""

    def __init__(self, tmp):
        self.files = {}
        for name, rows, key in (
                ('TerrainArt/Terrain.slk',
                 {'Ldrt': {'tileID': 'Ldrt', 'dir': 'TerrainArt\\Summer', 'file': 'Dirt'}},
                 'tileID'),
                ('TerrainArt/Water.slk',
                 {'LSha': {'waterID': 'LSha', 'mmRed': 0, 'mmGreen': 0, 'mmBlue': 192,
                           'mmAlpha': 64}}, 'waterID')):
            path = os.path.join(tmp, 'table.slk')
            table = SlkFile()
            table.import_data(rows, id_column=key)
            table.write(path)
            with open(path, 'rb') as handle:
                self.files['War3.w3mod:' + name] = handle.read()
        # 8 x 4: a left square of red, the extended half green.
        rgba = b''.join(bytes((200, 0, 0, 255)) if x < 4 else bytes((0, 200, 0, 255))
                        for y in range(4) for x in range(8))
        self.files['War3.w3mod:TerrainArt/Summer/Dirt.blp'] = \
            BlpFile.from_rgba(8, 4, rgba).to_bytes()

    def read(self, path):
        return self.files[path]


class TestGameColours(unittest.TestCase):
    def test_from_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = GameMinimapColours(_Storage(tmp))
        self.assertEqual(game.tile_colours(['Ldrt', 'Xzzz']), {'Ldrt': (200.0, 0.0, 0.0)})
        self.assertEqual(game.missing, {'Xzzz'})
        self.assertEqual(game.water_tint('L'), ((0, 0, 192), 64 / 255))
        self.assertIsNone(game.water_tint('Q'))

    def test_texture_colour(self):
        # 4 x 2: the left square (2 x 2) is dark, the rest light.
        row = bytes((10, 20, 30, 255)) * 2 + bytes((250, 250, 250, 255)) * 2
        self.assertEqual(texture_colour(row * 2, 4, 2), (10, 20, 30))

    def test_cli_needs_a_game(self):
        from wc3files.w3x.modes import _write_map
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3map.j': b'//'}, source)
            self.assertEqual(main(['minimap', source, os.path.join(tmp, 'out.blp'),
                                   '--game', tmp]), 1)


if __name__ == '__main__':
    unittest.main()
