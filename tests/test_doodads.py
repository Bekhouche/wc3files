"""Tests for war3map.doo, the doodad and destructable placement file.

This format had no reader at all. `war3map.doo` was being handed to
`MapUnitsFile` purely because of its `.doo` extension - the two files share
that extension and the `W3do` magic but are different record layouts - and it
failed on all 48 files in the benchmark corpus. With a real reader, 47 of
those 48 read and write back byte-for-byte (31/31 at format 8/11, 16/17 at
13/11); the one that does not is refused rather than returned.

The fixtures are two real maps: an 8/11 file, and a 13/11 file carrying 38
of the unmarked light blocks that make format 13 awkward.
"""
import struct
import tempfile
import os
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.doo import MapDoodadsFile


def round_trip(doodads, source):
    with tempfile.TemporaryDirectory() as directory:
        target = os.path.join(directory, 'out.doo')
        doodads.write(target)
        with open(source, 'rb') as handle:
            original = handle.read()
        with open(target, 'rb') as handle:
            rebuilt = handle.read()
    return original, rebuilt


class TestFormat8(unittest.TestCase):
    FIXTURE = 'sample_doodads_v8_11.doo'

    def setUp(self):
        self.path = sample_path(self.FIXTURE)
        self.doodads = MapDoodadsFile().read(self.path)

    def test_it_reads_the_header(self):
        data = self.doodads.get_data()
        self.assertEqual((data['version'], data['subversion']), (8, 11))
        self.assertEqual(len(data['doodads']), 54)

    def test_it_round_trips_byte_for_byte(self):
        original, rebuilt = round_trip(self.doodads, self.path)
        self.assertEqual(original, rebuilt)

    def test_a_doodad_has_sane_fields(self):
        doodad = self.doodads.get_data()['doodads'][0]
        self.assertEqual(len(doodad['id']), 4)
        self.assertEqual(len(doodad['position']), 3)
        self.assertEqual(len(doodad['scale']), 3)
        self.assertLessEqual(doodad['life'], 255)

    def test_format_8_carries_none_of_the_v13_fields(self):
        doodad = self.doodads.get_data()['doodads'][0]
        for key in ('unknown_v13', 'unknown_v13_b', 'unknown_v13_tail'):
            self.assertNotIn(key, doodad)


class TestFormat13(unittest.TestCase):
    FIXTURE = 'sample_doodads_v13_11.doo'

    def setUp(self):
        self.path = sample_path(self.FIXTURE)
        self.doodads = MapDoodadsFile().read(self.path)

    def test_it_reads_the_header(self):
        data = self.doodads.get_data()
        self.assertEqual((data['version'], data['subversion']), (13, 11))
        self.assertEqual(len(data['doodads']), 595)

    def test_it_round_trips_byte_for_byte(self):
        original, rebuilt = round_trip(self.doodads, self.path)
        self.assertEqual(original, rebuilt)

    def test_the_v13_fields_are_present(self):
        doodad = self.doodads.get_data()['doodads'][0]
        self.assertIn('unknown_v13', doodad)
        self.assertIn('unknown_v13_b', doodad)
        # The three values once carried as an unidentified tail are a roll,
        # a pitch and a count of lights.
        self.assertIn('roll', doodad)
        self.assertIn('pitch', doodad)
        self.assertIsInstance(doodad['lights'], list)

    def test_lights_are_read_from_the_count_the_record_declares(self):
        # These used to be found by looking ahead for the next record,
        # because the three values before them were not understood. The
        # third is the light count, so the data says how many there are.
        # This fixture has 38 doodads carrying one.
        doodads = self.doodads.get_data()['doodads']
        with_light = [d for d in doodads if d.get('lights')]
        self.assertEqual(len(with_light), 38)
        self.assertEqual(len(with_light[0]['lights'][0]), 36)

    def test_doodads_without_a_light_have_an_empty_list(self):
        doodads = self.doodads.get_data()['doodads']
        self.assertTrue(any(not d.get('lights') for d in doodads))

    def test_a_light_count_past_the_end_of_the_file_is_refused(self):
        # Built by hand rather than by patching the fixture, so the count
        # being tested is unambiguously the one the guard reads.
        record = (b'B000'                       # id
                  + struct.pack('<i', 0)        # variation
                  + struct.pack('<fff', 0.0, 0.0, 0.0)
                  + struct.pack('<f', 0.0)      # rotation
                  + struct.pack('<fff', 1.0, 1.0, 1.0)
                  + b'B000'                     # skin
                  + struct.pack('<i', -1)       # v13 int
                  + bytes([2, 100])             # flags, life
                  + struct.pack('<i', -1)       # item table pointer
                  + struct.pack('<i', 0)        # item set count
                  + struct.pack('<i', -1)       # v13 int
                  + struct.pack('<i', 0)        # editor id
                  + struct.pack('<ff', 0.0, 0.0)   # roll, pitch
                  + struct.pack('<I', 9999))    # light count: absurd
        raw = b'W3do' + struct.pack('<iii', 13, 11, 1) + record
        with self.assertRaises(ValueError) as caught:
            MapDoodadsFile().read(raw)
        self.assertIn('lights', str(caught.exception))


class TestRefusal(unittest.TestCase):
    def test_a_file_that_does_not_reproduce_is_refused(self):
        # The light-block test is a heuristic, so every read is checked by
        # writing it back. A file that will not reproduce must raise rather
        # than hand back a plausible-looking misreading.
        with open(sample_path('sample_doodads_v13_11.doo'), 'rb') as handle:
            data = bytearray(handle.read())
        # Understate the doodad count: the parse then stops early and the
        # bytes it did not consume have nowhere to go.
        count = struct.unpack_from('<i', data, 12)[0]
        struct.pack_into('<i', data, 12, count - 1)
        with self.assertRaises(ValueError):
            MapDoodadsFile().read(bytes(data))

    def test_verification_can_be_turned_off(self):
        with open(sample_path('sample_doodads_v13_11.doo'), 'rb') as handle:
            data = handle.read()
        # Reading with verify=False must still work on a good file.
        doodads = MapDoodadsFile().read(data, verify=False)
        self.assertEqual(len(doodads.get_data()['doodads']), 595)

    def test_a_bad_magic_is_refused(self):
        with self.assertRaises(ValueError):
            MapDoodadsFile().read(b'NOPE' + b'\x00' * 32)

    def test_trailing_bytes_are_reported(self):
        with open(sample_path('sample_doodads_v8_11.doo'), 'rb') as handle:
            data = handle.read()
        with self.assertRaises(ValueError) as caught:
            MapDoodadsFile().read(data + b'\x00\x00\x00\x00')
        self.assertIn('left over', str(caught.exception))


class TestDataRoundTrip(unittest.TestCase):
    def test_import_data_restores_what_get_data_returned(self):
        source = MapDoodadsFile().read(sample_path('sample_doodads_v8_11.doo'))
        restored = MapDoodadsFile().import_data(source.get_data())
        self.assertEqual(restored.to_bytes(), source.to_bytes())


if __name__ == '__main__':
    unittest.main()
