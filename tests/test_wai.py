"""Tests for the AI-editor script (`.wai`) reader.

The reader is deliberately partial - the header is parsed, the condition
tree is not. Verified against all six `.wai` files the installed game ships:
each one's header reads, and each one writes back byte-for-byte identical
(see TODO.md). The fixture here is one of those six.
"""
import struct
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.wai import WaiError, WaiFile


def build(version=2, name='Test', race=1, ident=7, units=('hpea', 'htow'),
          tail=b'\x00'):
    out = bytearray(struct.pack('<I', version))
    out += name.encode('latin-1') + b'\0'
    out += struct.pack('<III', race, ident, len(units))
    for code in units:
        out += code.encode('latin-1')
    return bytes(out + tail)


class TestRealFile(unittest.TestCase):
    def setUp(self):
        self.path = sample_path('reforged_u08_teal.wai')
        with open(self.path, 'rb') as handle:
            self.raw = handle.read()
        self.wai = WaiFile().read(self.raw)

    def test_the_header_reads(self):
        self.assertEqual(self.wai.version, 2)
        self.assertEqual(self.wai.name, 'TEAL')
        self.assertEqual(self.wai.race_name, 'human')
        self.assertEqual(self.wai.base_units, ['hpea', 'hpea', 'htow', 'htow'])

    def test_the_body_is_kept_rather_than_guessed_at(self):
        self.assertGreater(len(self.wai.tail), 1000)
        self.assertEqual(self.wai.get_data()['unparsed_bytes'], len(self.wai.tail))

    def test_writing_it_back_is_byte_exact(self):
        self.assertEqual(self.wai.to_bytes(), self.raw)

    def test_the_body_names_the_ai_functions(self):
        names = self.wai.strings()
        self.assertIn('GetBooleanOr', names)
        self.assertIn('OperatorCompareInteger', names)


class TestHeader(unittest.TestCase):
    def test_race_numbers_map_to_names(self):
        self.assertEqual(WaiFile().read(build(race=2)).race_name, 'orc')
        self.assertEqual(WaiFile().read(build(race=3)).race_name, 'undead')
        self.assertEqual(WaiFile().read(build(race=4)).race_name, 'nightelf')

    def test_an_unknown_race_is_reported_not_hidden(self):
        self.assertIn('unknown', WaiFile().read(build(race=9)).race_name)

    def test_a_synthetic_file_round_trips(self):
        raw = build(name='Grunt Master', race=2, units=('opeo', 'ogre'))
        self.assertEqual(WaiFile().read(raw).to_bytes(), raw)

    def test_editing_the_name_keeps_the_body(self):
        wai = WaiFile().read(build(tail=b'\x01\x02\x03'))
        wai.name = 'Renamed'
        again = WaiFile().read(wai.to_bytes())
        self.assertEqual(again.name, 'Renamed')
        self.assertEqual(again.tail, b'\x01\x02\x03')


class TestRefusals(unittest.TestCase):
    def test_another_version_raises_rather_than_guessing(self):
        with self.assertRaises(WaiError):
            WaiFile().read(build(version=3))

    def test_a_truncated_file_raises(self):
        with self.assertRaises(WaiError):
            WaiFile().read(b'\x02\x00\x00\x00')

    def test_an_unterminated_name_raises(self):
        with self.assertRaises(WaiError):
            WaiFile().read(b'\x02\x00\x00\x00' + b'no terminator')

    def test_a_unit_count_past_the_end_raises(self):
        raw = b'\x02\x00\x00\x00' + b'N\0' + struct.pack('<III', 1, 0, 9999)
        with self.assertRaises(WaiError):
            WaiFile().read(raw)


if __name__ == '__main__':
    unittest.main()
