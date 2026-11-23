"""Command output to a file or a pipe is UTF-8 (#153). On Windows Python
writes such a stream in the code page (cp1252), and a trigger name or string
outside it stopped the command with a 'charmap' codec error."""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import _helpers  # noqa: F401
from wc3files.cli.main import main

SCRIPT = ('globals\n'
          '    string greeting = "你好 привет"\n'
          'endglobals\n'
          'function main takes nothing returns nothing\n'
          'endfunction\n')


class TestUtf8Output(unittest.TestCase):

    def run_into_a_cp1252_pipe(self, argv, env=None):
        raw = io.BytesIO()
        pipe = io.TextIOWrapper(raw, encoding='cp1252', write_through=True)
        environ = {k: v for k, v in os.environ.items() if k != 'PYTHONIOENCODING'}
        environ.update(env or {})
        with mock.patch.object(sys, 'stdout', pipe), mock.patch.dict(os.environ, environ, clear=True):
            status = main(argv)
            pipe.flush()
            encoding = pipe.encoding
        return status, raw.getvalue(), encoding

    def script(self):
        path = os.path.join(tempfile.mkdtemp(), 'war3map.j')
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(SCRIPT)
        return path

    def test_text_outside_the_code_page_is_written_as_utf8(self):
        status, out, encoding = self.run_into_a_cp1252_pipe(['jass', self.script()])
        self.assertIn(status, (None, 0))
        self.assertEqual(encoding, 'utf-8')
        self.assertIn('你好 привет', out.decode('utf-8'))

    def test_pythonioencoding_is_kept(self):
        _status, _out, encoding = self.run_into_a_cp1252_pipe(
            ['jass', self.script(), '--check'],
            env={'PYTHONIOENCODING': 'cp1252'})
        self.assertEqual(encoding, 'cp1252')


if __name__ == '__main__':
    unittest.main()
