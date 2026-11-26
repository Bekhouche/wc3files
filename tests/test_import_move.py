"""wc3files.w3x.imports.move_imports and `import-move` (#214) on a small
map built here. A hero model, its portrait, a skin and an icon are imported
under war3mapImported\\. The unit data, a JASS script, an FDF file, the
model and war3map.imp all refer to them, and the script also builds one
path while it runs."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.imp.imp_file import ImpFile
from wc3files.mdl.mdx_io import load_mdx, save_mdx
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.imports import move_imports, strip_prefix_moves
from wc3files.w3x.modes import _read_files, _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = '\\'
HERO = 'war3mapImported\\Hero.mdx'
SKIN = 'war3mapImported\\Skin.blp'
ICON = 'war3mapImported\\BTNHero.blp'


def _units(fields):
    handler = ObjectFile()
    handler.import_data({'w3u': {'original': {}, 'custom': {
        'H000': dict({'ID': 'Hpal'}, **{key: {'value': value, 'type': 3}
                                        for key, value in fields.items()})}}})
    return handler.to_bytes('w3u')


def _unit_fields(data):
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, 'war3map.w3u')
        with open(path, 'wb') as handle:
            handle.write(data)
        handler = ObjectFile()
        handler.read(path)
    finally:
        shutil.rmtree(folder)
    unit = handler.export_data()['w3u']['custom']['H000']
    return {key: value['value'] for key, value in unit.items() if key != 'ID'}


def _model(texture):
    model = load_mdx(_sample_model())
    model['textures'] = [{'replaceableId': 0, 'path': texture, 'flags': 0}]
    return save_mdx(model)


def _sample_model():
    with open(_helpers.sample_path('sample.mdx'), 'rb') as handle:
        return handle.read()


def _imp(paths):
    return ImpFile().import_data(
        {'version': 1, 'files': [{'flags': 13, 'path': p} for p in paths]}).to_bytes()


def _listed(files):
    imports = ImpFile()
    imports.read(files['war3map.imp'])
    return [entry['path'] for entry in imports.get_data()['files']]


SCRIPT = (
    'function main takes nothing returns nothing\n'
    '    local string a = "war3mapImported\\\\BTNHero.blp"\n'
    '    local string b = "war3mapImported\\\\" + "Skin.blp"\n'
    '    local string c = "war3mapImported\\\\Hero.mdl"\n'
    'endfunction\n')
FDF = 'Frame "BACKDROP" "HeroBackdrop" {\n    BackdropBackground "war3mapImported\\BTNHero.blp",\n}\n'


class MoveImportsTest(unittest.TestCase):

    def setUp(self):
        self.files = {
            'war3map.w3u': _units({'umdl': 'war3mapImported\\Hero.mdl', 'uico': ICON,
                                   'umki': 'war3mapImported\\HeroGlow.mdl,' + ICON}),
            'war3map.j': SCRIPT.encode('utf-8'),
            'UI\\Hero.fdf': FDF.encode('utf-8'),
            HERO: _model(SKIN),
            'war3mapImported\\Hero_portrait.mdx': _model('war3mapImported/skin.BLP'),
            SKIN: b'skin',
            ICON: b'icon',
            'war3map.imp': _imp([HERO, 'war3mapImported\\Hero_portrait.mdx', SKIN, ICON]),
        }

    def test_a_model_moves_with_its_portrait_and_every_reference(self):
        files, report = move_imports(self.files, {HERO: 'Units\\Hero\\Hero.mdx'})
        self.assertEqual(report['moved'], [
            (HERO, 'Units\\Hero\\Hero.mdx'),
            ('war3mapImported\\Hero_portrait.mdx', 'Units\\Hero\\Hero_portrait.mdx')])
        self.assertEqual(files['Units\\Hero\\Hero.mdx'], self.files[HERO])
        self.assertNotIn(HERO, files)
        # The unit's reference keeps its .mdl, as the game loads either.
        self.assertEqual(_unit_fields(files['war3map.w3u'])['umdl'], 'Units\\Hero\\Hero.mdl')
        self.assertIn('"Units\\\\Hero\\\\Hero.mdl"', files['war3map.j'].decode())
        self.assertEqual(_listed(files)[:2], ['Units\\Hero\\Hero.mdx',
                                              'Units\\Hero\\Hero_portrait.mdx'])
        # The script's "war3mapImported\\" may build a path to the model.
        self.assertEqual([(item['member'], item['text']) for item in report['unresolved']],
                         [('war3map.j', 'war3mapImported\\\\')])

    def test_strip_prefix_rewrites_object_data_script_fdf_and_models(self):
        moves, skipped = strip_prefix_moves(self.files)
        self.assertEqual(skipped, [])
        files, report = move_imports(self.files, moves)
        self.assertEqual(sorted(new for _, new in report['moved']),
                         ['BTNHero.blp', 'Hero.mdx', 'Hero_portrait.mdx', 'Skin.blp'])
        fields = _unit_fields(files['war3map.w3u'])
        self.assertEqual(fields['uico'], 'BTNHero.blp')
        self.assertEqual(fields['umdl'], 'Hero.mdl')
        # A list keeps the item that is not an import of the map.
        self.assertEqual(fields['umki'], 'war3mapImported\\HeroGlow.mdl,BTNHero.blp')
        script = files['war3map.j'].decode()
        self.assertIn('local string a = "BTNHero.blp"', script)
        self.assertIn('local string c = "Hero.mdl"', script)
        self.assertIn('BackdropBackground "BTNHero.blp"', files['UI\\Hero.fdf'].decode())
        self.assertEqual(load_mdx(files['Hero.mdx'])['textures'][0]['path'], 'Skin.blp')
        # The portrait wrote its texture with / and in its own case.
        self.assertEqual(load_mdx(files['Hero_portrait.mdx'])['textures'][0]['path'], 'Skin.blp')
        self.assertEqual(_listed(files), ['Hero.mdx', 'Hero_portrait.mdx', 'Skin.blp',
                                          'BTNHero.blp'])
        # The path the script builds while it runs is reported, not changed.
        self.assertIn('"war3mapImported\\\\" + "Skin.blp"', script)
        self.assertEqual([(item['member'], item['text']) for item in report['unresolved']],
                         [('war3map.j', 'war3mapImported\\\\')])

    def test_a_longer_path_fits_the_mdx_field(self):
        longer = 'ReplaceableTextures\\Units\\Hero\\Textures\\Skin.blp'
        files, report = move_imports(self.files, {SKIN: longer})
        self.assertEqual(load_mdx(files[HERO])['textures'][0]['path'], longer)
        self.assertEqual(len(files[HERO]), len(self.files[HERO]))
        self.assertEqual(load_mdx(files['war3mapImported\\Hero_portrait.mdx'])
                         ['textures'][0]['path'], longer.replace(BS, '/'))

    def test_a_path_too_long_for_the_mdx_field_is_reported(self):
        files, report = move_imports(self.files, {SKIN: 'A' * 255 + '\\Skin.blp'})
        self.assertEqual(files[HERO], self.files[HERO])
        refused = [item['member'] for item in report['unresolved']
                   if item['reason'] == 'no room in the MDX field']
        self.assertEqual(sorted(refused), [HERO, 'war3mapImported\\Hero_portrait.mdx'])
        # The script's "Skin.blp" no longer names the file.
        self.assertIn(('war3map.j', 'Skin.blp'),
                      [(item['member'], item['text']) for item in report['unresolved']])

    def test_a_move_onto_a_member_is_refused(self):
        with self.assertRaises(ValueError):
            move_imports(self.files, {HERO: ICON})
        with self.assertRaises(ValueError):
            move_imports(self.files, {'war3mapImported\\Nothing.blp': 'x.blp'})

    def test_strip_prefix_skips_a_name_the_map_has(self):
        self.files['Skin.blp'] = b'other'
        moves, skipped = strip_prefix_moves(self.files)
        self.assertEqual(skipped, [(SKIN, 'Skin.blp')])
        self.assertNotIn(SKIN, moves)

    def test_other_files_are_unchanged(self):
        files, _ = move_imports(self.files, {ICON: 'ReplaceableTextures\\CommandButtons\\'
                                                   'BTNHero.blp'})
        self.assertEqual(files[HERO], self.files[HERO])
        self.assertEqual(files[SKIN], self.files[SKIN])

    def test_cli_report_only_writes_nothing_and_the_move_reads_back(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source = os.path.join(tmp, 'map.w3x')
        output = os.path.join(tmp, 'out.w3x')
        _write_map(self.files, source)
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'import-move', source,
                                 '--strip-prefix', '--report-only'],
                                capture_output=True, check=True, env=env)
        self.assertIn(b'4 moved', result.stdout)
        self.assertFalse(os.path.exists(output))
        subprocess.run([sys.executable, '-m', 'wc3files.cli', 'import-move', source, output,
                        '--move', ICON, 'ReplaceableTextures\\CommandButtons\\BTNHero.blp'],
                       capture_output=True, check=True, env=env)
        files = _read_files(output)
        self.assertEqual(_unit_fields(files['war3map.w3u'])['uico'],
                         'ReplaceableTextures\\CommandButtons\\BTNHero.blp')


if __name__ == '__main__':
    unittest.main()
