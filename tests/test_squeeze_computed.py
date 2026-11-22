"""Tests for the lossy optimizer passes: wc3files.mdl.squeeze (mdx_squf) and
wc3files.w3x.optimize.computed_text.

The model is built here byte by byte in the classic version 800 layout, so
the test knows exactly which floats the squeeze may touch (vertices,
normals, extents, texture coordinates, bone keys) and which it may not
(frame numbers, counts, triangle indices).
"""

import struct
import unittest

import _helpers  # noqa: F401

from wc3files.mdl.squeeze import squeeze_mdx
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.modes import _object_bytes
from wc3files.w3x.optimize import computed_text, _object, _OBJECT_EXTS


def _chunk(tag, body):
     return tag + struct.pack('<I', len(body)) + body


def _floats(*values):
     return struct.pack(f'<{len(values)}f', *values)


def _model():
     vertices = _floats(1.2345, 2.3456, 3.4567, 10.1, 20.2, 30.3, -5.55, 6.66, 7.77)
     geoset = (b'VRTX' + struct.pack('<I', 3) + vertices +
               b'NRMS' + struct.pack('<I', 3) + _floats(0.1, 0.2, 0.97, 0, 0, 1, 0, 1, 0) +
               b'PTYP' + struct.pack('<II', 1, 4) +
               b'PCNT' + struct.pack('<II', 1, 3) +
               b'PVTX' + struct.pack('<I3H', 3, 0, 1, 2) +
               b'GNDX' + struct.pack('<I3B', 3, 0, 0, 0) +
               b'MTGC' + struct.pack('<II', 1, 1) +
               b'MATS' + struct.pack('<II', 1, 0) +
               struct.pack('<III', 0, 0, 0) + _floats(50.5, -1.1, -2.2, -3.3, 4.4, 5.5, 6.6) +
               struct.pack('<I', 1) + _floats(50.5, -1.1, -2.2, -3.3, 4.4, 5.5, 6.6) +
               b'UVAS' + struct.pack('<I', 1) +
               b'UVBS' + struct.pack('<I', 3) + _floats(0.125, 0.333, 0.5, 0.777, 0.9, 0.1))
     geos = struct.pack('<I', len(geoset) + 4) + geoset
     track = (b'KGTR' + struct.pack('<IIi', 2, 0, -1) +
              struct.pack('<I', 0) + _floats(1.111, 2.222, 3.333) +
              struct.pack('<I', 500) + _floats(4.444, 5.555, 6.666))
     node = struct.pack('<I', 96 + len(track)) + b'Bone'.ljust(80, b'\0') + \
          struct.pack('<iiI', 0, -1, 256) + track
     bone = node + struct.pack('<ii', 0, -1)
     return (b'MDLX' + _chunk(b'VERS', struct.pack('<I', 800)) +
             _chunk(b'GEOS', geos) + _chunk(b'BONE', bone))


class TestSqueeze(unittest.TestCase):

     def test_floats_lose_their_low_bits_and_nothing_else_changes(self):
          data = _model()
          out = squeeze_mdx(data)
          self.assertEqual(len(out), len(data))
          changed = [i for i in range(len(data)) if data[i] != out[i]]
          self.assertTrue(changed)
          # only the two low bytes of 4-byte floats are ever touched
          for i in changed:
               self.assertEqual(out[i], 0)
          vrtx = data.index(b'VRTX') + 8
          before = struct.unpack_from('<9f', data, vrtx)
          after = struct.unpack_from('<9f', out, vrtx)
          for a, b in zip(before, after):
               self.assertLess(abs(a - b), abs(a) * 0.01 + 1e-9)
          # frame numbers and indices stay exact
          key = data.index(b'KGTR') + 16
          self.assertEqual(struct.unpack_from('<I', out, key + 16)[0], 500)
          self.assertEqual(out[data.index(b'PVTX'):data.index(b'GNDX')],
                           data[data.index(b'PVTX'):data.index(b'GNDX')])

     def test_other_versions_and_broken_files_are_left_alone(self):
          data = _model()
          newer = data.replace(struct.pack('<I', 800), struct.pack('<I', 1000), 1)
          self.assertIs(squeeze_mdx(newer), newer)
          broken = data[:-20]
          self.assertIs(squeeze_mdx(broken), broken)


def _objects(ext, data):
     obj = ObjectFile()
     tables = {e: None for e in _OBJECT_EXTS}
     tables[ext] = data
     obj.import_data(tables)
     return _object_bytes(obj, ext)


class TestComputedText(unittest.TestCase):

     def test_references_become_values(self):
          abilities = {'original': {}, 'custom': {'A000': {
               'ID': 'AHbz',
               'Hbz2': {'value': {1: 25.0, 2: 0.4}, 'type': 2, 'ptr': {1: 2, 2: 2},
                        'end': {1: 'A000', 2: 'A000'}},
               'atp1': {'value': {1: 'Deals <A000,DataB1> damage; <A000,DataB2,%>% slow; '
                                     '<hfoo,realhp> hp; <Zzzz,DataA1> stays'},
                        'type': 3, 'ptr': {1: 0}, 'end': {1: 'A000'}},
               'aub1': {'value': {1: 'TRIGSTR_7'}, 'type': 3, 'ptr': {1: 0},
                        'end': {1: 'A000'}}}}}
          files = {'war3map.w3a': _objects('w3a', abilities),
                   'war3map.wts': '﻿STRING 7\r\n// comment kept\r\n{\r\nCools '
                                  '<A000,Cool1> s\r\n}\r\n'.encode('utf-8')}
          out, report = computed_text(files)
          text = _object(out['war3map.w3a'], 'w3a').export_data()['w3a']['custom']['A000']
          self.assertEqual(text['atp1']['value'][1],
                           'Deals 25 damage; 40% slow; 420 hp; <Zzzz,DataA1> stays')
          wts = out['war3map.wts'].decode('utf-8-sig')
          self.assertIn('// comment kept', wts)
          self.assertNotIn('<A000,Cool1>', wts)
          self.assertEqual(report, {'replaced': 4, 'left': 1})


if __name__ == '__main__':
     unittest.main()
