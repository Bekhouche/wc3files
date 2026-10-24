"""Real tests for war3map.mmp (PreviewIconsFile) - minimap preview-icon overlays."""
import os
import tempfile
import unittest

import _helpers
from wc3files import PreviewIconsFile, PreviewIcon
from wc3files.mmp.data import PreviewIconType


class TestPreviewIconsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.pf = PreviewIconsFile()
        self.pf.read(_helpers.sample_path('sample.mmp'))

    def test_known_fields(self):
        icons = self.pf.get_icons()
        self.assertEqual(self.pf.version, 0)
        self.assertEqual(len(icons), 2)
        self.assertEqual(icons[0].icon_type, PreviewIconType.GOLD_MINE.value)
        self.assertEqual(icons[0].color, (255, 255, 255, 255))
        self.assertEqual(icons[1].icon_type, PreviewIconType.PLAYER_START.value)
        self.assertEqual(icons[1].color, (255, 3, 3, 255))  # real red player color

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.mmp'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.mmp')
            self.pf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_add_and_remove_icon(self):
        pf = PreviewIconsFile()
        pf.add_icon(0, PreviewIcon(icon_type=1, x=10, y=20))
        pf.add_icon(1, PreviewIcon(icon_type=1, x=30, y=40))
        self.assertEqual(pf.number, 2)
        pf.remove_icon(0)
        self.assertEqual(pf.number, 1)
        with self.assertRaises(KeyError):
            pf.remove_icon(0)

    def test_bgra_color_conversion(self):
        # Stored on disk as BGRA - confirmed against real fixtures with real
        # player colors (see TODO.md) - exposed here as (R, G, B, A).
        icon = PreviewIcon(color=(10, 20, 30, 255))
        raw = icon.get_data()['color'][1]
        self.assertEqual(raw, bytes([30, 20, 10, 255]))  # B, G, R, A on disk


if __name__ == '__main__':
    unittest.main()
