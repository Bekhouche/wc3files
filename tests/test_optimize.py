"""Tests for wc3files.w3x.optimize: removing unused objects and the files
only the World Editor reads.

Each kind of reference the unused-object pass follows has a custom object
of its own here (a JASS rawcode, a Lua FourCC, an integer literal, a placed
unit, an ability list, a tooltip, a text file), and one object nothing
refers to, which is the only one removed. A changed standard object is
never removed.
"""

import unittest

import _helpers  # noqa: F401

from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.optimize import remove_unused_objects, remove_we_only, _object, _OBJECT_EXTS
from wc3files.w3x.modes import _object_bytes


def _objects(ext, data):
     obj = ObjectFile()
     tables = {e: None for e in _OBJECT_EXTS}
     tables[ext] = data
     obj.import_data(tables)
     return _object_bytes(obj, ext)


def _unit(base, **fields):
     mods = {'ID': base}
     for field_id, value in fields.items():
          mods[field_id] = {'value': value, 'type': 3 if isinstance(value, str) else 0,
                            'end': '\0\0\0\0'}
     return mods


class TestRemoveUnused(unittest.TestCase):

     def test_only_the_unreferenced_object_goes(self):
          units = {'original': {'hfoo': {'umvs': {'value': 300, 'type': 0, 'end': '\0\0\0\0'}}},
                   'custom': {
                        'h001': _unit('hfoo'),                       # JASS rawcode
                        'h002': _unit('hfoo'),                       # Lua FourCC
                        'h003': _unit('hfoo'),                       # integer literal
                        'h004': _unit('hfoo', uabi='A001'),          # placed: refers to A001
                        'h005': _unit('hfoo', utub='Uses <A002,DataA1>'),  # via h001 below
                        'h006': _unit('hfoo'),                       # text file
                        'h009': _unit('hfoo'),                       # nothing: removed
                   }}
          units['custom']['h001']['ubui'] = {'value': 'h005', 'type': 3, 'end': '\0\0\0\0'}
          abilities = {'original': {}, 'custom': {
               'A001': {'ID': 'AHbz'}, 'A002': {'ID': 'AHtb'}, 'A009': {'ID': 'AHtb'}}}
          integer = 0
          for c in 'h003':
               integer = integer * 256 + ord(c)
          files = {
               'war3map.w3u': _objects('w3u', units),
               'war3map.w3a': _objects('w3a', abilities),
               'war3map.j': f"call CreateUnit(p, 'h001', 0, 0, 0)\ncall X({integer})\n".encode(),
               'scripts\\extra.lua': b'CreateUnit(p, FourCC("h002"), 0, 0, 0)',
               'war3mapExtra.txt': b'[x]\nid=h006\n',
               'war3mapUnits.doo': b'not a real doo, so every token in it counts: h004',
          }
          out, report = remove_unused_objects(files)
          self.assertEqual(report['removed'], {'w3u': ['h009'], 'w3a': ['A009']})
          left = _object(out['war3map.w3u'], 'w3u').export_data()['w3u']
          self.assertIn('hfoo', left['original'])
          self.assertEqual(sorted(left['custom']), ['h001', 'h002', 'h003', 'h004', 'h005', 'h006'])

     def test_nothing_custom(self):
          files = {'war3map.j': b''}
          self.assertEqual(remove_unused_objects(files)[1], {'removed': {}, 'kept': 0})


class TestRemoveWeOnly(unittest.TestCase):

     def test_editor_files_go_unless_the_script_needs_them(self):
          files = {'war3map.wtg': b'', 'war3map.wct': b'', 'war3map.imp': b'',
                   'war3map.w3r': b'', 'war3map.w3c': b'', 'war3map.w3s': b'',
                   'war3mapUnits.doo': b'', 'war3map.mmp': b'',
                   'war3map.j': b'function main takes nothing returns nothing\n'
                                b'call CreateRegions()\ncall InitSounds()\nendfunction\n'}
          out, report = remove_we_only(files)
          self.assertEqual(sorted(report['removed']),
                           ['war3map.imp', 'war3map.w3r', 'war3map.w3s', 'war3map.wct',
                            'war3map.wtg'])
          for kept in ('war3map.w3c', 'war3mapUnits.doo', 'war3map.mmp', 'war3map.j'):
               self.assertIn(kept, out)


if __name__ == '__main__':
     unittest.main()
