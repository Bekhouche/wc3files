"""Tests for wc3files.lni: object data as Lni text, and a whole map as a
folder of text files and back.

Each object case is a shape found in real maps' object files (levelled
values, sparse levels, field ids no bare key can hold, text with \\r\\n line
breaks, a float under an integer type); the map case checks the guarantee
the folder makes: every file comes back, byte for byte except object files,
which come back with the same objects, fields and values.
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

import _helpers

from wc3files.cli.main import main as cli_main
from wc3files.lni import objects_to_lni, lni_to_objects, map_to_lni, lni_files, MANIFEST
from wc3files.lni.folder import _object_file, _strip_seq
from wc3files.w3x import W3xFile


def round_trip(data, ext):
     return lni_to_objects(objects_to_lni(data, ext), ext)


class TestObjects(unittest.TestCase):

     def test_unit_fields_by_name(self):
          data = {'original': {'hfoo': {'unam': {'value': 'Captain', 'type': 3,
                                                  'end': '\0\0\0\0'}}},
                  'custom': {'h000': {'ID': 'hfoo',
                                      'umvs': {'value': 300, 'type': 0, 'end': 'h000'}}}}
          text = objects_to_lni(data, 'w3u')
          self.assertIn("[h000]\n_parent = 'hfoo'", text)
          self.assertIn("Name = 'Captain'", text)
          self.assertEqual(round_trip(data, 'w3u'), data)

     def test_levels_sparse_levels_and_odd_ids(self):
          mod = {'value': {1: 1.5, 2: 2.5, 3: 3.5}, 'type': 2, 'ptr': {1: 1, 2: 1, 3: 1},
                 'end': {1: 'A000', 2: 'A000', 3: 'A000'}}
          sparse = {'value': {2: 5, 4: 7}, 'type': 0, 'ptr': {2: 0, 4: 0},
                    'end': {2: 'A000', 4: 'A000'}}
          nul = {'value': {1: 0.43}, 'type': 1, 'ptr': {1: 1}, 'end': {1: 'Acrs'}}
          data = {'original': {}, 'custom': {'A000': {
               'ID': 'AHbz', 'Hbz1': mod, 'alev': sparse, 'Crs\0': nul}}}
          text = objects_to_lni(data, 'w3a')
          self.assertIn('{[2] = 5, [4] = 7}', text)
          self.assertIn("['Crs\\000'] = {0.43}", text)
          self.assertEqual(round_trip(data, 'w3a'), data)

     def test_text_line_breaks_and_float_under_integer(self):
          data = {'original': {}, 'custom': {'A001': {
               'ID': 'AHtb',
               'atar': {'value': {1: 'Air\r\nEnemy'}, 'type': 3, 'ptr': {1: 0},
                        'end': {1: 'A001'}},
               'acdn': {'value': {1: 6, 2: 6.5}, 'type': 0, 'ptr': {1: 0, 2: 0},
                        'end': {1: 'A001', 2: 'A001'}}}}}
          text = objects_to_lni(data, 'w3a')
          self.assertNotIn('\r', text)
          self.assertEqual(round_trip(data, 'w3a'), data)


class TestFolder(unittest.TestCase):

     def test_map_round_trip(self):
          archive = W3xFile()
          archive.read(_helpers.sample_path('sample.w3x'))
          folder = tempfile.mkdtemp()
          try:
               manifest = map_to_lni('sample.w3x', folder, archive.files)
               self.assertTrue(os.path.isfile(os.path.join(folder, MANIFEST)))
               back = lni_files(folder)
               for entry in manifest['files']:
                    original = bytes(archive.files[entry['name']])
                    if entry['kind'] == 'lni':
                         ext = entry['ext']
                         self.assertEqual(
                              _strip_seq(_object_file(back[entry['name']], ext).export_data()[ext]),
                              _strip_seq(_object_file(original, ext).export_data()[ext]))
                    else:
                         self.assertEqual(back[entry['name']], original, entry['name'])
          finally:
               shutil.rmtree(folder, ignore_errors=True)

     def test_cli_both_ways(self):
          work = tempfile.mkdtemp()
          try:
               folder = os.path.join(work, 'Sample')
               rebuilt = os.path.join(work, 'Rebuilt.w3x')
               with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli_main(['lni', _helpers.sample_path('sample.w3x'), folder]), 0)
                    self.assertEqual(cli_main(['lni', folder, rebuilt]), 0)
               original = W3xFile()
               original.read(_helpers.sample_path('sample.w3x'))
               again = W3xFile()
               again.read(rebuilt)
               names = {n for n in original.files if n != '(listfile)'}
               self.assertEqual(names, {n for n in again.files if n != '(listfile)'})
          finally:
               shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
     unittest.main()
