"""wc3files.mdl.cleanup (#232): unused materials, textures and texture
animations removed, and every geoset still drawn with the same textures."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.cleanup import remove_unused

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def layer(texture_id, animation=NONE, **extra):
    data = {'filterMode': 0, 'shadingFlags': 0, 'textureId': texture_id,
            'textureAnimationId': animation, 'coordId': 0, 'alpha': 1.0, 'tracks': {}}
    data.update(extra)
    return data


def texture(path):
    return {'replaceableId': 0, 'path': path, 'flags': 0}


def animation(x):
    return {'tracks': {'KTAT': {'interpolationType': 0, 'globalSequenceId': NONE,
                                'keys': [{'frame': 0, 'value': [x, 0.0, 0.0]}]}}}


def messy_model():
    """Geoset 0 uses material 2, whose layer flips between textures 3 and 4
    and plays texture animation 1. Material 0 and texture 0 (only it uses
    them) are unused, and so are texture 1, material 1's texture 2 and
    texture animation 0. A particle emitter uses texture 5."""
    model = walking_cube()
    model['textures'] = [texture(f't{i}.blp') for i in range(6)]
    model['textureAnimations'] = [animation(1.0), animation(2.0)]
    flip = {'KMTF': {'interpolationType': 0, 'globalSequenceId': NONE,
                     'keys': [{'frame': 0, 'value': 3}, {'frame': 500, 'value': 4}]}}
    model['materials'] = [
        {'priorityPlane': 0, 'flags': 0, 'layers': [layer(0, 0)]},
        {'priorityPlane': 0, 'flags': 0, 'layers': [layer(2)]},
        {'priorityPlane': 0, 'flags': 0, 'layers': [layer(3, 1, tracks=flip)]},
    ]
    model['geosets'][0]['materialId'] = 2
    model['ribbonEmitters'] = [{'node': {'name': 'r', 'objectId': 1, 'parentId': NONE,
                                         'flags': 0, 'tracks': {}}, 'materialId': 1}]
    model['particleEmitters2'] = [{'node': {'name': 'p', 'objectId': 2, 'parentId': NONE,
                                            'flags': 0, 'tracks': {}}, 'textureId': 5,
                                   'tracks': {}}]
    return model


def drawn(model):
    """The texture paths and animation of each geoset's and ribbon's layers."""
    out = []
    for user in model['geosets'] + model['ribbonEmitters']:
        for l in model['materials'][user['materialId']]['layers']:
            flips = [model['textures'][k['value']]['path']
                     for k in (l['tracks'].get('KMTF') or {}).get('keys', [])]
            anim = l['textureAnimationId']
            out.append((model['textures'][l['textureId']]['path'], flips,
                        model['textureAnimations'][anim] if anim != NONE else None))
    out.append([model['textures'][e['textureId']]['path'] for e in model['particleEmitters2']])
    return out


class RemoveUnusedTest(unittest.TestCase):

    def test_unused_go_and_the_rest_is_drawn_the_same(self):
        model = messy_model()
        before = drawn(model)
        report = remove_unused(model)
        self.assertEqual(report, {'materials': 1, 'textures': 2, 'textureAnimations': 1})
        self.assertEqual([t['path'] for t in model['textures']],
                         ['t2.blp', 't3.blp', 't4.blp', 't5.blp'])
        self.assertEqual(drawn(model), before)
        self.assertEqual(model['geosets'][0]['materialId'], 1)
        self.assertEqual(remove_unused(model), {'materials': 0, 'textures': 0,
                                                'textureAnimations': 0})

    def test_the_texture_list_of_newer_layers_counts(self):
        model = messy_model()
        model['version'] = 1100
        # The layer of material 2 also lists texture 1 as its second texture.
        model['materials'][2]['layers'][0]['textures'] = [
            {'textureId': 3, 'slot': 0, 'tracks': {}}, {'textureId': 1, 'slot': 1, 'tracks': {}}]
        remove_unused(model)
        listed = model['materials'][1]['layers'][0]['textures']
        self.assertEqual([model['textures'][e['textureId']]['path'] for e in listed],
                         ['t3.blp', 't1.blp'])

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'messy.mdx')
        ModelFile().import_data(messy_model()).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-remove-unused',
                                 path, '-o', os.path.join(tmp, 'out')], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'removed 1 material(s), 2 texture(s), 1 texture animation(s)',
                      result.stdout)
        model = ModelFile()
        model.read(os.path.join(tmp, 'out', 'messy.mdx'))
        self.assertEqual(len(model.get_data()['textures']), 4)


if __name__ == '__main__':
    unittest.main()
