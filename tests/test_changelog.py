"""CHANGELOG.md follows Keep a Changelog, and the version being built has its
section, so a release cannot ship without one (#133)."""
import os
import re
import unittest

from wc3files.version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, 'CHANGELOG.md')
HEADING = re.compile(r'^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?$', re.M)
GROUPS = {'Added', 'Changed', 'Deprecated', 'Removed', 'Fixed', 'Security'}


@unittest.skipUnless(os.path.exists(PATH), 'CHANGELOG.md is not next to the tests')
class TestChangelog(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(PATH, encoding='utf-8') as handle:
            cls.text = handle.read()
        cls.sections = HEADING.findall(cls.text)

    def test_unreleased_comes_first(self):
        self.assertEqual(self.sections[0], ('Unreleased', ''))

    def test_current_version_has_a_dated_section(self):
        dated = {name: date for name, date in self.sections[1:]}
        self.assertIn(__version__, dated)
        self.assertTrue(dated[__version__], f'{__version__} has no date')

    def test_versions_are_newest_first(self):
        versions = [tuple(int(p) for p in name.split('.'))
                    for name, _ in self.sections[1:]]
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_every_section_has_a_link(self):
        for name, _ in self.sections:
            with self.subTest(section=name):
                self.assertRegex(self.text, rf'(?m)^\[{re.escape(name)}\]: https://')

    def test_groups_are_the_standard_ones(self):
        for group in re.findall(r'(?m)^### (.+)$', self.text):
            with self.subTest(group=group):
                self.assertIn(group, GROUPS)


if __name__ == '__main__':
    unittest.main()
