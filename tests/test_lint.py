"""pyflakes finds nothing in src/ apart from the package __init__ re-exports,
which are deliberate star imports (#148). Skipped where pyflakes is not
installed; CI installs it."""
import os
import unittest

import _helpers  # noqa: F401

try:
    from pyflakes import api, reporter
except ImportError:  # pragma: no cover
    api = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src', 'wc3files')


class _Collect(reporter.Reporter if api else object):
    def __init__(self):
        self.messages = []

    def unexpectedError(self, filename, message):
        self.messages.append(f'{filename}: {message}')

    def syntaxError(self, filename, message, lineno, offset, text):
        self.messages.append(f'{filename}:{lineno}: {message}')

    def flake(self, message):
        self.messages.append(str(message))


@unittest.skipIf(api is None, 'pyflakes is not installed')
class TestPyflakes(unittest.TestCase):

    def test_src_is_clean(self):
        collect = _Collect()
        paths = []
        for folder, _dirs, files in os.walk(SRC):
            for name in files:
                if name.endswith('.py') and name != '__init__.py':
                    paths.append(os.path.join(folder, name))
        for path in sorted(paths):
            api.checkPath(path, collect)
        self.assertEqual([os.path.relpath(m, ROOT) if os.path.isabs(m.split(':')[0]) else m
                          for m in collect.messages], [])


if __name__ == '__main__':
    unittest.main()
