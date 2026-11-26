"""wc3files.mdl.keyframes.linearize (#231): curved tracks made linear, with
keys added where the line leaves the curve by more than a tolerance."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_keyframes import keyed_model
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import sample
from wc3files.mdl.keyframes import linearize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NONE = 0xFFFFFFFF


def curved(interpolation=2):
    """A Hermite (or Bezier) rise from 0 to 100 over 1000 ms."""
    model = keyed_model([(0, [0.0] * 3)])
    if interpolation == 2:
        keys = [{'frame': 0, 'value': [0.0] * 3, 'inTan': [0.0] * 3, 'outTan': [0.0] * 3},
                {'frame': 1000, 'value': [100.0, 0.0, 0.0], 'inTan': [0.0] * 3,
                 'outTan': [0.0] * 3}]
    else:
        keys = [{'frame': 0, 'value': [0.0] * 3, 'inTan': [0.0] * 3, 'outTan': [90.0, 0, 0]},
                {'frame': 1000, 'value': [100.0, 0.0, 0.0], 'inTan': [95.0, 0, 0],
                 'outTan': [100.0, 0.0, 0.0]}]
    model['bones'][0]['node']['tracks']['KGTR'] = {
        'interpolationType': interpolation, 'globalSequenceId': NONE, 'keys': keys}
    return model


def track_of(model):
    return model['bones'][0]['node']['tracks']['KGTR']


class LinearTest(unittest.TestCase):

    def test_tangents_go_and_the_ends_stay(self):
        model = curved()
        report = linearize(model)
        t = track_of(model)
        self.assertEqual(report, {'tracks': 1, 'added': 0})
        self.assertEqual(t['interpolationType'], 1)
        self.assertEqual(t['keys'], [{'frame': 0, 'value': [0.0] * 3},
                                     {'frame': 1000, 'value': [100.0, 0.0, 0.0]}])

    def test_a_tolerance_adds_keys_until_the_line_is_close(self):
        for interpolation in (2, 3):
            model = curved(interpolation)
            original = track_of(curved(interpolation))
            report = linearize(model, tolerance=0.5)
            self.assertGreater(report['added'], 0)
            line = track_of(model)
            for f in range(0, 1001):
                a = sample(original, f, (0, 1000))[0]
                b = sample(line, f, (0, 1000))[0]
                self.assertLessEqual(abs(a - b), 0.5 + 1e-9)

    def test_linear_and_step_tracks_are_left(self):
        model = keyed_model([(0, [0.0] * 3), (1000, [1.0, 0.0, 0.0])])
        self.assertEqual(linearize(model)['tracks'], 0)

    def test_cli_reports_the_size(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'm.mdx')
        ModelFile().import_data(curved()).write(path)
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-linear', path,
                                 '--in-place'], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'1 track(s) made linear, as MDX', result.stdout)
        model = ModelFile()
        model.read(path)
        self.assertEqual(track_of(model.get_data())['interpolationType'], 1)


if __name__ == '__main__':
    unittest.main()
