"""The package logs nothing unless the application asks (#145). loguru
prints to stderr by default, so a script that read one SLK table got an
INFO line. Run in fresh interpreters, since loguru's state is global."""
import os
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')
TABLE = os.path.join(SRC, 'wc3files', 'w3o', 'gamedata', 'UnitData.slk')


def stderr_of(code):
    env = dict(os.environ, PYTHONPATH=SRC + os.pathsep + os.environ.get('PYTHONPATH', ''))
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                            env=env, cwd=ROOT, timeout=120)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stderr


class TestLibraryLogging(unittest.TestCase):

    def test_quiet_by_default(self):
        code = '\n'.join(['import wc3files',
                          f'wc3files.SlkFile().read({TABLE!r})'])
        self.assertEqual(stderr_of(code), '')

    def test_enable_turns_it_back_on(self):
        # After the import, which is where the package disables its log.
        code = '\n'.join(['import wc3files',
                          'from loguru import logger',
                          "logger.enable('wc3files')",
                          f'wc3files.SlkFile().read({TABLE!r})'])
        err = stderr_of(code)
        self.assertIn('rows from', err)

    def test_the_command_line_still_shows_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, 'empty.slk')
            with open(empty, 'w') as handle:
                handle.write('ID;PWXL;N;E\nE\n')
            code = '\n'.join(['from wc3files.cli.main import main',
                              "main(['dump', 'w3i', 'no-such-file.w3i'])",
                              'import wc3files',
                              f'wc3files.SlkFile().read({empty!r})'])
            err = stderr_of(code)
        self.assertIn('No cells found', err)
        self.assertNotIn('INFO', err)


if __name__ == '__main__':
    unittest.main()
