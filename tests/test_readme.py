"""The README's usage examples, run on the checked-in sample files.

They used to call classes and methods that did not exist (`MPQ`,
`ObjectFile.from_file`, `RegionFile.regions`, ...), on the page PyPI shows.
Running them here keeps them true. Each example reads files by the plain
names a reader would use ("MyMap.w3x", "war3map.w3u"); the test copies a
sample under that name into a temporary folder and runs the block there.
The replay example needs a real replay, as the other replay tests do: set
WC3FILES_TEST_REPLAY to one.
"""
import contextlib
import io
import os
import re
import shutil
import tempfile
import unittest

import _helpers

README = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'README.md')

# The file name an example reads, and the sample that stands in for it.
SAMPLES = {
    'MyMap.w3x': 'sample.w3x',
    'war3map.w3u': 'sample.w3u',
    'war3map.w3r': 'sample.w3r',
}


def _examples():
    with open(README, encoding='utf-8') as handle:
        text = handle.read()
    return re.findall(r'```python\n(.*?)```', text, re.S)


class TestReadmeExamples(unittest.TestCase):

    def test_there_are_examples(self):
        self.assertGreaterEqual(len(_examples()), 5)

    def test_every_example_runs(self):
        replay = os.environ.get('WC3FILES_TEST_REPLAY')
        for number, code in enumerate(_examples(), 1):
            with self.subTest(example=number, first_line=code.splitlines()[0]):
                if 'replay.w3g' in code and not replay:
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    for name, sample in SAMPLES.items():
                        shutil.copyfile(_helpers.sample_path(sample), os.path.join(tmp, name))
                    if replay:
                        shutil.copyfile(replay, os.path.join(tmp, 'replay.w3g'))
                    cwd = os.getcwd()
                    os.chdir(tmp)
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            exec(compile(code, f'README example {number}', 'exec'), {})
                    finally:
                        os.chdir(cwd)


if __name__ == '__main__':
    unittest.main()
