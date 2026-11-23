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


class TestGridInOnePass(unittest.TestCase):
    """The tile grid is read and written as one block, not field by field:
    five reads per tile point made w3e the slowest map format (#137)."""

    def _count(self, method):
        from wc3files.files import BinaryFile
        calls = []
        original = getattr(BinaryFile, method)

        def counting(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)
        setattr(BinaryFile, method, counting)
        self.addCleanup(setattr, BinaryFile, method, original)
        return calls

    def test_read_and_write_calls_do_not_grow_with_the_grid(self):
        import copy
        terrain = TerrainFile()
        terrain.read(_helpers.sample_path('sample.w3e'))
        data = terrain.get_data()
        data['width'] = data['height'] = 30
        data['tiles'] = [copy.deepcopy(data['tiles'][i % 4]) for i in range(900)]
        path = os.path.join(tempfile.mkdtemp(), 'war3map.w3e')
        writes = self._count('write')
        terrain.write(path)
        self.assertLess(len(writes), 50)
        reads = self._count('read')
        again = TerrainFile()
        again.read(path)
        self.assertLess(len(reads), 50)
        self.assertEqual(again.get_data(), data)

    def test_out_of_range_height_still_raises(self):
        terrain = TerrainFile()
        terrain.read(_helpers.sample_path('sample.w3e'))
        terrain.get_data()['tiles'][0]['ground_height'] = 70000
        with self.assertRaises(Exception):
            terrain.write(os.path.join(tempfile.mkdtemp(), 'war3map.w3e'))


if __name__ == '__main__':
    unittest.main()
