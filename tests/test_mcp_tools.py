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

    def _full_map(self):
        """The sample map with real placed units and doodads, regions,
        cameras, sounds and imports, from the format samples (#271)."""
        path = os.path.join(self.root, 'full.w3x')
        if not os.path.exists(path):
            archive = W3xFile()
            archive.read(sample_path('sample.w3x'))
            files = dict(archive.files)
            for member, sample in (('war3mapUnits.doo', 'sample.doo'),
                                   ('war3map.doo', 'sample_doodads_v8_11.doo'),
                                   ('war3map.w3r', 'sample.w3r'), ('war3map.w3c', 'sample.w3c'),
                                   ('war3map.w3s', 'sample.w3s'), ('war3map.imp', 'sample.imp')):
                with open(sample_path(sample), 'rb') as handle:
                    files[member] = handle.read()
            files['(listfile)'] = '\r\n'.join(n for n in files if n != '(listfile)').encode()
            archive.build_from_files(files, path)
        return path

    def test_placed_objects(self):
        path = self._full_map()
        units = T.placed_objects(self.roots, path, 'units')
        self.assertEqual(units['total'], 2)
        unit = units['objects'][0]
        self.assertEqual(unit['id'], 'hfoo')
        self.assertTrue(0 <= unit['facing'] < 360)
        self.assertEqual(len(unit['scale']), 3)
        self.assertIn('owner', unit)
        doodads = T.placed_objects(self.roots, path, 'doodads', limit=5)
        self.assertEqual((doodads['total'], len(doodads['objects']), doodads['truncated']),
                         (54, 5, True))
        first = doodads['objects'][0]['id']
        same = T.placed_objects(self.roots, path, 'doodads', id=first)
        self.assertTrue(same['total'] >= 1)
        self.assertTrue(all(o['id'] == first for o in same['objects']))
        self.assertEqual(T.placed_objects(self.roots, path, 'units', owner=99)['total'], 0)
        with self.assertRaises(ValueError):
            T.placed_objects(self.roots, path, 'trees')

    def test_map_records(self):
        path = self._full_map()
        for kind in ('regions', 'cameras', 'sounds', 'imports'):
            with self.subTest(kind=kind):
                records = T.map_records(self.roots, path, kind)['records']
                self.assertTrue(records)
                self.assertIsInstance(records[0], dict)
        for region in T.map_records(self.roots, path, 'regions')['records']:
            self.assertNotIn('\x00', region.get('weather_effect') or '')
        strings = T.map_records(self.roots, self.map, 'strings')
        self.assertEqual(strings['member'], 'war3map.wts')
        self.assertNotIn('terrain', T.map_records(self.roots, self.map, 'terrain'))
        with self.assertRaises(ValueError):
            T.map_records(self.roots, path, 'units')

    def test_map_overview(self):
        overview = T.map_overview(self.roots, self._full_map())
        self.assertEqual(overview['name'], 'Sample Map')
        counts = overview['counts']
        self.assertEqual((counts['units'], counts['doodads']), (2, 54))
        for kind in ('regions', 'cameras', 'sounds', 'imports'):
            self.assertTrue(counts[kind], kind)
        # The plain sample map keeps unit records in war3map.doo, which is
        # not a doodad file: the count is null, not an error.
        self.assertIsNone(T.map_overview(self.roots, self.map)['counts']['doodads'])

    def test_a_protected_map_is_refused(self):
        for tool in (T.map_info, T.list_members, T.check_script, T.trigger_text,
                     T.map_overview, T.placed_objects,
                     lambda roots, path: T.read_member(roots, path, 'war3map.w3i'),
                     lambda roots, path: T.map_records(roots, path, 'regions')):
            with self.subTest(tool=getattr(tool, '__name__', 'lambda')):
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


class TestCompileScript(ToolTestCase):
    """compile_script (#334)."""

    def test_vjass_code(self):
        code = ('library Hello initializer Init\n'
                '    private function Init takes nothing returns nothing\n'
                '        call BJDebugMsg("hello")\n'
                '    endfunction\n'
                'endlibrary\n')
        result = T.compile_script(self.roots, code=code)
        self.assertTrue(result['ok'], result)
        self.assertEqual((result['language'], result['errors']), ('vjass', []))
        self.assertIn('function Hello__Init takes nothing returns nothing', result['jass'])
        self.assertEqual(T.compile_script(self.roots, code=code, include_jass=False)['jass'], '')

    def test_compile_error_is_data(self):
        result = T.compile_script(self.roots, code='function F takes nothing returns nothing\n'
                                                   '    call\nendfunction\n')
        self.assertFalse(result['ok'])
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual(result['errors'][0]['line'], 2)
        self.assertEqual(result['jass'], '')

    def test_check_findings_and_leaks(self):
        result = T.compile_script(self.roots, code=(
            'function F takes nothing returns nothing\n'
            '    call KillUnit(1, 2)\n'
            '    call AddSpecialEffect("a.mdl", 0, 0)\n'
            'endfunction\n'), language='jass')
        self.assertFalse(result['ok'])
        self.assertEqual(result['counts'], {'issue': 1, 'warning': 1})
        self.assertEqual(set(result['findings'][0]), {'severity', 'message', 'line'})

    def test_other_languages_and_paths(self):
        cjass = T.compile_script(self.roots, code='void Hi() { BJDebugMsg("hi") }',
                                 language='cjass')
        self.assertTrue(cjass['ok'], cjass)
        self.assertIn('function Hi takes nothing returns nothing', cjass['jass'])
        luck = T.compile_script(self.roots, code='nothing Hi():\n    print(1 + 2)\n',
                                language='luck')
        self.assertIn('call BJDebugMsg(I2S((1 + 2)))', luck['jass'])
        zinc = T.compile_script(self.roots, code=(
            'library Z {\n    public function Hi() { BJDebugMsg("z"); }\n}\n'),
            language='zinc')
        self.assertTrue(zinc['ok'], zinc)
        folder = os.path.join(self.root, 'luck')
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'a.luck'), 'w', encoding='utf-8') as handle:
            handle.write('nothing A():\n    B()\n')
        with open(os.path.join(folder, 'b.luck'), 'w', encoding='utf-8') as handle:
            handle.write('nothing B():\n    print("b")\n')
        project = T.compile_script(self.roots, path='luck')
        self.assertEqual((project['language'], project['ok']), ('luck', True))
        # sample.j passes Foo, which takes arguments, as code: JassHelper's
        # error, with its line; checked as plain JASS it compiles.
        errors = T.compile_script(self.roots, path='sample.j')['errors']
        self.assertEqual((len(errors), errors[0]['line']), (1, 23))
        self.assertIn('cannot be used as code', errors[0]['message'])
        self.assertTrue(T.compile_script(self.roots, path='sample.j', language='jass')['jass'])
        self.assertEqual(T.compile_script(self.roots, path='sample.w3x')['errors'], [])
        for bad in ({}, {'code': 'x', 'path': 'sample.j'}, {'code': 'x', 'language': 'cobol'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                T.compile_script(self.roots, **bad)
        with self.assertRaises(PermissionError):
            T.compile_script(self.roots, path=os.path.dirname(self.root))


class TestRunScript(ToolTestCase):
    """run_script (#335)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lua = os.path.join(cls.root, 'war3map.lua')
        with open(cls.lua, 'w', encoding='utf-8') as handle:
            handle.write('function main()\n'
                         '    print("started")\n'
                         '    TimerStart(CreateTimer(), 2.0, false, function()\n'
                         '        print("two seconds")\n'
                         '        MissingFunction()\n'
                         '    end)\n'
                         'end\n'
                         'function config() end\n')

    def test_lua(self):
        result = T.run_script(self.roots, 'war3map.lua', seconds=5)
        self.assertFalse(result['ok'])
        self.assertEqual(result['messages'], ['started', 'two seconds'])
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('MissingFunction', result['errors'][0])
        self.assertGreaterEqual(result['callbacks'], 1)
        early = T.run_script(self.roots, 'war3map.lua', seconds=1)
        self.assertEqual((early['ok'], early['messages']), (True, ['started']))

    def test_jass_map_and_limits(self):
        result = T.run_script(self.roots, 'sample.w3x', seconds=0)
        self.assertEqual(result['script'], 'war3map.j (as Lua)')
        loop = os.path.join(self.root, 'loop.lua')
        with open(loop, 'w', encoding='utf-8') as handle:
            handle.write('function main() while true do end end\n')
        stopped = T.run_script(self.roots, 'loop.lua', max_steps=10_000)
        self.assertIsNotNone(stopped['stopped'])
        for bad in ({'seconds': -1}, {'seconds': 601}, {'players': 0}, {'max_steps': 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                T.run_script(self.roots, 'war3map.lua', **bad)
        with self.assertRaises(ProtectedMapError):
            T.run_script(self.roots, self.protected)


class TestCompareMaps(ToolTestCase):
    """compare_maps (#336), on the two versions test_translation.py builds."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from test_translation import NEW, OLD
        from wc3files.w3x.modes import _write_map
        _write_map(OLD, os.path.join(cls.root, 'v1.w3x'))
        _write_map(NEW, os.path.join(cls.root, 'v2.w3x'))

    def test_all_parts(self):
        result = T.compare_maps(self.roots, 'v1.w3x', 'v2.w3x')
        self.assertEqual(result['summary'], {
            'members': {'added': 0, 'removed': 0, 'changed': 3},
            'objects': {'added': 1, 'removed': 0, 'changed': 1},
            'strings': {'added': 2, 'removed': 0, 'changed': 1, 'unchanged': 11}})
        self.assertEqual([m['name'] for m in result['members']['changed']],
                         ['war3map.j', 'war3map.w3u', 'war3map.wts'])
        self.assertEqual(result['objects']['added'],
                         [{'type': 'w3u', 'id': 'h001', 'base': 'hkni'}])
        self.assertEqual(result['objects']['changed'][0]['fields'],
                         [{'field': 'unam', 'old': 'TRIGSTR_001', 'new': 'TRIGSTR_007'}])
        self.assertEqual(result['strings']['changed'],
                         [{'key': 'script/Trig_Intro_Actions/1', 'old': 'Welcome!',
                           'new': 'Welcome, hero!'}])

    def test_one_part_and_refusals(self):
        strings = T.compare_maps(self.roots, 'v1.w3x', 'v2.w3x', part='strings')
        self.assertEqual(set(strings), {'old', 'new', 'strings', 'summary'})
        same = T.compare_maps(self.roots, 'v1.w3x', 'v1.w3x')
        self.assertEqual(same['members']['changed'] + same['objects']['changed'], [])
        with self.assertRaises(ValueError):
            T.compare_maps(self.roots, 'v1.w3x', 'v2.w3x', part='terrain')
        with self.assertRaises(ValueError):
            T.compare_maps(self.roots, 'v1.w3x', 'v2.w3x', max_chars=50)
        with self.assertRaises(ProtectedMapError):
            T.compare_maps(self.roots, 'v1.w3x', self.protected)


class TestMapImage(ToolTestCase):
    """map_image (#337)."""

    def test_rendered_and_own(self):
        from wc3files.img import BlpFile, read_png
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        files = dict(archive.files)
        with open(sample_path('sample.w3e'), 'rb') as handle:
            files['war3map.w3e'] = handle.read()
        archive.build_from_files(files, os.path.join(self.root, 'terrain.w3x'))
        rendered = T.map_image(self.roots, 'terrain.w3x')
        self.assertEqual((rendered['width'], rendered['height']), (256, 256))
        # the sample's war3map.doo is not a doodad file: drawn without doodads
        self.assertTrue(rendered['source'].startswith(
            'rendered from war3map.w3e (without doodads: war3map.doo does not read'))
        self.assertEqual(read_png(rendered['png'])[:2], (256, 256))
        with self.assertRaises(ValueError):           # no terrain to draw from
            T.map_image(self.roots, 'sample.w3x')
        # a map with its own minimap gives that one, and render redraws it
        files['war3mapMap.blp'] = BlpFile.from_rgba(64, 64, bytes([10, 200, 30, 255]) * 4096,
                                                    mipmaps=False).to_bytes()
        own = os.path.join(self.root, 'own.w3x')
        archive.build_from_files(files, own)
        minimap = T.map_image(self.roots, 'own.w3x')
        self.assertEqual((minimap['source'], minimap['width']), ('war3mapMap.blp', 64))
        width, height, rgba = read_png(minimap['png'])[:3]
        self.assertEqual(bytes(rgba[:4])[:3], bytes([10, 200, 30]))
        self.assertEqual(T.map_image(self.roots, 'own.w3x', 'preview')['source'],
                         'war3mapMap.blp')
        self.assertTrue(T.map_image(self.roots, 'own.w3x', render=True)['source'].startswith(
            'rendered from war3map.w3e'))

    def test_refusals(self):
        with self.assertRaises(ValueError):
            T.map_image(self.roots, 'sample.w3x', kind='loading')
        with self.assertRaises(ValueError):
            T.map_image(self.roots, 'sample.j')
        with self.assertRaises(ProtectedMapError):
            T.map_image(self.roots, self.protected)
