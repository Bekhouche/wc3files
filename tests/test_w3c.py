"""Real tests for war3map.w3c (CameraFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import CameraFile


class TestCameraRoundTrip(unittest.TestCase):
    def setUp(self):
        self.cf = CameraFile()
        self.cf.read(_helpers.sample_path('sample.w3c'))

    def test_known_fields(self):
        data = self.cf.get_data()
        self.assertEqual(data['version'], 0)
        self.assertTrue(data['has_local_rotation'])
        self.assertEqual(len(data['cameras']), 1)
        camera = data['cameras'][0]
        self.assertEqual(camera['cinematic_name'], 'Sample Camera')
        self.assertEqual(camera['field_of_view'], 70.0)
        self.assertEqual(camera['local_roll'], 128.0)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3c'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3c')
            self.cf.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_legacy_10_float_layout_round_trips(self):
        # Not confirmed against any real fixture (none of the 8 real local w3c
        # fixtures use this older layout - all either have zero cameras or use
        # the 13-float one) - structurally tested only, disclosed honestly
        # rather than silently assumed correct. See TODO.md.
        data = {
            'version': 0, 'has_local_rotation': False,
            'cameras': [{
                'target_x': 1.0, 'target_y': 2.0, 'z_offset': 3.0, 'rotation': 4.0,
                'angle_of_attack': 5.0, 'distance': 6.0, 'roll': 7.0,
                'field_of_view': 8.0, 'far_clipping_plane': 9.0, 'near_clipping_plane': 10.0,
                'cinematic_name': 'Old Camera',
            }],
        }
        cf = CameraFile()
        cf._data = data
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'legacy.w3c')
            cf.write(out)
            reread = CameraFile()
            reread.read(out)
            self.assertEqual(reread.get_data()['has_local_rotation'], False)
            self.assertEqual(reread.get_data()['cameras'][0]['cinematic_name'], 'Old Camera')
            self.assertNotIn('local_pitch', reread.get_data()['cameras'][0])

    def test_rejects_unsupported_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.w3c')
            with open(bad, 'wb') as fh:
                fh.write((1).to_bytes(4, 'little') + (0).to_bytes(4, 'little'))
            with self.assertRaises(ValueError):
                CameraFile().read(bad)


if __name__ == '__main__':
    unittest.main()
