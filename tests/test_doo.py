"""Real tests for war3mapUnits.doo (MapUnitsFile)."""
import os
import tempfile
import unittest

import _helpers
from wc3files import MapUnitsFile


class TestDooRoundTrip(unittest.TestCase):
    def setUp(self):
        self.doo = MapUnitsFile()
        self.doo.read(_helpers.sample_path('sample.doo'))

    def test_known_fields(self):
        data = self.doo.get_data()
        self.assertEqual(data['version'], 8)
        self.assertEqual(data['subversion'], 11)
        self.assertEqual(len(data['units']), 2)
        hero = data['units'][0]
        self.assertEqual(hero['id'], 'hfoo')
        self.assertEqual(hero['inventory'][0]['item_id'], 'stel')
        random_unit = data['units'][1]
        self.assertEqual(random_unit['random_mode'], 1)
        self.assertEqual(random_unit['random_data']['table_id'], 0)

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.doo'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.doo')
            self.doo.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)

    def test_rejects_bad_magic(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.doo')
            with open(bad, 'wb') as fh:
                fh.write(b'NOPE' + b'\x00' * 20)
            with self.assertRaises(ValueError):
                MapUnitsFile().read(bad)


class TestSkinsAreOneLayoutPerFile(unittest.TestCase):
    """Since 1.32 every record of a file carries a skin id, or none does, and
    the file version does not say which. The writers decided per record, so
    a record added by code chose on its own: a doodad without `has_skin` got
    a skin, a unit without one did not, and the file mixed two layouts that
    the game reads out of step."""

    def reread(self, cls, writer, name):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            writer.write(path)
            again = cls()
            again.read(path)
            return again.get_data()

    def check(self, cls, sample, key, name):
        from copy import deepcopy
        for skins in (True, False):
            with self.subTest(file=name, skins=skins):
                source = cls()
                source.read(_helpers.sample_path(sample))
                source.get_data()['skins'] = skins
                data = self.reread(cls, source, name)
                self.assertEqual(data['skins'], skins)
                # A record added by code, with neither has_skin nor skin.
                added = {k: v for k, v in deepcopy(data[key][0]).items()
                         if k not in ('has_skin', 'skin')}
                data[key].append(added)
                writer = cls()
                writer._data = data
                again = self.reread(cls, writer, name)
                self.assertEqual({record['has_skin'] for record in again[key]}, {skins})
                self.assertEqual(len(again[key]), len(data[key]))

    def test_units(self):
        from wc3files.doo.map_units_file import MapUnitsFile as Units
        self.check(Units, 'sample.doo', 'units', 'war3mapUnits.doo')

    def test_doodads(self):
        from wc3files.doo.map_doodads_file import MapDoodadsFile
        self.check(MapDoodadsFile, 'sample_doodads_v8_11.doo', 'doodads', 'war3map.doo')

    def test_reading_records_the_files_choice(self):
        from wc3files.doo.map_doodads_file import MapDoodadsFile
        doodads = MapDoodadsFile()
        doodads.read(_helpers.sample_path('sample_doodads_v13_11.doo'))
        self.assertIs(doodads.get_data()['skins'], True)


if __name__ == '__main__':
    unittest.main()
