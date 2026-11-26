"""`wc3files textconv` (#254): map files as text that `git diff` can compare.
The fixtures are the checked-in samples, copied under the names git gives
its temporary files (`XXXXXX_<name>`)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import _helpers
from wc3files.cli.formats import FORMATS
from wc3files.cli.textconv import textconv, to_lines
from wc3files.doo import MapDoodadsFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAMPLES = (
    ('sample_doodads_v8_11.doo', 'war3map.doo'),
    ('sample.doo', 'war3mapUnits.doo'),
    ('sample.imp', 'war3map.imp'),
    ('sample.mmp', 'war3map.mmp'),
    ('sample.shd', 'war3map.shd'),
    ('sample.wts', 'war3map.wts'),
    ('sample.mdx', 'Footman.mdx'),
    ('sample.j', 'war3map.j'),
    ('sample.blp', 'war3mapMap.blp'),
)


def _remove_read_only(function, path, _error):
    # git makes its object files read-only, which stops Windows removing them.
    os.chmod(path, stat.S_IWRITE)
    function(path)


class TextconvTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, onerror=_remove_read_only)

    def copy(self, sample, name):
        path = os.path.join(self.tmp, name)
        shutil.copyfile(_helpers.sample_path(sample), path)
        return path

    def test_two_reads_give_the_same_text(self):
        for sample, name in SAMPLES:
            with self.subTest(name=name):
                path = self.copy(sample, 'a1b2c3_' + name)
                self.assertEqual(textconv(path), textconv(path))

    def test_format_comes_from_the_end_of_a_git_temp_name(self):
        doodads = textconv(self.copy('sample_doodads_v8_11.doo', 'a1b2c3_war3map.doo'))
        units = textconv(self.copy('sample.doo', 'a1b2c3_war3mapUnits.doo'))
        self.assertIn('"doodads": [', doodads)
        self.assertNotIn('"doodads": [', units)
        self.assertFalse(units.startswith('binary file'))

    def test_text_is_the_dump_json_with_a_doodad_per_line(self):
        path = self.copy('sample_doodads_v8_11.doo', 'war3map.doo')
        text = textconv(path)
        dump = json.loads(json.dumps(FORMATS['doodads'].dump(path)))
        self.assertEqual(json.loads(text), dump)
        doodad_lines = [line for line in text.splitlines() if line.startswith('    {"id": ')]
        self.assertEqual(len(doodad_lines), len(dump['doodads']))

    def test_one_changed_doodad_is_one_changed_line(self):
        path = self.copy('sample_doodads_v8_11.doo', 'war3map.doo')
        before = textconv(path).splitlines()
        doodads = MapDoodadsFile()
        doodads.read(path)
        data = doodads.get_data()
        data['doodads'][3]['life'] = 7
        doodads.import_data(data)
        doodads.write(path)
        after = textconv(path).splitlines()
        self.assertEqual(len(before), len(after))
        changed = [n for n, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertIn('"life": 7', after[changed[0]])

    def test_a_shadow_map_without_its_terrain_goes_in_fixed_rows(self):
        raw = bytes(range(200))
        path = os.path.join(self.tmp, 'a1b2c3_war3map.shd')
        with open(path, 'wb') as handle:
            handle.write(raw)
        data = json.loads(textconv(path))
        self.assertEqual(data['cells'], 200)
        self.assertEqual(bytes.fromhex(''.join(data['rows'])), raw)
        self.assertEqual(len(data['rows']), 4)

    def test_text_files_print_as_they_are(self):
        path = self.copy('sample.j', 'war3map.j')
        # Bytes, so a checkout with CRLF endings compares as it is.
        with open(path, 'rb') as handle:
            self.assertEqual(textconv(path), handle.read().decode('utf-8'))
        listfile = os.path.join(self.tmp, '(listfile)')
        with open(listfile, 'wb') as handle:
            handle.write(b'war3map.j\r\nwar3map.w3i\r\n')
        self.assertEqual(textconv(listfile), 'war3map.j\r\nwar3map.w3i\r\n')

    def test_an_mdx_model_prints_as_mdl(self):
        text = textconv(self.copy('sample.mdx', 'Footman.mdx'))
        self.assertTrue(text.startswith('Version {'))

    def test_other_files_print_their_size_and_hash(self):
        text = textconv(self.copy('sample.blp', 'war3mapMap.blp'))
        size = os.path.getsize(_helpers.sample_path('sample.blp'))
        self.assertRegex(text, rf'^binary file, {size} bytes, sha256 [0-9a-f]{{64}}\n$')

    def test_a_file_that_does_not_read_still_gives_text(self):
        path = os.path.join(self.tmp, 'war3map.w3i')
        with open(path, 'wb') as handle:
            handle.write(b'\x00\x01junk')
        lines = textconv(path).splitlines()
        self.assertTrue(lines[0].startswith('binary file, 6 bytes'))
        self.assertTrue(lines[1].startswith('not read: '))

    def test_to_lines_keeps_nested_records_valid_json(self):
        data = {'a': [1, 2], 'b': [{'x': 1, 'y': [1, 2]}, {'x': 2, 'z': {'n': [{'k': 1}]}}],
                'c': {}, 'd': []}
        self.assertEqual(json.loads(to_lines(data)), data)

    def test_cli_prints_the_same_text(self):
        path = self.copy('sample_doodads_v8_11.doo', 'war3map.doo')
        out = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'textconv', path],
                             capture_output=True, check=True,
                             env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(out.stdout.decode('utf-8'), textconv(path))

    @unittest.skipUnless(shutil.which('git'), 'needs git')
    def test_git_diff_shows_the_changed_doodad(self):
        repo = os.path.join(self.tmp, 'repo')
        os.makedirs(repo)
        path = os.path.join(repo, 'war3map.doo')
        shutil.copyfile(_helpers.sample_path('sample_doodads_v8_11.doo'), path)
        with open(os.path.join(repo, '.gitattributes'), 'w') as handle:
            handle.write('*.doo diff=wc3files\n')
        python = sys.executable.replace(os.sep, '/')

        def git(*args):
            env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'),
                       GIT_CONFIG_NOSYSTEM='1')
            return subprocess.run(
                ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                 '-c', 'core.autocrlf=false',
                 '-c', f'diff.wc3files.textconv="{python}" -m wc3files.cli textconv',
                 *args], cwd=repo, capture_output=True, check=True, env=env,
            ).stdout.decode('utf-8')

        git('init', '-q')
        git('add', '-A')
        git('commit', '-qm', 'map')
        doodads = MapDoodadsFile()
        doodads.read(path)
        data = doodads.get_data()
        data['doodads'][0]['life'] = 7
        doodads.import_data(data)
        doodads.write(path)
        diff = git('diff', '--no-color', 'war3map.doo')
        removed = [line for line in diff.splitlines()
                   if line.startswith('-') and not line.startswith('---')]
        added = [line for line in diff.splitlines()
                 if line.startswith('+') and not line.startswith('+++')]
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(added), 1)
        self.assertIn('"life": 7', added[0])


if __name__ == '__main__':
    unittest.main()
