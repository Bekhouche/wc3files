"""The tools of the MCP server (#179), called as plain functions: they need
no `mcp` package. test_mcp_server.py calls them through an MCP client."""
import json
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
from wc3files.mcp import tools as T
from wc3files.mpq.data import MpqProtectionLevel
from wc3files.w3x.w3x_file import ProtectedMapError, W3xFile


class ToolTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        for name, folder in (('sample.w3x', ''), ('sample.mdx', 'models'),
                             ('sample.w3u', ''), ('sample.j', ''), ('sample.wtg', '')):
            target = os.path.join(cls.root, folder)
            os.makedirs(target, exist_ok=True)
            shutil.copy(sample_path(name), target)
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        cls.protected = os.path.join(cls.root, 'protected.w3x')
        archive.build_from_files(archive.files, cls.protected,
                                 protection=MpqProtectionLevel.SAFE)
        cls.roots = T.Roots([cls.root])
        cls.map = os.path.join(cls.root, 'sample.w3x')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)


class TestRoots(ToolTestCase):

    def test_paths_outside_the_roots_are_refused(self):
        outside = os.path.dirname(self.root)
        for path in (outside, os.path.join(self.root, '..', 'x.w3x'), sample_path('sample.w3x')):
            with self.subTest(path=path):
                with self.assertRaises(PermissionError):
                    self.roots.resolve(path)

    def test_a_relative_path_is_taken_from_the_first_root(self):
        self.assertEqual(self.roots.resolve('sample.w3x'), os.path.realpath(self.map))

    def test_a_missing_root_is_refused(self):
        with self.assertRaises(ValueError):
            T.Roots([os.path.join(self.root, 'missing')])
        with self.assertRaises(ValueError):
            T.Roots([])


class TestTools(ToolTestCase):

    def test_results_are_json(self):
        for result in (T.list_files(self.roots), T.map_info(self.roots, self.map),
                       T.list_members(self.roots, self.map),
                       T.read_member(self.roots, self.map, 'war3map.w3i'),
                       T.object_data(self.roots, 'sample.w3u'),
                       T.trigger_text(self.roots, self.map),
                       T.check_script(self.roots, self.map),
                       T.model_info(self.roots, 'models/sample.mdx')):
            json.dumps(result, allow_nan=False)

    def test_list_files(self):
        names = sorted(os.path.basename(f['path']) for f in T.list_files(self.roots)['files'])
        self.assertEqual(names, ['protected.w3x', 'sample.mdx', 'sample.w3x'])
        only = T.list_files(self.roots, pattern='SAMPLE.W3')['files']
        self.assertEqual([os.path.basename(f['path']) for f in only], ['sample.w3x'])

    def test_map_info(self):
        info = T.map_info(self.roots, self.map)
        self.assertEqual(info['name'], 'Sample Map')
        self.assertEqual(info['author'], 'wc3files test suite')
        self.assertEqual(info['script_language'], 'jass')
        self.assertEqual(info['players'][0]['name'], 'Player 1')
        self.assertIn(info['players'][0]['type'], T.PLAYER_TYPES.values())
        self.assertEqual(info['members'], 6)

    def test_list_members_reads_sizes_without_unpacking(self):
        members = {m['name']: m for m in T.list_members(self.roots, self.map)['members']}
        archive = W3xFile()
        archive.read(self.map)
        for name, data in archive.files.items():
            if name != '(listfile)':
                self.assertEqual(members[name]['size'], len(data))

    def test_read_member(self):
        w3i = T.read_member(self.roots, self.map, 'war3map.w3i')
        self.assertEqual(w3i['format'], 'w3i')
        self.assertEqual(w3i['data']['map_name'], 'Sample Map')
        script = T.read_member(self.roots, self.map, 'war3map.j')
        self.assertIn('function main', script['text'])
        with self.assertRaises(ValueError):
            T.read_member(self.roots, self.map, 'war3map.missing')

    def test_object_data(self):
        data = T.object_data(self.roots, 'sample.w3u')
        self.assertEqual(data['objects']['w3u']['original']['hfoo'], {'utst': 999})
        only = T.object_data(self.roots, 'sample.w3u', ids=['hfoo'])
        self.assertEqual(list(only['objects']['w3u']['custom']), [])
        with self.assertRaises(ValueError):
            T.object_data(self.roots, 'sample.w3u', object_type='xyz')

    def test_trigger_text(self):
        result = T.trigger_text(self.roots, self.map)
        self.assertEqual(result['triggers'][0]['name'], 'Sample Trigger')
        self.assertEqual(T.trigger_text(self.roots, self.map, names_only=True)['names'],
                         ['Sample Trigger'])
        self.assertEqual(T.trigger_text(self.roots, self.map, name='none such')['triggers'], [])

    def test_check_script(self):
        for path in (self.map, 'sample.j'):
            with self.subTest(path=path):
                result = T.check_script(self.roots, path)
                self.assertEqual(result['language'], 'jass')
                self.assertIsInstance(result['findings'], list)

    def test_model_info(self):
        info = T.model_info(self.roots, 'models/sample.mdx')
        self.assertEqual((info['name'], info['format'], info['sequences']),
                         ('SampleModel', 'mdx', [{'name': 'Stand', 'start': 0, 'end': 1000}]))
        with self.assertRaises(ValueError):
            T.model_info(self.roots, self.map)

    def test_model_info_textures(self):
        # Every texture came back as None: the reader keys it 'path', and
        # model_info read 'image'.
        with open(sample_path('sample.mdl'), encoding='utf-8') as handle:
            text = handle.read()
        text += ('Textures 2 {\n\tBitmap {\n\t\tImage "Units\\\\Human\\\\Footman\\\\Footman.blp",\n'
                 '\t}\n\tBitmap {\n\t\tImage "",\n\t\tReplaceableId 1,\n\t}\n}\n')
        path = os.path.join(self.root, 'models', 'textured.mdl')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)
        info = T.model_info(self.roots, 'models/textured.mdl')
        self.assertEqual(info['textures'], [
            {'path': 'Units\\Human\\Footman\\Footman.blp', 'replaceable_id': 0},
            {'path': '', 'replaceable_id': 1}])

    def test_a_protected_map_is_refused(self):
        for tool in (T.map_info, T.list_members, T.check_script, T.trigger_text,
                     lambda roots, path: T.read_member(roots, path, 'war3map.w3i')):
            with self.subTest(tool=getattr(tool, '__name__', 'read_member')):
                with self.assertRaises(ProtectedMapError):
                    tool(self.roots, self.protected)

    def test_a_large_result_is_refused_with_advice(self):
        with self.assertRaises(ValueError) as caught:
            T.read_member(self.roots, self.map, 'war3map.j', max_chars=50)
        self.assertIn('max_chars', str(caught.exception))

    def test_plain(self):
        self.assertEqual(T.plain({1: (b'\x00', float('nan'), float('inf'))}),
                         {'1': [{'base64': 'AA=='}, 'nan', 'inf']})


if __name__ == '__main__':
    unittest.main()
