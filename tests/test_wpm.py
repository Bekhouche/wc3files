"""Real tests for war3map.wpm (PathingFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import PathingFile


class TestPathingRoundTrip(unittest.TestCase):
    def setUp(self):
        self.pf = PathingFile()
        self.pf.read(_helpers.sample_path('sample.wpm'))

    def test_known_fields(self):
        data = self.pf.get_data()
        self.assertEqual(data['version'], 0)
        self.assertEqual(data['width'], 2)
        self.assertEqual(data['height'], 2)
        self.assertEqual(data['cells'], bytes([0x40, 0x48, 0xCA, 0xCE]))

    def test_decode_flags_matches_documented_common_values(self):
        # 0xCE = map boundary: nothing allowed at all.
        flags = PathingFile.decode_flags(0xCE)
        self.assertTrue(flags['no_walk'])
        self.assertTrue(flags['no_fly'])
        self.assertTrue(flags['no_build'])
        self.assertTrue(flags['no_water'])
        self.assertFalse(flags['blight'])
        # 0x40 = normal ground: only water blocked.
        flags = PathingFile.decode_flags(0x40)
        self.assertTrue(flags['no_water'])
        self.assertFalse(flags['no_walk'])
        self.assertFalse(flags['no_build'])

    def test_encode_flags_round_trips(self):
        for byte_value in (0x00, 0x08, 0x0A, 0x40, 0x48, 0xCA, 0xCE):
            self.assertEqual(PathingFile.encode_flags(PathingFile.decode_flags(byte_value)), byte_value)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.wpm'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.wpm')
            self.pf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_rejects_unsupported_version(self):
        data = dict(self.pf.get_data())
        data['version'] = 1
        bad = PathingFile()
        bad._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'bad.wpm')
            bad.write(out)
            with self.assertRaises(ValueError):
                PathingFile().read(out)

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.wpm')
            with open(bad, 'wb') as fh:
                fh.write(b'NOPE' + b'\x00' * 20)
            with self.assertRaises(ValueError):
                PathingFile().read(bad)


if __name__ == '__main__':
    unittest.main()
