import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.table.wts_file import WtsFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.translation import (apply_translation, diff_strings, string_keys,
                                      translation)
from wc3files.w3x.w3x_file import read_map

SAMPLE = os.path.join(os.path.dirname(__file__), 'samples', 'w3x', 'sample.w3x')


def _units(objects):
    table = ObjectFile()
    table.import_data({'w3u': {'original': {}, 'custom': {
        object_id: {'ID': base, 'unam': {'value': name, 'type': 3, 'end': object_id}}
        for object_id, (base, name) in objects.items()}}})
    return table.to_bytes('w3u')


def _script(strings):
    body = ''.join(f'    call DisplayTextToPlayer(Player(0), 0, 0, "{s}")\n' for s in strings)
    return ('function Trig_Intro_Actions takes nothing returns nothing\n' + body
            + 'endfunction\n').encode('utf-8')


def version(strings, units, script_ids, extra=None):
    """A map: the sample with a string table, units and a script."""
    files = dict(read_map(SAMPLE).files)
    files['war3map.wts'] = WtsFile().import_data(strings).to_bytes()
    files['war3map.w3u'] = _units(units)
    files['war3map.j'] = _script([f'TRIGSTR_{i:03d}' for i in script_ids])
    files.update(extra or {})
    return files


# Version 2 numbers the strings again, changes one, adds a unit and a line.
OLD = version({1: 'Footman', 2: 'Welcome!', 3: 'Defend the gate.'},
              {'h000': ('hfoo', 'TRIGSTR_001')}, [2, 3])
NEW = version({7: 'Footman', 8: 'Welcome, hero!', 9: 'Defend the gate.', 10: 'Knight',
               11: 'Good luck.'},
              {'h000': ('hfoo', 'TRIGSTR_007'), 'h001': ('hkni', 'TRIGSTR_010')}, [8, 9, 11])


class TestTranslation(unittest.TestCase):
    def test_keys(self):
        keys = string_keys(OLD)
        self.assertEqual({k: (e['string_id'], e['text']) for k, e in keys.items()
                          if not k.startswith('w3i/')},
                         {'w3u/h000/Name': (1, 'Footman'),
                          'script/Trig_Intro_Actions/1': (2, 'Welcome!'),
                          'script/Trig_Intro_Actions/2': (3, 'Defend the gate.')})
        self.assertEqual(keys['w3i/players/1/name']['text'], 'Player 2')

    def test_diff(self):
        found = diff_strings(OLD, NEW)
        self.assertEqual(found['added'], {'w3u/h001/Name': 'Knight',
                                          'script/Trig_Intro_Actions/3': 'Good luck.'})
        self.assertEqual(found['changed'], {'script/Trig_Intro_Actions/1': {
            'old': 'Welcome!', 'new': 'Welcome, hero!'}})
        self.assertEqual(found['removed'], {})
        self.assertEqual(found['unchanged'], 2 + 9)       # two strings and the map info

    def test_carry_over_and_apply(self):
        old_table, _ = translation(OLD)
        french = {'w3u/h000/Name': 'Fantassin', 'script/Trig_Intro_Actions/1': 'Bienvenue !',
                  'script/Trig_Intro_Actions/2': 'Défendez la porte.',
                  'w3i/map_name': 'Carte exemple'}
        for key, text in french.items():
            old_table['strings'][key]['translation'] = text
        table, counts = translation(NEW, old_table)
        self.assertEqual(counts, {'kept': 11, 'changed': 1, 'new': 2, 'dropped': 0})
        changed = table['strings']['script/Trig_Intro_Actions/1']
        self.assertEqual((changed['status'], changed['old_source'], changed['translation']),
                         ('changed', 'Welcome!', 'Bienvenue !'))
        out, report = apply_translation(NEW, table)
        self.assertEqual((report['applied'], report['stale']),
                         (3, ['script/Trig_Intro_Actions/1']))
        keys = string_keys(out)
        self.assertEqual(keys['w3u/h000/Name']['text'], 'Fantassin')
        self.assertEqual(keys['script/Trig_Intro_Actions/2']['text'], 'Défendez la porte.')
        self.assertEqual(keys['w3i/map_name']['text'], 'Carte exemple')   # inline, in the w3i
        self.assertEqual(keys['script/Trig_Intro_Actions/1']['text'], 'Welcome, hero!')
        forced, _ = apply_translation(NEW, table, force=True)
        self.assertEqual(string_keys(forced)['script/Trig_Intro_Actions/1']['text'],
                         'Bienvenue !')

    def test_inline_object_text(self):
        files = version({}, {'h000': ('hfoo', 'Footman')}, [])
        table, _ = translation(files)
        table['strings']['w3u/h000/Name']['translation'] = 'Fantassin'
        out, report = apply_translation(files, table)
        self.assertEqual(report['applied'], 1)
        self.assertEqual(string_keys(out)['w3u/h000/Name'],
                         {'text': 'Fantassin', 'string_id': None,
                          'source': ('object', 'war3map.w3u', 'w3u', 'h000', 'unam', None)})

    def test_cli(self):
        from wc3files.w3x.modes import _write_map
        with tempfile.TemporaryDirectory() as tmp:
            old, new = os.path.join(tmp, 'v1.w3x'), os.path.join(tmp, 'v2.w3x')
            _write_map(OLD, old)
            _write_map(NEW, new)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['map-translate', 'diff', old, new]), 0)
            self.assertIn("~ script/Trig_Intro_Actions/1\t'Welcome!' -> 'Welcome, hero!'",
                          out.getvalue())
            first, second = os.path.join(tmp, 'v1.json'), os.path.join(tmp, 'v2.json')
            with redirect_stderr(io.StringIO()):
                main(['map-translate', 'export', old, '-o', first])
            with open(first, encoding='utf-8') as handle:
                table = json.load(handle)
            table['strings']['w3u/h000/Name']['translation'] = 'Fantassin'
            with open(first, 'w', encoding='utf-8') as handle:
                json.dump(table, handle)
            err = io.StringIO()
            with redirect_stderr(err):
                main(['map-translate', 'export', new, '--previous', first, '-o', second])
                main(['map-translate', 'apply', new, second, '-o',
                      os.path.join(tmp, 'fr.w3x')])
            self.assertIn('11 translation(s) kept, 1 source(s) changed, 2 new', err.getvalue())
            self.assertIn('1 applied', err.getvalue())
            translated = read_map(os.path.join(tmp, 'fr.w3x')).files
            self.assertEqual(string_keys(translated)['w3u/h000/Name']['text'], 'Fantassin')


if __name__ == '__main__':
    unittest.main()
