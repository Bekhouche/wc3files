"""The MCP server (#179) through the MCP client of the `mcp` package: in
process, and as `python -m wc3files.mcp` over stdio. Skipped when `mcp` is
not installed (pip install "wc3files[mcp]"); CI installs it."""
import asyncio
import base64
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

TOOLS = ['check_script', 'compare_maps', 'compile_script', 'game_file', 'game_files',
         'game_image', 'list_files', 'list_members', 'map_image', 'map_info',
         'map_overview', 'map_records', 'missing_files', 'model_info', 'object_data',
         'placed_objects', 'read_member', 'replay_actions', 'replay_info', 'run_script',
         'trigger_text']
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Tools that return an image, which has no output schema.
IMAGE_TOOLS = ('game_image', 'map_image')
# A script with a compile error, which compile_script returns as data.
BROKEN = 'function F takes nothing returns nothing\ncall\nendfunction\n'


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
        for name in ('sample.w3x', 'sample.mdx', 'sample.w3u', 'sample.j'):
            shutil.copy(sample_path(name), cls.root)
        from wc3files.w3x.w3x_file import W3xFile
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        files = dict(archive.files)
        with open(sample_path('sample.w3e'), 'rb') as handle:
            files['war3map.w3e'] = handle.read()
        archive.build_from_files(files, os.path.join(cls.root, 'terrain.w3x'))
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

    def test_tool_definitions(self):
        # Every tool says it only reads, has a title, and describes every
        # parameter, so a client can choose and call it (#265).
        async def go():
            async with Client(self.server) as client:
                return (await client.list_tools()).tools
        for tool in _run(go()):
            with self.subTest(tool=tool.name):
                notes = tool.annotations
                self.assertTrue(notes.read_only_hint)
                self.assertFalse(notes.destructive_hint)
                self.assertTrue(notes.idempotent_hint)
                self.assertFalse(notes.open_world_hint)
                self.assertTrue(tool.title)
                self.assertNotIn('\n', tool.description)
                self.assertTrue(tool.description.endswith('gives the same result.'))
                self.assertTrue(tool.description.split()[0][0].isupper())
                properties = tool.input_schema['properties']
                self.assertTrue(properties)
                for name, schema in properties.items():
                    self.assertTrue(schema.get('description'), name)

    def test_structured_results_equal_the_functions(self):
        # Every tool publishes an output schema, and its structured result is
        # exactly what the function in tools.py returns: a key missing from
        # the schema would be dropped without an error (#268).
        from wc3files.mcp import tools as T
        roots = T.Roots([self.root])
        calls = [
            ('list_files', {}, lambda: T.list_files(roots)),
            ('map_info', {'path': 'sample.w3x'}, lambda: T.map_info(roots, 'sample.w3x')),
            ('list_members', {'path': 'sample.w3x'}, lambda: T.list_members(roots, 'sample.w3x')),
            ('read_member', {'path': 'sample.w3x', 'member': 'war3map.w3i'},
             lambda: T.read_member(roots, 'sample.w3x', 'war3map.w3i')),
            ('read_member', {'path': 'sample.w3x', 'member': 'war3map.j'},
             lambda: T.read_member(roots, 'sample.w3x', 'war3map.j')),
            ('object_data', {'path': 'sample.w3u'}, lambda: T.object_data(roots, 'sample.w3u')),
            ('trigger_text', {'path': 'sample.w3x', 'names_only': True},
             lambda: T.trigger_text(roots, 'sample.w3x', names_only=True)),
            ('trigger_text', {'path': 'sample.w3x'}, lambda: T.trigger_text(roots, 'sample.w3x')),
            ('check_script', {'path': 'sample.j'}, lambda: T.check_script(roots, 'sample.j')),
            ('compile_script', {'path': 'sample.j'}, lambda: T.compile_script(roots, path='sample.j')),
            ('compile_script', {'code': BROKEN}, lambda: T.compile_script(roots, code=BROKEN)),
            ('compare_maps', {'old': 'sample.w3x', 'new': 'sample.w3x'},
             lambda: T.compare_maps(roots, 'sample.w3x', 'sample.w3x')),
            ('run_script', {'path': 'sample.w3x', 'seconds': 0},
             lambda: T.run_script(roots, 'sample.w3x', seconds=0)),
            ('model_info', {'path': 'sample.mdx'}, lambda: T.model_info(roots, 'sample.mdx')),
            ('map_overview', {'path': 'sample.w3x'}, lambda: T.map_overview(roots, 'sample.w3x')),
            ('placed_objects', {'path': 'sample.w3x'},
             lambda: T.placed_objects(roots, 'sample.w3x')),
            ('map_records', {'path': 'sample.w3x', 'kind': 'imports'},
             lambda: T.map_records(roots, 'sample.w3x', 'imports')),
            ('map_records', {'path': 'sample.w3x', 'kind': 'strings'},
             lambda: T.map_records(roots, 'sample.w3x', 'strings')),
        ]

        async def go():
            async with Client(self.server) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}
                return tools, [await client.call_tool(name, args) for name, args, _ in calls]
        tools, results = _run(go())
        for tool in tools.values():
            if tool.name in IMAGE_TOOLS:
                continue                  # an image has no output schema
            with self.subTest(schema=tool.name):
                self.assertEqual(tool.output_schema.get('type'), 'object')
                self.assertTrue(tool.output_schema.get('properties'))
        self.assertEqual(set(tools['object_data'].input_schema['properties']['object_type']['enum']),
                         {'', 'w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q'})
        for (name, args, expected), result in zip(calls, results):
            with self.subTest(call=name, args=args):
                self.assertFalse(result.is_error, result.content)
                self.assertEqual(result.structured_content, json.loads(json.dumps(expected())))

    def test_map_image(self):
        async def go():
            async with Client(self.server) as client:
                return await client.call_tool('map_image', {'path': 'terrain.w3x'})
        result = _run(go())
        self.assertFalse(result.is_error, result.content)
        text, image = result.content
        self.assertTrue(json.loads(text.text)['source'].startswith('rendered from war3map.w3e'))
        self.assertEqual((image.type, image.mime_type), ('image', 'image/png'))
        self.assertTrue(base64.b64decode(image.data).startswith(b'\x89PNG'))

    def test_edit_tools_only_with_allow_write(self):
        # Without --allow-write no edit tool is listed, so the server stays
        # read-only; with it, edit_objects is listed as writing and writes a
        # copy (#272).
        from wc3files.mcp import build_server
        writable = build_server([self.root], allow_write=True)

        async def go():
            async with Client(self.server) as client:
                plain_names = {t.name for t in (await client.list_tools()).tools}
            async with Client(writable) as client:
                tools = {t.name: t for t in (await client.list_tools()).tools}
                result = await client.call_tool('edit_objects', {
                    'path': 'sample.w3x', 'object_type': 'w3u', 'out': 'server-edit.w3x',
                    'changes': [{'id': 'h001', 'base': 'hfoo', 'set': {'goldcost': 175}}]})
                return plain_names, tools, result
        plain_names, tools, result = _run(go())
        self.assertEqual(sorted(plain_names), TOOLS)
        self.assertNotIn('edit_objects', plain_names)
        notes = tools['edit_objects'].annotations
        self.assertFalse(notes.read_only_hint)
        self.assertTrue(notes.destructive_hint)
        self.assertFalse(notes.idempotent_hint)
        for name in TOOLS:
            self.assertTrue(tools[name].annotations.read_only_hint, name)
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.structured_content['created'], ['h001'])
        self.assertTrue(os.path.exists(os.path.join(self.root, 'server-edit.w3x')))

    def test_the_command_takes_allow_write(self):
        from wc3files.mcp.server import build_parser
        self.assertFalse(build_parser().parse_args(['--root', '.']).allow_write)
        self.assertTrue(build_parser().parse_args(['--root', '.', '--allow-write']).allow_write)

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
