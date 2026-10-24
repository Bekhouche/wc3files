"""Real tests for war3mapUnits.doo (MapUnitsFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import MapUnitsFile


class TestDooRoundTrip(unittest.TestCase):
    def setUp(self):
        self.doo = MapUnitsFile()
        self.doo.read(_helpers.sample_path('sample.doo'))

    def test_known_fields(self):
        data = self.doo.get_data()
        self.assertEqual(data['version'], 8)
        self.assertEqual(data['subversion'], 11)
        self.assertEqual(len(data['units']), 2)
        hero = data['units'][0]
        self.assertEqual(hero['id'], 'hfoo')
        self.assertEqual(hero['inventory'][0]['item_id'], 'stel')
        random_unit = data['units'][1]
        self.assertEqual(random_unit['random_mode'], 1)
        self.assertEqual(random_unit['random_data']['table_id'], 0)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.doo'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.doo')
            self.doo.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.doo')
            with open(bad, 'wb') as fh:
                fh.write(b'NOPE' + b'\x00' * 20)
            with self.assertRaises(ValueError):
                MapUnitsFile().read(bad)


if __name__ == '__main__':
    unittest.main()
