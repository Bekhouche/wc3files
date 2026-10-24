"""Tests for tools/build_map_corpus.py - id parsing, EpicWar page scraping,
and the live-download loop. Network is mocked; nothing here talks to EpicWar.
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

import _helpers  # noqa: F401  (src on path, quiet logging)

from wc3files.mpq.data import MpqProtectionLevel
from wc3files.w3x import W3xFile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOL = os.path.join(_ROOT, 'tools', 'build_map_corpus.py')

# tools/ is developer tooling kept out of the repo, so a fresh clone or an
# installed copy will not have it. Skip rather than fail at import: the
# module being absent is the expected case there, not a broken test.
if not os.path.exists(_TOOL):
    raise unittest.SkipTest('tools/build_map_corpus.py is not present')

_SPEC = importlib.util.spec_from_file_location('build_map_corpus', _TOOL)
corpus = importlib.util.module_from_spec(_SPEC)
sys.modules['build_map_corpus'] = corpus
_SPEC.loader.exec_module(corpus)


OPENABLE = {
    '(listfile)': b'war3map.w3i\r\nwar3map.j\r\n',
    'war3map.w3i': b'\x00' * 64,
    'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
}

EPICWAR_HTML = '''
<h1><a href="https://www.epicwar.com/maps/100001/download/?token=abc123&amp;time=1700000000000">Example Arena v1.0.0</a></h1>
<a rel="nofollow" href="https://www.epicwar.com/maps/100001/download/?token=abc123&amp;time=1700000000000">Download <!-- -->Example_Arena_v1_0_0.w3x</a>
'''

SPACED_HTML = '''
<a rel="nofollow" href="https://www.epicwar.com/maps/1/download/?token=deadbeef&amp;time=1">Download <!-- -->Example Defence 4.w3m</a>
'''

GONE_HTML = '''
<div role="alert">The requested map file is no longer available.</div>
'''

RELATED_ONLY_HTML = '''
<a href="https://www.epicwar.com/maps/100003/download/?token=000692&amp;time=1">Example Survival 1.0a.w3x</a>
'''


def _write_map(directory, name, files):
    path = os.path.join(directory, name)
    W3xFile().build_from_files(files, path)
    return path


class TestCheck(unittest.TestCase):
    def test_openable_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_map(tmp, 'good.w3x', OPENABLE)
            ok, reason, info = corpus.check(path)
            self.assertTrue(ok)
            self.assertEqual(reason, '')
            self.assertEqual(info.get('files'), 2)

    def test_protected_map_is_rejected_without_a_deep_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'protected.w3x')
            W3xFile().build_from_files(OPENABLE, path,
                                       protection=MpqProtectionLevel.SAFE)
            ok, reason, info = corpus.check(path)
            self.assertFalse(ok)
            self.assertEqual(reason, 'protected')


class TestParseId(unittest.TestCase):
    def test_bare_number(self):
        self.assertEqual(corpus.parse_id('100001'), '100001')

    def test_map_url(self):
        self.assertEqual(
            corpus.parse_id('https://www.epicwar.com/maps/100002/'), '100002')

    def test_download_url(self):
        self.assertEqual(
            corpus.parse_id(
                'https://www.epicwar.com/maps/100001/download/?token=abc'),
            '100001')

    def test_rejects_noise(self):
        self.assertIsNone(corpus.parse_id('Example Arena.w3x'))


class TestParseEpicwarPage(unittest.TestCase):
    def test_tokenised_download_and_filename(self):
        url, name, status = corpus.parse_epicwar_page(EPICWAR_HTML, '100001')
        self.assertEqual(status, 'ok')
        self.assertEqual(
            url,
            'https://www.epicwar.com/maps/100001/download/?token=abc123&time=1700000000000')
        self.assertEqual(name, 'Example_Arena_v1_0_0.w3x')

    def test_filename_with_spaces(self):
        url, name, status = corpus.parse_epicwar_page(SPACED_HTML, '1')
        self.assertEqual(status, 'ok')
        self.assertTrue(url.endswith('token=deadbeef&time=1'))
        self.assertEqual(name, 'Example Defence 4.w3m')

    def test_removed_map(self):
        url, name, status = corpus.parse_epicwar_page(GONE_HTML, '10000')
        self.assertEqual(status, 'gone')
        self.assertIsNone(url)
        self.assertIsNone(name)

    def test_ignores_a_related_map_download(self):
        url, name, status = corpus.parse_epicwar_page(RELATED_ONLY_HTML, '110014')
        self.assertEqual(status, 'no-download')
        self.assertIsNone(url)


class TestFilenameHelpers(unittest.TestCase):
    def test_content_disposition_quoted(self):
        self.assertEqual(
            corpus.filename_from_disposition(
                'attachment; filename="Example_Arena_v1_0_0.w3x"'),
            'Example_Arena_v1_0_0.w3x')

    def test_content_disposition_rfc5987(self):
        self.assertEqual(
            corpus.filename_from_disposition(
                "attachment; filename*=UTF-8''Example%20Defence%204.w3m"),
            'Example Defence 4.w3m')

    def test_safe_filename_strips_path_and_illegal_chars(self):
        self.assertEqual(
            corpus.safe_map_filename(r'C:\maps\foo<bar>.w3x'),
            'foo_bar_.w3x')

    def test_safe_filename_adds_extension(self):
        self.assertEqual(corpus.safe_map_filename('untitled'), 'untitled.w3x')


class TestFetchEpicwarMap(unittest.TestCase):
    def test_downloads_mpq_and_names_it_from_disposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = _write_map(tmp, 'payload.w3x', OPENABLE)
            with open(payload, 'rb') as handle:
                data = handle.read()
            stage = os.path.join(tmp, 'stage')

            def fake_get(url, dest=None, timeout=60, referer=None):
                if '/download/' in url:
                    with open(dest, 'wb') as handle:
                        handle.write(data)
                    return 200, {
                        'Content-Disposition':
                            'attachment; filename="Example_Arena_v1_0_0.w3x"',
                    }, None
                return 200, {}, EPICWAR_HTML.encode('utf-8')

            original = corpus.http_get
            corpus.http_get = fake_get
            try:
                path, name, reason = corpus.fetch_epicwar_map('100001', stage)
            finally:
                corpus.http_get = original

            self.assertEqual(reason, '')
            self.assertEqual(name, 'Example_Arena_v1_0_0.w3x')
            self.assertTrue(path.endswith('100001_Example_Arena_v1_0_0.w3x'))
            with open(path, 'rb') as handle:
                self.assertEqual(handle.read(4), b'MPQ\x1a')

    def test_404_is_not_found(self):
        def fake_get(url, dest=None, timeout=60, referer=None):
            return 404, {}, b'nope'

        original = corpus.http_get
        corpus.http_get = fake_get
        try:
            path, name, reason = corpus.fetch_epicwar_map('277617', 'unused')
        finally:
            corpus.http_get = original
        self.assertIsNone(path)
        self.assertEqual(reason, 'not found')


class TestLoadIds(unittest.TestCase):
    def test_pairs_and_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'ids.txt')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(
                    '100001                                  Example Arena v1.0.0.w3x\n'
                    'https://www.epicwar.com/maps/100002/    Example Melee v3.0.0.w3x\n'
                    '356470\n'
                    '# comment\n'
                )
            pairs, pending = corpus.load_ids(path)
            self.assertEqual(pairs['example arena v1.0.0.w3x'], '100001')
            self.assertEqual(pairs['example melee v3.0.0.w3x'], '100002')
            self.assertEqual(pending, ['356470'])


class TestLiveDownloadLoop(unittest.TestCase):
    def test_counts_down_keeps_openable_stops_at_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            openable = _write_map(tmp, 'good.w3x', OPENABLE)
            # Garbage, not a missing listfile: a real protected map is
            # `protected` and skipped, which this loop already covers via
            # fetch returning None.
            stripped = os.path.join(tmp, 'bad.w3x')
            with open(stripped, 'wb') as handle:
                handle.write(b'not an mpq')
            fetched = []

            def fetch(map_id, stage_dir):
                fetched.append(map_id)
                if map_id == 5:
                    return None, None, 'not found'
                if map_id == 4:
                    return stripped, os.path.basename(stripped), ''
                if map_id == 3:
                    return openable, 'Example Defence 4.w3m', ''
                return None, None, 'not found'

            out = os.path.join(tmp, 'corpus')
            os.makedirs(out)
            ledger = os.path.join(out, 'ledger.jsonl')
            reasons = {}
            kept, skipped, rejected = corpus.run_epicwar_download(
                5, 1, count=1, kept=0, skipped=0, rejected=0, reasons=reasons,
                out_dir=out, ledger_path=ledger, seen={}, by_id={},
                link_template='https://www.epicwar.com/maps/{id}/',
                no_copy=False, recheck=False, sleep=0, fetch=fetch)

            self.assertEqual(fetched, [5, 4, 3])
            self.assertEqual(kept, 1)
            self.assertEqual(rejected, 2)
            self.assertTrue(os.path.exists(os.path.join(out, 'Example Defence 4.w3m')))
            with open(ledger, encoding='utf-8') as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(rows[0]['id'], '5')
            self.assertEqual(rows[0]['reason'], 'not found')
            self.assertEqual(rows[-1]['id'], '3')
            self.assertTrue(rows[-1]['ok'])
            self.assertEqual(rows[-1]['id_source'], 'epicwar')

    def test_resume_skips_ledgered_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'corpus')
            os.makedirs(out)
            ledger = os.path.join(out, 'ledger.jsonl')
            by_id = {
                '5': {'id': '5', 'ok': True, 'name': 'already.w3x',
                      'reason': ''},
            }
            fetched = []

            def fetch(map_id, stage_dir):
                fetched.append(map_id)
                return None, None, 'not found'

            kept, skipped, rejected = corpus.run_epicwar_download(
                5, 4, count=1, kept=0, skipped=0, rejected=0, reasons={},
                out_dir=out, ledger_path=ledger, seen={}, by_id=by_id,
                link_template='https://www.epicwar.com/maps/{id}/',
                no_copy=True, recheck=False, sleep=0, fetch=fetch)

            self.assertEqual(fetched, [])
            self.assertEqual(kept, 1)
            self.assertEqual(skipped, 1)
            self.assertEqual(rejected, 0)


class TestMainDownloadFrom(unittest.TestCase):
    def test_download_from_alone_is_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            openable = _write_map(tmp, 'good.w3x', OPENABLE)

            def fetch(map_id, stage_dir):
                return openable, 'good.w3x', ''

            original = corpus.fetch_epicwar_map
            corpus.fetch_epicwar_map = fetch
            try:
                out = os.path.join(tmp, 'corpus')
                rc = corpus.main([
                    '--download-from', '9',
                    '--download-stop', '9',
                    '--count', '1',
                    '--sleep', '0',
                    '--out', out,
                ])
            finally:
                corpus.fetch_epicwar_map = original
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(out, 'good.w3x')))
            ledger = os.path.join(out, 'ledger.jsonl')
            with open(ledger, encoding='utf-8') as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual(rows[0]['id'], '9')
            self.assertEqual(rows[0]['id_source'], 'epicwar')

    def test_refuses_to_run_with_no_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                corpus.main(['--out', tmp, '--count', '1'])


if __name__ == '__main__':
    unittest.main()
