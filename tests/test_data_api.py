"""Every file class moves its data in and out with get_data() and import_data()
(#174). Eleven classes had only get_data(), seven only export_data() and
import_data(), and RegionFile and PreviewIconsFile neither, so the CLI set the
private _data to build a file."""
import inspect
import os
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
import wc3files as w

# Classes whose data is not one dict or object: images are pixel buffers,
# MpqFile and W3xFile hold archive members (W3xFile.files).
NOT_DATA = {'BinaryFile', 'BlpFile', 'BmpFile', 'DdsFile', 'TgaFile', 'TiffFile',
            'MpqFile', 'W3xFile'}

# (class, sample, read arguments) for a copy-and-write check.
SAMPLES = [
    (w.CameraFile, 'sample.w3c', ()),
    (w.CampaignInfoFile, 'sample.w3f', ()),
    (w.ImpFile, 'sample.imp', ()),
    (w.MapCustomTextTriggersFile, 'sample.wct', ()),
    (w.MapTriggersFile, 'sample.wtg', ()),
    (w.MapDoodadsFile, 'sample_doodads_v8_11.doo', ()),
    (w.MapUnitsFile, 'sample.doo', ()),
    (w.PathingFile, 'sample.wpm', ()),
    (w.SoundsFile, 'sample.w3s', ()),
    (w.TerrainFile, 'sample.w3e', ()),
    (w.W3iFile, 'sample.w3i', ()),
    (w.RegionFile, 'sample.w3r', ()),
    (w.PreviewIconsFile, 'sample.mmp', ()),
    (w.ModelFile, 'sample.mdx', ()),
    (w.ObjectFile, 'sample.w3u', ()),
    (w.WtsFile, 'sample.wts', ()),
]


def file_classes():
    for name in sorted(dir(w)):
        obj = getattr(w, name)
        if inspect.isclass(obj) and name.endswith('File') and name not in NOT_DATA:
            yield name, obj


class TestDataApi(unittest.TestCase):

    def test_every_file_class_has_get_data_and_import_data(self):
        missing = [name for name, cls in file_classes()
                   if not (callable(getattr(cls, 'get_data', None))
                           and callable(getattr(cls, 'import_data', None)))]
        self.assertEqual(missing, [])

    def test_export_data_is_still_there(self):
        for cls in (w.ObjectFile, w.ModelFile, w.WtsFile, w.SlkFile):
            with self.subTest(cls=cls.__name__):
                self.assertIs(cls.get_data, cls.export_data)

    def test_a_copy_made_from_get_data_writes_the_same_bytes(self):
        tmp = tempfile.mkdtemp()
        for cls, sample, args in SAMPLES:
            with self.subTest(cls=cls.__name__):
                source = sample_path(sample)
                original = cls()
                original.read(source, *args)
                copy = cls()
                self.assertIs(copy.import_data(original.get_data()), copy)
                ext = os.path.splitext(sample)[1]
                a = os.path.join(tmp, 'a' + ext)
                b = os.path.join(tmp, 'b' + ext)
                original.write(a)
                copy.write(b)
                with open(a, 'rb') as fa, open(b, 'rb') as fb:
                    self.assertEqual(fa.read(), fb.read())

    def test_region_and_icon_counts_follow_import_data(self):
        regions = w.RegionFile()
        regions.read(sample_path('sample.w3r'))
        data = regions.get_data()
        self.assertIs(data, regions.get_regions())
        first = next(iter(data))
        fresh = w.RegionFile().import_data({first: data[first]})
        self.assertEqual(fresh.number, 1)

        icons = w.PreviewIconsFile()
        icons.read(sample_path('sample.mmp'))
        self.assertIs(icons.get_data(), icons.get_icons())
        fresh = w.PreviewIconsFile().import_data({})
        self.assertEqual(fresh.number, 0)


if __name__ == '__main__':
    unittest.main()
