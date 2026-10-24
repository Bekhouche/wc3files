"""Tests for the PopcornFX readers (`.pkcf`, `.pkmm`, `.pkb`).

`.pkcf` is read and written in full. `.pkmm` and `.pkb` are partial by
design - their name tables decode, their payloads do not - and both keep the
original bytes so writing is exact. Verified against all the PopcornFX files
the installed game ships: 2 `.pkcf`, 6 `.pkmm` and 6 `.pkb`, every one of
which decodes its full name table (see TODO.md).
"""
import struct
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.popcorn import PKB_MAGIC, PkbFile, PkcfFile, PkmmFile, PopcornError


def name_table(names):
    out = bytearray(struct.pack('<I', len(names)))
    for name in names:
        out += bytes([len(name)]) + name.encode('latin-1')
    return bytes(out)


def build_pkmm(names, body=b'\x00\x01'):
    return struct.pack('<I', 0) + name_table(names) + body


def build_pkb(names, filler=b'\x00' * 64):
    table = name_table(names)
    header = bytes(PKB_MAGIC) + bytes([0x02, 0x05, 0x01, 0x01, 0xD7, 0xF7, 0, 0])
    offset = len(header) + 12 + len(filler)
    return header + struct.pack('<III', 1, 2, offset) + filler + table


class TestPkcf(unittest.TestCase):
    def setUp(self):
        self.pkcf = PkcfFile().read(sample_path('Circle.pkcf'))

    def test_a_real_bake_config_reads(self):
        self.assertEqual(self.pkcf.settings['Version'], '2.5.0.63166')
        self.assertEqual(len(self.pkcf.sections), 1)
        section = self.pkcf.sections[0]
        self.assertEqual(section['type'], 'COvenBakeConfig_Mesh')
        self.assertEqual(section['hash'], '$D857A09F')
        self.assertEqual(section['settings'], {'ImportAnimation': 'true'})

    def test_writing_and_rereading_is_stable(self):
        again = PkcfFile().read(self.pkcf.to_text())
        self.assertEqual(again.get_data(), self.pkcf.get_data())

    def test_a_section_without_a_hash_reads(self):
        pkcf = PkcfFile().read('CConfig\n{\n\tA = 1;\n}\n')
        self.assertIsNone(pkcf.sections[0]['hash'])
        self.assertEqual(pkcf.sections[0]['settings'], {'A': '1'})

    def test_an_unclosed_section_is_refused(self):
        with self.assertRaises(PopcornError):
            PkcfFile().read('CConfig\n{\n\tA = 1;\n')

    def test_a_brace_without_a_header_is_refused(self):
        with self.assertRaises(PopcornError):
            PkcfFile().read('{\n}\n')

    def test_an_unreadable_line_is_refused_rather_than_skipped(self):
        with self.assertRaises(PopcornError):
            PkcfFile().read('Version = 1;\nthis is not valid = = ;;\n')


class TestPkmm(unittest.TestCase):
    def test_the_name_table_splits_into_material_mesh_and_streams(self):
        raw = build_pkmm(['Acolyte_rig:Acolyte_DE_Mat', 'Acolyte',
                          'Position', 'Normal', 'Color', 'Texcoord'])
        pkmm = PkmmFile().read(raw)
        self.assertEqual(pkmm.material, 'Acolyte_rig:Acolyte_DE_Mat')
        self.assertEqual(pkmm.mesh, 'Acolyte')
        self.assertEqual(pkmm.streams, ['Position', 'Normal', 'Color', 'Texcoord'])

    def test_the_vertex_data_is_kept_and_writing_is_exact(self):
        raw = build_pkmm(['M', 'Mesh', 'Position'], body=b'\xde\xad\xbe\xef')
        pkmm = PkmmFile().read(raw)
        self.assertEqual(pkmm.body, b'\xde\xad\xbe\xef')
        self.assertEqual(pkmm.to_bytes(), raw)

    def test_a_different_layout_is_refused_rather_than_misread(self):
        bad = struct.pack('<I', 1) + name_table(['X'])
        with self.assertRaises(PopcornError):
            PkmmFile().read(bad)

    def test_a_name_running_past_the_end_is_refused(self):
        raw = struct.pack('<II', 0, 1) + bytes([40]) + b'short'
        with self.assertRaises(PopcornError):
            PkmmFile().read(raw)


class TestPkb(unittest.TestCase):
    def test_the_type_table_decodes(self):
        raw = build_pkb(['CParticleEffect', 'CParticleAttributeList'])
        pkb = PkbFile().read(raw)
        self.assertEqual(pkb.type_names,
                         ['CParticleEffect', 'CParticleAttributeList'])
        self.assertEqual(pkb.to_bytes(), raw)

    def test_the_signature_is_the_shipped_one(self):
        pkb = PkbFile().read(build_pkb(['CParticleEffect']))
        self.assertTrue(pkb.signature.startswith(PKB_MAGIC))
        self.assertEqual(len(pkb.signature), 12)

    def test_a_file_without_the_magic_is_refused(self):
        with self.assertRaises(PopcornError):
            PkbFile().read(b'NOTAPKB!' + b'\x00' * 32)

    def test_an_offset_pointing_at_nothing_reports_no_names(self):
        # Better to report nothing than to decode whatever happens to sit
        # at a bad offset as if it were a type table.
        header = bytes(PKB_MAGIC) + bytes([0x02, 0x05, 0x01, 0x01, 0xD7, 0xF7, 0, 0])
        raw = header + struct.pack('<III', 1, 2, 999999) + b'\x00' * 32
        self.assertEqual(PkbFile().read(raw).type_names, [])

    def test_a_table_of_unprintable_names_is_rejected(self):
        header = bytes(PKB_MAGIC) + bytes([0x02, 0x05, 0x01, 0x01, 0xD7, 0xF7, 0, 0])
        table = struct.pack('<I', 1) + bytes([4]) + b'\x01\x02\x03\x04'
        raw = header + struct.pack('<III', 1, 2, len(header) + 12) + table
        self.assertEqual(PkbFile().read(raw).type_names, [])


if __name__ == '__main__':
    unittest.main()
