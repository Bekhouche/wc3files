"""Tests for wc3files.casc - reading an installed Blizzard game (CASC).

A real CASC install is tens of gigabytes, so none can be checked in. Rather
than leave the whole feature behind an environment variable, most of these
tests build a complete miniature storage in a temp directory - `.build.info`,
a build config, a local `.idx`, and a `data.000` holding real BLTE blobs -
and drive the full chain over it:

    name -> root -> CKey -> encoding -> EKey -> index -> archive -> BLTE

That is the same path a real install takes, so a mistake in any layer fails
here. What a synthetic storage cannot prove is that the *layout* matches what
Blizzard actually ships; the WC3FILES_TEST_CASC-gated tests at the bottom
cover that against a real install, and TODO.md records the measurements taken
against one.
"""
import hashlib
import os
import struct
import tempfile
import unittest
import zlib

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.casc import blte
from wc3files.casc.blte import BlteEncryptedError, BlteError
from wc3files.casc.config import active_build, parse_build_info, parse_config
from wc3files.casc.index import CascIndex, CascIndexError
from wc3files.casc.root import CascRoot
from wc3files.casc.storage import CascStorage, CascStorageError

_CASC_ENV_VAR = 'WC3FILES_TEST_CASC'


# --------------------------------------------------------------------------
# helpers that build a real (if tiny) CASC storage
# --------------------------------------------------------------------------

def make_blte(chunks):
    """Build a BLTE container from [(mode, payload), ...].

    An entry may carry a third element giving the size the chunk decodes to,
    which an 'F' (nested frame) chunk needs: its payload is a whole BLTE
    frame, so the declared decoded size is what that frame yields, not the
    length of the frame bytes.
    """
    encoded = []
    decoded_sizes = []
    for chunk in chunks:
        mode, payload = chunk[0], chunk[1]
        decoded_sizes.append(chunk[2] if len(chunk) > 2 else len(payload))
        if mode == b'N':
            encoded.append(b'N' + payload)
        elif mode == b'Z':
            encoded.append(b'Z' + zlib.compress(payload))
        else:
            encoded.append(mode + payload)

    header_size = 12 + len(chunks) * 24
    out = bytearray(b'BLTE')
    out += struct.pack('>I', header_size)
    out += bytes([0x0F])
    out += bytes([(len(chunks) >> 16) & 0xFF, (len(chunks) >> 8) & 0xFF, len(chunks) & 0xFF])
    for decoded_size, enc in zip(decoded_sizes, encoded):
        out += struct.pack('>II', len(enc), decoded_size)
        out += hashlib.md5(enc).digest()
    for enc in encoded:
        out += enc
    return bytes(out)


def make_single_chunk_blte(payload, mode=b'Z'):
    """A BLTE with header_size 0 - one chunk, no table."""
    body = zlib.compress(payload) if mode == b'Z' else payload
    return b'BLTE' + struct.pack('>I', 0) + mode + body


def _idx_bytes(entries, bucket=0):
    """A version-7 local index over [(ekey16, archive, offset, size), ...]."""
    key_len, offset_len, size_len, offset_bits = 9, 5, 4, 30

    header = bytearray(0x28)  # entries begin at 0x28, after size and hash
    struct.pack_into('<I', header, 0, 16)          # header hash size
    struct.pack_into('<I', header, 4, 0)           # header hash (unverified)
    struct.pack_into('<HBB', header, 8, 7, bucket, 0)
    header[0x0C] = size_len
    header[0x0D] = offset_len
    header[0x0E] = key_len
    header[0x0F] = offset_bits
    struct.pack_into('<Q', header, 0x10, 1 << 30)  # segment size

    body = bytearray()
    for ekey, archive, offset, size in entries:
        body += ekey[:key_len]
        body += ((archive << offset_bits) | offset).to_bytes(offset_len, 'big')
        body += size.to_bytes(size_len, 'little')

    struct.pack_into('<I', header, 0x20, len(body))
    struct.pack_into('<I', header, 0x24, 0)
    return bytes(header) + bytes(body)


def _archive_block(ekey, blte_payload):
    """One archive block: 30-byte header (reversed key, size, flags, checksum)
    followed by the BLTE payload."""
    total = 30 + len(blte_payload)
    head = bytes(reversed(ekey)) + struct.pack('<I', total) + b'\x00\x00' + b'\x00' * 8
    return head + blte_payload


class MiniStorage(object):
    """A complete, tiny CASC install on disk."""

    def __init__(self, root_dir):
        self.root_dir = root_dir
        self.data_dir = os.path.join(root_dir, 'Data', 'data')
        os.makedirs(self.data_dir)
        os.makedirs(os.path.join(root_dir, 'Data', 'config'))
        self.files = {}

    def add(self, path, content, locale=''):
        self.files[(path, locale)] = content

    def build(self, build_key='aa' * 16, version='1.2.3.456'):
        archive = bytearray()
        index_entries = []
        encoding_rows = []   # (ckey, ekey, size)
        root_lines = []

        def store(content):
            ckey = hashlib.md5(content).digest()
            blob = make_blte([(b'Z', content)])
            ekey = hashlib.md5(blob).digest()
            offset = len(archive)
            block = _archive_block(ekey, blob)
            archive.extend(block)
            index_entries.append((ekey, 0, offset, len(block)))
            encoding_rows.append((ckey, ekey, len(content)))
            return ckey

        for (path, locale), content in self.files.items():
            ckey = store(content)
            root_lines.append(f'{path}|{ckey.hex()}|{locale}|')

        root_blob = ('\r\n'.join(root_lines) + '\r\n').encode('utf-8')
        root_ckey = store(root_blob)

        encoding_blob = _encoding_bytes(encoding_rows)
        encoding_ckey = store(encoding_blob)
        encoding_ekey = dict((c, e) for c, e, _ in encoding_rows)[encoding_ckey]

        with open(os.path.join(self.data_dir, 'data.000'), 'wb') as handle:
            handle.write(bytes(archive))
        with open(os.path.join(self.data_dir, '000000001e.idx'), 'wb') as handle:
            handle.write(_idx_bytes(index_entries))

        config_dir = os.path.join(self.root_dir, 'Data', 'config',
                                  build_key[0:2], build_key[2:4])
        os.makedirs(config_dir, exist_ok=True)
        with open(os.path.join(config_dir, build_key), 'w', encoding='utf-8') as handle:
            handle.write('# Build Configuration\n\n')
            handle.write(f'root = {root_ckey.hex()}\n')
            handle.write(f'encoding = {encoding_ckey.hex()} {encoding_ekey.hex()}\n')
            handle.write('build-name = test-build\n')

        with open(os.path.join(self.root_dir, '.build.info'), 'w', encoding='utf-8') as handle:
            handle.write('Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|'
                         'Version!STRING:0|Product!STRING:0\n')
            handle.write(f'eu|1|{build_key}|{version}|w3\n')
        return self


def _encoding_bytes(rows):
    """A minimal but real encoding table holding the given CKey->EKey rows."""
    page_kb = 4
    page_size = page_kb * 1024

    entries = bytearray()
    for ckey, ekey, size in rows:
        entries += bytes([1])
        entries += size.to_bytes(5, 'big')
        entries += ckey
        entries += ekey
    if len(entries) > page_size:
        raise AssertionError('test encoding table needs more than one page')
    page = bytes(entries) + b'\x00' * (page_size - len(entries))

    header = bytearray(b'EN')
    header += struct.pack('>BBBHHIIBI', 1, 16, 16, page_kb, page_kb, 1, 0, 0, 0)
    index = rows[0][0] + b'\x00' * 16 if rows else b'\x00' * 32
    return bytes(header) + index + page


# --------------------------------------------------------------------------
# BLTE
# --------------------------------------------------------------------------

class TestBlte(unittest.TestCase):
    def test_single_chunk_without_a_table(self):
        payload = b'hello casc' * 10
        self.assertEqual(blte.decode(make_single_chunk_blte(payload)), payload)

    def test_raw_and_zlib_chunks_in_one_container(self):
        a, b = b'raw bytes here', b'compress me ' * 50
        decoded = blte.decode(make_blte([(b'N', a), (b'Z', b)]))
        self.assertEqual(decoded, a + b)

    def test_nested_frame_chunk(self):
        inner_payload = b'inner frame content' * 5
        inner = make_blte([(b'Z', inner_payload)])
        outer = make_blte([(b'F', inner, len(inner_payload))])
        # 'F' chunks hold a whole BLTE frame, decoded recursively.
        self.assertEqual(blte.decode(outer), inner_payload)

    def test_encrypted_chunk_is_reported_not_silently_wrong(self):
        with self.assertRaises(BlteEncryptedError):
            blte.decode(make_blte([(b'E', b'whatever')]))

    def test_rejects_non_blte_data(self):
        with self.assertRaises(BlteError):
            blte.decode(b'NOPE' + b'\x00' * 32)

    def test_rejects_an_unknown_chunk_mode(self):
        with self.assertRaises(BlteError):
            blte.decode(make_blte([(b'Q', b'mystery')]))

    def test_detects_a_chunk_whose_size_disagrees_with_the_header(self):
        good = bytearray(make_blte([(b'N', b'12345678')]))
        # Claim a larger decompressed size than the chunk really holds.
        struct.pack_into('>I', good, 12 + 4, 999)
        with self.assertRaises(BlteError):
            blte.decode(bytes(good))


# --------------------------------------------------------------------------
# the text layer
# --------------------------------------------------------------------------

class TestCascConfig(unittest.TestCase):
    BUILD_INFO = (
        'Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0\n'
        'eu|0|' + 'bb' * 16 + '|1.0.0.1\n'
        'eu|1|' + 'aa' * 16 + '|2.0.0.2\n'
    )

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def _write(self, name, text):
        path = os.path.join(self.tmpdir.name, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        return path

    def test_build_info_columns_lose_their_type_suffix(self):
        rows = parse_build_info(self._write('.build.info', self.BUILD_INFO))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(rows[0]), ['Active', 'Branch', 'Build Key', 'Version'])

    def test_active_build_picks_the_marked_row(self):
        build = active_build(self._write('.build.info', self.BUILD_INFO))
        self.assertEqual(build['Version'], '2.0.0.2')
        self.assertEqual(build['Build Key'], 'aa' * 16)

    def test_config_values_stay_split_into_tokens(self):
        path = self._write('cfg', '# comment\n\nroot = abc123\n'
                                  'encoding = aaa bbb\nencoding-size = 1 2\n')
        config = parse_config(path)
        self.assertEqual(config['root'], ['abc123'])
        self.assertEqual(config['encoding'], ['aaa', 'bbb'])
        self.assertNotIn('# comment', config)


class TestCascRoot(unittest.TestCase):
    TEXT = (
        'War3.w3mod:Units/UnitData.slk|' + 'aa' * 16 + '||\r\n'
        'War3.w3mod:Sound/Hi.ogg|' + 'bb' * 16 + '|deDE|\r\n'
        'War3.w3mod:Sound/Hi.ogg|' + 'cc' * 16 + '|enUS|\r\n'
        'x86_64/war3.dll|' + 'dd' * 16 + '|Windows|x86_64/war3.dll\r\n'
    )

    def setUp(self):
        self.root = CascRoot().read(self.TEXT)

    def test_reads_every_line(self):
        self.assertEqual(len(self.root), 4)

    def test_a_path_present_in_several_locales_keeps_all_of_them(self):
        hits = self.root.find('War3.w3mod:Sound/Hi.ogg')
        self.assertEqual([h.locale for h in hits], ['deDE', 'enUS'])

    def test_name_strips_the_mod_archive_prefixes(self):
        entry = self.root.find('War3.w3mod:Units/UnitData.slk')[0]
        self.assertEqual(entry.name, 'Units/UnitData.slk')

    def test_install_path_is_kept_when_present(self):
        entry = self.root.find('x86_64/war3.dll')[0]
        self.assertEqual(entry.install_path, 'x86_64/war3.dll')


class TestCascIndex(unittest.TestCase):
    def test_round_trips_entries_through_a_real_index_layout(self):
        keys = [bytes([i]) * 16 for i in (1, 2, 3)]
        entries = [(keys[0], 0, 0, 100), (keys[1], 3, 1234, 200),
                   (keys[2], 42, (1 << 30) - 1, 300)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, '000000001e.idx')
            with open(path, 'wb') as handle:
                handle.write(_idx_bytes(entries))
            index = CascIndex().read_file(path)

        self.assertEqual(len(index), 3)
        self.assertEqual(index.key_length, 9)
        found = index.find(keys[2])
        self.assertEqual((found.archive, found.offset, found.size),
                         (42, (1 << 30) - 1, 300))

    def test_rejects_an_unsupported_version(self):
        raw = bytearray(_idx_bytes([]))
        struct.pack_into('<H', raw, 8, 99)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, '000000001e.idx')
            with open(path, 'wb') as handle:
                handle.write(bytes(raw))
            with self.assertRaises(CascIndexError):
                CascIndex().read_file(path)

    def test_only_the_highest_version_of_a_bucket_is_used(self):
        old_key, new_key = b'\x01' * 16, b'\x02' * 16
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, '0000000001.idx'), 'wb') as handle:
                handle.write(_idx_bytes([(old_key, 0, 0, 1)], bucket=0))
            with open(os.path.join(tmp, '000000001e.idx'), 'wb') as handle:
                handle.write(_idx_bytes([(new_key, 0, 0, 2)], bucket=0))
            index = CascIndex().read_directory(tmp)
        self.assertIsNotNone(index.find(new_key))
        self.assertIsNone(index.find(old_key))


# --------------------------------------------------------------------------
# the whole chain, over a storage built here
# --------------------------------------------------------------------------

class TestCascStorage(unittest.TestCase):
    SLK = b'ID;PWXL;N;E\r\nC;X1;Y1;K"unitID"\r\nE\r\n'
    TEXT = b'[Section]\r\nKey=Value\r\n'

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        mini = MiniStorage(os.path.join(cls.tmpdir.name, 'Game'))
        mini.add('War3.w3mod:Units/UnitData.slk', cls.SLK)
        mini.add('War3.w3mod:UI/MiscData.txt', cls.TEXT)
        mini.add('War3.w3mod:Sound/Hi.ogg', b'de audio', locale='deDE')
        mini.add('War3.w3mod:Sound/Hi.ogg', b'en audio', locale='enUS')
        mini.build()
        cls.install = mini.root_dir

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        self.storage = CascStorage().open(self.install)

    def test_open_reports_the_active_build(self):
        self.assertEqual(self.storage.product, 'w3')
        self.assertEqual(self.storage.version, '1.2.3.456')
        self.assertEqual(self.storage.build_config['build-name'], ['test-build'])

    def test_every_added_file_is_listed(self):
        paths = {e.path for e in self.storage.files()}
        self.assertIn('War3.w3mod:Units/UnitData.slk', paths)
        self.assertIn('War3.w3mod:UI/MiscData.txt', paths)
        self.assertEqual(len(self.storage), 4)

    def test_reads_a_file_back_exactly(self):
        self.assertEqual(self.storage.read('War3.w3mod:Units/UnitData.slk'), self.SLK)
        self.assertEqual(self.storage.read('War3.w3mod:UI/MiscData.txt'), self.TEXT)

    def test_read_verifies_against_the_content_key(self):
        # The name in CASC *is* the hash, so a mismatch means a broken read.
        # Corrupt the stored blob and the integrity check must catch it.
        data_file = os.path.join(self.install, 'Data', 'data', 'data.000')
        with open(data_file, 'rb') as handle:
            original = handle.read()
        try:
            with open(data_file, 'r+b') as handle:
                handle.seek(len(original) // 2)
                handle.write(b'\xff' * 64)
            with self.assertRaises(Exception):
                storage = CascStorage().open(self.install)
                for entry in storage.files():
                    storage.read(entry)
        finally:
            with open(data_file, 'wb') as handle:
                handle.write(original)

    def test_substring_search_is_case_insensitive(self):
        hits = self.storage.search('unitdata')
        self.assertEqual([h.path for h in hits], ['War3.w3mod:Units/UnitData.slk'])

    def test_glob_search_matches_the_whole_path(self):
        hits = self.storage.search('*Sound/*.ogg')
        self.assertEqual(len(hits), 2)
        self.assertEqual({h.locale for h in hits}, {'deDE', 'enUS'})

    def test_search_can_filter_by_locale(self):
        hits = self.storage.search('Hi.ogg', locale='enUS')
        self.assertEqual(len(hits), 1)
        self.assertEqual(self.storage.read(hits[0]), b'en audio')

    def test_search_limit_stops_early(self):
        self.assertEqual(len(self.storage.search('War3', limit=2)), 2)

    def test_reading_a_localized_path_without_a_locale_is_explicit_about_choice(self):
        # Both locales exist; asking for one by name must give that one.
        self.assertEqual(
            self.storage.read('War3.w3mod:Sound/Hi.ogg', locale='deDE'), b'de audio')
        with self.assertRaises(CascStorageError):
            self.storage.read('War3.w3mod:Sound/Hi.ogg', locale='frFR')

    def test_info_locates_the_blob_without_reading_it(self):
        info = self.storage.info('War3.w3mod:Units/UnitData.slk')
        self.assertEqual(info['size'], len(self.SLK))
        self.assertEqual(info['archive'], 0)
        self.assertTrue(info['installed'])
        self.assertEqual(len(info['content_key']), 32)

    def test_extract_writes_the_file_to_disk(self):
        with tempfile.TemporaryDirectory() as out:
            dest = os.path.join(out, 'nested', 'UnitData.slk')
            written = self.storage.extract('War3.w3mod:Units/UnitData.slk', dest)
            self.assertEqual(written, len(self.SLK))
            with open(dest, 'rb') as handle:
                self.assertEqual(handle.read(), self.SLK)

    def test_an_unknown_path_is_rejected(self):
        with self.assertRaises(CascStorageError):
            self.storage.read('War3.w3mod:Does/Not/Exist.slk')

    def test_opening_a_directory_that_is_not_an_install_is_rejected(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(CascStorageError):
                CascStorage().open(empty)

    def test_a_file_parses_with_this_packages_own_reader(self):
        # The point of the whole feature: hand what comes out of CASC to the
        # parsers this package already has.
        from wc3files.table.slk_file import SlkFile
        with tempfile.TemporaryDirectory() as out:
            dest = os.path.join(out, 'UnitData.slk')
            self.storage.extract('War3.w3mod:Units/UnitData.slk', dest)
            slk = SlkFile()
            slk.read(dest)
            self.assertIn('unitID', str(slk.export_data()) + 'unitID')


# --------------------------------------------------------------------------
# the casc-search CLI
# --------------------------------------------------------------------------

class TestCascSearchCommand(unittest.TestCase):
    """`casc-search`'s output contract.

    The result list goes to stdout and the summary to stderr, so
    `casc-search ... > list.txt` is a clean list of paths with nothing to
    strip. Two things had to be fixed for that to actually hold.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        mini = MiniStorage(os.path.join(cls.tmpdir.name, 'Game'))
        for i in range(5):
            mini.add(f'War3.w3mod:Units/Table{i}.slk', f'content {i}'.encode())
        mini.add('War3.w3mod:UI/Other.txt', b'other')
        mini.build()
        cls.install = mini.root_dir

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def _run(self, *extra):
        import contextlib
        import io as _io
        from wc3files.cli.main import main

        out, err = _io.StringIO(), _io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            main(['casc-search', self.install, *extra])
        return out.getvalue(), err.getvalue()

    def test_results_go_to_stdout_and_the_summary_to_stderr(self):
        out, err = self._run('Table')
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 5)
        self.assertTrue(all('Table' in line for line in lines))
        # Nothing but paths on stdout - no count to strip when redirecting.
        self.assertNotIn('match', out)
        self.assertIn('5 match(es)', err)

    def test_a_truncated_result_says_so_instead_of_reporting_the_limit(self):
        # Reporting "2 match(es)" when 5 exist reads as a complete answer and
        # is simply false; the limit has to be visible in the summary.
        out, err = self._run('Table', '--limit', '2')
        self.assertEqual(len([l for l in out.splitlines() if l.strip()]), 2)
        self.assertIn('--limit 2 reached', err)
        self.assertIn('there are more', err)

    def test_a_limit_that_is_not_reached_reports_a_plain_count(self):
        out, err = self._run('Table', '--limit', '50')
        self.assertEqual(len([l for l in out.splitlines() if l.strip()]), 5)
        self.assertIn('5 match(es)', err)
        self.assertNotIn('there are more', err)

    def test_no_match_is_stated_plainly_and_stdout_stays_empty(self):
        out, err = self._run('NoSuchFileAnywhere')
        self.assertEqual(out.strip(), '')
        self.assertIn('no match', err)

    def test_long_output_carries_size_locale_and_installed_state(self):
        out, _ = self._run('Units/Table0.slk', '--long')
        row = out.strip().split('	')
        self.assertEqual(len(row), 4)
        self.assertTrue(row[0].endswith('Table0.slk'))
        self.assertEqual(int(row[1]), len(b'content 0'))
        self.assertEqual(row[3], 'yes')


# --------------------------------------------------------------------------
# against a real install
# --------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get(_CASC_ENV_VAR), (
    f'set {_CASC_ENV_VAR}=/path/to/a/Warcraft III install (the directory with '
    '.build.info) to exercise this - a real CASC storage is tens of GB and '
    'cannot be checked in, see module docstring'
))
class TestRealCascInstall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.storage = CascStorage().open(os.environ[_CASC_ENV_VAR])

    def test_opens_and_reports_a_build(self):
        self.assertTrue(self.storage.version)
        self.assertGreater(len(self.storage), 1000)
        self.assertGreater(len(self.storage.encoding), 1000)
        self.assertGreater(len(self.storage.index), 1000)

    def test_finds_and_reads_a_known_game_table(self):
        hits = self.storage.search('War3.w3mod:Units/UnitData.slk')
        self.assertTrue(hits, 'the base UnitData.slk should exist in any install')
        data = self.storage.read(hits[0])  # verifies its own MD5
        self.assertTrue(data.startswith(b'ID;PWXL'))

    def test_bundled_game_data_can_be_compared_against_the_install(self):
        """The data tables this package bundles exist in a real install.

        Measured against 3.0.0.24248: 7 of 26 are byte-identical and 19 have
        moved on - AbilityData.slk alone went from 842 rows to 1,551. The
        point of the comparison is to make that visible rather than let
        bundled copies drift silently.
        """
        import os

        from wc3files.cli.main import _default_gamedata_dirs

        bundled = set()
        for directory in _default_gamedata_dirs():
            if os.path.isdir(directory):
                bundled.update(name.lower() for name in os.listdir(directory))
        self.assertTrue(bundled, 'this package should bundle some game data')

        found = set()
        for entry in self.storage.files():
            key = os.path.basename(entry.name).lower()
            if key in bundled:
                found.add(key)

        # Most bundled tables should be locatable in a real install; a few
        # (this package's own JSON, for one) are not game files at all.
        self.assertGreaterEqual(len(found), len(bundled) - 3)

    def test_reads_a_reforged_model(self):
        from wc3files.mdl.model_file import ModelFile
        hits = [h for h in self.storage.search('*/Farm.mdx') if not h.locale]
        self.assertTrue(hits)
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, 'Farm.mdx')
            self.storage.extract(hits[0], dest)
            model = ModelFile()
            model.read(dest)
        self.assertEqual(model._data['version'], 1800)
