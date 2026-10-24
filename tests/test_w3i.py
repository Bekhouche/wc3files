"""Real tests for war3map.w3i (W3iFile): round-trip the bundled sample.w3i and
check specific field values, not just "it didn't crash"."""
import os
import tempfile
import unittest

import _helpers
from wc3files import W3iFile


class TestW3iRoundTrip(unittest.TestCase):
    def setUp(self):
        self.w3i = W3iFile()
        self.w3i.read(_helpers.sample_path('sample.w3i'))

    def test_known_fields(self):
        data = self.w3i.get_data()
        self.assertEqual(data['map_name'], 'Sample Map')
        self.assertEqual(data['file_version'], 31)
        self.assertEqual(len(data['players']), 2)
        self.assertEqual(data['players'][0]['race'], 1)
        self.assertEqual(len(data['forces']), 1)
        self.assertEqual(data['script_type'], 'jass')

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3i'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3i')
            self.w3i.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_flag_helpers_round_trip(self):
        flags = {'melee_map': True, 'use_custom_forces': True}
        encoded = W3iFile.encode_flags(flags)
        decoded = W3iFile.decode_flags(encoded)
        self.assertTrue(decoded['melee_map'])
        self.assertTrue(decoded['use_custom_forces'])
        self.assertFalse(decoded['fixed_player_force_settings'])

    def test_rejects_unsupported_version(self):
        data = dict(self.w3i.get_data())
        data['file_version'] = 999
        bad = W3iFile()
        bad._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'bad.w3i')
            with self.assertRaises(ValueError):
                bad.write(out)


if __name__ == '__main__':
    unittest.main()
