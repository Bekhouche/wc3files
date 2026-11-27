"""wc3files.mdl.sequences (#238): sequences removed with the keys and event
frames only they used, the timeline kept or packed."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import bone, cube, sequence, track, walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.extents import compute_extents
from wc3files.mdl.sequences import remove_sequences

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def three_sequences():
    """Stand 0-1000, Attack 2000-2500, Death 4000-5000, a key outside all
    of them at 3000, a global sequence track and footstep events."""
    model = walking_cube()
    model['sequences'] = [sequence('Stand', 0, 1000), sequence('Attack', 2000, 2500),
                          sequence('Death', 4000, 5000)]
    keys = [(0, [0.0] * 3), (1000, [1.0, 0, 0]), (2000, [2.0, 0, 0]), (2500, [3.0, 0, 0]),
            (3000, [9.0, 0, 0]), (4000, [4.0, 0, 0]), (5000, [5.0, 0, 0])]
    model['globalSequences'] = [600]
    model['bones'] = [bone(0, {'KGTR': track(keys),
                               'KGSC': track([(0, [1.0] * 3), (600, [2.0] * 3)],
                                             global_sequence=0)})]
    model['eventObjects'] = [{'node': {'name': 'FPT', 'objectId': 1, 'parentId': NONE,
                                       'flags': 1024, 'tracks': {}},
                              'globalSequenceId': NONE, 'tracks': [500, 2200, 4500]}]
    model['geosets'] = [cube()]
    compute_extents(model)
    return model


def frames(model, tag='KGTR'):
    return [k['frame'] for k in model['bones'][0]['node']['tracks'][tag]['keys']]


class RemoveSequencesTest(unittest.TestCase):

    def test_remove_one_and_keep_the_timeline(self):
        model = three_sequences()
        attack_extent = model['geosets'][0]['sequenceExtents'][1]
        report = remove_sequences(model, ['attack', 'Missing'])
        self.assertEqual(report, {'removed': ['Attack'], 'missing': ['Missing'], 'keys': 3})
        self.assertEqual([s['name'] for s in model['sequences']], ['Stand', 'Death'])
        self.assertEqual(frames(model), [0, 1000, 3000, 4000, 5000])
        self.assertEqual(frames(model, 'KGSC'), [0, 600])
        self.assertEqual(model['eventObjects'][0]['tracks'], [500, 4500])
        extents = model['geosets'][0]['sequenceExtents']
        self.assertEqual(len(extents), 2)
        self.assertNotIn(attack_extent, extents)

    def test_pack(self):
        model = three_sequences()
        remove_sequences(model, ['Attack'], pack=True)
        # The smallest gap between two sequences was 1000 (1000 to 2000), so
        # Death now starts at 2000; the key at 3000 no sequence played goes.
        self.assertEqual([s['interval'] for s in model['sequences']], [[0, 1000], [2000, 3000]])
        self.assertEqual(frames(model), [0, 1000, 2000, 3000])
        self.assertEqual([k['value'][0] for k in
                          model['bones'][0]['node']['tracks']['KGTR']['keys']], [0, 1, 4, 5])
        self.assertEqual(model['eventObjects'][0]['tracks'], [500, 2500])
        self.assertEqual(frames(model, 'KGSC'), [0, 600])

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'm.mdx')
        ModelFile().import_data(three_sequences()).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-remove-sequences',
                                 path, '--in-place', '--sequence', 'Death', '--sequence', 'Nope'],
                                capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'removed Death', result.stdout)
        self.assertIn(b'not found: Nope', result.stdout)
        model = ModelFile()
        model.read(path)
        self.assertEqual([s['name'] for s in model.get_data()['sequences']], ['Stand', 'Attack'])


if __name__ == '__main__':
    unittest.main()
