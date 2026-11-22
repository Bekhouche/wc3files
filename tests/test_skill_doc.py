"""Keeps .claude/skills/wc3files/SKILL.md in step with the code.

The skill is what a new session reads first, and it had drifted (issue #6):
it described w3g and w3i as they were months earlier and did not mention
eight packages. These checks fail when a package is added without an entry,
or when a path, class or CLI command the skill names no longer exists."""
import contextlib
import importlib
import io
import os
import pkgutil
import re
import unittest

import _helpers

_ROOT = os.path.dirname(_helpers._SRC)
_SKILL = os.path.join(_ROOT, '.claude', 'skills', 'wc3files', 'SKILL.md')
_PACKAGE = os.path.join(_helpers._SRC, 'wc3files')

# Class names the skill uses, recognised by suffix; anything else in
# backticks (fields, flags, file names) is not checked.
_CLASS = re.compile(r'[A-Z][A-Za-z0-9]+(File|Storage|Compiler|Runtime|Writer)')


@unittest.skipUnless(os.path.isfile(_SKILL), 'no .claude/skills/wc3files/SKILL.md')
class TestSkillDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(_SKILL, encoding='utf-8') as f:
            cls.text = f.read()
        cls.ticks = set(re.findall(r'`([^`\n]+)`', cls.text))

    def test_every_package_has_an_entry(self):
        packages = sorted(name for name in os.listdir(_PACKAGE)
                          if os.path.isfile(os.path.join(_PACKAGE, name, '__init__.py')))
        missing = [name for name in packages
                   if f'`src/wc3files/{name}/' not in self.text]
        self.assertEqual(missing, [])

    def test_named_paths_exist(self):
        paths = sorted(t for t in self.ticks if t.startswith('src/wc3files/'))
        self.assertTrue(paths)
        missing = [p for p in paths
                   if not os.path.exists(os.path.join(_ROOT, p.rstrip('/')))]
        self.assertEqual(missing, [])

    def test_named_classes_exist(self):
        import wc3files
        defined = set()
        for info in pkgutil.walk_packages(wc3files.__path__, 'wc3files.'):
            try:
                module = importlib.import_module(info.name)
            except Exception:
                continue
            defined.update(vars(module))
        named = sorted(t for t in self.ticks if _CLASS.fullmatch(t))
        self.assertTrue(named)
        self.assertEqual([name for name in named if name not in defined], [])

    def test_named_cli_commands_exist(self):
        main = importlib.import_module('wc3files.cli.main')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                main.main(['--help'])
        commands = set(re.search(r'\{([^}]+)\}', out.getvalue()).group(1).split(','))
        named = set(re.findall(r'CLI `([a-z0-9-]+)`', self.text))
        named |= set(re.findall(r'`wc3files ([a-z][a-z0-9-]*)', self.text))
        named |= {t for t in self.ticks if re.fullmatch(r'(casc|replay)-[a-z]+', t)}
        self.assertTrue(named)
        self.assertEqual(sorted(named - commands), [])


if __name__ == '__main__':
    unittest.main()
