"""File names with more than one dot (#213): wc3files.w3x.imports
multi_dot_members, multi_dot_moves and `import-move --fix-dots`, on a small
map built here. A town hall model named `TownHallElfV1.01.mdx`, which patch
3.0 does not load, is referenced by the unit data, the script and
war3map.imp."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.imp.imp_file import ImpFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.imports import (move_imports, multi_dot_members, multi_dot_moves,
                                  undot_moves, undotted)
from wc3files.w3x.modes import _read_files, _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HALL = 'war3mapImported\\TownHallElfV1.01.mdx'
FIXED = 'war3mapImported\\TownHallElfV1_01.mdx'


def _units(model):
    handler = ObjectFile()
    handler.import_data({'w3u': {'original': {}, 'custom': {
        'h000': {'ID': 'htow', 'umdl': {'value': model, 'type': 3}}}}})
    return handler.to_bytes('w3u')


def _model_of(data):
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, 'war3map.w3u')
        with open(path, 'wb') as handle:
            handle.write(data)
        handler = ObjectFile()
        handler.read(path)
    finally:
        shutil.rmtree(folder)
    return handler.export_data()['w3u']['custom']['h000']['umdl']['value']


def _listed(files):
    imports = ImpFile()
    imports.read(files['war3map.imp'])
    return [entry['path'] for entry in imports.get_data()['files']]


class MultiDotTest(unittest.TestCase):

    def setUp(self):
        self.files = {
            'war3map.w3i': b'info',
            'war3map.w3u': _units('war3mapImported\\TownHallElfV1.01.mdl'),
            'war3map.j': (b'function main takes nothing returns nothing\n'
                          b'    call AddSpecialEffect("war3mapImported\\\\TownHallElfV1.01.mdl"'
                          b', 0, 0)\nendfunction\n'),
            HALL: b'model',
            'war3mapImported\\Banner.blp': b'banner',
            'war3map.imp': ImpFile().import_data({'version': 1, 'files': [
                {'flags': 13, 'path': HALL},
                {'flags': 13, 'path': 'war3mapImported\\Banner.blp'}]}).to_bytes(),
        }

    def test_members_with_more_than_one_dot_are_listed(self):
        self.files['Units\\v1.0\\Grunt.mdx'] = b''
        self.assertEqual(multi_dot_members(self.files), [HALL])

    def test_undotted_keeps_the_folder_and_the_last_dot(self):
        self.assertEqual(undotted(HALL), FIXED)
        self.assertEqual(undotted('a.b.c.blp'), 'a_b_c.blp')
        self.assertEqual(undotted('Units\\v1.0\\x.y.mdx'), 'Units\\v1.0\\x_y.mdx')

    def test_the_fix_renames_the_file_and_rewrites_its_references(self):
        moves, skipped = multi_dot_moves(self.files)
        self.assertEqual((moves, skipped), ({HALL: FIXED}, []))
        files, report = move_imports(self.files, moves)
        self.assertEqual(files[FIXED], b'model')
        self.assertNotIn(HALL, files)
        self.assertEqual(_model_of(files['war3map.w3u']),
                         'war3mapImported\\TownHallElfV1_01.mdl')
        self.assertIn(b'"war3mapImported\\\\TownHallElfV1_01.mdl"', files['war3map.j'])
        self.assertEqual(_listed(files), [FIXED, 'war3mapImported\\Banner.blp'])
        self.assertEqual(report['unresolved'], [])

    def test_a_name_the_map_already_has_is_skipped(self):
        self.files[FIXED] = b'other'
        moves, skipped = multi_dot_moves(self.files)
        self.assertEqual((moves, skipped), ({}, [(HALL, FIXED)]))

    def test_a_move_to_a_dotted_name_is_undotted(self):
        moves, skipped = undot_moves(self.files, {HALL: 'Buildings\\Elf.Hall.mdx',
                                                  'war3mapImported\\Banner.blp': 'Banner.blp'})
        self.assertEqual(moves, {HALL: 'Buildings\\Elf_Hall.mdx',
                                 'war3mapImported\\Banner.blp': 'Banner.blp'})

    def test_cli_lists_with_report_only_and_fixes_the_map(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source = os.path.join(tmp, 'map.w3x')
        output = os.path.join(tmp, 'out.w3x')
        _write_map(self.files, source)
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))

        def run(*args):
            return subprocess.run([sys.executable, '-m', 'wc3files.cli', 'import-move',
                                   *args], capture_output=True, check=True, env=env)

        listing = run(source, '--fix-dots', '--report-only').stdout.decode()
        self.assertIn('1 file names with more than one dot', listing)
        self.assertIn(f'moved: {HALL} -> {FIXED}', listing)
        self.assertFalse(os.path.exists(output))
        run(source, output, '--fix-dots', '--strip-prefix')
        files = _read_files(output)
        self.assertEqual(files['TownHallElfV1_01.mdx'], b'model')
        self.assertEqual(_model_of(files['war3map.w3u']), 'TownHallElfV1_01.mdl')


if __name__ == '__main__':
    unittest.main()
