"""Every map sub-file can be written to memory with to_bytes() (#173). Only
MapDoodadsFile and the image classes could; the others took only a path, so
a parsed member of a map could not be put back without a temporary file."""
import os
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
import wc3files as w

# (class, sample, read arguments, to_bytes arguments)
SAMPLES = [
    (w.TerrainFile, 'sample.w3e', (), {}),
    (w.W3iFile, 'sample.w3i', (), {}),
    (w.WtsFile, 'sample.wts', (), {}),
    (w.ObjectFile, 'sample.w3u', (), {}),
    (w.MapTriggersFile, 'sample.wtg', (), {}),
    (w.MapCustomTextTriggersFile, 'sample.wct', (), {}),
    (w.ScriptFile, 'sample.j', (), {}),
    (w.ScriptFile, 'sample.lua', (), {}),
    (w.RegionFile, 'sample.w3r', (), {}),
    (w.CameraFile, 'sample.w3c', (), {}),
    (w.SoundsFile, 'sample.w3s', (), {}),
    (w.PathingFile, 'sample.wpm', (), {}),
    (w.ShadowMapFile, 'sample.shd', (3, 3), {}),
    (w.PreviewIconsFile, 'sample.mmp', (), {}),
    (w.ImpFile, 'sample.imp', (), {}),
    (w.MapUnitsFile, 'sample.doo', (), {}),
    (w.MapDoodadsFile, 'sample_doodads_v8_11.doo', (), {}),
    (w.CampaignInfoFile, 'sample.w3f', (), {}),
]


class TestToBytes(unittest.TestCase):

    def test_to_bytes_is_what_write_saves(self):
        tmp = tempfile.mkdtemp()
        for cls, sample, read_args, kwargs in SAMPLES:
            with self.subTest(sample=sample):
                handler = cls()
                handler.read(sample_path(sample), *read_args)
                data = handler.to_bytes(**kwargs)
                self.assertIsInstance(data, bytes)
                out = os.path.join(tmp, sample)
                handler.write(out)
                with open(out, 'rb') as handle:
                    self.assertEqual(handle.read(), data)

    def test_object_file_type_follows_the_read_or_the_argument(self):
        source = w.ObjectFile()
        source.read(sample_path('sample.w3u'))
        fresh = w.ObjectFile().import_data(source.get_data())
        self.assertEqual(fresh.to_bytes('w3u'), source.to_bytes())
        # Nothing was read: a war3map.w3o, which holds every object type.
        combined = w.ObjectFile()
        combined.read(fresh.to_bytes(), object_type='w3o')
        self.assertEqual(combined.to_bytes('w3u'), source.to_bytes())

    def test_a_parsed_member_goes_back_into_the_map(self):
        archive = w.W3xFile()
        archive.read(sample_path('sample.w3x'))
        info = w.W3iFile()
        info.read(archive.files['war3map.w3i'])
        info.get_data()['map_name'] = 'Edited in memory'
        archive.files['war3map.w3i'] = info.to_bytes()

        out = os.path.join(tempfile.mkdtemp(), 'edited.w3x')
        archive.build_from_files(archive.files, out)
        again = w.W3xFile()
        again.read(out)
        check = w.W3iFile()
        check.read(again.files['war3map.w3i'])
        self.assertEqual(check.get_data()['map_name'], 'Edited in memory')


if __name__ == '__main__':
    unittest.main()
