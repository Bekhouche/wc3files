"""Source-level checks over the whole package.

Every module is compiled from its text with warnings turned into errors. An
import alone does not catch a SyntaxWarning: once a module is cached as .pyc,
Python no longer recompiles it and the warning is never shown again. That is
how the invalid escapes in table/sylk.py (issue #1) went unnoticed."""
import os
import unittest
import warnings

import _helpers

_PACKAGE = os.path.join(_helpers._SRC, 'wc3files')


def _sources():
    for root, dirs, files in os.walk(_PACKAGE):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(root, name)


class TestSourceCompiles(unittest.TestCase):
    def test_every_module_compiles_without_warnings(self):
        failures = []
        for path in _sources():
            with open(path, encoding='utf-8') as f:
                text = f.read()
            with warnings.catch_warnings():
                warnings.simplefilter('error')
                try:
                    compile(text, path, 'exec')
                except (SyntaxError, SyntaxWarning) as exc:
                    failures.append(f'{os.path.relpath(path, _PACKAGE)}: {exc}')
        self.assertEqual(failures, [])


class TestSylkFormatKeys(unittest.TestCase):
    def test_currency_formats_keep_their_backslashes(self):
        # Excel escapes a literal space and parenthesis with a backslash in
        # these number formats. Writing the keys as raw strings must not change
        # them, or a SYLK file using one would lose its 'float' type.
        from wc3files.table.sylk import SYLK
        self.assertEqual(SYLK.knownformats['"$"#,##0\\ ;;\\("$"#,##0\\,'], 'float')
        self.assertEqual(SYLK.knownformats['"$"#,##0.00\\ ;;\\("$"#,##0.00\\,'], 'float')


if __name__ == '__main__':
    unittest.main()
