"""wc3files.w3x.imports and `import-folder` (#215) on a small map built
here: a map info file, a script and one import already listed in
war3map.imp, and a folder tree to import into it."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers
from wc3files.imp.imp_file import ImpFile
from wc3files.w3x.imports import DEFAULT_FLAGS, import_folder, is_map_data
from wc3files.w3x.modes import _read_files, _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _imp(paths, flags=10):
    return ImpFile().import_data(
        {'version': 1, 'files': [{'flags': flags, 'path': p} for p in paths]}).to_bytes()


def _listed(files):
    imports = ImpFile()
    imports.read(files['war3map.imp'])
    return [(entry['flags'], entry['path']) for entry in imports.get_data()['files']]


class ImportFolderTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        with open(_helpers.sample_path('sample.mdx'), 'rb') as handle:
            self.model = handle.read()
        self.files = {
            'war3map.w3i': b'info',
            'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
            'war3map.imp': _imp(['Units\\Hero\\Hero.mdx']),
            'Units\\Hero\\Hero.mdx': b'old model',
        }
        self.folder = os.path.join(self.tmp, 'import')
        self.write('Units/Hero/Hero.mdx', self.model)
        self.write('Units/Hero/Textures/Skin.blp', b'skin')
        self.write('UI/Bar.fdf', b'Frame "SIMPLEFRAME" "Bar" {}\n')
        self.write('war3map.j', b'not the script')

    def write(self, relative, data):
        path = os.path.join(self.folder, *relative.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(data)

    def test_every_file_of_the_tree_is_added_and_listed(self):
        files, report = import_folder(self.files, self.folder)
        self.assertEqual(report['added'], ['UI\\Bar.fdf', 'Units\\Hero\\Textures\\Skin.blp'])
        self.assertEqual(report['replaced'], ['Units\\Hero\\Hero.mdx'])
        self.assertEqual(report['skipped'], ['war3map.j'])
        self.assertEqual(report['listed'], 2)
        self.assertEqual(files['Units\\Hero\\Hero.mdx'], self.model)
        self.assertEqual(files['Units\\Hero\\Textures\\Skin.blp'], b'skin')
        # The new entries copy the flags the map's own entry uses.
        self.assertEqual(_listed(files), [(10, 'Units\\Hero\\Hero.mdx'),
                                          (10, 'UI\\Bar.fdf'),
                                          (10, 'Units\\Hero\\Textures\\Skin.blp')])
        self.assertEqual(files['war3map.j'], self.files['war3map.j'])
        self.assertEqual(files['war3map.w3i'], b'info')

    def test_keep_existing_leaves_a_member_as_it_was(self):
        files, report = import_folder(self.files, self.folder, replace=False)
        self.assertEqual(report['kept'], ['Units\\Hero\\Hero.mdx'])
        self.assertEqual(files['Units\\Hero\\Hero.mdx'], b'old model')

    def test_a_member_is_matched_ignoring_case(self):
        self.files['units\\hero\\hero.mdx'] = self.files.pop('Units\\Hero\\Hero.mdx')
        files, report = import_folder(self.files, self.folder)
        self.assertEqual(report['replaced'], ['units\\hero\\hero.mdx'])
        self.assertNotIn('Units\\Hero\\Hero.mdx', files)

    def test_prefix_goes_before_every_name(self):
        files, report = import_folder(self.files, self.folder, prefix='war3mapImported/')
        self.assertIn('war3mapImported\\UI\\Bar.fdf', report['added'])
        # war3map.j under a prefix is an import like any other file.
        self.assertIn('war3mapImported\\war3map.j', report['added'])
        self.assertEqual(report['skipped'], [])

    def test_a_map_without_war3map_imp_gets_one(self):
        del self.files['war3map.imp']
        files, _ = import_folder(self.files, self.folder)
        self.assertEqual([flags for flags, _ in _listed(files)], [DEFAULT_FLAGS] * 3)

    def test_map_data_names(self):
        for name in ('war3map.w3i', 'War3map.j', 'war3mapUnits.doo', 'war3campaign.imp',
                     '(listfile)', 'scripts\\war3map.lua'):
            self.assertTrue(is_map_data(name), name)
        for name in ('war3mapImported\\a.blp', 'Units\\war3map.j', 'UI\\Bar.fdf'):
            self.assertFalse(is_map_data(name), name)

    def test_cli_writes_a_map_that_reads_back(self):
        source = os.path.join(self.tmp, 'map.w3x')
        output = os.path.join(self.tmp, 'out.w3x')
        _write_map(self.files, source)
        subprocess.run([sys.executable, '-m', 'wc3files.cli', 'import-folder',
                        source, self.folder, output], capture_output=True, check=True,
                       env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        files = _read_files(output)
        self.assertEqual(files['Units\\Hero\\Textures\\Skin.blp'], b'skin')
        self.assertEqual(files['Units\\Hero\\Hero.mdx'], self.model)
        self.assertEqual([path for _, path in _listed(files)],
                         ['Units\\Hero\\Hero.mdx', 'UI\\Bar.fdf',
                          'Units\\Hero\\Textures\\Skin.blp'])


if __name__ == '__main__':
    unittest.main()
