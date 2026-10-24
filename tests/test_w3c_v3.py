"""Tests for war3map.w3c format 3 (Reforged cameras).

Format 3 was refused outright ("only 0 is implemented"), which left 14 of
the 34 camera files in the benchmark corpus unreadable. It differs from
format 0 by three further floats before the cinematic name and a dword after
it, measured against real maps: a one-camera format 0 record is 63 bytes,
the format 3 equivalents 79 and 88. All 34 files now round-trip byte-for-byte.
"""
import os
import struct
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.w3c import CameraFile


def round_trip(instance, source):
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, 'out.w3c')
        instance.write(target)
        with open(source, 'rb') as handle:
            original = handle.read()
        with open(target, 'rb') as handle:
            rebuilt = handle.read()
    return original, rebuilt


class TestVersion3(unittest.TestCase):
    def setUp(self):
        self.path = sample_path('sample_v3.w3c')
        self.cameras = CameraFile()
        self.cameras.read(self.path)

    def test_it_reads_the_version_and_camera(self):
        data = self.cameras.get_data()
        self.assertEqual(data['version'], 3)
        self.assertEqual(len(data['cameras']), 1)

    def test_the_name_decodes(self):
        camera = self.cameras.get_data()['cameras'][0]
        self.assertEqual(camera['cinematic_name'], 'Sample Intro Shot')

    def test_the_v3_specific_fields_are_present(self):
        camera = self.cameras.get_data()['cameras'][0]
        for key in ('dof_distance', 'dof_scale', 'pos_absolute_z'):
            self.assertIn(key, camera)
        self.assertEqual(len(camera['free_camera']), 4)

    def test_it_round_trips_byte_for_byte(self):
        original, rebuilt = round_trip(self.cameras, self.path)
        self.assertEqual(original, rebuilt)

    def test_the_base_fields_still_decode(self):
        camera = self.cameras.get_data()['cameras'][0]
        for key in ('target_x', 'target_y', 'rotation', 'distance'):
            self.assertIn(key, camera)


class TestVersion0StillWorks(unittest.TestCase):
    def test_the_original_fixture_round_trips(self):
        path = sample_path('sample.w3c')
        cameras = CameraFile()
        cameras.read(path)
        self.assertEqual(cameras.get_data()['version'], 0)
        original, rebuilt = round_trip(cameras, path)
        self.assertEqual(original, rebuilt)


class TestRefusal(unittest.TestCase):
    def test_an_unknown_version_still_raises(self):
        with open(sample_path('sample_v3.w3c'), 'rb') as handle:
            data = bytearray(handle.read())
        struct.pack_into('<i', data, 0, 9)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'v9.w3c')
            with open(path, 'wb') as handle:
                handle.write(bytes(data))
            with self.assertRaises(ValueError) as caught:
                CameraFile().read(path)
        self.assertIn('Unsupported', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
