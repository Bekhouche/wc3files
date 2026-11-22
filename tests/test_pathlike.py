"""Every file reader and writer takes a pathlib.Path as well as a str, as
open() does. Each case reads a checked-in sample through a Path, writes it
through a Path, and checks the bytes match what a str path gives."""
import os
import pathlib
import tempfile
import unittest

import _helpers
from wc3files.doo.map_doodads_file import MapDoodadsFile
from wc3files.doo.map_units_file import MapUnitsFile
from wc3files.img import BlpFile, BmpFile, DdsFile, TgaFile, TiffFile
from wc3files.imp.imp_file import ImpFile
from wc3files.mdl.model_file import ModelFile
from wc3files.mmp.preview_icons_file import PreviewIconsFile
from wc3files.mpq.mpq import MpqFile
from wc3files.scripts.script_file import ScriptFile
from wc3files.shd.shadow_map_file import ShadowMapFile
from wc3files.table.wts_file import WtsFile
from wc3files.w3c.camera_file import CameraFile
from wc3files.w3e.terrain_file import TerrainFile
from wc3files.w3f.campaign_info_file import CampaignInfoFile
from wc3files.w3i.w3i_file import W3iFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3r.region_file import RegionFile
from wc3files.w3s.sounds_file import SoundsFile
from wc3files.w3x.w3x_file import W3xFile
from wc3files.wpm.pathing_file import PathingFile
from wc3files.wtg.wct_file import MapCustomTextTriggersFile
from wc3files.wtg.wtg_file import MapTriggersFile

# (class, sample, extra read arguments)
READ_WRITE = [
    (W3iFile, 'sample.w3i', ()),
    (TerrainFile, 'sample.w3e', ()),
    (RegionFile, 'sample.w3r', ()),
    (CameraFile, 'sample.w3c', ()),
    (SoundsFile, 'sample.w3s', ()),
    (PathingFile, 'sample.wpm', ()),
    (PreviewIconsFile, 'sample.mmp', ()),
    (ImpFile, 'sample.imp', ()),
    (MapDoodadsFile, 'sample_doodads_v8_11.doo', ()),
    (MapUnitsFile, 'sample.doo', ()),
    (MapTriggersFile, 'sample.wtg', ()),
    (MapCustomTextTriggersFile, 'sample.wct', ()),
    (CampaignInfoFile, 'sample.w3f', ()),
    (ObjectFile, 'sample.w3u', ()),
    (ModelFile, 'sample.mdx', ()),
    (ModelFile, 'sample.mdl', ()),
    (ScriptFile, 'sample.j', ()),
    (ScriptFile, 'sample.lua', ()),
    (ShadowMapFile, 'sample.shd', (3, 3)),
    (WtsFile, 'sample.wts', ()),
    (BmpFile, 'sample.bmp', ()),
    (TgaFile, 'sample.tga', ()),
    (TiffFile, 'sample.tif', ()),
]

# Read only: these writers take an image built with from_rgba(), not one
# that was read.
READ_ONLY_IMAGES = [
    (BlpFile, 'sample.blp'),
    (DdsFile, 'sample.dds'),
]


def _round_trip(cls, source, target, extra):
    instance = cls()
    result = instance.read(source, *extra)
    # A few readers return a new object instead of filling in self.
    if result is not None and isinstance(result, cls):
        instance = result
    instance.write(target)
    with open(target, 'rb') as handle:
        return handle.read()


class TestPathLike(unittest.TestCase):
    def test_every_reader_and_writer_accepts_a_path(self):
        for cls, sample, extra in READ_WRITE:
            with self.subTest(cls=cls.__name__, sample=sample):
                source = _helpers.sample_path(sample)
                ext = os.path.splitext(sample)[1]
                with tempfile.TemporaryDirectory() as tmp:
                    from_str = _round_trip(cls, source, os.path.join(tmp, 'a' + ext), extra)
                    from_path = _round_trip(cls, pathlib.Path(source),
                                            pathlib.Path(tmp) / ('b' + ext), extra)
                self.assertEqual(from_path, from_str)

    def test_image_readers_accept_a_path(self):
        for cls, sample in READ_ONLY_IMAGES:
            with self.subTest(cls=cls.__name__):
                source = _helpers.sample_path(sample)
                from_str, from_path = cls(), cls()
                from_str.read(source)
                from_path.read(pathlib.Path(source))
                self.assertEqual(from_path.get_rgba(), from_str.get_rgba())

    def test_map_archives_accept_a_path(self):
        source = _helpers.sample_path('sample.w3x')
        expected = W3xFile()
        expected.read(source)

        archive = W3xFile()
        archive.read(pathlib.Path(source))
        self.assertEqual(archive.files, expected.files)
        self.assertEqual(archive.current_name, expected.current_name)

        mpq = MpqFile()
        mpq.read(pathlib.Path(source))
        mpq.read_files()
        self.assertEqual(mpq.current_name, 'sample')
        self.assertIn('war3map.j', mpq.files)

        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp)
            archive.build_from_files(archive.files, out / 'rebuilt.w3x')
            archive.export(out)
            self.assertTrue((out / 'sample' / 'war3map.j').exists())
            archive.build(out / 'sample', out / 'repacked.w3x')
            again = W3xFile()
            again.read(out / 'repacked.w3x')
            self.assertEqual(again.files['war3map.j'], expected.files['war3map.j'])


if __name__ == '__main__':
    unittest.main()
