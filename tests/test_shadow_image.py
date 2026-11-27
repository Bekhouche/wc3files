import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.img.png_reader import read_png_gray
from wc3files.img.png_writer import write_png, write_png_gray
from wc3files.shd import ShadowMapFile
from wc3files.shd.image import shadow_from_png, shadow_png, shadow_size
from wc3files.w3e import TerrainFile


def _terrain(tiles_x=2, tiles_y=1):
    width, height = tiles_x + 1, tiles_y + 1
    tile = {'ground_height': 8192, 'water_level': 0, 'is_map_edge': False,
            'ground_texture': 0, 'is_ramp': False, 'is_blight': False, 'is_water': False,
            'is_boundary': False, 'ground_variation': 0, 'cliff_variation': 0,
            'layer_height': 2, 'cliff_texture': 15}
    return TerrainFile().import_data({
        'version': 11, 'main_tileset': 'L', 'custom_tileset': False,
        'ground_tilesets': ['Ldrt'], 'cliff_tilesets': [], 'width': width,
        'height': height, 'offset_x': 0.0, 'offset_y': 0.0,
        'tiles': [dict(tile) for _ in range(width * height)]})


def _shadow():
    # 8x4 cells; shadow in the bottom-left cell and along the top row.
    cells = bytearray(32)
    cells[0] = 0xFF
    for x in range(8):
        cells[3 * 8 + x] = 0xFF
    return ShadowMapFile().import_data({'width': 8, 'height': 4, 'cells': bytes(cells)})


class TestShadowImage(unittest.TestCase):
    def test_size_is_four_cells_per_tile(self):
        self.assertEqual(shadow_size(_terrain(2, 1).get_data()), (8, 4))

    def test_round_trip(self):
        shadow = _shadow()
        back = shadow_from_png(shadow_png(shadow.get_data()), (8, 4))
        self.assertEqual(back.to_bytes(), shadow.to_bytes())

    def test_black_is_shadow_north_up(self):
        width, height, values, depth = read_png_gray(shadow_png(_shadow().get_data()))
        self.assertEqual((width, height, depth), (8, 4, 8))
        self.assertEqual(values[:8], [0] * 8)                  # top row: shadow
        self.assertEqual(values[3 * 8:], [0] + [255] * 7)      # bottom-left cell

    def test_painted_colour_image(self):
        # Dark red counts as shadow, light gray does not.
        rgba = bytes([120, 0, 0, 255, 200, 200, 200, 255])
        shadow = shadow_from_png(write_png(2, 1, rgba))
        self.assertEqual(shadow.get_data()['cells'], b'\xff\x00')

    def test_size_mismatch(self):
        with self.assertRaisesRegex(ValueError, '4x4 pixels but .* is 8x4 cells'):
            shadow_from_png(write_png_gray(4, 4, [0] * 16, 8), (8, 4))


class TestShadowImageCli(unittest.TestCase):
    def test_map(self):
        from wc3files.w3x.modes import _write_map
        from wc3files.w3x.w3x_file import read_map
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3map.w3e': _terrain().to_bytes(),
                        'war3map.shd': _shadow().to_bytes(), 'war3map.j': b'//'}, source)
            png = os.path.join(tmp, 'shadow.png')
            self.assertEqual(main(['shadow-image', 'export', source, png]), 0)
            width, height, values, _ = read_png_gray(png)
            values[3 * 8 + 7] = 0                              # paint bottom-right
            with open(png, 'wb') as handle:
                handle.write(write_png_gray(width, height, values, 8))
            out = os.path.join(tmp, 'out.w3x')
            self.assertEqual(main(['shadow-image', 'import', source, png, out]), 0)
            files = read_map(out).files
            self.assertEqual(files['war3map.shd'][7], 0xFF)
            self.assertEqual(files['war3map.shd'][:7], _shadow().to_bytes()[:7])
            self.assertEqual(files['war3map.j'], b'//')
            self.assertNotIn('(attributes)', {k.lower() for k, v in files.items() if v})

    def test_bare_file_needs_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            shd = os.path.join(tmp, 'war3map.shd')
            _shadow().write(shd)
            png = os.path.join(tmp, 's.png')
            self.assertEqual(main(['shadow-image', 'export', shd, png]), 1)
            self.assertFalse(os.path.exists(png))
            self.assertEqual(main(['shadow-image', 'export', shd, png, '--size', '8x4']), 0)
            w3e = os.path.join(tmp, 'war3map.w3e')
            _terrain().write(w3e)
            out = os.path.join(tmp, 'out.shd')
            self.assertEqual(main(['shadow-image', 'import', w3e, png, out]), 0)
            with open(out, 'rb') as handle:
                self.assertEqual(handle.read(), _shadow().to_bytes())


if __name__ == '__main__':
    unittest.main()
