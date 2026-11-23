"""Every map-member reader takes the file's bytes as well as a path, so a
member of W3xFile.files can be parsed without a temporary file (#146).
RegionFile and PreviewIconsFile took the extension from the argument and
failed on bytes; ObjectFile needs object_type= for bytes, and says so."""
import os
import tempfile
import unittest

import _helpers
from wc3files import (CameraFile, ImpFile, MapCustomTextTriggersFile, MapDoodadsFile,
                      MapTriggersFile, MapUnitsFile, ObjectFile, PathingFile,
                      PreviewIconsFile, RegionFile, SoundsFile, TerrainFile, W3iFile)

READERS = [
    (W3iFile, 'sample.w3i', {}), (TerrainFile, 'sample.w3e', {}),
    (RegionFile, 'sample.w3r', {}), (CameraFile, 'sample.w3c', {}),
    (SoundsFile, 'sample.w3s', {}), (PathingFile, 'sample.wpm', {}),
    (PreviewIconsFile, 'sample.mmp', {}), (ImpFile, 'sample.imp', {}),
    (MapDoodadsFile, 'sample_doodads_v8_11.doo', {}), (MapUnitsFile, 'sample.doo', {}),
    (MapTriggersFile, 'sample.wtg', {}), (MapCustomTextTriggersFile, 'sample.wct', {}),
    (ObjectFile, 'sample.w3u', {'object_type': 'w3u'}),
]


class TestReadBytes(unittest.TestCase):

    def written(self, reader, name):
        path = os.path.join(tempfile.mkdtemp(), name)
        reader.write(path)
        return _helpers.read_bytes(path)

    def test_bytes_read_like_the_path(self):
        for cls, sample, options in READERS:
            with self.subTest(cls=cls.__name__):
                path = _helpers.sample_path(sample)
                from_path = cls()
                from_path.read(path)
                from_bytes = cls()
                from_bytes.read(_helpers.read_bytes(path), **options)
                self.assertEqual(self.written(from_bytes, sample),
                                 self.written(from_path, sample))

    def test_object_data_from_bytes_says_it_needs_a_type(self):
        data = _helpers.read_bytes(_helpers.sample_path('sample.w3u'))
        with self.assertRaises(ValueError) as caught:
            ObjectFile().read(data)
        self.assertIn('object_type=', str(caught.exception))

    def test_object_type_names_a_path_without_an_object_extension(self):
        data = _helpers.read_bytes(_helpers.sample_path('sample.w3u'))
        path = os.path.join(tempfile.mkdtemp(), 'units.bin')
        with open(path, 'wb') as handle:
            handle.write(data)
        objects = ObjectFile()
        objects.read(path, object_type='w3u')
        self.assertIn('w3u', objects.export_data())
        with self.assertRaises(ValueError) as caught:
            ObjectFile().read(path)
        self.assertIn('object_type=', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
