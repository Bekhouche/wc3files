import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import _helpers
from wc3files.cli.main import main
from wc3files.img import BlpFile, write_png
from wc3files.mdl.mdx_io import load_mdx
from wc3files.w3i import W3iFile
from wc3files.w3x.loading_screen import add_loading_screen, build, fill, tiles


def _quadrants(width=64, height=48):
    """Red top-left, green top-right, blue bottom-left, white bottom-right."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            top, left = y < height // 2, x < width // 2
            colour = ((255, 0, 0) if left else (0, 255, 0)) if top else \
                ((0, 0, 255) if left else (255, 255, 255))
            out += bytes(colour) + b'\xff'
    return bytes(out)


def _mean(rgba):
    n = len(rgba) // 4
    return tuple(round(sum(rgba[c::4]) / n) for c in range(3))


class TestLayout(unittest.TestCase):
    def test_fill_cuts_the_overflow(self):
        wide = _quadrants(80, 30)
        out = fill(wide, 80, 30, 40, 30)
        self.assertEqual(len(out), 40 * 30 * 4)

    def test_classic_tiles(self):
        parts = {name: (quad, image) for name, quad, image in tiles(_quadrants(), 64, 48)}
        self.assertEqual(sorted(parts), ['BotLeft', 'BotRight', 'TopLeft', 'TopRight'])
        self.assertEqual(parts['TopLeft'][0], (0.0, 0.2, 0.4, 0.6))
        self.assertEqual(parts['TopLeft'][1][1:], (512, 512))
        self.assertEqual(parts['BotLeft'][1][1:], (512, 256))
        # 1024 x 768: the top 512 rows are the two top tiles, the picture's
        # top half and the first 128 rows of its bottom half.
        self.assertEqual(_mean(parts['TopRight'][1][0][:512 * 64 * 4]), (0, 255, 0))
        self.assertEqual(_mean(parts['BotLeft'][1][0]), (0, 0, 255))
        self.assertEqual(_mean(parts['BotRight'][1][0]), (255, 255, 255))

    def test_reforged_tile(self):
        [(name, quad, (rgba, w, h))] = tiles(_quadrants(), 64, 48, 'reforged')
        self.assertEqual((name, quad, w, h), ('FullScreen', (0.0, 0.0, 0.8, 0.6), 2048, 1024))
        with self.assertRaisesRegex(ValueError, 'unknown layout'):
            tiles(_quadrants(), 64, 48, 'wide')


class TestModel(unittest.TestCase):
    def test_classic_model(self):
        members, model = build(_quadrants(), 64, 48, quality=50)
        self.assertEqual(model, 'war3mapImported\\LoadingScreen.mdx')
        m = load_mdx(members[model])
        self.assertEqual(m['version'], 800)
        self.assertEqual([(s['name'], s['interval'], s['flags']) for s in m['sequences']],
                         [('Birth', [1000, 2000], 1)])
        paths = [t['path'] for t in m['textures']]
        self.assertEqual(paths, ['war3mapImported\\LoadingScreen%s.blp' % n
                                 for n in ('BotLeft', 'BotRight', 'TopRight', 'TopLeft')])
        for geoset, (x0, y0, x1, y1) in zip(m['geosets'], ((0, 0, .4, .2), (.4, 0, .8, .2),
                                                           (.4, .2, .8, .6), (0, .2, .4, .6))):
            xs, ys = geoset['vertices'][0::3], geoset['vertices'][1::3]
            self.assertAlmostEqual(min(xs), x0, 5)
            self.assertAlmostEqual(max(ys), y1, 5)
        layer = m['materials'][0]['layers'][0]
        self.assertEqual(layer['shadingFlags'], 1 | 16 | 32)   # unshaded, two-sided, unfogged
        for path in paths:
            blp = BlpFile()
            blp.read(members[path])
            self.assertEqual(blp.compression, 0)
        top_right = BlpFile()
        top_right.read(members[paths[2]])
        self.assertEqual((top_right.width, top_right.height), (512, 512))


class TestAddToMap(unittest.TestCase):
    def _map_files(self):
        with open(_helpers.sample_path('sample.w3i'), 'rb') as handle:
            return {'war3map.w3i': handle.read(), 'war3map.j': b'//'}

    def test_files_imports_and_map_info(self):
        from wc3files.w3x.imports import read_imports
        files, report = add_loading_screen(self._map_files(), _quadrants(), 64, 48,
                                           layout='reforged', quality=40, size=(64, 32))
        self.assertEqual(sorted(report['added']),
                         ['war3mapImported\\LoadingScreen.mdx',
                          'war3mapImported\\LoadingScreenFullScreen.blp'])
        info = W3iFile()
        info.read(files['war3map.w3i'])
        screen = info.get_data()['loading_screen']
        self.assertEqual((screen['id'], screen['path']),
                         (-1, 'war3mapImported\\LoadingScreen.mdx'))
        _, listing = read_imports(files)
        self.assertEqual(sorted(e['path'] for e in listing['files']), sorted(report['added']))
        again, report = add_loading_screen(files, _quadrants(), 64, 48, layout='reforged',
                                           quality=40, size=(64, 32))
        self.assertEqual(len(report['replaced']), 2)
        self.assertEqual(len(read_imports(again)[1]['files']), 2)

    def test_needs_map_info(self):
        with self.assertRaisesRegex(ValueError, 'war3map.w3i'):
            add_loading_screen({'war3map.j': b''}, _quadrants(), 64, 48)

    def test_cli(self):
        from wc3files.w3x.modes import _write_map
        from wc3files.w3x.w3x_file import read_map
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map(self._map_files(), source)
            picture = os.path.join(tmp, 'art.png')
            with open(picture, 'wb') as handle:
                handle.write(write_png(64, 48, _quadrants()))
            out = os.path.join(tmp, 'out.w3x')
            self.assertEqual(main(['loading-screen', source, picture, out, '--quality', '30']),
                             0)
            names = {k.lower() for k, v in read_map(out).files.items() if v}
            self.assertIn('war3mapimported\\loadingscreentopleft.blp', names)
            self.assertIn('war3mapimported\\loadingscreen.mdx', names)


if __name__ == '__main__':
    unittest.main()
