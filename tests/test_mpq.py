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

    def test_experimental_and_aggressive_also_defeat_this_packages_own_reader(self):
        # Documented, known limitation (see protection.py): this package's own
        # MpqHashTable.get_hash_table_entry() resolves by plain name-match, the
        # same "naive" behavior the decoy technique is designed to defeat - it
        # doesn't cross-reference block-table validity to skip a decoy the way
        # Storm.dll is believed to. Real files therefore become unresolvable via
        # wc3files' own reader too at these tiers - tracked here as intentional,
        # not silently left unexamined.
        self._build(MpqProtectionLevel.EXPERIMENTAL)

        mpq = MpqFile()
        mpq.read(self.archive_path)
        mpq.read_files()
        self.assertNotEqual(mpq.files.get('war3map.j'), self.FILES['war3map.j'])

    def test_protection_and_attributes_compose_without_crashing(self):
        self._build(MpqProtectionLevel.AGGRESSIVE, attributes=True)
        self.assertTrue(os.path.exists(self.archive_path))
        # Structural sanity only (see the test above - AGGRESSIVE defeats this
        # package's own reader by design), so just confirm the archive parses.
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


if __name__ == '__main__':
    unittest.main()
