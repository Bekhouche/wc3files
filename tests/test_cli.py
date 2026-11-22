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

    def test_a_command_reporting_failure_sets_the_exit_status(self):
        # img-convert prints the error and returns 1 itself rather than
        # raising; main() used to drop that and exit 0.
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(['img-convert', _helpers.sample_path('sample.blp'),
                       os.path.join(tmp, 'out.png'), '--resize', 'big'])
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


class TestVersionFlag(unittest.TestCase):
    def test_version_prints_the_package_version_and_exits_with_zero(self):
        import contextlib
        import io
        import wc3files
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as caught:
            main(['--version'])
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f'wc3files {wc3files.__version__}')


class TestPythonDashM(unittest.TestCase):
    """`python -m wc3files` failed: "No module named wc3files.__main__"."""

    def test_python_m_wc3files(self):
        import subprocess
        import sys
        import wc3files
        src = os.path.dirname(os.path.dirname(os.path.abspath(wc3files.__file__)))
        env = dict(os.environ, PYTHONPATH=src)
        for module in ('wc3files', 'wc3files.cli'):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, '-m', module, '--version'],
                                        capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'wc3files {wc3files.__version__}')


class TestProtectedMaps(unittest.TestCase):
    """unpack, lni, slk, obj and optimize read a map without its (listfile)
    as almost empty and wrote that out with status 0; run-map said it had no
    script. They now refuse a protected map, and read it with --deep."""

    SCRIPT = b'function main takes nothing returns nothing\nendfunction\n'

    def setUp(self):
        from wc3files import W3xFile
        from wc3files.mpq.data import MpqProtectionLevel
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.map = os.path.join(self.tmp.name, 'protected.w3x')
        W3xFile().build_from_files({'war3map.j': self.SCRIPT, 'war3map.w3i': b'\x00' * 64},
                                   self.map, protection=MpqProtectionLevel.SAFE)

    def run_quietly(self, argv):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = main(argv)
        return rc, err.getvalue()

    def commands(self, out):
        return {
            'unpack': ['unpack', self.map, os.path.join(out, 'unpacked')],
            'lni': ['lni', self.map, os.path.join(out, 'lni')],
            'slk': ['slk', self.map, os.path.join(out, 'slk.w3x')],
            'obj': ['obj', self.map, os.path.join(out, 'obj.w3x')],
            'optimize': ['optimize', self.map, os.path.join(out, 'opt.w3x'), '--remove-we-only'],
            'run-map': ['run-map', self.map, '--seconds', '0'],
        }

    def test_refused_without_deep(self):
        for name, argv in self.commands(self.tmp.name).items():
            with self.subTest(command=name):
                rc, err = self.run_quietly(argv)
                self.assertEqual(rc, 1)
                self.assertIn('is protected: it has no (listfile)', err)
                self.assertIn('--deep', err)
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ['protected.w3x'])

    def test_read_with_deep(self):
        from wc3files import W3xFile
        out = self.tmp.name
        for name, argv in self.commands(out).items():
            with self.subTest(command=name):
                rc, err = self.run_quietly(argv + ['--deep'])
                self.assertEqual(rc, 0, err)
                if name != 'run-map':
                    self.assertIn('note:', err)
        with open(os.path.join(out, 'unpacked', 'protected', 'war3map.j'), 'rb') as handle:
            self.assertEqual(handle.read(), self.SCRIPT)
        for built in ('slk.w3x', 'obj.w3x', 'opt.w3x'):
            archive = W3xFile()
            archive.read(os.path.join(out, built))
            self.assertEqual(archive.files.get('war3map.j'), self.SCRIPT, built)


if __name__ == '__main__':
    unittest.main()
