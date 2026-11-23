"""docs/cli.md mentions every subcommand the parser defines (#147). The
README counted 28 when there were 35, and two were missing from the page."""
import argparse
import os
import re
import unittest

import _helpers  # noqa: F401
from wc3files.cli.main import build_parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_DOC = os.path.join(ROOT, 'docs', 'cli.md')


def subcommands():
    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    return []


@unittest.skipUnless(os.path.exists(CLI_DOC), 'docs/ is not next to the tests')
class TestCliDocs(unittest.TestCase):

    def test_every_subcommand_is_on_the_cli_page(self):
        with open(CLI_DOC, encoding='utf-8') as handle:
            text = handle.read()
        names = subcommands()
        self.assertGreater(len(names), 30)
        missing = [name for name in names
                   if not re.search(rf'wc3files (--\S+ \S+ )?{re.escape(name)}\b', text)]
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
