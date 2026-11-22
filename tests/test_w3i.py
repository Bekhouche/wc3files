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


class TestFileThatStopsEarly(unittest.TestCase):
    """Some maps stop after the force table, with a lone 0xFF or nothing,
    instead of writing empty upgrade/tech/random tables: SurvivalChaos4.31.w3x
    has a 652-byte w3i ending that way. It must write back as it was."""

    def _cut_after_forces(self):
        from wc3files.files.binary_file import BinaryFile
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3i'))
        info, data = W3iFile(), {}
        with BinaryFile(original) as reader:
            data['file_version'] = info._read_i32(reader)
            info._read_head(reader, data, data['file_version'])
            info._read_players(reader, data, data['file_version'])
            info._read_forces(reader, data)
            return original[:reader.tell()]

    def test_it_writes_back_as_it_was(self):
        head = self._cut_after_forces()
        for ending in (b'\xff', b''):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, 'short.w3i')
                with open(source, 'wb') as handle:
                    handle.write(head + ending)
                info = W3iFile()
                info.read(source)
                self.assertEqual(info.get_data()['upgrades'], [])
                out = os.path.join(tmp, 'rebuilt.w3i')
                info.write(out)
                self.assertEqual(_helpers.read_bytes(out), head + ending)

    def test_a_complete_file_has_no_extra_keys(self):
        info = W3iFile()
        info.read(_helpers.sample_path('sample.w3i'))
        self.assertNotIn('stops_before', info.get_data())


if __name__ == '__main__':
    unittest.main()
