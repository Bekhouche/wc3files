"""Tests for the map sub-files added to the CLI's dump/build registry.

`dump` -> `build` -> `dump` must give back the same JSON, and for these seven
the bytes come back identical too - verified here against the checked-in
fixtures. Before this, `dump`/`build` covered 12 of the 19 map sub-files the
package can read; these seven close that.
"""
import json
import os
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.cli.formats import FORMATS

# (format, fixture, dump options)
CASES = [
    ('w3e', 'sample.w3e', None),
    ('w3c', 'sample.w3c', None),
    ('w3s', 'sample.w3s', None),
    ('wpm', 'sample.wpm', None),
    ('w3r', 'sample.w3r', None),
    ('mmp', 'sample.mmp', None),
    ('shd', 'sample.shd', {'width': 3, 'height': 3}),
]


class TestRoundTrip(unittest.TestCase):
    def _round_trip(self, name, fixture, options):
        source = sample_path(fixture)
        fmt = FORMATS[name]
        # Must survive a real JSON encode/decode, not merely be a dict -
        # bytes and int keys both look fine until json.dumps sees them.
        first = json.loads(json.dumps(fmt.dump(source, options)))
        with tempfile.TemporaryDirectory() as directory:
            rebuilt = os.path.join(directory, 'rebuilt.' + name)
            fmt.build(first, rebuilt)
            second = json.loads(json.dumps(fmt.dump(rebuilt, options)))
            with open(source, 'rb') as handle:
                original_bytes = handle.read()
            with open(rebuilt, 'rb') as handle:
                new_bytes = handle.read()
        return first, second, original_bytes, new_bytes

    def test_each_format_round_trips_through_json_and_bytes(self):
        for name, fixture, options in CASES:
            with self.subTest(format=name):
                first, second, original, rebuilt = self._round_trip(
                    name, fixture, options)
                self.assertEqual(first, second, f'{name}: dump differs after build')
                self.assertEqual(original, rebuilt, f'{name}: bytes differ after build')

    def test_every_registered_format_is_listed_for_dump(self):
        for name, _fixture, _options in CASES:
            self.assertIn(name, FORMATS)


class TestEncodings(unittest.TestCase):
    def test_a_pathing_grid_is_base64_not_an_integer_array(self):
        data = FORMATS['wpm'].dump(sample_path('sample.wpm'))
        self.assertIsInstance(data['cells'], str)

    def test_a_region_keeps_its_weather_rawcode_exactly(self):
        data = FORMATS['w3r'].dump(sample_path('sample.w3r'))
        region = data['regions']['0']
        # b'RAhr' is a real weather id; latin-1 round-trips every byte value,
        # including the zero bytes an unset one uses.
        self.assertEqual(region['weather_effect'], 'RAhr')
        self.assertEqual(len(region['weather_effect'].encode('latin-1')), 4)

    def test_a_region_colour_survives_as_a_list(self):
        data = FORMATS['w3r'].dump(sample_path('sample.w3r'))
        self.assertEqual(data['regions']['0']['color'], [255, 0, 0])

    def test_minimap_icons_keep_their_index(self):
        data = FORMATS['mmp'].dump(sample_path('sample.mmp'))
        self.assertIn('0', data['icons'])
        self.assertEqual(data['icons']['0']['x'], 40)

    def test_a_sound_needs_no_encoding(self):
        # The dialogue fields used to be carried as an opaque byte blob and
        # base64-encoded here. They are read as strings and ints now, so the
        # whole record is JSON-native.
        data = FORMATS['w3s'].dump(sample_path('sample.w3s'))
        sound = data['sounds'][0]
        self.assertNotIn('tail', sound)
        for value in sound.values():
            self.assertNotIsInstance(value, (bytes, bytearray))


class TestShadowMapDimensions(unittest.TestCase):
    """A shadow map has no header, so its size can only come from the terrain.
    Guessing silently reshapes the whole grid, so it has to be refused."""

    def test_dumping_without_dimensions_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            FORMATS['shd'].dump(sample_path('sample.shd'))
        self.assertIn('--width', str(caught.exception))

    def test_the_wrong_dimensions_are_refused_rather_than_reshaped(self):
        with self.assertRaises(ValueError):
            FORMATS['shd'].dump(sample_path('sample.shd'),
                                {'width': 4, 'height': 4})

    def test_the_right_dimensions_read(self):
        data = FORMATS['shd'].dump(sample_path('sample.shd'),
                                   {'width': 3, 'height': 3})
        self.assertEqual((data['width'], data['height']), (3, 3))


class TestBuildBugsThatWereSilent(unittest.TestCase):
    """Two defects the dump/build registry carried, both found by auditing
    every format's writer rather than trusting the table."""

    def test_building_an_slk_does_not_raise(self):
        # _slk_write() lacked the `validate` keyword every write hook is
        # called with, so `build slk` failed with a TypeError for every input.
        import glob
        tables = glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'wc3files', 'w3o', 'gamedata', '*.slk'))
        self.assertTrue(tables, 'no bundled .slk table to test against')
        source = tables[0]
        first = json.loads(json.dumps(FORMATS['slk'].dump(source)))
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'out.slk')
            FORMATS['slk'].build(first, target)
            second = json.loads(json.dumps(FORMATS['slk'].dump(target)))
        # The writer emits a more verbose SLK than Blizzard's, so the bytes
        # differ - but no row may be lost.
        self.assertEqual(first['rows'], second['rows'])
        self.assertEqual(first['id_column'], second['id_column'])

    def test_a_multi_section_wes_file_survives_dump_and_build(self):
        # export_data() returns a flat {key: value} dict, and real files
        # repeat keys across sections: UnitEditorData.txt has 43 sections and
        # 404 `key=` lines but only 53 distinct keys, so building from the
        # flat dict silently kept 53 of them.
        source = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'src', 'wc3files', 'w3o', 'gamedata', 'UnitEditorData.txt')
        if not os.path.isfile(source):
            self.skipTest('bundled UnitEditorData.txt not present')
        first = json.loads(json.dumps(FORMATS['wes'].dump(source)))
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'out.txt')
            FORMATS['wes'].build(first, target)
            with open(source, 'rb') as handle:
                original = handle.read()
            with open(target, 'rb') as handle:
                rebuilt = handle.read()
        self.assertEqual(original, rebuilt)

    def test_a_flat_wes_dict_is_still_accepted(self):
        # Older dumps hold the flat dict; they must still build, lossily.
        from wc3files.w3o import WesFile
        wes = WesFile()
        wes.import_full({'A': '1', 'B': '2'})
        self.assertEqual(wes.export_data(), {'A': '1', 'B': '2'})


if __name__ == '__main__':
    unittest.main()
