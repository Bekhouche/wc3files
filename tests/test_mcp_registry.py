"""server.json describes wc3files-mcp for the official MCP Registry (#195).

The registry refuses a publish whose package version does not exist on
PyPI, or whose PyPI description lacks the `mcp-name:` line, so these keep
server.json, version.py and the README in agreement.
"""
import json
import os
import re
import unittest

from wc3files.mcp.server import build_parser
from wc3files.version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_JSON = os.path.join(ROOT, 'server.json')


@unittest.skipUnless(os.path.exists(SERVER_JSON), 'server.json is only in the repository')
class TestServerJson(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(SERVER_JSON, encoding='utf-8') as f:
            cls.server = json.load(f)
        cls.package = cls.server['packages'][0]

    def test_versions_match_the_package(self):
        self.assertEqual(self.server['version'], __version__)
        self.assertEqual(self.package['version'], __version__)
        self.assertEqual(self.package['runtimeArguments'][0]['value'],
                         f'wc3files[mcp]=={__version__}')

    def test_registry_limits(self):
        self.assertRegex(self.server['name'], r'^io\.github\.Bekhouche/[a-zA-Z0-9._-]+$')
        self.assertLessEqual(len(self.server['description']), 100)
        self.assertEqual(self.package['registryType'], 'pypi')
        self.assertEqual(self.package['identifier'], 'wc3files')
        self.assertEqual(self.package['transport'], {'type': 'stdio'})

    def test_readme_names_the_server(self):
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        names = re.findall(r'mcp-name: (\S+?)(?:\s|-->)', readme)
        self.assertEqual(names, [self.server['name']])

    def test_arguments_are_the_server_options(self):
        self.assertEqual(self.package['runtimeArguments'][1]['value'], 'wc3files-mcp')
        option = self.package['packageArguments'][0]
        self.assertEqual(option['name'], '--root')
        self.assertTrue(option['isRequired'] and option['isRepeated'])
        args = build_parser().parse_args(['--root', 'a', '--root', 'b'])
        self.assertEqual(args.root, ['a', 'b'])
        # --allow-write (#272) is listed as an optional flag the server has.
        flag = self.package['packageArguments'][1]
        self.assertEqual((flag['name'], flag['isRequired']), ('--allow-write', False))
        self.assertTrue(build_parser().parse_args(['--root', 'a', '--allow-write']).allow_write)


if __name__ == '__main__':
    unittest.main()
