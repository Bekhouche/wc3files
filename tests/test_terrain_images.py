import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.img.png_reader import PngError, read_png_gray
from wc3files.img.png_writer import write_png, write_png_gray
from wc3files.w3e import TerrainFile
from wc3files.w3e.images import (export_layer, export_tiles, import_layer, import_tiles,
                                 layer_values)


def _terrain(width=5, height=4, version=11):
    tiles = []
    for y in range(height):
        for x in range(width):
            tiles.append({
                'ground_height': 8192 + 37 * x - 211 * y + (5000 if (x, y) == (1, 0) else 0),
                'water_level': 7000 + 13 * x + y,
                'is_map_edge': False, 'ground_texture': 0, 'is_ramp': False,
                'is_blight': False, 'is_water': (x + y) % 3 == 0, 'is_boundary': False,
                'ground_variation': 0, 'cliff_variation': 0, 'layer_height': 2,
                'cliff_texture': 15,
            })
    data = {'version': version, 'main_tileset': 'L', 'custom_tileset': False,
            'ground_tilesets': ['Ldrt', 'Lgrs'], 'cliff_tilesets': ['CLdi'],
            'width': width, 'height': height, 'offset_x': -256.0, 'offset_y': -192.0,
            'tiles': tiles}
    # Read back from its bytes, so the tiles hold what the reader gives.
    terrain = TerrainFile()
    terrain.read(TerrainFile().import_data(data).to_bytes())
    return terrain


class TestGrayPng(unittest.TestCase):
    def test_16_bit_values_kept(self):
        values = [0, 1, 255, 256, 8192, 65535]
        width, height, back, depth = read_png_gray(write_png_gray(3, 2, values))
        self.assertEqual((width, height, back, depth), (3, 2, values, 16))

    def test_8_bit(self):
        values = [0, 128, 255, 7]
        self.assertEqual(read_png_gray(write_png_gray(2, 2, values, 8)), (2, 2, values, 8))

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            write_png_gray(1, 1, [256], 8)

    def test_colour_png_gives_the_gray(self):
        rgba = bytes([10, 10, 10, 255, 200, 200, 200, 0])
        self.assertEqual(read_png_gray(write_png(2, 1, rgba))[2], [10, 200])

    def test_not_a_png(self):
        with self.assertRaises(PngError):
            read_png_gray(b'GIF89a')


class TestTerrainImages(unittest.TestCase):
    def test_round_trip_is_byte_for_byte(self):
        for version in (11, 12):
            terrain = _terrain(version=version)
            original = terrain.to_bytes()
            d = terrain.get_data()
            for layer in ('height', 'water', 'water-flag'):
                png, value_range = export_layer(d, layer)
                self.assertIsNone(value_range)
                self.assertEqual(import_layer(d, layer, png), 0)
            self.assertEqual(terrain.to_bytes(), original)

    def test_north_is_up(self):
        d = _terrain().get_data()
        width, height, values, depth = read_png_gray(export_layer(d, 'height')[0])
        self.assertEqual((width, height, depth), (5, 4, 16))
        # The corner (1, 0) is at the bottom of the map: the image's last row.
        self.assertEqual(values[3 * width + 1], 8192 + 37 + 5000)
        self.assertEqual(values[1], 8192 + 37 - 211 * 3)

    def test_stretch_comes_back_exactly(self):
        terrain = _terrain()
        original = terrain.to_bytes()
        d = terrain.get_data()
        png, value_range = export_layer(d, 'height', stretch=True)
        heights = [t['ground_height'] for t in d['tiles']]
        self.assertEqual(value_range, (min(heights), max(heights)))
        values = read_png_gray(png)[2]
        self.assertEqual((min(values), max(values)), (0, 65535))
        for tile in d['tiles']:
            tile['ground_height'] = 0
        import_layer(d, 'height', png, value_range)
        self.assertEqual(terrain.to_bytes(), original)

    def test_8_bit_image_on_the_terrain_range(self):
        d = _terrain().get_data()
        heights = layer_values(d, 'height')
        low, high = min(heights), max(heights)
        png = write_png_gray(5, 4, [0] * 19 + [255], 8)
        import_layer(d, 'height', png)
        self.assertEqual(layer_values(d, 'height'), [low] * 19 + [high])
        import_layer(d, 'height', write_png_gray(5, 4, [0] * 19 + [255], 8), (8000, 9000))
        self.assertEqual(layer_values(d, 'height'), [8000] * 19 + [9000])

    def test_flag_from_threshold(self):
        d = _terrain().get_data()
        import_layer(d, 'water-flag', write_png_gray(5, 4, [127] * 10 + [128] * 10, 8))
        self.assertEqual(layer_values(d, 'water-flag'), [0] * 10 + [1] * 10)

    def test_errors(self):
        d = _terrain().get_data()
        with self.assertRaisesRegex(ValueError, '4x4 pixels but the terrain has 5x4'):
            import_layer(d, 'height', write_png_gray(4, 4, [0] * 16))
        with self.assertRaisesRegex(ValueError, 'water values go from 0 to 16383'):
            import_layer(d, 'water', write_png_gray(5, 4, [20000] * 20))
        with self.assertRaisesRegex(ValueError, 'unknown terrain layer'):
            layer_values(d, 'cliffs')
        flat = _terrain().get_data()
        for tile in flat['tiles']:
            tile['water_level'] = 0
        with self.assertRaisesRegex(ValueError, 'flat'):
            import_layer(flat, 'water', write_png_gray(5, 4, [3] * 20, 8))


def _painted(terrain, layer, paint):
    """The exported image of `layer` with `paint` {(x, row): '#rrggbb'} on it."""
    from wc3files.img.png_reader import read_png
    png, legend = export_tiles(terrain.get_data(), layer)
    width, height, rgba = read_png(png)
    rgba = bytearray(rgba)
    for (x, row), colour in paint.items():
        i = (row * width + x) * 4
        rgba[i:i + 3] = bytes(int(colour[k:k + 2], 16) for k in (1, 3, 5))
    return write_png(width, height, bytes(rgba)), legend


class TestTileImages(unittest.TestCase):
    def test_cliff_levels_come_back(self):
        terrain = _terrain()
        d = terrain.get_data()
        for i, tile in enumerate(d['tiles']):
            tile['layer_height'] = i % 16
        original = terrain.to_bytes()
        png, _ = export_layer(d, 'cliff-level')
        self.assertEqual(sorted(set(read_png_gray(png)[2])), [v * 17 for v in range(16)])
        self.assertEqual(import_layer(d, 'cliff-level', png), 0)
        self.assertEqual(terrain.to_bytes(), original)
        # A gray between two steps takes the nearest level.
        import_layer(d, 'cliff-level', write_png_gray(5, 4, [40] * 20, 8))
        self.assertEqual(set(layer_values(d, 'cliff-level')), {2})

    def test_ground_round_trip_and_legend(self):
        terrain = _terrain()
        d = terrain.get_data()
        for i, tile in enumerate(d['tiles']):
            tile['ground_texture'] = i % 2
        original = terrain.to_bytes()
        png, legend = export_tiles(d, 'ground')
        self.assertEqual(list(legend.values()), ['Ldrt', 'Lgrs'])
        self.assertEqual(import_tiles(d, 'ground', png, legend)['changed'], 0)
        self.assertEqual(terrain.to_bytes(), original)

    def test_paint_a_new_texture(self):
        terrain = _terrain()
        png, legend = _painted(terrain, 'ground', {(0, 0): '#123456', (4, 3): '#3cb44b'})
        legend['#123456'] = 'Lrok'
        d = terrain.get_data()
        report = import_tiles(d, 'ground', png, legend, variation='zero')
        self.assertEqual(report, {'changed': 2, 'added': ['Lrok']})
        self.assertEqual(d['ground_tilesets'], ['Ldrt', 'Lgrs', 'Lrok'])
        top_left = d['tiles'][(d['height'] - 1) * d['width']]
        self.assertEqual((top_left['ground_texture'], top_left['ground_variation']), (2, 0))
        self.assertEqual(d['tiles'][4]['ground_texture'], 1)       # bottom-right, Lgrs
        back = TerrainFile()
        back.read(terrain.to_bytes())
        self.assertEqual(back.get_data()['ground_tilesets'], ['Ldrt', 'Lgrs', 'Lrok'])

    def test_unknown_colour(self):
        terrain = _terrain()
        png, legend = _painted(terrain, 'ground', {(2, 1): '#e6194c'})
        d = terrain.get_data()
        with self.assertRaisesRegex(ValueError, r'pixel \(2, 1\) is #e6194c'):
            import_tiles(d, 'ground', png, legend)
        self.assertEqual(d['ground_tilesets'], ['Ldrt', 'Lgrs'])
        # Next to the first colour, #e6194b: the nearest is Ldrt, as it was.
        self.assertEqual(import_tiles(d, 'ground', png, legend, nearest=True)['changed'], 0)

    def test_random_variation_is_seeded(self):
        results = []
        for _ in range(2):
            terrain = _terrain()
            paint = {(x, row): '#3cb44b' for x in range(5) for row in range(4)}
            png, legend = _painted(terrain, 'ground', paint)
            d = terrain.get_data()
            import_tiles(d, 'ground', png, legend, variation='random', seed=7)
            results.append([t['ground_variation'] for t in d['tiles']])
        self.assertEqual(results[0], results[1])
        self.assertTrue(set(results[0]) <= set(range(18)))

    def test_duplicate_code(self):
        terrain = _terrain()
        d = terrain.get_data()
        d['ground_tilesets'] = ['Ldrt', 'Lgrs', 'Ldrt']
        for i, tile in enumerate(d['tiles']):
            tile['ground_texture'] = i % 3
        original = terrain.to_bytes()
        png, legend = export_tiles(d, 'ground')
        import_tiles(d, 'ground', png, legend)
        self.assertEqual(terrain.to_bytes(), original)

    def test_cliff_type_none_and_full_list(self):
        terrain = _terrain()
        d = terrain.get_data()
        png, legend = export_tiles(d, 'cliff-type')
        self.assertEqual(legend, {'#e6194b': 'CLdi', '#000000': 'none'})
        self.assertEqual(import_tiles(d, 'cliff-type', png, legend)['changed'], 0)
        d['cliff_tilesets'] = ['C%03d' % i for i in range(15)]
        with self.assertRaisesRegex(ValueError, 'list is full'):
            import_tiles(d, 'cliff-type', png, {'#000000': 'none', '#e6194b': 'CXXX'})
        with self.assertRaisesRegex(ValueError, "'none' is only a cliff type"):
            import_tiles(d, 'ground', export_tiles(d, 'ground')[0], {'#000000': 'none'})


class TestTerrainImageCli(unittest.TestCase):
    def test_ground_with_legend(self):
        import json
        terrain = _terrain()
        with tempfile.TemporaryDirectory() as tmp:
            w3e = os.path.join(tmp, 'war3map.w3e')
            terrain.write(w3e)
            png = os.path.join(tmp, 'ground.png')
            self.assertEqual(main(['terrain-image', 'export', w3e, 'ground', png]), 0)
            with open(os.path.join(tmp, 'ground.json'), encoding='utf-8') as handle:
                self.assertEqual(json.load(handle), {'#e6194b': 'Ldrt', '#3cb44b': 'Lgrs'})
            painted, _ = _painted(terrain, 'ground', {(0, 0): '#3cb44b'})
            with open(png, 'wb') as handle:
                handle.write(painted)
            out = os.path.join(tmp, 'out.w3e')
            self.assertEqual(main(['terrain-image', 'import', w3e, 'ground', png, out]), 0)
            back = TerrainFile()
            back.read(out)
            d = back.get_data()
            self.assertEqual(d['tiles'][(d['height'] - 1) * d['width']]['ground_texture'], 1)

    def test_export_edit_import(self):
        terrain = _terrain()
        with tempfile.TemporaryDirectory() as tmp:
            w3e = os.path.join(tmp, 'war3map.w3e')
            terrain.write(w3e)
            png = os.path.join(tmp, 'height.png')
            self.assertEqual(main(['terrain-image', 'export', w3e, 'height', png]), 0)
            width, height, values, _ = read_png_gray(png)
            values[0] += 100
            with open(png, 'wb') as handle:
                handle.write(write_png_gray(width, height, values))
            out = os.path.join(tmp, 'out.w3e')
            self.assertEqual(main(['terrain-image', 'import', w3e, 'height', png, out]), 0)
            back = TerrainFile()
            back.read(out)
            d = terrain.get_data()
            top_left = (d['height'] - 1) * d['width']
            self.assertEqual(back.get_data()['tiles'][top_left]['ground_height'],
                             d['tiles'][top_left]['ground_height'] + 100)

    def test_into_a_map(self):
        from wc3files.w3x.modes import _write_map
        from wc3files.w3x.w3x_file import read_map
        terrain = _terrain()
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3map.w3e': terrain.to_bytes(), 'war3map.j': b'//'}, source)
            png = os.path.join(tmp, 'water.png')
            main(['terrain-image', 'export', source, 'water-flag', png])
            width, height, values, _ = read_png_gray(png)
            with open(png, 'wb') as handle:
                handle.write(write_png_gray(width, height, [255] * len(values), 8))
            out = os.path.join(tmp, 'out.w3x')
            self.assertEqual(main(['terrain-image', 'import', source, 'water-flag', png,
                                   out]), 0)
            files = read_map(out).files
            back = TerrainFile()
            back.read(files['war3map.w3e'])
            self.assertTrue(all(t['is_water'] for t in back.get_data()['tiles']))
            self.assertEqual(files['war3map.j'], b'//')


if __name__ == '__main__':
    unittest.main()
