"""wc3files.mdl.texture_list (#242): the textures of many models, and of a
map's models against the map's files and the game's."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_cleanup import texture
from test_model_extents import walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.mdx_io import save_mdx
from wc3files.mdl.texture_list import list_textures, map_textures, texture_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = chr(92)


def model_with(*paths):
    model = walking_cube()
    model['textures'] = [texture(p) for p in paths]
    return model


def a_map():
    """Two models, three imported images and an object file naming one."""
    return {
        'war3mapImported' + BS + 'Hero.mdx': save_mdx(model_with(
            'war3mapImported' + BS + 'Hero.blp', 'Textures/Footman.blp', '')),
        'Tree.mdx': save_mdx(model_with('war3mapImported/hero.BLP', 'Lost.blp',
                                        'Other.blp')),
        'war3mapImported' + BS + 'Hero.blp': b'BLP1',
        'war3mapImported' + BS + 'Old.blp': b'BLP1',
        'war3mapImported' + BS + 'BTNIcon.blp': b'BLP1',
        'Other.dds': b'DDS ',
        'war3map.w3u': b'uico\x03\x00\x00\x00war3mapImported' + BS.encode() + b'BTNIcon.blp\x00',
    }


class TextureListTest(unittest.TestCase):

    def test_texture_paths_once_each_without_replaceables(self):
        self.assertEqual(texture_paths(model_with('A.blp', 'a.BLP', '', 'B.blp')),
                         ['A.blp', 'B.blp'])

    def test_a_texture_lists_its_models(self):
        found = list_textures([('one', model_with('A.blp')), ('two', model_with('a.blp', 'B.blp'))])
        self.assertEqual(found['a.blp']['models'], ['one', 'two'])
        self.assertEqual(found['b.blp']['models'], ['two'])

    def test_a_map(self):
        report = map_textures(a_map())
        self.assertEqual(report['models'], 2)
        hero = report['textures']['war3mapimported' + BS + 'hero.blp']
        self.assertEqual(sorted(hero['models']), ['Tree.mdx', 'war3mapImported' + BS + 'Hero.mdx'])
        # Other.dds is also listed with Other.blp, as another extension.
        self.assertEqual(report['unused'], ['Other.dds', 'war3mapImported' + BS + 'Old.blp'])
        self.assertEqual(report['named_elsewhere'],
                         {'war3mapImported' + BS + 'BTNIcon.blp': ['war3map.w3u']})
        self.assertEqual(sorted(e['path'] for e in report['missing']),
                         ['Lost.blp', 'Other.blp', 'Textures/Footman.blp'])
        self.assertEqual(report['other_extension'], [('Other.blp', 'Other.dds')])

    def test_a_name_with_another_extension_or_none_counts(self):
        files = a_map()
        files['war3map.j'] = (b'call AddIcon("war3mapImported\\\\Old" )\n'
                              b'call BlzFrameSetTexture(f, "Other.tga", 0, true)\n'
                              b'call AddIcon("war3mapImported\\\\Older.blp")\n')
        report = map_textures(files)
        self.assertEqual(report['unused'], [])
        self.assertEqual(report['named_elsewhere']['war3mapImported' + BS + 'Old.blp'],
                         ['war3map.j'])
        self.assertEqual(report['named_elsewhere']['Other.dds'], ['war3map.j'])

    def test_the_game_files_count_under_any_image_extension(self):
        game = {'textures' + BS + 'footman'}
        missing = map_textures(a_map(), game)['missing']
        self.assertEqual(sorted(e['path'] for e in missing), ['Lost.blp', 'Other.blp'])

    def test_cli_on_a_folder(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        ModelFile().import_data(model_with('A.blp')).write(os.path.join(tmp, 'one.mdx'))
        ModelFile().import_data(model_with('A.blp', 'B.blp')).write(os.path.join(tmp, 'two.mdl'))
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-textures', tmp,
                                 '--json'], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        found = json.loads(result.stdout)['models']['textures']
        self.assertEqual(len(found['a.blp']['models']), 2)


if __name__ == '__main__':
    unittest.main()
