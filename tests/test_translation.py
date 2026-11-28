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
from wc3files.w3x.imports import read_imports
from wc3files.w3x.translation import (apply_translation, diff_strings, externalize_strings,
                                      locale_tables, locale_translations, string_keys,
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

    def test_externalize(self):
        files = version({5: 'Welcome!'}, {'h000': ('hfoo', 'Footman'), 'h001': ('hkni', '')},
                        [5])
        before = string_keys(files)
        inline = [k for k, e in before.items() if e['string_id'] is None and e['text']]
        self.assertIn('w3u/h000/Name', inline)
        self.assertIn('w3i/map_name', inline)
        out, count = externalize_strings(files)
        self.assertEqual(count, len(inline))
        after = string_keys(out)
        self.assertEqual({k: e['text'] for k, e in after.items()},
                         {k: e['text'] for k, e in before.items()})
        moved = {after[k]['string_id'] for k in inline}
        self.assertNotIn(None, moved)
        self.assertEqual(min(moved), 6)            # after the highest number in use
        self.assertEqual(after['script/Trig_Intro_Actions/1']['string_id'], 5)
        # an empty text stays inline, and a second pass has nothing left to move
        self.assertEqual(after['w3u/h001/Name']['string_id'], None)
        self.assertEqual(externalize_strings(out)[1], 0)
        # a translation then reaches the text through war3map.wts
        table, _ = translation(out)
        table['strings']['w3u/h000/Name']['translation'] = 'Fantassin'
        translated, _ = apply_translation(out, table)
        self.assertEqual(string_keys(translated)['w3u/h000/Name']['text'], 'Fantassin')
        self.assertEqual(translated['war3map.w3u'], out['war3map.w3u'])

    def test_locale_tables(self):
        files = version({1: 'Welcome!', 2: 'Defend the gate.'}, {'h000': ('hfoo', 'Footman')},
                        [1, 2])
        table, _ = translation(files)
        table['strings']['w3u/h000/Name']['translation'] = 'Fantassin'
        table['strings']['script/Trig_Intro_Actions/1']['translation'] = 'Bienvenue !'
        german, _ = translation(files)
        german['strings']['script/Trig_Intro_Actions/2']['translation'] = 'Verteidigt das Tor.'
        german['strings']['script/Trig_Intro_Actions/2']['source'] = 'Hold the gate.'
        out, reports = locale_tables(files, {'frFR': table, 'deDE': german})
        self.assertEqual(reports['frFR']['applied'], 2)
        self.assertEqual(reports['deDE']['stale'], ['script/Trig_Intro_Actions/2'])
        self.assertGreater(reports['frFR']['externalized'], 0)   # the unit name was inline
        keys = string_keys(out)
        base = WtsFile()
        base.read(out['war3map.wts'])
        self.assertEqual(keys['w3u/h000/Name']['text'], 'Footman')     # the base keeps it
        french = WtsFile()
        french.read(out['_Locales\\frFR.w3mod\\war3map.wts'])
        french = dict(french.export_data())
        self.assertEqual(set(french), set(dict(base.export_data())))   # every string
        self.assertEqual(french[keys['w3u/h000/Name']['string_id']], 'Fantassin')
        self.assertEqual(french[1], 'Bienvenue !')
        self.assertEqual(french[2], 'Defend the gate.')             # untranslated: the base
        listed = read_imports(out)[1]['files']
        self.assertEqual(sorted(e['path'] for e in listed if e['path'].startswith('_Locales')),
                         ['_Locales\\deDE.w3mod\\war3map.wts',
                          '_Locales\\frFR.w3mod\\war3map.wts'])
        # writing again replaces the tables and lists them once
        again, _ = locale_tables(out, {'frFR': table})
        self.assertEqual(len(read_imports(again)[1]['files']), len(listed))
        with self.assertRaises(ValueError):
            locale_tables(files, {'fr': table})

    def test_locale_translations(self):
        german = translation(OLD)[0]
        for key, text in {'w3u/h000/Name': 'Fußsoldat',
                          'script/Trig_Intro_Actions/1': 'Willkommen!'}.items():
            german['strings'][key]['translation'] = text
        old, _ = locale_tables(OLD, {'deDE': german})
        found = locale_translations(old)
        self.assertEqual(list(found), ['deDE'])
        read = found['deDE']['strings']
        self.assertEqual(read['w3u/h000/Name']['translation'], 'Fußsoldat')
        # a string the table gives its base text for reads as that text
        self.assertEqual(read['script/Trig_Intro_Actions/2']['translation'], 'Defend the gate.')
        # a string missing from the table reads as untranslated
        old['_Locales\\deDE.w3mod\\war3map.wts'] = WtsFile().import_data(
            {1: 'Fußsoldat'}).to_bytes()
        self.assertEqual(locale_translations(old)['deDE']['strings']
                         ['script/Trig_Intro_Actions/1']['translation'], '')
        # carried over to the next version as a translation file is
        table, counts = translation(NEW, found['deDE'])
        self.assertEqual((counts['changed'], counts['new']), (1, 2))
        self.assertEqual(table['strings']['w3u/h000/Name']['translation'], 'Fußsoldat')
        self.assertEqual(table['strings']['script/Trig_Intro_Actions/1']['old_source'],
                         'Welcome!')

    def test_cli_locales(self):
        from wc3files.w3x.modes import _write_map
        with tempfile.TemporaryDirectory() as tmp:
            old, new = os.path.join(tmp, 'v1.w3x'), os.path.join(tmp, 'v2.w3x')
            _write_map(OLD, old)
            _write_map(NEW, new)
            french = os.path.join(tmp, 'fr.json')
            with redirect_stderr(io.StringIO()):
                main(['map-translate', 'export', old, '-o', french])
            with open(french, encoding='utf-8') as handle:
                table = json.load(handle)
            table['strings']['script/Trig_Intro_Actions/2']['translation'] = 'Défendez la porte.'
            with open(french, 'w', encoding='utf-8') as handle:
                json.dump(table, handle)
            both = os.path.join(tmp, 'both.w3x')
            err = io.StringIO()
            with redirect_stderr(err):
                main(['map-translate', 'locales', old, '--locale', f'frFR={french}', '-o', both])
            self.assertIn('frFR: 1 applied', err.getvalue())
            carried = os.path.join(tmp, 'fr2.json')
            with redirect_stderr(io.StringIO()):
                main(['map-translate', 'export', new, '--previous', both, '--locale', 'frFR',
                      '-o', carried])
            with open(carried, encoding='utf-8') as handle:
                strings = json.load(handle)['strings']
            self.assertEqual(strings['script/Trig_Intro_Actions/2']['translation'],
                             'Défendez la porte.')
            err = io.StringIO()
            with redirect_stderr(err):
                code = main(['map-translate', 'export', new, '--previous', both,
                             '--locale', 'deDE', '-o', carried])
            self.assertNotEqual(code, 0)
            self.assertIn('has no deDE string table (it has: frFR)', err.getvalue())

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
