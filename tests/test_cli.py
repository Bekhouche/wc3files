"""Real tests for the installed wc3files CLI (dump/build and unpack/pack),
invoked in-process via main(argv) - matches how TODO.md documents this CLI
having been verified (through the actual command, not just the library)."""
import json
import os
import tempfile
import unittest

import _helpers
from wc3files.cli.main import main
from wc3files.img import read_png, CLASSIC_BORDERS, REFORGED_BORDERS, border_path


class TestDumpBuildRoundTrip(unittest.TestCase):
    FORMATS_AND_SAMPLES = [
        ('w3i', 'sample.w3i'),
        ('doo', 'sample.doo'),
        ('imp', 'sample.imp'),
        ('w3f', 'sample.w3f'),
        ('wtg', 'sample.wtg'),
        ('wct', 'sample.wct'),
    ]

    def test_dump_then_build_reproduces_the_original_bytes(self):
        for fmt, sample_name in self.FORMATS_AND_SAMPLES:
            with self.subTest(format=fmt):
                original = _helpers.read_bytes(_helpers.sample_path(sample_name))
                with tempfile.TemporaryDirectory() as tmp:
                    json_path = os.path.join(tmp, 'dump.json')
                    rebuilt_path = os.path.join(tmp, 'rebuilt')

                    rc = main(['dump', fmt, _helpers.sample_path(sample_name), json_path])
                    self.assertEqual(rc, 0)
                    with open(json_path, encoding='utf-8') as fh:
                        dumped = json.load(fh)
                    self.assertTrue(dumped)

                    rc = main(['build', fmt, json_path, rebuilt_path])
                    self.assertEqual(rc, 0)
                    self.assertEqual(_helpers.read_bytes(rebuilt_path), original)


class TestUnpackPackRoundTrip(unittest.TestCase):
    def test_unpack_then_pack_preserves_member_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            unpack_dir = os.path.join(tmp, 'unpacked')
            repacked = os.path.join(tmp, 'repacked.w3x')

            rc = main(['unpack', _helpers.sample_path('sample.w3x'), unpack_dir])
            self.assertEqual(rc, 0)

            map_dirs = os.listdir(unpack_dir)
            self.assertEqual(len(map_dirs), 1)
            map_dir = os.path.join(unpack_dir, map_dirs[0])
            self.assertTrue(os.path.exists(os.path.join(map_dir, 'war3map.j')))

            rc = main(['pack', map_dir, repacked])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(repacked))

            second_unpack = os.path.join(tmp, 'unpacked2')
            rc = main(['unpack', repacked, second_unpack])
            self.assertEqual(rc, 0)
            second_map_dir = os.path.join(second_unpack, os.listdir(second_unpack)[0])
            with open(os.path.join(map_dir, 'war3map.j'), 'rb') as fh:
                first = fh.read()
            with open(os.path.join(second_map_dir, 'war3map.j'), 'rb') as fh:
                second = fh.read()
            self.assertEqual(first, second)


class TestCliErrorHandling(unittest.TestCase):
    def test_dump_of_missing_file_returns_error_code(self):
        rc = main(['dump', 'w3i', '/no/such/file.w3i', '-'])
        self.assertEqual(rc, 1)


class TestBundledIconBorders(unittest.TestCase):
    """wc3files.img.borders - real border art bundled with this package (see
    TODO.md for provenance) as ready-made defaults for make-icon/
    make-icon-set's --border-family flags."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.source = _helpers.sample_path('BTNMountainKing.blp')

    def test_every_bundled_border_file_decodes_at_its_documented_size(self):
        for kind in CLASSIC_BORDERS:
            with self.subTest(style='classic', kind=kind):
                width, height, _rgba = read_png(border_path(kind, reforged=False))
                self.assertEqual((width, height), (64, 64))
        for kind in REFORGED_BORDERS:
            with self.subTest(style='reforged', kind=kind):
                width, height, _rgba = read_png(border_path(kind, reforged=True))
                self.assertEqual((width, height), (256, 256))

    def test_make_icon_with_bundled_border_applies_a_real_border(self):
        plain_path = os.path.join(self.tmpdir.name, 'plain.png')
        bordered_path = os.path.join(self.tmpdir.name, 'bordered.png')

        self.assertEqual(main(['make-icon', self.source, plain_path]), 0)
        self.assertEqual(
            main(['make-icon', self.source, bordered_path, '--bundled-border', 'classic']), 0)

        _w, _h, plain_rgba = read_png(plain_path)
        _w, _h, bordered_rgba = read_png(bordered_path)
        self.assertNotEqual(plain_rgba, bordered_rgba)

    def test_explicit_border_takes_precedence_over_bundled(self):
        # A solid, fully-opaque magenta 64x64 "border" - trivially distinguishable
        # from any bundled art, so precedence is unambiguous either way it goes.
        custom_border_path = os.path.join(self.tmpdir.name, 'custom_border.png')
        with open(custom_border_path, 'wb') as fh:
            from wc3files.img import write_png
            fh.write(write_png(64, 64, b'\xff\x00\xff\xff' * (64 * 64)))

        out_path = os.path.join(self.tmpdir.name, 'out.png')
        rc = main(['make-icon', self.source, out_path,
                   '--border', custom_border_path, '--bundled-border', 'classic'])
        self.assertEqual(rc, 0)

        _w, _h, rgba = read_png(out_path)
        self.assertEqual(rgba, b'\xff\x00\xff\xff' * (64 * 64))  # fully overwritten by the custom border

    def test_make_icon_set_assigns_a_distinct_bundled_border_per_kind(self):
        out_dir = os.path.join(self.tmpdir.name, 'icon_set')
        rc = main(['make-icon-set', self.source, out_dir, 'Test',
                   '--kinds', 'active,passive,autocast,upgrade,attack',
                   '--bundled-borders', 'classic', '--format', 'png'])
        self.assertEqual(rc, 0)

        paths = {
            'active': os.path.join(out_dir, 'ReplaceableTextures', 'CommandButtons', 'BTNTest.png'),
            'passive': os.path.join(out_dir, 'ReplaceableTextures', 'PassiveButtons', 'PASTest.png'),
            'autocast': os.path.join(out_dir, 'ReplaceableTextures', 'CommandButtons', 'ATCTest.png'),
            'upgrade': os.path.join(out_dir, 'ReplaceableTextures', 'CommandButtons', 'UPGTest.png'),
            'attack': os.path.join(out_dir, 'ReplaceableTextures', 'ATTTest.png'),
        }
        rgba_by_kind = {}
        for kind, path in paths.items():
            self.assertTrue(os.path.exists(path), f'missing output for {kind}: {path}')
            _w, _h, rgba_by_kind[kind] = read_png(path)

        # Each kind's bundled border is a genuinely different real asset (see
        # CLASSIC_BORDERS) - the composited results should differ pairwise,
        # not all collapse to the same (e.g. always-"active") border by a
        # wiring bug.
        kinds = list(rgba_by_kind)
        for i, a in enumerate(kinds):
            for b in kinds[i + 1:]:
                self.assertNotEqual(rgba_by_kind[a], rgba_by_kind[b],
                                     f'{a} and {b} produced identical output - border wiring bug')


if __name__ == '__main__':
    unittest.main()
