"""Tests for the CLI commands that wrap models, scripts and triggers.

Before these, `model-convert`, `model-info`, `jass`, `trigger-text` and
`script-to-triggers` had no CLI at all despite the underlying modules being
among the most complete in the package. These drive `main()` the way a user
would, so a broken argument or dispatch shows up here rather than in use.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.cli.main import main


def run(*argv):
    """Run the CLI, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TestModelCommands(unittest.TestCase):
    def test_model_info_reports_the_real_model(self):
        code, out, _ = run('model-info', sample_path('sample.mdx'))
        self.assertEqual(code, 0)
        self.assertIn('SampleModel', out)
        self.assertIn('mdx', out)

    def test_model_convert_mdx_to_mdl_then_back(self):
        with tempfile.TemporaryDirectory() as directory:
            as_text = os.path.join(directory, 'out.mdl')
            as_binary = os.path.join(directory, 'back.mdx')
            self.assertEqual(run('model-convert', sample_path('sample.mdx'),
                                 as_text)[0], 0)
            self.assertTrue(os.path.getsize(as_text) > 0)
            self.assertEqual(run('model-convert', as_text, as_binary)[0], 0)
            with open(as_binary, 'rb') as handle:
                self.assertEqual(handle.read(4), b'MDLX')

    def test_model_info_json_carries_the_full_data(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'model.json')
            self.assertEqual(run('model-info', sample_path('sample.mdx'),
                                 '--json', '--out', target)[0], 0)
            self.assertTrue(os.path.getsize(target) > 0)


class TestJassCommand(unittest.TestCase):
    def test_a_script_reprints(self):
        code, out, _ = run('jass', sample_path('sample.j'))
        self.assertEqual(code, 0)
        self.assertIn('function Foo', out)
        self.assertIn('endglobals', out)

    def test_lua_reprints_too(self):
        code, out, _ = run('jass', sample_path('sample.lua'))
        self.assertEqual(code, 0)
        self.assertIn('Foo', out)

    def test_obfuscating_with_no_reachable_entry_point_is_refused(self):
        # Silently renaming nothing - or, with --strip, emptying the file -
        # is worse than saying the entry points do not match.
        code, _, err = run('jass', sample_path('sample.j'), '--obfuscate')
        self.assertEqual(code, 1)
        self.assertIn('nothing is reachable', err)
        self.assertIn('--entry', err)

    def test_obfuscating_from_a_named_entry_point_renames(self):
        code, out, err = run('jass', sample_path('sample.j'),
                             '--obfuscate', '--entry', 'Foo')
        self.assertEqual(code, 0)
        self.assertIn('renamed', err)
        self.assertNotIn('function Foo', out)

    def test_a_kept_name_is_left_alone(self):
        code, out, _ = run('jass', sample_path('sample.j'), '--obfuscate',
                           '--entry', 'Foo', '--keep', 'Foo')
        self.assertEqual(code, 0)
        self.assertIn('function Foo', out)

    def test_stripping_lua_is_refused_rather_than_ignored(self):
        code, _, err = run('jass', sample_path('sample.lua'), '--strip')
        self.assertEqual(code, 1)
        self.assertIn('JASS only', err)

    def test_output_can_go_to_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'out.j')
            self.assertEqual(run('jass', sample_path('sample.j'), target)[0], 0)
            with open(target, encoding='utf-8') as handle:
                self.assertIn('function Foo', handle.read())


class TestTriggerText(unittest.TestCase):
    def test_a_trigger_renders_as_editor_text(self):
        code, out, _ = run('trigger-text', sample_path('sample.wtg'))
        self.assertEqual(code, 0)
        self.assertIn('Sample Trigger', out)

    def test_filtering_by_name_finds_it(self):
        code, out, _ = run('trigger-text', sample_path('sample.wtg'),
                           '--name', 'sample')
        self.assertEqual(code, 0)
        self.assertIn('Sample Trigger', out)

    def test_a_name_that_matches_nothing_is_an_error_not_silence(self):
        code, _, err = run('trigger-text', sample_path('sample.wtg'),
                           '--name', 'no such trigger')
        self.assertEqual(code, 1)
        self.assertIn('no trigger matching', err)


class TestScriptToTriggers(unittest.TestCase):
    def test_it_reports_what_it_recovered(self):
        code, out, _ = run('script-to-triggers', sample_path('sample.j'))
        self.assertEqual(code, 0)
        self.assertIn('triggers', out)
        self.assertIn('coverage', out)

    def test_json_output_carries_every_section(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'triggers.json')
            self.assertEqual(run('script-to-triggers', sample_path('sample.j'),
                                 '--json', '--out', target)[0], 0)
            with open(target, encoding='utf-8') as handle:
                data = json.load(handle)
            for key in ('triggers', 'variables', 'regions', 'cameras',
                        'sounds', 'coverage'):
                self.assertIn(key, data)


class TestErrorReporting(unittest.TestCase):
    def test_a_vjass_script_reports_an_error_with_a_hint(self):
        # vJass is a superset this package preprocesses but does not compile;
        # a user hitting it deserves to be told that, not a traceback.
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'vjass.j')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write('function F takes nothing returns nothing\n'
                             '    local Node n = Node.allocate()\n'
                             'endfunction\n')
            code, _, err = run('script-to-triggers', path)
        self.assertEqual(code, 1)
        self.assertIn('vJass', err)

    def test_a_truncated_binary_reports_an_error_with_a_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'cut.wtg')
            with open(sample_path('sample.wtg'), 'rb') as handle:
                data = handle.read()
            with open(path, 'wb') as handle:
                handle.write(data[:len(data) // 2])
            code, _, err = run('trigger-text', path)
        self.assertEqual(code, 1)
        self.assertIn('end of the file', err)


class TestLineNumbersOnRealWorldScripts(unittest.TestCase):
    def test_bare_carriage_returns_do_not_inflate_the_reported_line(self):
        # Real map scripts turn up with runs of bare CR; universal-newline
        # translation turns each into a line break and makes every reported
        # line number useless (one real script: 14,867 lines reported as
        # 2,017,171). The lexer already treats CR as whitespace.
        from wc3files.scripts import ScriptFile, JassLexError
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'crs.j')
            source = ('function F takes nothing returns nothing\n'
                      + '\r' * 5000
                      + '    local integer i = .\n'
                      + 'endfunction\n')
            with open(path, 'w', encoding='utf-8', newline='') as handle:
                handle.write(source)
            with self.assertRaises(JassLexError) as caught:
                ScriptFile().read(path)
        self.assertLess(caught.exception.line, 10)


if __name__ == '__main__':
    unittest.main()
