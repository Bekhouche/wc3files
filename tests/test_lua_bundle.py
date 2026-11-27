import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.scripts.lua_bundle import BundleError, bundle, collect, insert_bundle
from wc3files.scripts.wc3_runtime import run_map_script

SHOW = 'print('

FILES = {
    'main.lua': ('local util = require("lib.util")\n'
                 'local lib = require "lib"\n'
                 'require("lib.util")\n'
                 f'{SHOW}util.name .. " " .. lib.twice(21))\n'),
    'lib/init.lua': ('local util = require("lib.util")\n'
                     'local M = {}\n'
                     'function M.twice(x) return util.add(x, x) end\n'
                     'return M\n'),
    'lib/util.lua': (f'{SHOW}"util loaded as " .. ...)\n'
                     'return {name = "util", add = function(a, b) return a + b end}\n'),
}

MAP_SCRIPT = ('gg_trg_A = nil\n'
              'function InitGlobals()\n'
              'end\n\n'
              'function main()\n'
              '    InitGlobals()\n'
              f'    {SHOW}"main")\n'
              'end\n\n'
              'function config()\n'
              'end\n')


def write_project(folder, files=FILES):
    for name, text in files.items():
        path = os.path.join(folder, *name.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)


class TestLuaBundle(unittest.TestCase):
    def test_collect_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_project(tmp)
            modules, dynamic = collect('main', tmp)
            self.assertEqual([m[0] for m in modules], ['main', 'lib.util', 'lib'])
            self.assertEqual(dynamic, 0)
            self.assertEqual(collect(os.path.join(tmp, 'lib', 'init.lua'), tmp)[0][0][0], 'lib')

    def test_bundle_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_project(tmp)
            text, report = bundle('main', tmp, run=False)
            script = insert_bundle(MAP_SCRIPT, text, report['entry'])
            run = run_map_script(script)
            self.assertEqual(run.errors, [])
            # Each module runs once, with its name as `...`, after main's own
            # code: the call is put at the end of main.
            self.assertEqual(run.messages, ['main', 'util loaded as lib.util', 'util 42'])
            loaded = insert_bundle(MAP_SCRIPT, text, report['entry'], at='load')
            self.assertEqual(run_map_script(loaded).messages,
                             ['util loaded as lib.util', 'util 42', 'main'])

    def test_insert_again_keeps_generated_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_project(tmp)
            text, report = bundle('main', tmp, run=False)
            once = insert_bundle(MAP_SCRIPT, text, report['entry'])
            twice = insert_bundle(once, text, report['entry'])
            self.assertEqual(once, twice)
            self.assertEqual(twice.count('require("main")'), 1)
            self.assertTrue(twice.endswith(MAP_SCRIPT.replace(
                '"main")\nend', '"main")\n    require("main") --[[ wc3files bundle run ]]\nend')))

    def test_missing_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_project(tmp, {'main.lua': 'require("nope")\nrequire(name)\n'})
            with self.assertRaises(BundleError) as caught:
                bundle('main', tmp)
            self.assertIn("module 'nope' (required by main) not found", str(caught.exception))
            write_project(tmp, {'main.lua': 'local m = require(name)\n'})
            self.assertEqual(bundle('main', tmp)[1]['dynamic requires'], 1)

    def test_cli(self):
        from wc3files.w3x.modes import _write_map
        from wc3files.w3x.w3x_file import read_map
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'src')
            write_project(src)
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3map.lua': MAP_SCRIPT.encode('utf-8'),
                        'war3map.w3i': b'kept'}, source)
            out = os.path.join(tmp, 'out.w3x')
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(['lua-bundle', os.path.join(src, 'main.lua'),
                                       '--map', source, '-o', out]), 0)
            self.assertIn('bundled 3 module(s)', err.getvalue())
            files = read_map(out).files
            self.assertEqual(files['war3map.w3i'], b'kept')
            script = files['war3map.lua'].decode('utf-8')
            self.assertEqual(run_map_script(script).messages[-1], 'util 42')
            alone = io.StringIO()
            with redirect_stdout(alone), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['lua-bundle', 'main', '--root', src]), 0)
            self.assertTrue(alone.getvalue().rstrip().endswith('--[[ wc3files bundle end ]]'))
            # The checker runs on the result.
            write_project(src, {'main.lua': 'UndefinedThing()\n'})
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['lua-bundle', 'main', '--root', src]), 1)


if __name__ == '__main__':
    unittest.main()
