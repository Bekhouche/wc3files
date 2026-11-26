"""wc3files.mdl.teamglow (#240): team glow made transparent or removed,
on a small model built here with a glow geoset of its own."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_cleanup import layer, texture
from test_model_extents import cube, walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.teamglow import remove_team_glow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def glowing_model():
    """Geoset 0: the body (texture 0). Geoset 1: the team glow (texture 1,
    replaceable 2), with an alpha track and a geoset animation. Geoset 2:
    the cape, whose geoset animation and bone come after the glow's."""
    model = walking_cube()
    model['geosets'] = [cube(), cube(offset=(0.0, 0.0, 30.0)), cube(offset=(50.0, 0.0, 0.0))]
    for i, g in enumerate(model['geosets']):
        g['materialId'] = [0, 1, 2][i]
    glow = texture('')
    glow['replaceableId'] = 2
    model['textures'] = [texture('body.blp'), glow, texture('cape.blp')]
    alpha = {'KMTA': {'interpolationType': 0, 'globalSequenceId': NONE,
                      'keys': [{'frame': 0, 'value': 1.0}]}}
    model['materials'] = [{'priorityPlane': 0, 'flags': 0, 'layers': [layer(0)]},
                          {'priorityPlane': 0, 'flags': 0, 'layers': [layer(1, tracks=alpha)]},
                          {'priorityPlane': 0, 'flags': 0, 'layers': [layer(2)]}]
    model['geosetAnimations'] = [
        {'alpha': 1.0, 'flags': 0, 'color': [1.0] * 3, 'geosetId': 1, 'tracks': {}},
        {'alpha': 0.5, 'flags': 0, 'color': [1.0] * 3, 'geosetId': 2, 'tracks': {}}]
    model['bones'][0]['geosetId'] = 2
    model['bones'][0]['geosetAnimationId'] = 1
    return model


class TeamGlowTest(unittest.TestCase):

    def test_transparent(self):
        model = glowing_model()
        report = remove_team_glow(model)
        self.assertEqual(report['layers'], 1)
        glow_layer = model['materials'][1]['layers'][0]
        self.assertEqual((glow_layer['alpha'], glow_layer['tracks']), (0.0, {}))
        self.assertEqual(len(model['geosets']), 3)
        self.assertEqual(model['materials'][0]['layers'][0]['alpha'], 1.0)

    def test_remove(self):
        model = glowing_model()
        report = remove_team_glow(model, 'remove')
        self.assertEqual(report, {'layers': 1, 'geosets': 1, 'materials': 1, 'textures': 1})
        self.assertEqual([model['textures'][model['materials'][g['materialId']]['layers'][0]
                                            ['textureId']]['path'] for g in model['geosets']],
                         ['body.blp', 'cape.blp'])
        self.assertEqual(model['geosets'][1]['vertices'][0], 40.0)
        # The cape's geoset animation and bone follow it to index 1 and 0.
        self.assertEqual([(a['geosetId'], a['alpha']) for a in model['geosetAnimations']],
                         [(1, 0.5)])
        self.assertEqual((model['bones'][0]['geosetId'], model['bones'][0]['geosetAnimationId']),
                         (1, 0))

    def test_a_ribbon_keeps_its_material(self):
        model = glowing_model()
        model['ribbonEmitters'] = [{'node': {'name': 'r', 'objectId': 1, 'parentId': NONE,
                                             'flags': 0, 'tracks': {}}, 'materialId': 1}]
        remove_team_glow(model, 'remove')
        material = model['materials'][model['ribbonEmitters'][0]['materialId']]
        self.assertEqual(material['layers'][0]['alpha'], 0.0)

    def test_no_glow_and_a_bad_mode(self):
        model = walking_cube()
        self.assertEqual(remove_team_glow(model)['layers'], 0)
        with self.assertRaises(ValueError):
            remove_team_glow(model, 'hide')

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'glow.mdx')
        ModelFile().import_data(glowing_model()).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-team-glow', path,
                                 '--in-place', '--mode', 'remove'], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        model = ModelFile()
        model.read(path)
        self.assertEqual(len(model.get_data()['geosets']), 2)


if __name__ == '__main__':
    unittest.main()
