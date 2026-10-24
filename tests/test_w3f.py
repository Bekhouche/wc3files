"""Real tests for war3campaign.w3f (CampaignInfoFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import CampaignInfoFile


class TestW3fRoundTrip(unittest.TestCase):
    def setUp(self):
        self.w3f = CampaignInfoFile()
        self.w3f.read(_helpers.sample_path('sample.w3f'))

    def test_known_fields(self):
        data = self.w3f.get_data()
        self.assertEqual(data['campaign_name'], 'Sample Campaign')
        self.assertEqual(data['format_version'], 2)
        self.assertEqual(len(data['map_buttons']), 1)
        self.assertEqual(data['map_buttons'][0]['chapter'], 'Chapter One')

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3f'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3f')
            self.w3f.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_flag_helpers_round_trip(self):
        flags = {'requires_expansion': True}
        encoded = CampaignInfoFile.encode_flags(flags)
        decoded = CampaignInfoFile.decode_flags(encoded)
        self.assertTrue(decoded['requires_expansion'])
        self.assertFalse(decoded['variable_difficulty_levels'])


if __name__ == '__main__':
    unittest.main()
