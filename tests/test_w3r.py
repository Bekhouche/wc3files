"""Real tests for war3map.w3r (RegionFile / Region)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import RegionFile, Region


class TestW3rRoundTrip(unittest.TestCase):
    def setUp(self):
        self.region_file = RegionFile()
        self.region_file.read(_helpers.sample_path('sample.w3r'))

    def test_known_fields(self):
        region = self.region_file.get_regions()[0]
        self.assertEqual(region.name, 'Sample Region')
        self.assertEqual(region.left, -512.0)
        self.assertEqual(region.right, 512.0)
        self.assertEqual(region.color, (255, 0, 0))

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3r'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3r')
            self.region_file.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_add_and_remove_region(self):
        rf = RegionFile()
        rf.add_region(0, Region(name='A'))
        rf.add_region(1, Region(name='B'))
        self.assertEqual(rf.number, 2)
        rf.remove_region(0)
        self.assertEqual(rf.number, 1)
        with self.assertRaises(KeyError):
            rf.remove_region(0)


if __name__ == '__main__':
    unittest.main()
