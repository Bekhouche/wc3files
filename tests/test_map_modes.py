"""Tests for wc3files.w3x.modes: whole-map Slk and Obj modes.

A map is built here from object data with the cases that broke the first
version on real maps: a custom object with no changed field (it vanished,
having nothing for the tables or the object file), a field set to the
game's own default (the rebuild dropped it), and an ability (every ability
rebuild crashed on Reforged's data slots past J).
"""

import os
import tempfile
import unittest

import _helpers  # noqa: F401

from wc3files.w3o.data import ObjectFileType
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.modes import (map_to_slk, map_to_obj, _read_files, _object, _object_bytes,
                                _differences, _OBJECT_EXTS)


def _objects(ext, data):
     obj = ObjectFile()
     tables = {e: None for e in _OBJECT_EXTS}
     tables[ext] = data
     obj.import_data(tables)
     return _object_bytes(obj, ext)


class TestModes(unittest.TestCase):

     def setUp(self):
          units = {'original': {'hfoo': {'umvs': {'value': 300, 'type': 0, 'end': '\0\0\0\0'},
                                         'ugol': {'value': 135, 'type': 0, 'end': '\0\0\0\0'}}},
                   'custom': {'h000': {'ID': 'hfoo',
                                       'uhpm': {'value': 900, 'type': 0, 'end': 'h000'},
                                       'unam': {'value': 'Captain', 'type': 3, 'end': 'h000'}},
                              'h001': {'ID': 'hkni'}}}
          abilities = {'original': {}, 'custom': {'A000': {
               'ID': 'AHbz',
               'acdn': {'value': {1: 5.0, 2: 4.0}, 'type': 2, 'ptr': {1: 0, 2: 0},
                        'end': {1: 'A000', 2: 'A000'}}}}}
          self.files = {'war3map.w3u': _objects('w3u', units),
                        'war3map.w3a': _objects('w3a', abilities)}
          self.work = tempfile.mkdtemp()

     def tearDown(self):
          import shutil
          shutil.rmtree(self.work, ignore_errors=True)

     def test_slk_then_obj_gives_the_same_objects(self):
          slk = os.path.join(self.work, 'slk.w3x')
          obj = os.path.join(self.work, 'obj.w3x')
          report = map_to_slk(None, slk, self.files)
          self.assertTrue(all(v.startswith('converted') for v in report.values()), report)
          packed = _read_files(slk)
          self.assertIn('Units\\UnitData.slk', packed)
          self.assertIn('Units\\AbilityData.slk', packed)
          map_to_obj(slk, obj)
          back = _read_files(obj)
          self.assertFalse(any(n.lower().endswith('.slk') for n in back))
          for name in ('war3map.w3u', 'war3map.w3a'):
               ext = name[-3:]
               self.assertEqual(_differences(_object(self.files[name], ext).export_data()[ext],
                                             _object(back[name], ext).export_data()[ext]), '')
          # the plain copy kept its parent
          units = _object(back['war3map.w3u'], 'w3u').export_data()['w3u']
          self.assertEqual(units['custom']['h001']['ID'], 'hkni')

     def test_a_map_with_its_own_tables_keeps_that_category(self):
          files = dict(self.files)
          files['Units\\UnitData.slk'] = b'ID;PWXL\r\nE\r\n'
          report = map_to_slk(None, os.path.join(self.work, 'x.w3x'), files)
          self.assertIn('kept: the map already has', report['war3map.w3u'])

     def test_ability_tables_rebuild(self):
          obj = _object(self.files['war3map.w3a'], 'w3a')
          tables, remainder = obj.to_override_tables()
          rebuilt, _ = ObjectFile.from_override_tables(ObjectFileType.Abilities, tables, remainder)
          self.assertIn('A000', rebuilt.export_data()['w3a']['custom'])


if __name__ == '__main__':
     unittest.main()
