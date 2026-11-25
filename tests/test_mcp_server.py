"""The MCP server (#179) through the MCP client of the `mcp` package: in
process, and as `python -m wc3files.mcp` over stdio. Skipped when `mcp` is
not installed (pip install "wc3files[mcp]"); CI installs it."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path

try:
    from mcp import Client, StdioServerParameters
except ImportError:  # pragma: no cover
    Client = None

TOOLS = ['check_script', 'list_files', 'list_members', 'map_info', 'model_info',
         'object_data', 'read_member', 'replay_info', 'trigger_text']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(coroutine):
    return asyncio.run(coroutine)


def _json(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


@unittest.skipIf(Client is None, 'the mcp package is not installed')
class TestMcpServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from wc3files.mcp import build_server
        cls.root = tempfile.mkdtemp()
        shutil.copy(sample_path('sample.w3x'), cls.root)
        cls.server = build_server([cls.root])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_tools_and_a_call(self):
        async def go():
            async with Client(self.server) as client:
                names = sorted(tool.name for tool in (await client.list_tools()).tools)
                info = await client.call_tool('map_info', {'path': 'sample.w3x'})
                return names, info
        names, info = _run(go())
        self.assertEqual(names, TOOLS)
        self.assertFalse(info.is_error)
        self.assertEqual(_json(info)['name'], 'Sample Map')

    def test_an_expected_failure_reaches_the_client_with_its_message(self):
        async def go():
            async with Client(self.server) as client:
                return await client.call_tool('map_info', {'path': sample_path('sample.w3x')})
        result = _run(go())
        self.assertTrue(result.is_error)
        self.assertIn('outside the folders this server may read', result.content[0].text)

    def test_stdio(self):
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(
            [os.path.join(ROOT, 'src')] + ([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
        params = StdioServerParameters(command=sys.executable,
                                       args=['-m', 'wc3files.mcp', '--root', self.root], env=env)

        async def go():
            async with Client(params) as client:
                return await client.call_tool('list_members', {'path': 'sample.w3x'})
        result = _run(go())
        self.assertFalse(result.is_error)
        self.assertIn('war3map.w3i', [m['name'] for m in _json(result)['members']])


class TestWithoutMcp(unittest.TestCase):

    def test_the_tools_import_without_mcp(self):
        import wc3files.mcp.tools  # noqa: F401

    def test_the_command_needs_a_root(self):
        from wc3files.mcp import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])


if __name__ == '__main__':
    unittest.main()
