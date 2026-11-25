"""wc3files.w3x.strings and `map-strings` (#252) on a small map built here:
a units file and an abilities file whose text is partly in war3map.wts and
partly in the object data, a map info file that uses one string, and one
string nothing uses."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.table.wts_file import WtsFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.strings import check_strings, map_strings
from wc3files.w3x.w3x_file import W3xFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _objects(ext, custom, original=None):
    handler = ObjectFile()
    handler.import_data({ext: {'original': original or {}, 'custom': custom}})
    return handler.to_bytes(ext)


def _files():
    units = _objects('w3u', {
        # h000 was copied from h001: its short tooltip still trains a Knight.
        'h000': {'ID': 'hfoo', 'unam': {'value': 'TRIGSTR_001', 'type': 3},
                 'utip': {'value': 'TRIGSTR_002', 'type': 3}},
        'h001': {'ID': 'hkni', 'unam': {'value': 'Knight', 'type': 3},
                 'utip': {'value': 'Train |cffffcc00K|rnight', 'type': 3},
                 'utub': {'value': 'TRIGSTR_009', 'type': 3}},
    })
    abilities = _objects('w3a', {
        'A000': {'ID': 'AHtb', 'anam': {'value': {0: 'Hammer'}, 'type': 3, 'ptr': {0: 0}},
                 'atp1': {'value': {1: 'Hammer - Level 1'}, 'type': 3, 'ptr': {1: 0}},
                 'aub1': {'value': {1: 'Deals <A000,DataA1> damage, <A0ZZ,DataA1> more '
                                       'and <A000,Nothing1> else.'},
                          'type': 3, 'ptr': {1: 0}}},
        # Copied from A000 with two more levels, its tips not changed.
        'A001': {'ID': 'AHtb', 'anam': {'value': {0: 'Thunder Maul'}, 'type': 3, 'ptr': {0: 0}},
                 'alev': {'value': {0: 6}, 'type': 0, 'ptr': {0: 0}},
                 'atp1': {'value': {1: 'Hammer - Level 1', 2: 'Hammer - Level 2'},
                          'type': 3, 'ptr': {1: 0, 2: 0}},
                 'aub1': {'value': {5: 'Deals <A001,DataA5> damage.'},
                          'type': 3, 'ptr': {5: 0}}},
    })
    strings = WtsFile().import_data({1: 'Swordsman', 2: 'Train Knight', 3: 'Map name',
                                     5: 'Nobody uses this'})
    return {'war3map.w3u': units, 'war3map.w3a': abilities,
            'war3map.wts': strings.to_bytes(),
            'war3map.w3i': b'\x00\x00TRIGSTR_003\x00'}


class TestMapStrings(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.files = _files()
        cls.listing = map_strings(cls.files)

    def _entry(self, object_id, field, level=None):
        found = [e for e in self.listing['objects']
                 if (e['id'], e['field'], e['level']) == (object_id, field, level)]
        self.assertEqual(len(found), 1, (object_id, field, level))
        return found[0]

    def test_text_from_the_strings_file_and_from_the_object_data(self):
        name = self._entry('h000', 'Name')
        self.assertEqual((name['string_id'], name['text'], name['base'], name['custom']),
                         (1, 'Swordsman', 'hfoo', True))
        inline = self._entry('h001', 'Tip')
        self.assertEqual((inline['string_id'], inline['text']),
                         (None, 'Train |cffffcc00K|rnight'))
        # Abilities: Name has no levels, Tip and Ubertip have.
        self.assertEqual(self._entry('A000', 'Name')['text'], 'Hammer')
        self.assertEqual(self._entry('A000', 'Tip', 1)['text'], 'Hammer - Level 1')

    def test_other_files_and_unused_strings(self):
        self.assertEqual(self.listing['strings'], 4)
        self.assertEqual(self.listing['references'],
                         [{'file': 'war3map.w3i', 'string_ids': [3]}])
        self.assertEqual(self.listing['unused'], [{'string_id': 5, 'text': 'Nobody uses this'}])

    def test_the_check(self):
        found = sorted((f['severity'], f['kind'], f['id'], f['field'], f['message'])
                       for f in check_strings(self.files))
        self.assertEqual(found, [
            ('issue', 'missing string', 'h001', 'Ubertip', 'TRIGSTR_009 is not in war3map.wts'),
            ('issue', 'unresolved reference', 'A000', 'Ubertip',
             '<A000,Nothing1>: no field Nothing1 on A000'),
            ('issue', 'unresolved reference', 'A000', 'Ubertip', '<A0ZZ,DataA1>: no object A0ZZ'),
            ('note', "other object's name", 'A001', 'Tip',
             "names 'Hammer', not the object's own name 'Thunder Maul' (levels 1 to 2)"),
            ('note', "other object's name", 'h000', 'Tip',
             "names 'Knight', not the object's own name 'Swordsman'"),
        ])

    def test_a_level_the_map_added_takes_the_game_s_last_level(self):
        # A001 has six levels; the game's table for AHtb has columns up to
        # level 4. As w3x2lni fills them, levels 5 and 6 take the last one.
        from wc3files.w3o.data import ObjectFileType
        from wc3files.w3o.object_base_data import BaseObjectData
        from wc3files.w3x.optimize import reference_resolver
        last = BaseObjectData.default().get(ObjectFileType.Abilities, 'AHtb')['DataA4']
        self.assertEqual(reference_resolver(self.files)('A001,DataA5'), str(int(last)))
        # DataA5 of the table is '-', so level 6 goes past it to level 4 too.
        self.assertEqual(reference_resolver(self.files)('A001,DataA6'), str(int(last)))

    def test_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'strings.w3x')
            files = dict(self.files)
            files['(listfile)'] = '\r\n'.join(files).encode()
            W3xFile().build_from_files(files, path)
            env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'),
                       PYTHONIOENCODING='utf-8')
            run = lambda *a: subprocess.run([sys.executable, '-m', 'wc3files', 'map-strings',
                                             path, *a], capture_output=True, text=True,
                                            encoding='utf-8', env=env)
            listed = run()
            self.assertEqual(listed.returncode, 0, listed.stderr)
            for expected in ('war3map.w3u h000 (custom, from hfoo)', 'TRIGSTR_001',
                             'Swordsman', 'inline', '1 not used by any file'):
                self.assertIn(expected, listed.stdout)
            checked = run('--check')
            self.assertEqual(checked.returncode, 1)
            self.assertIn('h001 Ubertip: issue: missing string', checked.stdout)
            self.assertIn('3 issue(s), 2 note(s)', checked.stderr)
            parsed = json.loads(run('--check', '--json').stdout)
            self.assertEqual(len(parsed), 5)


if __name__ == '__main__':
    unittest.main()
