"""wc3files.mdl.sequences.transfer_sequences (#239): sequences copied
between two models that share a skeleton, bones matched by name."""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import bone, sequence, track
from test_model_sequences import three_sequences
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import sample
from wc3files.mdl.sequences import remove_sequences, transfer_sequences

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def node_track(model, name, tag='KGTR'):
    for entry in model['bones'] + model.get('helpers', []):
        if entry['node']['name'] == name:
            return entry['node']['tracks'].get(tag)
    return None


class TransferTest(unittest.TestCase):

    def test_back_into_the_model_it_came_from(self):
        source = three_sequences()
        target = copy.deepcopy(source)
        remove_sequences(target, ['Attack', 'Death'])
        report = transfer_sequences(target, source, ['Death'])
        self.assertEqual(report['sequences'], ['Death'])
        # After the last sequence (0-1000) and the last key, at 3000, with
        # the target's gap; it had one sequence, so the default 100.
        death = target['sequences'][-1]
        self.assertEqual(death['interval'], [3100, 4100])
        old, new = node_track(source, 'b0'), node_track(target, 'b0')
        for f in range(4000, 5001, 50):
            self.assertEqual(sample(old, f, (4000, 5000)), sample(new, f - 900, (3100, 4100)))
        self.assertEqual(target['eventObjects'][0]['tracks'], [500, 3600])
        self.assertEqual(len(target['geosets'][0]['sequenceExtents']), 2)

    def test_nodes_by_name_and_what_does_not_match(self):
        source = three_sequences()
        source['bones'].append(bone(5, {'KGRT': track([(4000, [0.0, 0.0, 0.0, 1.0])])}))
        source['bones'][-1]['node']['name'] = ' Tail '
        target = three_sequences()
        target['bones'][0]['node']['name'] = 'B0'
        target['helpers'] = [{'node': {'name': 'tail', 'objectId': 7, 'parentId': NONE,
                                       'flags': 0, 'tracks': {}}}]
        target['bones'].append(bone(8))
        target['bones'][-1]['node']['name'] = 'Wing'
        report = transfer_sequences(target, source, ['Death'])
        self.assertEqual(report['unmatched_nodes'], [])
        self.assertIn('wing', report['target_only_nodes'])
        self.assertEqual(report['already_named'], ['Death'])
        self.assertEqual(len(node_track(target, 'tail', 'KGRT')['keys']), 1)

    def test_interpolation_follows_the_target(self):
        source = three_sequences()
        target = three_sequences()
        target_track = node_track(target, 'b0')
        target_track['interpolationType'] = 2
        for key in target_track['keys']:
            key['inTan'] = key['outTan'] = [0.0] * 3
        report = transfer_sequences(target, source, ['Death'])
        self.assertEqual(report['converted'], 0)
        start, end = target['sequences'][-1]['interval']
        moved = node_track(target, 'b0')
        # A linear source becomes a Hermite track that is still straight.
        for f in range(start, end + 1, 100):
            self.assertAlmostEqual(sample(moved, f, (start, end))[0],
                                   sample(node_track(source, 'b0'), f - start + 4000,
                                          (4000, 5000))[0])

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source, target = os.path.join(tmp, 'a.mdx'), os.path.join(tmp, 'b.mdx')
        ModelFile().import_data(three_sequences()).write(source)
        stripped = three_sequences()
        remove_sequences(stripped, ['Death'])
        ModelFile().import_data(stripped).write(target)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-transfer-sequences',
                                 source, target, '--in-place', '--sequence', 'Death'],
                                capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'copied Death', result.stdout)
        model = ModelFile()
        model.read(target)
        self.assertEqual([s['name'] for s in model.get_data()['sequences']],
                         ['Stand', 'Attack', 'Death'])


if __name__ == '__main__':
    unittest.main()
