"""Real tests for war3map.w3s (SoundsFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import SoundsFile


class TestSoundsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.sf = SoundsFile()
        self.sf.read(_helpers.sample_path('sample.w3s'))

    def test_known_fields(self):
        data = self.sf.get_data()
        self.assertEqual(data['version'], 3)
        self.assertEqual(len(data['sounds']), 1)
        sound = data['sounds'][0]
        self.assertEqual(sound['name'], 'gg_snd_SampleSound')
        self.assertEqual(sound['volume'], 127)
        self.assertEqual(sound['priority'], -1)  # regression: must stay signed
        self.assertEqual(sound['dialogue_text_key'], -1)
        self.assertEqual(sound['trailing_marker'], 1)

    def test_signed_sentinel_fields_round_trip(self):
        # Regression: these fields legitimately use -1 as a sentinel and must
        # be read as signed - the first version read them unsigned, so -1
        # became 4294967295 and crashed on write (struct.pack('<i', ...) only
        # accepts the signed int32 range). See TODO.md.
        sound = self.sf.get_data()['sounds'][0]
        for field in ('priority', 'channel', 'cone_outside_volume',
                      'dialogue_text_key', 'dialogue_speaker_name_key'):
            self.assertEqual(sound[field], -1, field)

    def test_decode_flags(self):
        # sample's flags = 0x6 = 0x2 (is_3d) | 0x4 (stop_when_out_of_range)
        flags = SoundsFile.decode_flags(self.sf.get_data()['sounds'][0]['flags'])
        self.assertTrue(flags['is_3d'])
        self.assertTrue(flags['stop_when_out_of_range'])
        self.assertFalse(flags['looping'])
        self.assertFalse(flags['music'])

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3s'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3s')
            self.sf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_version_1_has_no_tail_or_head2(self):
        data = {
            'version': 1,
            'sounds': [{
                'name': 'gg_snd_Old', 'file_path': 'x.wav', 'eax_setting': '',
                'flags': 0, 'fade_in_rate': 10, 'fade_out_rate': 10, 'volume': -1,
                'pitch': 1.0, 'pitch_variance': 0.0, 'priority': -1, 'channel': -1,
                'min_distance': 0.0, 'max_distance': 0.0, 'distance_cutoff': 0.0,
                'cone_angle_inside': 0.0, 'cone_angle_outside': 0.0, 'cone_outside_volume': -1,
                'cone_orientation': (0.0, 0.0, 0.0),
            }],
        }
        sf = SoundsFile()
        sf._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'v1.w3s')
            sf.write(out)
            reread = SoundsFile()
            reread.read(out)
            self.assertNotIn('tail', reread.get_data()['sounds'][0])
            self.assertNotIn('trailing_marker', reread.get_data()['sounds'][0])

    def test_version_2_dialogue_fields_round_trip(self):
        # Disclosed: no real version-2 fixture was available locally, so this
        # is structurally tested only (see TODO.md) - the 8-byte tail length
        # follows the same documented formula as version 3's 9-byte one.
        data = {
            'version': 2,
            'sounds': [{
                'name': 'gg_snd_V2', 'file_path': 'x.wav', 'eax_setting': '',
                'flags': 0, 'fade_in_rate': 10, 'fade_out_rate': 10, 'volume': -1,
                'pitch': 1.0, 'pitch_variance': 0.0, 'priority': -1, 'channel': -1,
                'min_distance': 0.0, 'max_distance': 0.0, 'distance_cutoff': 0.0,
                'cone_angle_inside': 0.0, 'cone_angle_outside': 0.0, 'cone_outside_volume': -1,
                'cone_orientation': (0.0, 0.0, 0.0),
                'name_repeated': 'gg_snd_V2', 'slk_label': '', 'file_path_repeated': 'x.wav',
                'dialogue_text_key': -1, 'comments': '', 'dialogue_speaker_name_key': -1,
                'dialogue_unknown_1': '',
                'dialogue_unknown_2': 0,
            }],
        }
        sf = SoundsFile()
        sf._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'v2.w3s')
            sf.write(out)
            reread = SoundsFile()
            reread.read(out)
            sound = reread.get_data()['sounds'][0]
            # The five strings and the int that close a version-2 record
            # used to be read as one opaque 8-byte blob. They are fields.
            self.assertEqual(sound['dialogue_unknown_1'], '')
            self.assertEqual(sound['dialogue_unknown_2'], 0)
            self.assertNotIn('trailing_marker', sound)

    def test_rejects_unsupported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.w3s')
            with open(bad, 'wb') as fh:
                fh.write((4).to_bytes(4, 'little') + (0).to_bytes(4, 'little'))
            with self.assertRaises(ValueError):
                SoundsFile().read(bad)


if __name__ == '__main__':
    unittest.main()
