"""The deep scan of a protected map reports progress only when asked.

It used to wrap its loops in tqdm unconditionally: any library call that read
a protected map wrote progress bars to stderr, and tqdm was a required
dependency for that alone.
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files import W3xFile, W3xReadMode
from wc3files.mpq.data import MpqProtectionLevel

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')


class TestDeepScanProgress(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.map = os.path.join(self.tmp.name, 'protected.w3x')
        W3xFile().build_from_files({'war3map.j': b'function main takes nothing returns nothing\n'},
                                   self.map, protection=MpqProtectionLevel.SAFE)

    def test_silent_by_default(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            archive = W3xFile()
            archive.read(self.map, mode=W3xReadMode.DEEP)
        self.assertTrue(archive.files.get('war3map.j'))
        self.assertEqual(err.getvalue(), '')

    def test_a_callback_gets_each_step(self):
        calls = []
        archive = W3xFile()
        archive.read(self.map, mode=W3xReadMode.DEEP,
                     progress=lambda done, total, label: calls.append((done, total, label)))
        labels = {label for _, _, label in calls}
        self.assertIn('probing names', labels)
        probing = [(done, total) for done, total, label in calls if label == 'probing names']
        self.assertEqual(probing[-1][0], probing[-1][1])
        self.assertEqual(probing[-1][1], len(archive.default_files))

    def test_works_without_tqdm(self):
        code = ("import sys; sys.modules['tqdm'] = None\n"
                "from wc3files import W3xFile, W3xReadMode\n"
                "import wc3files.mpq.files, wc3files.cli.main\n"
                "w = W3xFile(); w.read(sys.argv[1], mode=W3xReadMode.DEEP, progress=True)\n"
                "assert w.files.get('war3map.j'), w.files\n")
        env = dict(os.environ, PYTHONPATH=os.path.abspath(SRC))
        result = subprocess.run([sys.executable, '-c', code, self.map],
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
