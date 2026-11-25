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


class TestEditPlaced(EditTestCase):

    def setUp(self):
        super().setUp()
        # The sample map's war3map.doo holds unit records; give it real
        # placed units and doodads from the format samples.
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        files = dict(archive.files)
        for member, sample in (('war3mapUnits.doo', 'sample.doo'),
                               ('war3map.doo', 'sample_doodads_v8_11.doo')):
            with open(sample_path(sample), 'rb') as handle:
                files[member] = handle.read()
        files['(listfile)'] = '\r\n'.join(n for n in files if n != '(listfile)').encode()
        self.placed = os.path.join(self.root, 'placed.w3x')
        archive.build_from_files(files, self.placed)

    def test_add_change_delete_units(self):
        before = T.placed_objects(self.roots, 'placed.w3x', 'units')['objects']
        result = E.edit_placed(self.roots, 'placed.w3x', 'units',
                               add=[{'id': 'hkni', 'x': 64, 'y': 64, 'owner': 1, 'facing': 90}],
                               change=[{'select': {'index': 0}, 'set': {'x': 32.0, 'owner': 3}}],
                               delete=[{'index': 1}])
        self.assertEqual((result['changed'], result['deleted'], result['total']), ([0], [1], 2))
        after = T.placed_objects(self.roots, result['path'], 'units')['objects']
        self.assertEqual((after[0]['x'], after[0]['owner'], after[0]['y']),
                         (32.0, 3, before[0]['y']))
        new = after[1]
        self.assertEqual((new['id'], new['owner'], new['facing'], new['x']), ('hkni', 1, 90.0, 64.0))
        self.assertEqual(new['creation_number'],
                         max(u['creation_number'] for u in before) + 1)

    def test_untouched_records_and_members_are_unchanged(self):
        from wc3files.doo.map_doodads_file import MapDoodadsFile
        result = E.edit_placed(self.roots, 'placed.w3x', 'doodads',
                               change=[{'select': {'index': 3}, 'set': {'life': 50}}])
        before, after = _read_files(self.placed), _read_files(result['path'])
        for name, data in before.items():
            if name != 'war3map.doo':
                self.assertEqual(after[name], data, name)
        old, new = MapDoodadsFile(), MapDoodadsFile()
        old.read(before['war3map.doo'])
        new.read(after['war3map.doo'])
        old_records, new_records = old.get_data()['doodads'], new.get_data()['doodads']
        self.assertEqual(len(old_records), len(new_records))
        for index, (a, b) in enumerate(zip(old_records, new_records)):
            if index == 3:
                self.assertEqual(b['life'], 50)
                self.assertEqual({k: v for k, v in a.items() if k != 'life'},
                                 {k: v for k, v in b.items() if k != 'life'})
            else:
                self.assertEqual(a, b)

    def test_select_by_id_changes_every_match(self):
        first = T.placed_objects(self.roots, 'placed.w3x', 'doodads', limit=1)['objects'][0]['id']
        total = T.placed_objects(self.roots, 'placed.w3x', 'doodads', id=first)['total']
        result = E.edit_placed(self.roots, 'placed.w3x', 'doodads',
                               change=[{'select': {'id': first}, 'set': {'scale': 2}}])
        self.assertEqual(len(result['changed']), total)
        scales = {tuple(o['scale']) for o in
                  T.placed_objects(self.roots, result['path'], 'doodads', id=first)['objects']}
        self.assertEqual(scales, {(2.0, 2.0, 2.0)})

    def test_refusals_write_nothing(self):
        for kind, call in (
                ('units', dict(add=[{'id': 'zzzz', 'x': 0, 'y': 0}])),      # unknown type
                ('units', dict(add=[{'id': 'hfoo', 'x': 0}])),             # no y
                ('units', dict(change=[{'select': {'index': 99}, 'set': {'x': 0}}])),
                ('units', dict(change=[{'select': {'index': 0}, 'set': {'life': 5}}])),
                ('units', dict(change=[{'select': {'index': 0, 'id': 'hfoo'}, 'set': {'x': 0}}])),
                ('units', dict(delete=[{'creation_number': 999}])),
                ('units', dict(change=[{'select': {'index': 0}, 'set': {'owner': 99}}])),
                ('doodads', dict(change=[{'select': {'index': 0}, 'set': {'owner': 1}}])),
                ('units', {}),
                ('trees', dict(delete=[{'index': 0}]))):
            with self.subTest(kind=kind, call=call):
                with self.assertRaises(ValueError):
                    E.edit_placed(self.roots, 'placed.w3x', kind, **call)
        self.assertEqual(sorted(os.listdir(self.root)), ['placed.w3x', 'sample.w3x'])


class TestEditRecords(EditTestCase):

    def setUp(self):
        super().setUp()
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        files = dict(archive.files)
        for member, sample in (('war3map.w3r', 'sample.w3r'), ('war3map.w3c', 'sample.w3c')):
            with open(sample_path(sample), 'rb') as handle:
                files[member] = handle.read()
        regions = T.map_records
        files['(listfile)'] = '\r\n'.join(n for n in files if n != '(listfile)').encode()
        self.records = os.path.join(self.root, 'records.w3x')
        archive.build_from_files(files, self.records)
        self.region = regions(self.roots, 'records.w3x', 'regions')['records'][0]['name']
        # The same map with a script that uses that region.
        key = next(k for k in files if k.lower() == 'war3map.j')
        files[key] = files[key] + ('\r\nglobals\r\n    rect gg_rct_%s = null\r\nendglobals\r\n'
                                   % self.region.replace(' ', '_')).encode()
        self.used = os.path.join(self.root, 'used.w3x')
        archive.build_from_files(files, self.used)

    def test_regions(self):
        before = T.map_records(self.roots, 'records.w3x', 'regions')['records']
        result = E.edit_records(self.roots, 'records.w3x', 'regions', add=[
            {'name': 'Test Zone', 'left': -64, 'right': 64, 'bottom': -64, 'top': 64,
             'color': [0, 128, 255], 'weather': 'RAhr'}])
        after = T.map_records(self.roots, result['path'], 'regions')['records']
        self.assertEqual(after[:len(before)], before)
        new = after[-1]
        self.assertEqual((new['name'], new['left'], new['top'], new['color'], new['weather_effect']),
                         ('Test Zone', -64.0, 64.0, [0, 128, 255], 'RAhr'))
        self.assertEqual(new['index'], max(r['index'] for r in before) + 1)
        moved = E.edit_records(self.roots, 'records.w3x', 'regions', out='moved.w3x', change=[
            {'select': {'index': 0}, 'set': {'right': before[0]['right'] + 32}}])
        self.assertEqual(T.map_records(self.roots, moved['path'], 'regions')['records'][0]['right'],
                         before[0]['right'] + 32)

    def test_a_region_the_script_uses_is_kept(self):
        for call in (dict(delete=[{'name': self.region}]),
                     dict(change=[{'select': {'name': self.region}, 'set': {'name': 'Other'}}])):
            with self.subTest(call=call):
                with self.assertRaises(ValueError) as caught:
                    E.edit_records(self.roots, 'used.w3x', 'regions', **call)
                self.assertIn('gg_rct_', str(caught.exception))
        result = E.edit_records(self.roots, 'records.w3x', 'regions',
                                delete=[{'name': self.region}])
        self.assertEqual(result['deleted'], [0])

    def test_cameras_start_as_the_game_default(self):
        result = E.edit_records(self.roots, 'records.w3x', 'cameras',
                                add=[{'name': 'Intro', 'target_x': 100, 'target_y': 200}])
        camera = T.map_records(self.roots, result['path'], 'cameras')['records'][-1]
        self.assertEqual((camera['cinematic_name'], camera['target_x'], camera['distance'],
                          camera['angle_of_attack'], camera['field_of_view'],
                          camera['far_clipping_plane'], camera['rotation']),
                         ('Intro', 100.0, 1650.0, 304.0, 70.0, 5000.0, 90.0))

    def test_strings(self):
        first = E.edit_records(self.roots, 'sample.w3x', 'strings', out='s1.w3x',
                               add=[{'text': 'Hello'}, {'text': 'Two\r\nlines'}])
        ids = [a['id'] for a in first['added']]
        self.assertEqual(first['added'][0]['reference'], f'TRIGSTR_{ids[0]:03d}')
        second = E.edit_records(self.roots, first['path'], 'strings', out='s2.w3x',
                                change=[{'id': ids[0], 'text': 'Hello again'}])
        texts = {r['id']: r['text'] for r in
                 T.map_records(self.roots, second['path'], 'strings')['records']}
        self.assertEqual((texts[ids[0]], texts[ids[1]]), ('Hello again', 'Two\r\nlines'))
        for call in (dict(delete=[{'index': 0}]),
                     dict(change=[{'id': 99999, 'text': 'x'}]),
                     dict(add=[{'text': 'bad\r\n}\r\nend'}])):
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    E.edit_records(self.roots, first['path'], 'strings', **call)

    def test_refusals_write_nothing(self):
        for kind, call in (
                ('regions', dict(add=[{'name': 'Far', 'left': 0, 'right': 1, 'bottom': 1, 'top': 0}])),
                ('regions', dict(add=[{'left': 0, 'right': 1, 'bottom': 0, 'top': 1}])),
                ('regions', dict(add=[{'name': 'C', 'left': 0, 'right': 1, 'bottom': 0, 'top': 1,
                                       'color': [300, 0, 0]}])),
                ('regions', dict(change=[{'select': {'index': 0}, 'set': {'target_x': 1}}])),
                ('regions', dict(change=[{'select': {'name': 'nothing'}, 'set': {'left': 1}}])),
                ('cameras', dict(add=[{'name': 'N', 'near_clipping_plane': 9000}])),
                ('cameras', dict(add=[{'name': 'N', 'left': 1}])),
                ('units', dict(delete=[{'index': 0}])),
                ('regions', {})):
            with self.subTest(kind=kind, call=call):
                with self.assertRaises(ValueError):
                    E.edit_records(self.roots, 'records.w3x', kind, **call)
        self.assertEqual(sorted(os.listdir(self.root)), ['records.w3x', 'sample.w3x', 'used.w3x'])


if __name__ == '__main__':
    unittest.main()
