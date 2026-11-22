"""Real tests for wc3files.mpq / wc3files.w3x: build a synthetic .w3x archive
entirely in memory (no personal map files needed) and verify it round-trips
byte-for-byte through both the writer and the reader."""
import hashlib
import os
import random
import tempfile
import unittest
import zlib

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files import W3xFile, MpqFile
from wc3files.mpq.files import MpqFiles
from wc3files.mpq.writer import MpqWriter
from wc3files.mpq.data import MpqProtectionLevel, MPQHashTableEntry
from wc3files.mpq.cipher import MpqCipher
from wc3files.mpq.attributes import pack_attributes, unpack_attributes
from wc3files.mpq import protection


class TestMpqRoundTrip(unittest.TestCase):
    FILES = {
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
        'war3map.txt': b'hello world',
        '(empty)': b'',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def test_build_from_files_round_trips(self):
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path)
        self.assertTrue(os.path.exists(self.archive_path))

        reread = W3xFile()
        reread.read(self.archive_path)
        for name, data in self.FILES.items():
            self.assertEqual(reread.files.get(name), data, f'mismatch for {name!r}')

    def test_build_from_files_across_sector_boundary(self):
        # Exercise a file that spans more than one sector plus a partial
        # remainder - the exact bug class documented in TODO.md's MPQ entry
        # (a stale sector-count formula silently truncated these).
        sector_size = 1 << (3 + 9)  # sector_size_shift=3 default -> 512 << 3
        big = bytes(range(256)) * ((sector_size * 2 + 37) // 256 + 1)
        big = big[:sector_size * 2 + 37]
        files = {'war3map.j': big}

        w3x = W3xFile()
        w3x.build_from_files(files, self.archive_path, sector_size_shift=3)

        reread = W3xFile()
        reread.read(self.archive_path)
        self.assertEqual(reread.files.get('war3map.j'), big)

    def test_mpq_file_reads_the_same_archive(self):
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        mpq.read_files()
        for name, data in self.FILES.items():
            self.assertEqual(mpq.files.get(name), data, f'mismatch for {name!r}')

    def test_get_filelist_returns_a_real_reference_list(self):
        # Backed by the bundled data/reference/*.txt files, not a personal path.
        names = MpqFiles.get_filelist()
        self.assertGreater(len(names), 1000)
        self.assertIn('war3map.j', names)
        self.assertIn('war3map.w3i', names)


class TestMpqTamperedFormatVersion(unittest.TestCase):
    """The game reads every map as format 0 whatever the header's format
    version says, and map protectors set it to junk (41 of 1,025 benchmark
    maps, e.g. 37103). For 2 and 3 the reader used to print() to stdout
    (issue #12). Patched copies of the real sample.w3x must read silently and
    give the same files as the original."""

    def read_all(self, path):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            w3x = W3xFile()
            w3x.read(path)
            mpq = MpqFile()
            mpq.read(path)
            mpq.read_files()
            mpq.reader.close()
        return w3x.files, mpq.files, out.getvalue()

    def test_any_format_version_reads_as_format_0(self):
        import struct
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3x'))
        self.assertEqual(original[:4], b'MPQ')
        self.assertEqual(struct.unpack_from('<H', original, 12)[0], 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'sample.w3x')
            with open(path, 'wb') as f:
                f.write(original)
            expected_w3x, expected_mpq, _ = self.read_all(path)
            self.assertTrue(expected_w3x)
            for version in (2, 3, 37103):
                patched = bytearray(original)
                struct.pack_into('<H', patched, 12, version)
                with open(path, 'wb') as f:
                    f.write(patched)
                files_w3x, files_mpq, stdout = self.read_all(path)
                self.assertEqual(stdout, '', f'format version {version}')
                self.assertEqual(files_w3x, expected_w3x, f'format version {version}')
                self.assertEqual(files_mpq, expected_mpq, f'format version {version}')


class TestMpqAttributes(unittest.TestCase):
    """(attributes) generation - see mpq/attributes.py. Previously unimplemented
    anywhere in this package (TODO.md)."""

    FILES = {
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
        'war3map.w3i': b'fake w3i content, padding padding padding padding',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def test_pack_unpack_round_trips(self):
        contents = [b'alpha', b'beta', b'gamma content here']
        packed = pack_attributes(contents, timestamp=1_700_000_000)
        parsed = unpack_attributes(packed, len(contents))
        self.assertEqual(parsed['version'], 100)
        self.assertEqual(parsed['flags'], 7)  # CRC32 | FILETIME | MD5
        self.assertEqual(parsed['crc32'], [zlib.crc32(c) for c in contents])
        self.assertEqual(parsed['md5'], [hashlib.md5(c).digest() for c in contents])
        self.assertEqual(len(set(parsed['filetime'])), 1)  # same timestamp for every entry

    def test_attributes_file_is_readable_and_correct_in_a_real_archive(self):
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path, attributes=True)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        mpq.read_files()

        attr_data = mpq.files.get('(attributes)')
        self.assertTrue(attr_data)
        block_count = len(mpq._data['BLOCK_TABLE'])
        # (attributes) never covers its own (still-being-computed) entry.
        parsed = unpack_attributes(attr_data, block_count - 1)

        for index, name in enumerate(self.FILES):
            self.assertEqual(parsed['crc32'][index], zlib.crc32(self.FILES[name]),
                              f'crc32 mismatch for {name!r}')
            self.assertEqual(parsed['md5'][index], hashlib.md5(self.FILES[name]).digest(),
                              f'md5 mismatch for {name!r}')

    def test_attributes_default_is_off(self):
        # Adding (attributes) is opt-in - existing callers/tests must see no change.
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        self.assertIsNone(mpq.mpq_hash_table.get_hash_table_entry('(attributes)'))


class TestMpqProtection(unittest.TestCase):
    """Opt-in MPQ-container "protection" - see mpq/protection.py for the full
    design rationale, sources, and verification-status disclosure per tier."""

    FILES = {
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
        'war3map.w3i': b'fake w3i content, padding padding padding padding',
        'war3map.wts': b'fake wts content here',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def _build(self, level, **kwargs):
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path, protection=level, **kwargs)
        return w3x

    def test_none_is_the_default_and_unchanged(self):
        # protection=NONE must be byte-identical to omitting the argument entirely -
        # this is the existing, already-verified-byte-for-byte writer behavior.
        default_writer = MpqWriter(rng=random.Random(1))
        explicit_writer = MpqWriter(protection=MpqProtectionLevel.NONE, rng=random.Random(1))
        self.assertEqual(default_writer.build(dict(self.FILES)), explicit_writer.build(dict(self.FILES)))

    def test_safe_strips_listfile_but_standard_files_still_resolve(self):
        self._build(MpqProtectionLevel.SAFE)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        self.assertIsNone(mpq.mpq_hash_table.get_hash_table_entry('(listfile)'))

        mpq.read_files()
        for name, data in self.FILES.items():
            self.assertEqual(mpq.files.get(name), data, f'mismatch for {name!r}')

    def test_safe_pads_the_hash_table_with_non_colliding_decoys(self):
        plain = MpqWriter(protection=MpqProtectionLevel.NONE, rng=random.Random(1))
        safe = MpqWriter(protection=MpqProtectionLevel.SAFE, rng=random.Random(1))

        plain.build(dict(self.FILES))
        safe.build(dict(self.FILES))

        cipher = MpqCipher()
        real_hashes = {(cipher.hash(n, 1), cipher.hash(n, 2)) for n in self.FILES}

        # Rebuild each table directly (independent of file layout/offsets) to inspect it.
        plain_table = protection.build_hash_table(
            [(n, i) for i, n in enumerate(self.FILES)], cipher, level=MpqProtectionLevel.NONE)
        safe_table = protection.build_hash_table(
            [(n, i) for i, n in enumerate(self.FILES)], cipher, level=MpqProtectionLevel.SAFE)

        self.assertGreater(len(safe_table), len(plain_table))

        occupied = [e for e in safe_table if e[MPQHashTableEntry.FILE_BLOCK_INDEX.value] != 0xFFFFFFFF]
        decoy_count = sum(
            1 for e in occupied
            if (e[MPQHashTableEntry.FILE_PATH_HASH_A.value], e[MPQHashTableEntry.FILE_PATH_HASH_B.value])
            not in real_hashes
        )
        self.assertGreater(decoy_count, 0)
        # A decoy's hash can never equal a real file's - it's structurally
        # impossible for it to be returned for a real lookup.
        for entry in occupied:
            key = (entry[MPQHashTableEntry.FILE_PATH_HASH_A.value], entry[MPQHashTableEntry.FILE_PATH_HASH_B.value])
            if key not in real_hashes:
                self.assertNotEqual(key, (0xFFFFFFFF, 0xFFFFFFFF))

    def test_format_version_is_nonstandard_above_none(self):
        rng = random.Random(7)
        self.assertEqual(protection.format_version_for(MpqProtectionLevel.NONE, rng), 0)
        for level in (MpqProtectionLevel.SAFE, MpqProtectionLevel.EXPERIMENTAL, MpqProtectionLevel.AGGRESSIVE):
            version = protection.format_version_for(level, rng)
            self.assertNotIn(version, (0, 1, 2, 3))

    def test_experimental_inserts_one_decoy_ahead_of_the_real_entry_in_probe_order(self):
        self._assert_decoy_probe_order(MpqProtectionLevel.EXPERIMENTAL, expected_decoys=1)

    def test_aggressive_inserts_three_decoys_ahead_of_the_real_entry_in_probe_order(self):
        self._assert_decoy_probe_order(MpqProtectionLevel.AGGRESSIVE, expected_decoys=3)

    def _assert_decoy_probe_order(self, level, expected_decoys):
        cipher = MpqCipher()
        name = 'war3map.j'
        named = [(name, 0)]
        table = protection.build_hash_table(named, cipher, level=level,
                                             invalid_block_index=99, rng=random.Random(3))

        size = len(table)
        home_slot = cipher.hash(name, 0) & (size - 1)
        hash_a = cipher.hash(name, 1)
        hash_b = cipher.hash(name, 2)

        # Walk the real open-addressing probe chain from the home slot (the
        # algorithm any correct MPQ reader - including Storm.dll - uses),
        # independent of this project's own reader's array-index-order scan.
        matches = []
        slot = home_slot
        for _ in range(size):
            entry = table[slot]
            if entry[MPQHashTableEntry.FILE_BLOCK_INDEX.value] == 0xFFFFFFFF:
                break
            if (entry[MPQHashTableEntry.FILE_PATH_HASH_A.value] == hash_a
                    and entry[MPQHashTableEntry.FILE_PATH_HASH_B.value] == hash_b):
                matches.append(entry[MPQHashTableEntry.FILE_BLOCK_INDEX.value])
            slot = (slot + 1) % size

        self.assertEqual(len(matches), expected_decoys + 1)
        self.assertEqual(matches[:-1], [99] * expected_decoys)  # decoys first, all invalid
        self.assertEqual(matches[-1], 0)  # the real block index, last in probe order

    def test_both_readers_see_through_every_protection_level(self):
        # Lookups probe from the home slot and skip a match whose block does
        # not exist, as the game does, so the decoys ahead of each real entry
        # (EXPERIMENTAL, AGGRESSIVE) no longer hide the file. Before, the
        # reader compared the name with every entry and took the decoy.
        for level in MpqProtectionLevel:
            with self.subTest(level=level.value):
                self._build(level)
                mpq = MpqFile()
                mpq.read(self.archive_path)
                mpq.read_files()
                w3x = W3xFile()
                w3x.read(self.archive_path, mode='deep')
                for name, data in self.FILES.items():
                    self.assertEqual(mpq.files.get(name), data, name)
                    self.assertEqual(w3x.files.get(name), data, name)

    def test_protection_and_attributes_compose_without_crashing(self):
        self._build(MpqProtectionLevel.AGGRESSIVE, attributes=True)
        self.assertTrue(os.path.exists(self.archive_path))
        # Structural sanity only: the archive parses with (attributes) on top
        # of the decoys. Reading the files back is covered above.
        mpq = MpqFile()
        mpq.read(self.archive_path)

    def test_deterministic_with_a_seeded_rng(self):
        first = MpqWriter(protection=MpqProtectionLevel.AGGRESSIVE, rng=random.Random(99)).build(dict(self.FILES))
        second = MpqWriter(protection=MpqProtectionLevel.AGGRESSIVE, rng=random.Random(99)).build(dict(self.FILES))
        self.assertEqual(first, second)


class TestMpqUserDataHeader(unittest.TestCase):
    """`MPQ\x1b` archives - ones that begin with a 16-byte user-data header
    saying where the real MPQ header is, instead of with the header itself.

    MpqUserData was unfinished scaffolding: it recognized the magic and did
    nothing, so ARCHIVE_OFFSET stayed on the user-data header and MpqHeader
    refused the file ("Invalid MPQ file header. Got: b'MPQ\x1b'") - a clean
    refusal, but these archives could not be opened at all.

    No real Warcraft III file exercises this (of 168 real local archives, 158
    are plain `MPQ\x1a` and 9 `HM3W`; the user-data layout is mostly a
    StarCraft II / WoW-era thing), so the field layout comes from public
    documentation rather than real WC3 bytes. These tests therefore verify it
    the strongest way available without such a file: build the archive with
    this package's own writer, wrap that *real* archive in a user-data header,
    and require the wrapped parse to match the unwrapped one file for file.
    """

    FILES = {
        'war3map.j': b'function main\nendfunction\n',
        'war3map.w3i': b'\x00' * 64,
        'Textures\\Custom.blp': b'not-really-a-blp-just-content',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.plain_path = os.path.join(self.tmpdir.name, 'plain.w3x')
        W3xFile().build_from_files(self.FILES, self.plain_path)
        with open(self.plain_path, 'rb') as fh:
            self.plain_bytes = fh.read()
        self.assertEqual(self.plain_bytes[:4], b'MPQ\x1a')

    def _wrap(self, header_offset, user_data_size=None):
        """Prepend a well-formed user-data header to the real archive."""
        import struct as _struct
        size = user_data_size if user_data_size is not None else header_offset
        wrapper = bytearray(b'MPQ\x1b' + _struct.pack('<III', size, header_offset, 16))
        self.assertLessEqual(len(wrapper), header_offset)
        wrapper += b'\x00' * (header_offset - len(wrapper))
        wrapper += self.plain_bytes
        path = os.path.join(self.tmpdir.name, f'wrapped_{header_offset}.w3x')
        with open(path, 'wb') as fh:
            fh.write(bytes(wrapper))
        return path

    def _read_files(self, path):
        mpq = MpqFile()
        mpq.read(path)
        mpq.read_files()
        return {name: data for name, data in mpq.files.items() if data}

    def test_a_wrapped_archive_reads_identically_to_the_unwrapped_one(self):
        baseline = self._read_files(self.plain_path)
        for name, expected in self.FILES.items():
            self.assertEqual(baseline.get(name), expected, f'baseline wrong for {name!r}')

        for header_offset in (0x200, 0x400, 32):
            with self.subTest(header_offset=hex(header_offset)):
                self.assertEqual(self._read_files(self._wrap(header_offset)), baseline)

    def test_the_user_data_header_fields_are_parsed(self):
        mpq = MpqFile()
        mpq.read(self._wrap(0x200))
        user_data = mpq._data['USER_DATA']
        self.assertEqual(user_data['MAGIC'], b'MPQ\x1b')
        self.assertEqual(user_data['HEADER_OFFSET'], 0x200)
        self.assertEqual(user_data['USER_DATA_HEADER'], 16)
        # ARCHIVE_OFFSET must end up on the real header, not the wrapper.
        self.assertEqual(user_data['ARCHIVE_OFFSET'], 0x200)
        self.assertEqual(len(user_data['USER_DATA']), 0x200 - 16)

    def test_a_header_offset_inside_the_user_data_header_is_rejected(self):
        # Would mean the real header sits inside the 16-byte wrapper itself.
        # Raise rather than seek somewhere nonsensical.
        import struct as _struct
        path = os.path.join(self.tmpdir.name, 'bogus.w3x')
        with open(path, 'wb') as fh:
            fh.write(b'MPQ\x1b' + _struct.pack('<III', 8, 8, 16) + self.plain_bytes)
        with self.assertRaises(ValueError):
            MpqFile().read(path)

    def test_a_plain_archive_is_untouched_by_the_user_data_path(self):
        mpq = MpqFile()
        mpq.read(self.plain_path)
        self.assertEqual(mpq._data['USER_DATA']['ARCHIVE_OFFSET'], 0)
        self.assertNotIn('MAGIC', mpq._data['USER_DATA'])


class TestMpqDeepFileDiscovery(unittest.TestCase):
    """No-(listfile) file discovery (MpqFiles.read()) - a real gap found by
    testing against a real protected map: the previous version only scanned
    war3map.j/.lua and object-data files once, so a file only named inside a
    *second-hop* reference (e.g. a custom .txt import list a script points
    at, itself naming further real assets) was never found at all. This now
    iterates - scan every discovered text-bearing file (script, object data,
    SLK, TXT) for further references, keep going until a pass finds nothing
    new - so a chain of references gets fully unrolled, not just one link."""

    FILES = {
        'war3map.j': b'call Foo("Imports\\MyList.txt")\nendfunction\n',
        'Imports\\MyList.txt': b'Imports\\MyTable.slk\nSounds\\Direct.wav\n',
        'Imports\\MyTable.slk': b'ID;X1;K"Textures\\Deep.blp"\n',
        'Sounds\\Direct.wav': b'not-really-a-wav-just-content',
        'Textures\\Deep.blp': b'not-really-a-blp-just-content',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def test_a_three_hop_reference_chain_is_fully_discovered_with_no_listfile(self):
        w3x = W3xFile()
        w3x.build_from_files(self.FILES, self.archive_path, protection=MpqProtectionLevel.SAFE)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        self.assertIsNone(mpq.mpq_hash_table.get_hash_table_entry('(listfile)'))  # confirms no real listfile

        mpq.read_files()
        for name, data in self.FILES.items():
            self.assertEqual(mpq.files.get(name), data, f'not discovered/misread: {name!r}')

    def test_deep_chain_members_are_not_bundled_reference_names(self):
        # Confirms the test data actually exercises the deep-scan chain, not
        # a coincidence of these exact names already being in the bundled
        # reference filename list (which alone would find them with no
        # scanning at all, proving nothing about the chain logic).
        from wc3files.mpq.files import MpqFiles as _MpqFiles
        reference_names = {n.lower() for n in _MpqFiles.get_filelist()}
        for name in self.FILES:
            if name == 'war3map.j':
                continue  # war3map.j itself is expected to be a bundled name
            self.assertNotIn(name.lower(), reference_names, f'{name!r} unexpectedly already bundled')


class TestMpqDeepFileDiscoveryFindsModelEmbeddedTextures(unittest.TestCase):
    """A real .mdx/.mdl model embeds further real file references
    structurally (its own texture list, at minimum) - a user's own
    suggestion ("mdl and mdx could include paths for files ... a full scan
    could help on them"), not something the chain test above exercises at
    all (that one only used plain-text formats). Reuses this package's own
    ModelFile writer to build the fixture, matching this project's own
    "build real fixtures via the format's own writer" convention."""

    CUSTOM_TEXTURE_PATH = 'Textures\\MySpecialCustomTexture.blp'

    @classmethod
    def setUpClass(cls):
        from wc3files.mdl.model_file import ModelFile
        from wc3files.mdl.mdx_io import empty_model

        model = empty_model()
        model['textures'] = [{'replaceableId': 0, 'path': cls.CUSTOM_TEXTURE_PATH, 'flags': 0}]
        mf = ModelFile()
        mf.import_data(model)

        with tempfile.TemporaryDirectory() as tmp:
            mdx_path = os.path.join(tmp, 'model.mdx')
            mf.write(mdx_path)
            with open(mdx_path, 'rb') as fh:
                cls.mdx_bytes = fh.read()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def test_a_texture_named_only_inside_an_mdx_is_discovered(self):
        files = {
            # war3map.j must actually name MyModel.mdx for it to be discovered
            # at all with no listfile - only *then* does scanning its own
            # embedded texture list surface the (otherwise unnamed anywhere
            # else) custom texture one hop further.
            'war3map.j': b'call CreateSomething("MyModel.mdx")\nendfunction\n',
            'MyModel.mdx': self.mdx_bytes,
            self.CUSTOM_TEXTURE_PATH: b'not-really-a-blp-just-content',
        }
        w3x = W3xFile()
        w3x.build_from_files(files, self.archive_path, protection=MpqProtectionLevel.SAFE)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        mpq.read_files()

        self.assertEqual(mpq.files.get('MyModel.mdx'), self.mdx_bytes)
        self.assertEqual(mpq.files.get(self.CUSTOM_TEXTURE_PATH), files[self.CUSTOM_TEXTURE_PATH],
                          'texture named only inside the .mdx was not discovered')

    def test_extract_references_pulls_the_texture_path_out_directly(self):
        from wc3files.mpq.files import MpqFiles
        refs = MpqFiles._extract_references('MyModel.mdx', self.mdx_bytes)
        self.assertEqual(refs, {self.CUSTOM_TEXTURE_PATH})


class TestMpqHeaderFieldEnums(unittest.TestCase):
    """mpq/data.py defined MPQFileHeaderExtendedFieldsFormatV4 twice, and the
    second copy (V2's members) replaced the real one (issue #4). The field
    lists are zipped with struct.unpack() results, so their order and length
    have to follow the header layout: StormLib's TMPQHeader."""

    def test_no_class_is_defined_twice(self):
        import ast
        import collections
        import importlib
        data = importlib.import_module('wc3files.mpq.data')
        with open(data.__file__, encoding='utf-8') as f:
            tree = ast.parse(f.read())
        names = collections.Counter(node.name for node in tree.body
                                    if isinstance(node, ast.ClassDef))
        self.assertEqual([name for name, n in names.items() if n > 1], [])

    def test_v4_fields_follow_the_header_layout(self):
        import struct
        from wc3files.mpq.data import MPQFileHeaderExtendedFieldsFormatV4 as V4
        self.assertEqual([field.value for field in V4], [
            'HASH_TABLE_SIZE_64', 'BLOCK_TABLE_SIZE_64', 'HI_BLOCK_TABLE_SIZE_64',
            'HET_TABLE_SIZE_64', 'BET_TABLE_SIZE_64', 'RAW_CHUNK_SIZE',
            'MD5_BLOCK_TABLE', 'MD5_HASH_TABLE', 'MD5_HI_BLOCK_TABLE',
            'MD5_BET_TABLE', 'MD5_HET_TABLE', 'MD5_MPQ_HEADER'])
        # Five 64-bit sizes, one DWORD and six MD5 digests: the 140 bytes a
        # version-4 header (208 bytes) adds after a version-3 one (68 bytes).
        layout = '<5QI' + '16s' * 6
        self.assertEqual(len(struct.unpack(layout, bytes(struct.calcsize(layout)))),
                         len(V4))
        self.assertEqual(struct.calcsize(layout), 208 - 68)

    def test_v2_and_v3_fields_are_unchanged(self):
        from wc3files.mpq.data import (MPQFileHeaderExtendedFieldsFormatV2 as V2,
                                       MPQFileHeaderExtendedFieldsFormatV3 as V3)
        self.assertEqual([field.value for field in V2], [
            'EXTENDED_BLOCK_TABLE_OFFSET', 'HASH_TABLE_OFFSET_HIGH',
            'BLOCK_TABLE_OFFSET_HIGH'])
        self.assertEqual([field.value for field in V3], [
            'ARCHIVE_SIZE_64', 'BET_TABLE_POS_64', 'HET_TABLE_POS_64'])


class TestExportStaysInsideTheFolder(unittest.TestCase):
    """Member names come from the archive, which may be untrusted: a name with
    `..` parts or an absolute path must not be written outside the folder."""

    def _archive(self, tmp):
        self.outside = os.path.join(tmp, 'absolute.txt')
        files = {
            'war3map.j': b'KEEP',
            '..\\..\\evil.txt': b'EVIL',
            '../../evil2.txt': b'EVIL',
            self.outside: b'EVIL',
        }
        path = os.path.join(tmp, 'evil.w3x')
        MpqWriter().write(files, path)
        return path

    def _written(self, tmp):
        return sorted(os.path.relpath(os.path.join(root, name), tmp)
                      for root, _, names in os.walk(tmp) for name in names)

    def _check(self, tmp, out, skipped):
        written = self._written(tmp)
        self.assertNotIn('evil.txt', written)
        self.assertNotIn('evil2.txt', written)
        self.assertFalse(os.path.exists(self.outside))
        with open(os.path.join(out, 'evil', 'war3map.j'), 'rb') as handle:
            self.assertEqual(handle.read(), b'KEEP')
        if skipped is not None:
            self.assertEqual(sorted(skipped),
                             sorted(['..\\..\\evil.txt', '../../evil2.txt', self.outside]))

    def test_mpq_file_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = MpqFile()
            archive.read(self._archive(tmp))
            archive.read_files()
            out = os.path.join(tmp, 'a', 'b')
            self._check(tmp, out, archive.export(out))

    def test_w3x_file_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = W3xFile()
            archive.read(self._archive(tmp))
            out = os.path.join(tmp, 'a', 'b')
            self._check(tmp, out, archive.export(out))

    def test_cli_unpack(self):
        from wc3files.cli.main import main
        with tempfile.TemporaryDirectory() as tmp:
            source = self._archive(tmp)
            out = os.path.join(tmp, 'a', 'b')
            self.assertEqual(main(['unpack', source, out]), 0)
            self._check(tmp, out, None)


def _archive_with_encrypted_members(members):
    """A classic MPQ whose `members` ({name: bytes}) are each stored as one
    encrypted, uncompressed single-unit block, plus a plain (listfile).

    MpqWriter does not encrypt, and real maps encrypt files inside folders
    (scripts\\war3map.j), so this builds the blocks by hand with the same
    cipher, keyed on the name after its last backslash as the format says."""
    import struct
    from wc3files.mpq.data import (MPQBlockTableEntry, MPQFileDataType, MPQHashType,
                                   MPQKeyType, MpqMagicType, MpqUnpack)
    cipher = MpqCipher()
    writer = MpqWriter()
    names = list(members) + ['(listfile)']
    contents = dict(members)
    contents['(listfile)'] = ''.join(name + '\r\n' for name in members).encode()
    exists, single = MPQFileDataType.EXISTS.value, MPQFileDataType.SINGLE_UNIT.value
    data, blocks = bytearray(), []
    for name in names:
        raw = contents[name]
        flags = exists | single
        if name != '(listfile)':
            key = cipher.hash(name.rsplit('\\', 1)[-1], MPQHashType.MPQ_HASH_FILE_KEY.value)
            raw = cipher.encrypt(raw, key)
            flags |= MPQFileDataType.ENCRYPTED.value
        blocks.append({MPQBlockTableEntry.BLOCK_OFFSET.value: 32 + len(data),
                       MPQBlockTableEntry.BLOCK_SIZE.value: len(raw),
                       MPQBlockTableEntry.FILE_SIZE.value: len(raw),
                       MPQBlockTableEntry.FLAGS.value: flags})
        data += raw
    hash_table = protection.build_hash_table([(name, i) for i, name in enumerate(names)], cipher)
    packed_hash = writer._pack_table(hash_table, MPQHashTableEntry, MpqUnpack.HASH_TABLE.value,
                                     MPQKeyType.MPQ_HASH_TABLE)
    packed_block = writer._pack_table(blocks, MPQBlockTableEntry, MpqUnpack.BLOCK_ENTRY.value,
                                      MPQKeyType.MPQ_BLOCK_TABLE)
    hash_offset = 32 + len(data)
    block_offset = hash_offset + len(packed_hash)
    header = struct.pack(MpqUnpack.HEADER.value, MpqMagicType.MPQ_ARCHIVE_V1.value, 32,
                         block_offset + len(packed_block), 0, 3, hash_offset, block_offset,
                         len(hash_table), len(blocks))
    return header + bytes(data) + packed_hash + packed_block


class TestEncryptedMembersInFolders(unittest.TestCase):
    """The file key is the hash of the name after its last backslash, on
    every platform. os.path.basename only splits on a backslash on Windows,
    so on Linux and macOS these members decrypted to garbage."""
    MEMBERS = {
        'war3map.w3i': b'ROOT' * 64,
        'scripts\\war3map.j': b'function main takes nothing returns nothing\r\n'.ljust(256, b' '),
        'war3mapImported\\deep\\icon.blp': bytes(range(256)) * 4,
    }

    def test_both_readers_decrypt_members_in_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'encrypted.w3x')
            with open(path, 'wb') as handle:
                handle.write(_archive_with_encrypted_members(self.MEMBERS))

            w3x = W3xFile()
            w3x.read(path)
            mpq = MpqFile()
            mpq.read(path)
            mpq.read_files()
            for name, expected in self.MEMBERS.items():
                with self.subTest(name=name):
                    self.assertEqual(w3x.files.get(name), expected)
                    self.assertEqual(mpq.files.get(name), expected)


class TestRebuildDropsStaleTables(unittest.TestCase):
    """(attributes) and (signature) describe the block table of the archive
    they came from. Carried into a rebuilt archive they are wrong, and a real
    client crashed on such a map, so every way of rebuilding leaves them out.
    Two corpus maps (Wild-Hunt 1.2.0 [Protected]) list (attributes) in their
    listfile, so a plain read() hands it to build_from_files()."""

    def _source(self, tmp):
        # An archive whose listfile names a stale (attributes), as those maps
        # have. The writer now refuses to copy one, so let it through here.
        from unittest import mock
        path = os.path.join(tmp, 'source.w3x')
        files = {'war3map.j': b'J', '(attributes)': b'STALE ATTRIBUTES', '(signature)': b'SIG'}
        with mock.patch.object(MpqWriter, 'GENERATED', ('(listfile)',), create=True):
            MpqWriter().write(files, path)
        return path

    def _members(self, path):
        archive = MpqFile()
        archive.read(path)
        archive.read_files()
        blocks = archive.mpq_block_table.get_data()
        return {name: archive.mpq_hash_table.get_hash_table_entry(name, block_table=blocks)
                for name in ('(attributes)', '(signature)', 'war3map.j')}, archive

    def test_build_from_files_after_read_leaves_them_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            w3x = W3xFile()
            w3x.read(self._source(tmp))
            self.assertEqual(w3x.files.get('(attributes)'), b'STALE ATTRIBUTES')  # the setup

            out = os.path.join(tmp, 'rebuilt.w3x')
            w3x.build_from_files(w3x.files, out)
            members, _ = self._members(out)
            self.assertIsNone(members['(attributes)'])
            self.assertIsNone(members['(signature)'])
            self.assertIsNotNone(members['war3map.j'])

            MpqFile().build(w3x.files, out)
            members, _ = self._members(out)
            self.assertIsNone(members['(attributes)'])
            self.assertIsNone(members['(signature)'])

    def test_attributes_true_writes_a_fresh_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            w3x = W3xFile()
            w3x.read(self._source(tmp))
            out = os.path.join(tmp, 'rebuilt.w3x')
            w3x.build_from_files(w3x.files, out, attributes=True)
            members, archive = self._members(out)
            self.assertIsNotNone(members['(attributes)'])
            self.assertNotEqual(archive.files.get('(attributes)'), b'STALE ATTRIBUTES')
            self.assertIsNone(members['(signature)'])


class TestEmptyMembers(unittest.TestCase):
    """An existing file whose block size is 0 is an empty member, not a
    missing one. Real maps store such files (25 in 9 corpus maps), often to
    blank a game asset."""

    def _zero_block_size(self, path, name):
        """Set the block size of `name` to 0 in the archive's (encrypted)
        block table, the way real archives store an empty file."""
        import struct
        from wc3files.mpq.data import MPQHashType, MPQKeyType
        archive = MpqFile()
        archive.read(path)
        blocks = archive.mpq_block_table.get_data()
        index = archive.mpq_hash_table.get_hash_table_entry(name, block_table=blocks)['FileBlockIndex']
        header = archive.mpq_header.get_data()
        cipher = MpqCipher()
        key = cipher.hash(MPQKeyType.MPQ_BLOCK_TABLE.value, MPQHashType.MPQ_HASH_FILE_KEY.value)
        with open(path, 'r+b') as handle:
            handle.seek(header['BlockTableOffset'])
            table = bytearray(cipher.decrypt(handle.read(16 * len(blocks)), key))
            struct.pack_into('<I', table, 16 * index + 4, 0)  # BlockSize
            handle.seek(header['BlockTableOffset'])
            handle.write(cipher.encrypt(bytes(table), key))

    def test_both_readers_keep_an_empty_member(self):
        files = {'war3map.j': b'J', 'war3mapImported\\blank.blp': b''}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'empty.w3x')
            MpqWriter().write(files, path)
            self._zero_block_size(path, 'war3mapImported\\blank.blp')

            w3x = W3xFile()
            w3x.read(path)
            mpq = MpqFile()
            mpq.read(path)
            mpq.read_files()
            for found in (w3x.files, mpq.files):
                self.assertEqual(found.get('war3mapImported\\blank.blp'), b'')
                self.assertEqual(found.get('war3map.j'), b'J')

            # and a repack keeps it
            out = os.path.join(tmp, 'repacked.w3x')
            w3x.build_from_files(w3x.files, out)
            again = W3xFile()
            again.read(out)
            self.assertEqual(again.files.get('war3mapImported\\blank.blp'), b'')


class TestDeepReadSkipsAnUnreadableBlock(unittest.TestCase):
    """A guessed name can point at a block that is not that file. In DEEP
    mode that block is skipped and the other files are still recovered; a
    file the listfile names must still raise."""
    FILES = {
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
        'war3map.w3i': b'map info ' * 20,
        'war3map.wts': b'STRING 0\r\n{\r\nName\r\n}\r\n',
    }

    def _corrupt(self, path, name):
        """Overwrite the first sector offset of `name` with a value past the
        end of its block, as a decoy's garbage table would hold."""
        import struct
        archive = MpqFile()
        archive.read(path)
        blocks = archive.mpq_block_table.get_data()
        entry = archive.mpq_hash_table.get_hash_table_entry(name, block_table=blocks)
        offset = blocks[entry['FileBlockIndex']]['BlockOffset']
        with open(path, 'r+b') as handle:
            handle.seek(offset)
            handle.write(struct.pack('<I', 0xFFFFFFF0))

    def test_deep_read_skips_the_block_and_recovers_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'protected.w3x')
            MpqWriter(protection=MpqProtectionLevel.SAFE, rng=random.Random(1)).write(self.FILES, path)
            self._corrupt(path, 'war3map.w3i')

            w3x = W3xFile()
            w3x.read(path, mode='deep')
            mpq = MpqFile()
            mpq.read(path)
            mpq.read_files()
            for files in (w3x.files, mpq.files):
                self.assertNotIn('war3map.w3i', files)
                self.assertEqual(files['war3map.j'], self.FILES['war3map.j'])
                self.assertEqual(files['war3map.wts'], self.FILES['war3map.wts'])

    def test_a_listed_file_with_a_bad_table_still_raises(self):
        from wc3files.mpq.sectors import MpqBlockError
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'plain.w3x')
            MpqWriter().write(self.FILES, path)
            self._corrupt(path, 'war3map.w3i')
            with self.assertRaises(MpqBlockError):
                W3xFile().read(path)


class TestHashLookupProbes(unittest.TestCase):
    """find_hash_entry walks the table from the name's home slot, as the MPQ
    format, Storm and StormLib do."""
    FREE, DELETED = 0xFFFFFFFF, 0xFFFFFFFE

    def _entry(self, a, b, block, language=0):
        return {'FilePathHashA': a, 'FilePathHashB': b, 'Language': language,
                'Platform': 0, 'FileBlockIndex': block}

    def _table(self, *entries):
        table = [self._entry(self.FREE, self.FREE, self.FREE) for _ in range(8)]
        for slot, entry in entries:
            table[slot] = entry
        return table

    def _find(self, table, home, blocks=None, language=0):
        # The plain walk and the indexed lookup must agree.
        from wc3files.mpq.hash_table import build_hash_index, find_hash_entry
        walked = find_hash_entry(table, home, 1, 2, language, 0, blocks)
        indexed = find_hash_entry(table, home, 1, 2, language, 0, blocks, build_hash_index(table))
        self.assertIs(indexed, walked)
        return walked

    def test_a_free_entry_ends_the_walk(self):
        # The match sits past a free slot, so the game never reaches it.
        table = self._table((3, self._entry(9, 9, 0)), (5, self._entry(1, 2, 0)))
        self.assertIsNone(self._find(table, 3))

    def test_a_deleted_entry_does_not_end_the_walk(self):
        table = self._table((3, self._entry(9, 9, self.DELETED)), (4, self._entry(1, 2, 7)))
        self.assertEqual(self._find(table, 3)['FileBlockIndex'], 7)

    def test_the_walk_wraps_around_the_end_of_the_table(self):
        table = self._table((7, self._entry(9, 9, 0)), (0, self._entry(1, 2, 5)))
        self.assertEqual(self._find(table, 7)['FileBlockIndex'], 5)

    def test_a_match_on_a_block_that_does_not_exist_is_skipped(self):
        blocks = {0: {'Flags': 0}, 1: {'Flags': 0x80000000}}
        table = self._table((2, self._entry(1, 2, 0)), (3, self._entry(1, 2, 99)),
                            (4, self._entry(1, 2, 1)))
        self.assertEqual(self._find(table, 2, blocks)['FileBlockIndex'], 1)

    def test_an_exact_language_beats_the_neutral_entry(self):
        table = self._table((2, self._entry(1, 2, 3)), (3, self._entry(1, 2, 4, language=0x409)))
        self.assertEqual(self._find(table, 2, language=0x409)['FileBlockIndex'], 4)
        self.assertEqual(self._find(table, 2)['FileBlockIndex'], 3)


class TestNonAsciiNames(unittest.TestCase):
    """The MPQ hash runs over the bytes of a name, upper-casing ASCII only."""

    def test_table_keys_match_the_format(self):
        # The two fixed keys every MPQ reader uses for its tables.
        cipher = MpqCipher()
        self.assertEqual(cipher.hash('(hash table)', 3), 0xC3AF3770)
        self.assertEqual(cipher.hash('(block table)', 3), 0xEC83B3A3)

    def test_a_name_hashes_as_its_utf8_bytes_and_only_ascii_is_upper_cased(self):
        cipher, w3x = MpqCipher(), W3xFile()
        from wc3files.mpq.data import MPQHashType
        for name in ('war3map.j', 'Units\\café.blp', '中文\\图标.blp'):
            with self.subTest(name=name):
                expected = cipher.hash(name.encode('utf-8'), 1)
                self.assertEqual(cipher.hash(name, 1), expected)
                self.assertEqual(w3x.hash(name, MPQHashType.MPQ_HASH_NAME_A), expected)
        # 'e' with an accent is two bytes in UTF-8 and neither is a letter
        # to upper-case, so the upper-case form is a different name.
        self.assertNotEqual(cipher.hash('café', 1), cipher.hash('CAFÉ', 1))
        self.assertEqual(cipher.hash('café', 1), cipher.hash('CAFé', 1))

    def test_names_outside_ascii_round_trip_through_both_readers(self):
        files = {
            'war3map.j': b'J',
            '中文\\图标.blp': b'CJK',
            'Units\\café.mdx': b'LATIN',
            # A legacy code-page name, not valid UTF-8: the byte 0xE9 on its
            # own, as a surrogateescape decode keeps it.
            'Sound\\\udce9t\udce9.wav': b'CP1252',
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'names.w3x')
            MpqWriter().write(files, path)
            w3x = W3xFile()
            w3x.read(path)
            mpq = MpqFile()
            mpq.read(path)
            mpq.read_files()
            for name, data in files.items():
                with self.subTest(name=name):
                    self.assertEqual(w3x.files.get(name), data)
                    self.assertEqual(mpq.files.get(name), data)


class TestExportCreatesFolders(unittest.TestCase):
    """Archive names separate folders with a backslash, on every platform."""

    def test_member_in_a_folder_is_written_inside_that_folder(self):
        files = {'war3map.j': b'J', 'Units\\Custom.txt': b'A', 'war3mapImported\\a\\b.blp': b'B'}
        for cls in (MpqFile, W3xFile):
            with self.subTest(cls=cls.__name__), tempfile.TemporaryDirectory() as tmp:
                source = os.path.join(tmp, 'folders.w3x')
                MpqWriter().write(files, source)
                archive = cls()
                archive.read(source)
                if cls is MpqFile:
                    archive.read_files()
                archive.export(tmp)
                root = os.path.join(tmp, 'folders')
                with open(os.path.join(root, 'Units', 'Custom.txt'), 'rb') as handle:
                    self.assertEqual(handle.read(), b'A')
                with open(os.path.join(root, 'war3mapImported', 'a', 'b.blp'), 'rb') as handle:
                    self.assertEqual(handle.read(), b'B')
                self.assertFalse(any('\\' in name for name in os.listdir(root)))

    def test_export_then_build_keeps_the_member_names(self):
        files = {'war3map.j': b'J', 'Units\\Custom.txt': b'A'}
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'folders.w3x')
            MpqWriter().write(files, source)
            archive = W3xFile()
            archive.read(source)
            archive.export(tmp)
            archive.build(os.path.join(tmp, 'folders'), os.path.join(tmp, 'repacked.w3x'))
            again = W3xFile()
            again.read(os.path.join(tmp, 'repacked.w3x'))
            self.assertEqual({k: v for k, v in again.files.items() if k != '(listfile)'}, files)


class TestOneCipher(unittest.TestCase):
    """W3xFile used to carry its own copy of the MPQ cipher. Its encrypt()
    never masked the key to 32 bits (OverflowError on every platform) and read
    words through ctypes.c_ulong, which is 8 bytes on 64-bit Linux and macOS."""

    DATA = bytes(range(256)) * 4 + b'xyz'   # a tail that is not a whole word
    KEY = 0x12345678

    def test_w3x_encrypt_works_and_matches_the_cipher(self):
        encrypted = W3xFile().encrypt(bytearray(self.DATA), self.KEY)
        self.assertEqual(bytes(encrypted), MpqCipher().encrypt(self.DATA, self.KEY))
        self.assertEqual(W3xFile().decrypt(bytes(encrypted), self.KEY), self.DATA)

    def test_w3x_uses_the_shared_cipher(self):
        self.assertTrue(W3xFile().crypt_table is MpqCipher().crypt_table)


if __name__ == '__main__':
    unittest.main()
