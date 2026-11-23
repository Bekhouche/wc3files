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
                    # EOFError too, for ModelFile (#141). struct.error is
                    # no longer accepted: MapDoodadsFile raised it (#140).
                    with self.assertRaises((ValueError, EOFError)):
                        cls().read(path)

    def test_doodads_cut_anywhere_or_damaged(self):
        import random
        rng = random.Random(140)
        for sample in ('sample_doodads_v8_11.doo', 'sample_doodads_v13_11.doo'):
            data = _helpers.read_bytes(_helpers.sample_path(sample))
            cases = [data[:cut] for cut in range(len(data))]
            for _ in range(200):
                damaged = bytearray(data)
                damaged[rng.randrange(len(data))] = rng.randrange(256)
                cases.append(bytes(damaged))
            for case in cases:
                with self.subTest(sample=sample, size=len(case)):
                    try:
                        MapDoodadsFile().read(case)
                    except ValueError:
                        pass
                    except Exception as exc:
                        self.fail(f'{type(exc).__name__}: {exc}')

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


class TestReadDoesNotAskForTheOffset(unittest.TestCase):
    """BinaryFile.read asked the file for its position before every read,
    only for a message it rarely needed: about a third of the time of
    reading a terrain or trigger file (#136). The offsets in the message
    must stay right."""

    class _Counting:
        def __init__(self, inner):
            self.inner, self.tells = inner, 0

        def tell(self):
            self.tells += 1
            return self.inner.tell()

        def __getattr__(self, name):
            return getattr(self.inner, name)

    def _reader(self, data, from_disk):
        if not from_disk:
            return BinaryFile(data)
        handle, path = tempfile.mkstemp()
        os.write(handle, data)
        os.close(handle)
        self.addCleanup(os.remove, path)
        return BinaryFile(path)

    def test_reads_do_not_call_tell(self):
        for from_disk in (False, True):
            with self.subTest(from_disk=from_disk):
                with self._reader(bytes(4000), from_disk) as reader:
                    reader.file = counting = self._Counting(reader.file)
                    for _ in range(500):
                        reader.read(BinaryDataType.INT, num=4)
                        reader.read(BinaryDataType.BYTES, num=4)
                    self.assertEqual(counting.tells, 0)

    def test_short_read_reports_its_offsets(self):
        for from_disk in (False, True):
            with self.subTest(from_disk=from_disk):
                with self._reader(bytes(10), from_disk) as reader:
                    reader.read(BinaryDataType.INT, num=4)
                    reader.read(BinaryDataType.INT, num=4)
                    with self.assertRaises(TruncatedDataError) as caught:
                        reader.read(BinaryDataType.INT, num=4)
                self.assertEqual(
                    str(caught.exception),
                    'the data ends at offset 10: 4 bytes were needed at offset 8, 2 are left')


if __name__ == '__main__':
    unittest.main()
