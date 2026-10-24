"""Real tests for wc3files.table (SlkFile / WtsFile) - the generic SYLK/WTS
formats reused across w3o and w3i. Uses the real Blizzard SLK tables already
bundled in w3o/gamedata/ rather than personal map data."""
import os
import tempfile
import unittest

import _helpers
import wc3files
from wc3files import SlkFile, WtsFile

_GAMEDATA = os.path.join(os.path.dirname(wc3files.__file__), 'w3o', 'gamedata')
_UNIT_DATA_SLK = os.path.join(_GAMEDATA, 'UnitData.slk')


class TestSlkRoundTrip(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.exists(_UNIT_DATA_SLK), 'bundled UnitData.slk is missing')
        self.slk = SlkFile()
        self.slk.read(_UNIT_DATA_SLK)

    def test_known_real_row(self):
        rows = self.slk.export_data()
        self.assertIn('hfoo', rows)  # Footman - present in every real WC3 install

    def test_round_trip_preserves_row_count(self):
        original_rows = self.slk.export_data()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.slk')
            self.slk.write(out)
            reread = SlkFile()
            reread.read(out)
            self.assertEqual(len(reread.export_data()), len(original_rows))
            self.assertEqual(reread.export_data()['hfoo'], original_rows['hfoo'])


class TestWtsRoundTrip(unittest.TestCase):
    def test_round_trip_and_resolve(self):
        data = {0: 'Sample Map', 5: 'Another string with "quotes" inside'}
        wts = WtsFile()
        wts.import_data(data)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.wts')
            wts.write(out)
            reread = WtsFile()
            reread.read(out)
            self.assertEqual(reread.export_data(), data)
            self.assertEqual(reread.resolve('TRIGSTR_000'), 'Sample Map')
            self.assertEqual(reread.resolve('not a trigstr'), 'not a trigstr')


if __name__ == '__main__':
    unittest.main()
