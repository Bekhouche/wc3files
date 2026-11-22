"""A truncated file raises instead of being read.

BinaryFile used to return short reads as values: an INT past the end came
back as 0, BYTES came back short, and six readers accepted a cut file without
any error. Every reader here gets each checked-in sample cut in half and cut
three bytes short, and must refuse both."""
import os
import struct
import tempfile
import unittest

import _helpers
from wc3files.doo.map_doodads_file import MapDoodadsFile
from wc3files.doo.map_units_file import MapUnitsFile
from wc3files.files.binary_file import BinaryFile, TruncatedDataError
from wc3files.data import BinaryDataType
from wc3files.imp.imp_file import ImpFile
from wc3files.mdl.model_file import ModelFile
from wc3files.mmp.preview_icons_file import PreviewIconsFile
from wc3files.w3c.camera_file import CameraFile
from wc3files.w3e.terrain_file import TerrainFile
from wc3files.w3f.campaign_info_file import CampaignInfoFile
from wc3files.w3i.w3i_file import W3iFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3r.region_file import RegionFile
from wc3files.w3s.sounds_file import SoundsFile
from wc3files.wpm.pathing_file import PathingFile
from wc3files.wtg.wct_file import MapCustomTextTriggersFile
from wc3files.wtg.wtg_file import MapTriggersFile

READERS = [
    (W3iFile, 'sample.w3i'), (TerrainFile, 'sample.w3e'), (RegionFile, 'sample.w3r'),
    (CameraFile, 'sample.w3c'), (SoundsFile, 'sample.w3s'), (PathingFile, 'sample.wpm'),
    (PreviewIconsFile, 'sample.mmp'), (ImpFile, 'sample.imp'),
    (MapDoodadsFile, 'sample_doodads_v8_11.doo'), (MapUnitsFile, 'sample.doo'),
    (MapTriggersFile, 'sample.wtg'), (MapCustomTextTriggersFile, 'sample.wct'),
    (CampaignInfoFile, 'sample.w3f'), (ObjectFile, 'sample.w3u'), (ModelFile, 'sample.mdx'),
]


class TestTruncatedFilesAreRefused(unittest.TestCase):
    def test_every_reader_refuses_a_cut_file(self):
        for cls, sample in READERS:
            data = _helpers.read_bytes(_helpers.sample_path(sample))
            for cut in (len(data) // 2, len(data) - 3):
                with self.subTest(cls=cls.__name__, cut=cut), tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, 'cut' + os.path.splitext(sample)[1])
                    with open(path, 'wb') as handle:
                        handle.write(data[:cut])
                    # struct.error too: MapDoodadsFile parses its bytes with
                    # struct directly, and refuses that way.
                    with self.assertRaises((ValueError, EOFError, struct.error)):
                        cls().read(path)

    def test_the_complete_samples_still_read(self):
        for cls, sample in READERS:
            with self.subTest(cls=cls.__name__):
                cls().read(_helpers.sample_path(sample))


class TestBinaryFileShortReads(unittest.TestCase):
    def test_a_short_int_bytes_or_real_raises(self):
        for data_type, kwargs in ((BinaryDataType.INT, {}), (BinaryDataType.REAL, {}),
                                  (BinaryDataType.BYTES, {'num': 4})):
            with self.subTest(data_type=data_type), BinaryFile(b'\x01\x02') as reader:
                with self.assertRaises(TruncatedDataError) as caught:
                    reader.read(data_type, **kwargs)
                self.assertIn('offset 0', str(caught.exception))

    def test_reading_at_the_end_raises(self):
        with BinaryFile(b'\x01\x00\x00\x00') as reader:
            self.assertEqual(reader.read(BinaryDataType.INT), 1)
            self.assertEqual(reader.remaining(), 0)
            with self.assertRaises(TruncatedDataError):
                reader.read(BinaryDataType.INT)

    def test_a_string_without_its_terminator_raises(self):
        with BinaryFile(b'abc') as reader, self.assertRaises(TruncatedDataError):
            reader.read(BinaryDataType.STRING)

    def test_it_is_both_an_eof_error_and_a_value_error(self):
        # So code catching ValueError to try another layout still can.
        self.assertTrue(issubclass(TruncatedDataError, EOFError))
        self.assertTrue(issubclass(TruncatedDataError, ValueError))


if __name__ == '__main__':
    unittest.main()
