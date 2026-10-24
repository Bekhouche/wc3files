"""Real tests for war3map.shd (ShadowMapFile) - the one map format with no
header/version at all, so width/height must be supplied by the caller."""
import os
import tempfile
import unittest

import _helpers
from wc3files import ShadowMapFile


class TestShadowMapRoundTrip(unittest.TestCase):
    def setUp(self):
        self.sf = ShadowMapFile()
        self.sf.read(_helpers.sample_path('sample.shd'), 3, 3)

    def test_known_fields(self):
        data = self.sf.get_data()
        self.assertEqual(data['width'], 3)
        self.assertEqual(data['height'], 3)
        self.assertEqual(data['cells'], bytes([0, 0, 0, 0, 0xFF, 0, 0, 0, 0]))

    def test_to_bool_grid(self):
        self.assertEqual(
            self.sf.to_bool_grid(),
            [False, False, False, False, True, False, False, False, False],
        )

    def test_from_bool_grid_round_trips(self):
        bools = [True, False, True, False]
        rebuilt = ShadowMapFile.from_bool_grid(2, 2, bools)
        self.assertEqual(rebuilt.get_data()['cells'], bytes([0xFF, 0x00, 0xFF, 0x00]))
        self.assertEqual(rebuilt.to_bool_grid(), bools)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.shd'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.shd')
            self.sf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_rejects_size_mismatch_in_either_direction(self):
        # Too short.
        with self.assertRaises(ValueError):
            ShadowMapFile().read(_helpers.sample_path('sample.shd'), 10, 10)
        # Too long (regression: a bulk-read that silently truncated extra
        # trailing bytes instead of flagging the mismatch - caught against a
        # real 24.7MB corrupted fixture whose declared map dimensions implied
        # a 65536-byte file; see TODO.md).
        with self.assertRaises(ValueError):
            ShadowMapFile().read(_helpers.sample_path('sample.shd'), 1, 1)


if __name__ == '__main__':
    unittest.main()
