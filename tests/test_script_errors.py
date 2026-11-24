"""JASS and Lua syntax errors are ValueErrors, as every other reader's bad-
input errors are (#167). They subclassed Exception, so `except ValueError`
around ScriptFile.read let them through."""
import os
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files import ScriptFile
from wc3files.cli.main import main
from wc3files.scripts import JassLexError, JassParseError, LuaLexError, LuaParseError

CASES = [
    ('war3map.j', 'function main takes nothing returns nothing\n  set x = "\n', JassLexError),
    ('war3map.j', 'function main takes nothing returns\n', JassParseError),
    ('war3map.lua', 'local x = `\n', LuaLexError),
    ('war3map.lua', 'local = 1\n', LuaParseError),
]


def _write(name, text):
    path = os.path.join(tempfile.mkdtemp(), name)
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text)
    return path


class TestScriptErrors(unittest.TestCase):

    def test_syntax_errors_are_value_errors(self):
        for name, text, error in CASES:
            with self.subTest(error=error.__name__):
                with self.assertRaises(error) as caught:
                    ScriptFile().read(_write(name, text))
                self.assertIsInstance(caught.exception, ValueError)

    def test_the_cli_still_gives_the_vjass_hint(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = main(['jass', _write('war3map.j', CASES[1][1])])
        self.assertEqual(status, 1)
        self.assertIn('hint: if this script uses vJass', err.getvalue())


if __name__ == '__main__':
    unittest.main()
