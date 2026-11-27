"""wc3files.mdl.paths (#241): texture paths rewritten by whole path, prefix
or pattern, in MDX and MDL."""
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
from wc3files.mdl.paths import rewrite_texture_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = chr(92)


def model_with(*paths):
    model = walking_cube()
    model['textures'] = [texture(p) for p in paths]
    return model


def paths(model):
    return [t['path'] for t in model['textures']]


class TexturePathsTest(unittest.TestCase):

    def test_exact_ignores_case_and_separators(self):
        model = model_with('Textures' + BS + 'Footman.blp', 'Textures/Other.blp')
        report = rewrite_texture_paths(model, [('exact', 'textures/FOOTMAN.blp', 'Hero.blp')])
        self.assertEqual(paths(model), ['Hero.blp', 'Textures/Other.blp'])
        self.assertEqual(report['changed'], [('Textures' + BS + 'Footman.blp', 'Hero.blp')])

    def test_prefix_keeps_the_rest_of_the_path(self):
        model = model_with('war3mapImported' + BS + 'Skins' + BS + 'A.blp', 'B.blp')
        rewrite_texture_paths(model, [('prefix', 'War3mapImported' + BS, '')])
        self.assertEqual(paths(model), ['Skins' + BS + 'A.blp', 'B.blp'])

    def test_pattern_with_groups_and_the_first_rule_wins(self):
        model = model_with('a/Hero_1.BLP', 'b/Hero_2.blp')
        rewrite_texture_paths(model, [('exact', 'b/Hero_2.blp', 'kept.blp'),
                                      ('pattern', r'Hero_(\d)\.blp$', r'Hero\1.dds')])
        self.assertEqual(paths(model), ['a/Hero1.dds', 'kept.blp'])

    def test_replaceable_and_too_long(self):
        model = model_with('', 'short.blp')
        model['textures'][0]['replaceableId'] = 1
        report = rewrite_texture_paths(model, [('pattern', '^.*$', 'x' * 300)])
        self.assertEqual(paths(model), ['', 'short.blp'])
        self.assertEqual(len(report['refused']), 1)

    def test_bad_rules(self):
        for rule in (('glob', '*', ''), ('pattern', '(', '')):
            with self.assertRaises(ValueError):
                rewrite_texture_paths(model_with('a.blp'), [rule])

    def test_cli_on_mdl_in_order(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'm.mdl')
        ModelFile().import_data(model_with('Old/A.blp', 'Old/B.blp')).write(path)
        result = subprocess.run(
            [sys.executable, '-m', 'wc3files.cli', 'model-texture-paths', path, '--in-place',
             '--replace', 'Old/A.blp', 'First.blp', '--prefix', 'Old/', 'New/'],
            capture_output=True, env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'2 path(s) rewritten', result.stdout)
        model = ModelFile()
        model.read(path)
        self.assertEqual(paths(model.get_data()), ['First.blp', 'New/B.blp'])


if __name__ == '__main__':
    unittest.main()
