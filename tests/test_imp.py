"""Real tests for war3map.imp (ImpFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import ImpFile


class TestImpRoundTrip(unittest.TestCase):
    def setUp(self):
        self.imp = ImpFile()
        self.imp.read(_helpers.sample_path('sample.imp'))

    def test_known_fields(self):
        data = self.imp.get_data()
        self.assertEqual(data['version'], 1)
        self.assertEqual(len(data['files']), 2)
        self.assertEqual(data['files'][1]['flags'], 8)
        self.assertTrue(data['files'][0]['path'].endswith('sample.mdx'))

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.imp'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.imp')
            self.imp.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)


if __name__ == '__main__':
    unittest.main()
