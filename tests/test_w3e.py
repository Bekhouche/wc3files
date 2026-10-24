"""Real tests for war3map.w3e (TerrainFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import TerrainFile


class TestTerrainRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tf = TerrainFile()
        self.tf.read(_helpers.sample_path('sample.w3e'))

    def test_known_fields(self):
        data = self.tf.get_data()
        self.assertEqual(data['version'], 11)
        self.assertEqual(data['main_tileset'], 'Y')
        self.assertTrue(data['custom_tileset'])
        self.assertEqual(data['ground_tilesets'], ['Ydrt', 'Yblm'])
        self.assertEqual(data['width'], 2)
        self.assertEqual(data['height'], 2)
        self.assertEqual(len(data['tiles']), 4)
        self.assertTrue(data['tiles'][0]['is_map_edge'])
        self.assertFalse(data['tiles'][1]['is_map_edge'])

    def test_ground_variation_exceeds_a_nibble(self):
        # Regression: War3Net's 4-bit/4-bit nibble split for the variation byte
        # can't represent this - confirmed against real fixtures with variation
        # values of 16/17, impossible under a 4-bit split (max 15). This package
        # uses the HiveWE/mdx-m3-viewer 5-bit ground / 3-bit cliff split instead.
        tile = self.tf.get_data()['tiles'][0]
        self.assertEqual(tile['ground_variation'], 17)
        self.assertEqual(tile['cliff_variation'], 2)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3e'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3e')
            self.tf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_rejects_unsupported_version(self):
        data = dict(self.tf.get_data())
        data['version'] = 99
        bad = TerrainFile()
        bad._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'bad.w3e')
            bad.write(out)
            with self.assertRaises(ValueError):
                TerrainFile().read(out)

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.w3e')
            with open(bad, 'wb') as fh:
                fh.write(b'NOPE' + b'\x00' * 20)
            with self.assertRaises(ValueError):
                TerrainFile().read(bad)


if __name__ == '__main__':
    unittest.main()
