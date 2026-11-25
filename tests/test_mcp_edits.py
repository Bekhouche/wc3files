"""The MCP edit tools (#272) as plain functions: what they write, what they
leave alone, and what they refuse. No mcp package needed."""
import hashlib
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
from wc3files import ObjectFile
from wc3files.mcp import edits as E, tools as T
from wc3files.mpq.data import MpqProtectionLevel
from wc3files.w3x.modes import _read_files
from wc3files.w3x.w3x_file import ProtectedMapError, W3xFile


def _digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class EditTestCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        shutil.copy(sample_path('sample.w3x'), self.root)
        self.map = os.path.join(self.root, 'sample.w3x')
        self.roots = T.Roots([self.root])
        self.before = _digest(self.map)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestTargetPath(EditTestCase):

    def test_default_is_an_edited_copy(self):
        source, out = E.target_path(self.roots, 'sample.w3x')
        self.assertEqual(out, os.path.join(os.path.dirname(source), 'sample.edited.w3x'))

    def test_out_must_be_a_map_under_the_roots(self):
        with self.assertRaises(PermissionError):
            E.target_path(self.roots, 'sample.w3x', os.path.join(os.path.dirname(self.root), 'x.w3x'))
        with self.assertRaises(ValueError):
            E.target_path(self.roots, 'sample.w3x', 'out.txt')
        with self.assertRaises(ValueError):
            E.target_path(self.roots, 'missing.w3x')


class TestEditObjects(EditTestCase):

    def test_create_and_change_units(self):
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3u', [
            {'id': 'h001', 'base': 'hfoo', 'set': {'Name': 'Knight of Tests', 'goldcost': 175}},
            {'id': 'hfoo', 'set': {'ugol': 140}}])
        self.assertEqual((result['created'], result['changed']), (['h001'], ['hfoo']))
        self.assertFalse(result['overwrote_source'])
        self.assertEqual(_digest(self.map), self.before)
        objects = T.object_data(self.roots, result['path'], 'w3u')['objects']['w3u']
        self.assertEqual(objects['custom']['h001']['template_id'], 'hfoo')
        self.assertEqual(objects['custom']['h001']['Name'], 'Knight of Tests')
        self.assertEqual(objects['custom']['h001']['goldcost'], 175)
        self.assertEqual(objects['original']['hfoo']['goldcost'], 140)

    def test_other_members_are_unchanged(self):
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3u',
                                [{'id': 'h001', 'base': 'hfoo', 'set': {'goldcost': 1}}])
        before, after = _read_files(self.map), _read_files(result['path'])
        changed = {name.lower() for name in result['changed_members']}
        self.assertEqual({n.lower() for n in before} | changed, {n.lower() for n in after})
        for name, data in before.items():
            if name.lower() not in changed:
                self.assertEqual(after[name], data, name)

    def test_ability_levels(self):
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3a', [
            {'id': 'A00Z', 'base': 'AHbz', 'set': {'Name': 'Big Blizzard', 'levels': 2,
                                                   'DataA': {'1': 7, '2': 9}}}], out='abilities.w3x')
        files = _read_files(result['path'])
        handler = ObjectFile()
        handler.read(files['war3map.w3a'], object_type='w3a')
        entry = handler.export_data()['w3a']['custom']['A00Z']
        self.assertEqual(entry['ID'], 'AHbz')
        self.assertEqual(entry['Hbz1']['value'], {1: 7, 2: 9})
        self.assertEqual(entry['Hbz1']['ptr'], {1: 1, 2: 1})
        self.assertEqual(entry['anam']['value'], {0: 'Big Blizzard'})
        with self.assertRaises(ValueError):
            E.edit_objects(self.roots, 'sample.w3x', 'w3a',
                           [{'id': 'A00Y', 'base': 'AHbz', 'set': {'DataA': 7}}])

    def test_reset_and_delete(self):
        first = E.edit_objects(self.roots, 'sample.w3x', 'w3u', [
            {'id': 'h001', 'base': 'hfoo', 'set': {'goldcost': 1, 'HP': 5}},
            {'id': 'h002', 'base': 'hkni'}], out='a.w3x')
        second = E.edit_objects(self.roots, first['path'], 'w3u', [
            {'id': 'h001', 'reset': ['HP']}, {'id': 'h002', 'delete': True}], out='b.w3x')
        self.assertEqual(second['deleted'], ['h002'])
        custom = T.object_data(self.roots, second['path'], 'w3u')['objects']['w3u']['custom']
        self.assertNotIn('h002', custom)
        self.assertNotIn('HP', custom['h001'])
        self.assertEqual(custom['h001']['goldcost'], 1)

    def test_overwrite_only_when_out_is_the_source(self):
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3u',
                                [{'id': 'h001', 'base': 'hfoo'}], out='sample.w3x')
        self.assertTrue(result['overwrote_source'])
        self.assertNotEqual(_digest(self.map), self.before)

    def test_refusals_write_nothing(self):
        for changes in ([{'id': 'hfoo', 'base': 'hfoo'}],            # standard id as new
                        [{'id': 'h001', 'base': 'zzzz'}],            # no such base
                        [{'id': 'h001'}],                            # neither there nor new
                        [{'id': 'h001', 'base': 'hfoo', 'set': {'nope': 1}}],
                        [{'id': 'h001', 'base': 'hfoo', 'set': {'goldcost': 'cheap'}}],
                        [{'id': 'h001', 'base': 'hfoo', 'set': {'goldcost': 1.5}}],
                        [{'id': 'toolong', 'base': 'hfoo'}],
                        [{'id': 'h009', 'delete': True}],
                        []):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    E.edit_objects(self.roots, 'sample.w3x', 'w3u', changes)
        self.assertEqual(sorted(os.listdir(self.root)), ['sample.w3x'])
        with self.assertRaises(ValueError):
            E.edit_objects(self.roots, 'sample.w3x', 'w3x', [{'id': 'h001', 'base': 'hfoo'}])

    def test_editor_limits_warn_but_write(self):
        # Real maps go past the editor's limits on purpose (sight 2500 against
        # a maximum of 1800); the game takes it, so it is a warning.
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3u',
                                [{'id': 'h001', 'base': 'hfoo', 'set': {'sight': 5000}}])
        self.assertTrue(result['warnings'])
        self.assertIn('h001', result['warnings'][0])
        self.assertTrue(os.path.exists(result['path']))

    def test_item_names_come_from_the_unit_fields(self):
        result = E.edit_objects(self.roots, 'sample.w3x', 'w3t',
                                [{'id': 'I0ZZ', 'base': 'ratc', 'set': {'Name': 'Claws of Tests'}}])
        handler = ObjectFile()
        handler.read(_read_files(result['path'])['war3map.w3t'], object_type='w3t')
        self.assertEqual(handler.export_data()['w3t']['custom']['I0ZZ']['unam']['value'],
                         'Claws of Tests')

    def test_a_protected_map_is_refused(self):
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        protected = os.path.join(self.root, 'protected.w3x')
        archive.build_from_files(archive.files, protected, protection=MpqProtectionLevel.SAFE)
        with self.assertRaises(ProtectedMapError):
            E.edit_objects(self.roots, protected, 'w3u', [{'id': 'h001', 'base': 'hfoo'}])


if __name__ == '__main__':
    unittest.main()
