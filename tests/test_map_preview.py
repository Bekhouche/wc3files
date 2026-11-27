import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.img import BlpFile, TgaFile, write_png
from wc3files.w3x.preview import draw_icons, fit, preview_tga


def _pixel(rgba, x, y, size=256):
    o = (y * size + x) * 4
    return tuple(rgba[o:o + 3])


GREEN = bytes((40, 140, 40, 255)) * (256 * 256)


class TestPreview(unittest.TestCase):
    def test_fit_keeps_the_shape(self):
        wide = bytes((200, 10, 10, 255)) * (64 * 32)
        out = fit(wide, 64, 32)
        self.assertEqual(_pixel(out, 128, 10), (0, 0, 0))       # black above
        self.assertEqual(_pixel(out, 128, 128), (200, 10, 10))
        self.assertEqual(_pixel(out, 128, 250), (0, 0, 0))      # and below

    def test_icons(self):
        icons = [{'icon_type': 0, 'x': 40, 'y': 40, 'color': (255, 255, 255, 255)},
                 {'icon_type': 1, 'x': 100, 'y': 100, 'color': (255, 255, 255, 255)},
                 {'icon_type': 2, 'x': 200, 'y': 50, 'color': (255, 3, 3, 255)}]
        out = draw_icons(GREEN, icons)
        self.assertEqual(_pixel(out, 40, 40), (255, 215, 0))     # a plain gold square
        self.assertEqual(_pixel(out, 100, 100), (255, 255, 255))
        self.assertEqual(_pixel(out, 200, 50), (255, 3, 3))      # the player's colour
        self.assertEqual(_pixel(out, 10, 200), (40, 140, 40))    # the rest unchanged
        gold = (bytes((0, 0, 255, 255)) * 16, 4, 4)
        self.assertEqual(_pixel(draw_icons(GREEN, icons[:1], gold_icon=gold), 40, 40),
                         (0, 0, 255))

    def test_start_on_top_of_a_mine(self):
        icons = [{'icon_type': 2, 'x': 60, 'y': 60, 'color': (3, 3, 255, 255)},
                 {'icon_type': 0, 'x': 60, 'y': 60, 'color': (255, 255, 255, 255)}]
        self.assertEqual(_pixel(draw_icons(GREEN, icons), 60, 60), (3, 3, 255))

    def test_tga(self):
        tga = TgaFile()
        tga.read(preview_tga(GREEN))
        self.assertEqual((tga.width, tga.height), (256, 256))
        self.assertEqual(tga.get_rgba(), GREEN)


class TestPreviewCli(unittest.TestCase):
    def _map(self, tmp, with_minimap=True):
        from wc3files.mmp import PreviewIconsFile
        from wc3files.w3x.modes import _write_map
        files = {'war3map.j': b'//'}
        if with_minimap:
            files['war3mapMap.blp'] = BlpFile.from_rgba(256, 256, GREEN).to_bytes()
        icons = PreviewIconsFile()
        from wc3files.mmp.preview_icon import PreviewIcon
        icons.import_data({0: PreviewIcon(icon_type=2, x=128, y=128, color=(255, 3, 3, 255))})
        files['war3map.mmp'] = icons.to_bytes()
        path = os.path.join(tmp, 'in.w3x')
        _write_map(files, path)
        return path

    def test_from_the_minimap(self):
        from wc3files.w3x.w3x_file import read_map
        with tempfile.TemporaryDirectory() as tmp:
            source = self._map(tmp)
            out = os.path.join(tmp, 'out.w3x')
            self.assertEqual(main(['map-preview', source, out]), 0)
            tga = TgaFile()
            tga.read(read_map(out).files['war3mapPreview.tga'])
            self.assertEqual(_pixel(tga.get_rgba(), 128, 128), (255, 3, 3))
            self.assertEqual(_pixel(tga.get_rgba(), 20, 20), (40, 140, 40))

    def test_from_a_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._map(tmp, with_minimap=False)
            self.assertEqual(main(['map-preview', source, os.path.join(tmp, 'p.tga')]), 1)
            picture = os.path.join(tmp, 'art.png')
            with open(picture, 'wb') as handle:
                handle.write(write_png(32, 64, bytes((9, 9, 200, 255)) * (32 * 64)))
            out = os.path.join(tmp, 'p.tga')
            self.assertEqual(main(['map-preview', source, out, '--image', picture]), 0)
            tga = TgaFile()
            tga.read(out)
            self.assertEqual(_pixel(tga.get_rgba(), 128, 128), (9, 9, 200))
            self.assertEqual(_pixel(tga.get_rgba(), 5, 128), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
