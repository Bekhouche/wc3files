"""wc3files.mdl.keyframes (#230): keyframes removed when they change
nothing, checked by sampling every track at every frame before and after."""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import bone, sequence, track, walking_cube
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import sample
from wc3files.mdl.keyframes import remove_redundant_keys, tracks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def keyed_model(keys, interpolation=1, tag='KGTR', sequences=((0, 1000),)):
    model = walking_cube()
    model['bones'] = [bone(0, {tag: track(keys, interpolation)})]
    model['sequences'] = [sequence(f's{i}', a, b) for i, (a, b) in enumerate(sequences)]
    return model


def same_everywhere(test, before, after):
    for (tag, old), (_, new) in zip(tracks(before), tracks(after)):
        for s in before['sequences']:
            window = tuple(s['interval'])
            for f in range(window[0], window[1] + 1):
                a = sample(old, f, window, rotation=tag == 'KGRT')
                b = sample(new, f, window, rotation=tag == 'KGRT')
                if isinstance(a, list):
                    for x, y in zip(a, b):
                        test.assertAlmostEqual(x, y, places=9)
                else:
                    test.assertEqual(a, b)


def frames(model, tag='KGTR'):
    return [k['frame'] for k in model['bones'][0]['node']['tracks'][tag]['keys']]


class RedundantKeysTest(unittest.TestCase):

    def check(self, model, **options):
        before = copy.deepcopy(model)
        report = remove_redundant_keys(model, **options)
        same_everywhere(self, before, model)
        return report

    def test_linear_points_on_a_line_go(self):
        model = keyed_model([(0, [0.0, 0.0, 0.0]), (250, [25.0, 0.0, 0.0]),
                             (500, [50.0, 0.0, 0.0]), (750, [80.0, 0.0, 0.0]),
                             (1000, [100.0, 0.0, 0.0])])
        self.assertEqual(self.check(model)['redundant'], 1)
        self.assertEqual(frames(model), [0, 500, 750, 1000])

    def test_repeated_steps_go(self):
        model = keyed_model([(0, 1.0), (100, 1.0), (200, 1.0), (300, 2.0), (1000, 2.0)],
                            interpolation=0, tag='KGSC')
        model['bones'][0]['node']['tracks']['KGSC']['keys'] = [
            {'frame': f, 'value': [v] * 3} for f, v in ((0, 1.0), (100, 1.0), (200, 1.0),
                                                       (300, 2.0), (1000, 2.0))]
        self.check(model)
        self.assertEqual(frames(model, 'KGSC'), [0, 300, 1000])

    def test_a_curve_goes_only_where_it_is_flat(self):
        flat = {'interpolationType': 2, 'globalSequenceId': NONE, 'keys': [
            {'frame': f, 'value': [v, 0.0, 0.0], 'inTan': [0.0] * 3, 'outTan': [0.0] * 3}
            for f, v in ((0, 5.0), (300, 5.0), (600, 5.0), (800, 9.0), (1000, 9.0))]}
        model = keyed_model([(0, [0.0] * 3)])
        model['bones'][0]['node']['tracks']['KGTR'] = flat
        self.check(model)
        self.assertEqual(frames(model), [0, 600, 800, 1000])

    def test_sequence_ends_stay_and_windows_are_apart(self):
        # The key at 1000 ends the first sequence; the one at 2000 starts the
        # second, and a line through 1000 and 3000 would not be the same.
        model = keyed_model([(0, [0.0] * 3), (500, [5.0, 0.0, 0.0]), (1000, [10.0, 0.0, 0.0]),
                             (2000, [0.0] * 3), (3000, [30.0, 0.0, 0.0])],
                            sequences=((0, 1000), (2000, 3000)))
        self.check(model)
        self.assertEqual(frames(model), [0, 1000, 2000, 3000])

    def test_rotations_by_slerp(self):
        half = 0.7071067811865476
        q = [[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.3826834323650898, 0.9238795325112867],
             [0.0, 0.0, half, half]]
        model = keyed_model([(0, q[0]), (500, q[1]), (1000, q[2])], tag='KGRT')
        self.check(model, tolerance=1e-9)
        self.assertEqual(frames(model, 'KGRT'), [0, 1000])

    def test_a_tolerance_does_not_add_up(self):
        # The middle keys are 1 to 2 off the straight line: with a tolerance
        # of 1.5 some go, and every value stays within 1.5 of where it was.
        model = keyed_model([(f, [v, 0.0, 0.0]) for f, v in
                             ((0, 0.0), (250, 26.0), (500, 52.0), (750, 76.0), (1000, 100.0))])
        remove_redundant_keys(model, tolerance=1.5)
        kept = frames(model)
        line = model['bones'][0]['node']['tracks']['KGTR']
        for f, v in ((250, 26.0), (500, 52.0), (750, 76.0)):
            self.assertLessEqual(abs(sample(line, f, (0, 1000))[0] - v), 1.5 + 1e-9)
        self.assertLess(len(kept), 5)

    def test_dead_keys_only_when_asked(self):
        model = keyed_model([(0, [0.0] * 3), (1000, [1.0, 0, 0]), (5000, [9.0, 0, 0])])
        self.assertEqual(remove_redundant_keys(copy.deepcopy(model))['dead'], 0)
        report = remove_redundant_keys(model, dead=True)
        self.assertEqual((report['dead'], frames(model)), (1, [0, 1000]))

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'm.mdx')
        ModelFile().import_data(keyed_model([(0, [0.0] * 3), (500, [5.0, 0.0, 0.0]),
                                             (1000, [10.0, 0.0, 0.0])])).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-keyframes', path,
                                 '--in-place'], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'1 of 3 key(s) removed', result.stdout)


if __name__ == '__main__':
    unittest.main()
