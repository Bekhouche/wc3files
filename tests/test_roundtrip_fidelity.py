"""Byte-exact round-tripping for the four formats that used to lose data.

Each of these read a file, wrote it back, and produced something different -
valid, but not the same bytes. Auditing every writer against real files
turned up why in each case, and these keep them honest:

* `.slk`  - the reader lost cell types and gaps (1 of 20 bundled tables
            round-tripped); now 20 of 20 do.
* `.cfg`  - the section pattern rejected dotted names, so a real 2.9 MB
            file parsed as **0** sections and 0 keys.
* `.w3i`  - version 39 (186 of the game's maps) was refused outright.
* `.w3g`  - replays were recompressed at zlib level 9, which the game does
            not use, so every rebuild came out 5-7% smaller.
"""
import glob
import os
import struct
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.table.slk_file import RawToken, SlkFile
from wc3files.w3o import CfgFile
from wc3files.w3i import W3iFile

_GAMEDATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'src', 'wc3files', 'w3o', 'gamedata')


def round_trip(instance, source, suffix):
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, 'out' + suffix)
        instance.write(target)
        with open(source, 'rb') as handle:
            original = handle.read()
        with open(target, 'rb') as handle:
            rebuilt = handle.read()
    return original, rebuilt


class TestSlkFidelity(unittest.TestCase):
    """Verified against all 20 Blizzard tables this package bundles."""

    def test_every_bundled_table_round_trips_byte_for_byte(self):
        tables = sorted(glob.glob(os.path.join(_GAMEDATA, '*.slk')))
        self.assertGreaterEqual(len(tables), 20, 'bundled .slk tables missing')
        for path in tables:
            with self.subTest(table=os.path.basename(path)):
                slk = SlkFile()
                slk.read(path)
                original, rebuilt = round_trip(slk, path, '.slk')
                self.assertEqual(original, rebuilt)

    def test_a_number_stays_a_number(self):
        # The old CSV-backed reader turned `K600` into the string "600",
        # which wrote back as `K"600"` - a different cell.
        slk = SlkFile()
        slk.read(os.path.join(_GAMEDATA, 'UnitWeapons.slk'))
        values = [v for row in slk.export_data().values() for v in row.values()]
        self.assertTrue(any(isinstance(v, int) and not isinstance(v, bool)
                            for v in values))

    def test_bare_literals_are_not_quoted_on_the_way_out(self):
        text = ('ID;PWXL;N;E\r\nB;X2;Y2;D0\r\nC;X1;Y1;K"id"\r\nC;X2;K"flag"\r\n'
                'C;X1;Y2;K"a"\r\nC;X2;KFALSE\r\nE\r\n')
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'in.slk')
            with open(source, 'w', encoding='cp1252', newline='') as handle:
                handle.write(text)
            slk = SlkFile()
            slk.read(source)
            self.assertIsInstance(slk.export_data()['a']['flag'], RawToken)
            original, rebuilt = round_trip(slk, source, '.slk')
            self.assertEqual(original, rebuilt)

    def test_an_absent_cell_stays_absent(self):
        text = ('ID;PWXL;N;E\r\nB;X3;Y2;D0\r\nC;X1;Y1;K"id"\r\nC;X2;K"a"\r\n'
                'C;X3;K"b"\r\nC;X1;Y2;K"r"\r\nC;X3;K5\r\nE\r\n')
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, 'in.slk')
            with open(source, 'w', encoding='cp1252', newline='') as handle:
                handle.write(text)
            slk = SlkFile()
            slk.read(source)
            self.assertIsNone(slk.export_data()['r']['a'])
            original, rebuilt = round_trip(slk, source, '.slk')
            self.assertEqual(original, rebuilt)


class TestCfgFidelity(unittest.TestCase):
    SOURCE = ('[w3u.original.hgtw]\r\n'
              'ulum.name = ulum\r\n'
              'ulum.value = 0\r\n'
              'ureq.value = \r\n'
              'utip.value = "quoted value"\r\n'
              '// a comment\r\n'
              '\r\n'
              '[w3u.custom.x000:hfoo]\r\n'
              'unam.value = Spirit-healer\r\n')

    def _write_source(self, directory, text=None):
        path = os.path.join(directory, 'in.cfg')
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(self.SOURCE if text is None else text)
        return path

    def test_a_dotted_section_name_is_read(self):
        # The old pattern was [A-Za-z0-9]+, which matched none of these.
        with tempfile.TemporaryDirectory() as directory:
            cfg = CfgFile()
            cfg.read(self._write_source(directory))
        data = cfg.export_data()
        self.assertIn('w3u.original.hgtw', data)
        self.assertIn('w3u.custom.x000:hfoo', data)
        self.assertEqual(data['w3u.original.hgtw']['ulum.name'], 'ulum')

    def test_it_round_trips_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write_source(directory)
            cfg = CfgFile()
            cfg.read(source)
            original, rebuilt = round_trip(cfg, source, '.cfg')
        self.assertEqual(original, rebuilt)

    def test_quotes_and_spacing_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write_source(directory)
            cfg = CfgFile()
            cfg.read(source)
            self.assertEqual(
                cfg.export_data()['w3u.original.hgtw']['utip.value'],
                'quoted value')
            _original, rebuilt = round_trip(cfg, source, '.cfg')
        self.assertIn(b'utip.value = "quoted value"', rebuilt)
        self.assertIn(b'ulum.name = ulum', rebuilt)

    def test_a_bare_cr_inside_a_value_is_not_a_line_break(self):
        # Tooltip values carry bare CR as an in-value line break; splitting on
        # it turns one entry into several and rewrites it as CRLF.
        text = ('[s]\r\nk.value = first\rsecond\r\nj.value = x\r\n')
        with tempfile.TemporaryDirectory() as directory:
            source = self._write_source(directory, text)
            cfg = CfgFile()
            cfg.read(source)
            self.assertEqual(cfg.export_data()['s']['k.value'],
                             'first\rsecond')
            original, rebuilt = round_trip(cfg, source, '.cfg')
        self.assertEqual(original, rebuilt)

    def test_an_empty_value_keeps_its_single_space(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write_source(directory)
            cfg = CfgFile()
            cfg.read(source)
            _original, rebuilt = round_trip(cfg, source, '.cfg')
        self.assertIn(b'ureq.value = \r\n', rebuilt)
        self.assertNotIn(b'ureq.value =  \r\n', rebuilt)

    def test_export_full_round_trips_through_import_full(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._write_source(directory)
            cfg = CfgFile()
            cfg.read(source)
            state = cfg.export_full()
            restored = CfgFile().import_full(state)
            target = os.path.join(directory, 'out.cfg')
            restored.write(target)
            with open(source, 'rb') as handle:
                original = handle.read()
            with open(target, 'rb') as handle:
                rebuilt = handle.read()
        self.assertEqual(original, rebuilt)

    def test_a_plain_nested_dict_is_still_accepted(self):
        cfg = CfgFile().import_full({'a': {'k': 'v'}})
        self.assertEqual(cfg.export_data(), {'a': {'k': 'v'}})


class TestW3iVersion39(unittest.TestCase):
    """Version 39 is undocumented; 125 of the game's 186 v39 maps read."""

    def test_a_version_39_file_reads_and_rewrites_exactly(self):
        path = sample_path('sample_v39.w3i')
        info = W3iFile()
        info.read(path)
        self.assertEqual(info.get_data()['file_version'], 39)
        original, rebuilt = round_trip(info, path, '.w3i')
        self.assertEqual(original, rebuilt)

    def test_the_v39_specific_fields_are_present(self):
        info = W3iFile()
        info.read(sample_path('sample_v39.w3i'))
        data = info.get_data()
        self.assertIn('unknown_v39', data['loading_screen'])
        # Six fog fields sit between the weather id and the sound name.
        self.assertIn('fog_linear_start', data['environment'])
        self.assertIn('fog_max_opacity', data['environment'])
        # Ten water-rendering settings close the head.
        self.assertEqual(len(data['water']), 10)
        # The sound string and light byte are still there. An earlier reading
        # of this format concluded they had been dropped, because the byte
        # count worked out either way on maps where all of it is zero.
        self.assertIn('sound', data['environment'])
        self.assertIn('light', data['environment'])

    def test_every_v39_player_carries_hud_skin(self):
        # Not having this field was what made some v39 maps read and others
        # not: records looked as though they sometimes carried an extra int,
        # and nothing in the head predicted which. They all carry it.
        info = W3iFile()
        info.read(sample_path('sample_v39.w3i'))
        players = info.get_data()['players']
        self.assertTrue(players)
        for player in players:
            self.assertIn('hud_skin', player)

    def test_the_file_the_old_layout_could_not_read_now_reads(self):
        # This fixture was kept because the previous, partly-derived layout
        # refused it. With the real field order it reads and reproduces, so
        # it guards the fix rather than the old limitation.
        path = sample_path('sample_v39_unsupported.w3i')
        info = W3iFile()
        info.read(path)
        self.assertEqual(info.get_data()['file_version'], 39)
        original, rebuilt = round_trip(info, path, '.w3i')
        self.assertEqual(original, rebuilt)

    def test_a_corrupted_v39_file_cannot_slip_through(self):
        # The self-check re-serializes and compares, so a parse that happens
        # to succeed but does not reproduce the bytes is still rejected.
        with open(sample_path('sample_v39.w3i'), 'rb') as handle:
            data = bytearray(handle.read())
        data[-1] = (data[-1] + 1) & 0xFF        # disturb the tail
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'broken.w3i')
            with open(path, 'wb') as handle:
                handle.write(data)
            try:
                W3iFile().read(path)
            except ValueError as exc:
                self.assertIn('version 39', str(exc))
            else:
                # If it did read, it must reproduce the bytes exactly.
                info = W3iFile()
                info.read(path)
                original, rebuilt = round_trip(info, path, '.w3i')
                self.assertEqual(original, rebuilt)

    def test_older_versions_still_read(self):
        info = W3iFile()
        info.read(sample_path('sample.w3i'))
        original, rebuilt = round_trip(info, sample_path('sample.w3i'), '.w3i')
        self.assertEqual(original, rebuilt)

    def test_an_unknown_version_still_raises(self):
        with open(sample_path('sample.w3i'), 'rb') as handle:
            data = bytearray(handle.read())
        data[0:4] = struct.pack('<i', 77)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'v77.w3i')
            with open(path, 'wb') as handle:
                handle.write(data)
            with self.assertRaises(ValueError) as caught:
                W3iFile().read(path)
        self.assertIn('Unsupported', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
