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

    def test_nan_cell_is_left_out_like_a_missing_one(self):
        # The writer skips a float NaN cell, as it did with pd.isna() (issue
        # #5): writing one should give the same file as removing the cell.
        row = self.slk.export_data()['hfoo']
        column = next(c for c, v in row.items() if v not in (None, ''))
        with tempfile.TemporaryDirectory() as tmp:
            row[column] = float('nan')
            with_nan = os.path.join(tmp, 'nan.slk')
            self.slk.write(with_nan)
            row[column] = None
            without = os.path.join(tmp, 'none.slk')
            self.slk.write(without)
            self.assertEqual(_helpers.read_bytes(with_nan), _helpers.read_bytes(without))


class TestNoPandas(unittest.TestCase):
    """pandas was a hard dependency for one pd.isna() call (issue #5). The
    package must import and read/write SYLK with pandas unavailable. A child
    process is used so this suite's own imports do not hide the result."""

    def test_package_works_without_pandas(self):
        import subprocess
        import sys
        script = (
            "import sys, os, tempfile\n"
            "sys.modules['pandas'] = None  # any 'import pandas' now fails\n"
            "sys.modules['numpy'] = None\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import wc3files\n"
            "from wc3files import SlkFile\n"
            "slk = SlkFile(); slk.read(sys.argv[2])\n"
            "out = os.path.join(tempfile.mkdtemp(), 'out.slk'); slk.write(out)\n"
            "again = SlkFile(); again.read(out)\n"
            "assert again.export_data()['hfoo'] == slk.export_data()['hfoo']\n"
            "print('ok')\n")
        result = subprocess.run([sys.executable, '-c', script, _helpers._SRC, _UNIT_DATA_SLK],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'ok')

    def test_import_does_not_load_pandas(self):
        import subprocess
        import sys
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import wc3files; "
                  "print('pandas' in sys.modules, 'numpy' in sys.modules)")
        result = subprocess.run([sys.executable, '-c', script, _helpers._SRC],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], 'False False')


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
