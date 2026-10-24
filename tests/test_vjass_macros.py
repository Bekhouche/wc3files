"""Tests for the vJass text-macro preprocessor.

vJass is the dialect JassHelper compiles to plain JASS, and text macros are
the pass that has to run before anything can parse the file. Verified
against every real vJass file found locally: 20 of 20 expand with nothing
left over and no macro called that was never defined, across 43 expansions
(see TODO.md).

This is the preprocessor only - expanding macros leaves vJass, not JASS.
"""
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.scripts.vjass_macros import (MacroError, collect, expand,
                                           strip_comments,
                                           unexpanded_placeholders)

SIMPLE = """
//! textmacro MAKE takes NAME, TYPE
function Get$NAME$ takes nothing returns $TYPE$
    return udg_$NAME$
endfunction
//! endtextmacro

//! runtextmacro MAKE("Gold", "integer")
//! runtextmacro MAKE("Label", "string")
"""


class TestCollect(unittest.TestCase):
    def test_definitions_are_taken_out_of_the_source(self):
        macros, lines, _ = collect(SIMPLE)
        self.assertEqual(sorted(macros), ['MAKE'])
        self.assertEqual(macros['MAKE'].parameters, ['NAME', 'TYPE'])
        text = '\n'.join(lines)
        # The body must not be left behind where it was defined.
        self.assertNotIn('function Get$NAME$', text)
        self.assertIn('runtextmacro MAKE', text)

    def test_an_unclosed_macro_is_refused(self):
        with self.assertRaises(MacroError):
            collect('//! textmacro BROKEN takes A\nsomething\n')

    def test_other_directives_are_reported_not_consumed(self):
        _macros, lines, directives = collect('//! import "Other.j"\nfunction f\n')
        self.assertIn('import', [name for _n, name, _t in directives])
        self.assertIn('//! import "Other.j"', '\n'.join(lines))


class TestExpand(unittest.TestCase):
    def test_arguments_are_substituted_positionally(self):
        text, report = expand(SIMPLE)
        self.assertEqual(report['expansions'], 2)
        self.assertIn('function GetGold takes nothing returns integer', text)
        self.assertIn('function GetLabel takes nothing returns string', text)
        self.assertIn('return udg_Gold', text)

    def test_nothing_is_left_holding_a_placeholder(self):
        text, _ = expand(SIMPLE)
        self.assertEqual(unexpanded_placeholders(text), [])

    def test_a_macro_body_may_run_another(self):
        source = """
//! textmacro INNER takes X
function Inner$X$ takes nothing returns nothing
endfunction
//! endtextmacro
//! textmacro OUTER takes Y
//! runtextmacro INNER("$Y$")
//! endtextmacro
//! runtextmacro OUTER("Deep")
"""
        text, report = expand(source)
        self.assertIn('function InnerDeep', text)
        self.assertGreaterEqual(report['expansions'], 2)

    def test_runaway_recursion_is_stopped(self):
        source = """
//! textmacro LOOP takes X
//! runtextmacro LOOP("$X$")
//! endtextmacro
//! runtextmacro LOOP("a")
"""
        with self.assertRaises(MacroError):
            expand(source, max_depth=8)

    def test_an_undefined_macro_is_reported_not_silently_dropped(self):
        text, report = expand('//! runtextmacro NOPE("x")\n')
        self.assertEqual(report['missing'], ['NOPE'])
        # The call is left in place so nothing disappears unexplained.
        self.assertIn('runtextmacro NOPE', text)

    def test_strict_mode_refuses_an_undefined_macro(self):
        with self.assertRaises(MacroError):
            expand('//! runtextmacro NOPE("x")\n', strict=True)

    def test_an_optional_call_to_a_missing_macro_is_skipped(self):
        # JassHelper treats `runtextmacro optional` as "use it if it exists".
        text, report = expand('//! runtextmacro optional NOPE("x")\nkeep me\n')
        self.assertEqual(report['missing'], [])
        self.assertNotIn('NOPE', text)
        self.assertIn('keep me', text)

    def test_textmacro_once_expands_a_single_time(self):
        source = """
//! textmacro_once ONLY takes A
function Once$A$ takes nothing returns nothing
endfunction
//! endtextmacro
//! runtextmacro ONLY("X")
//! runtextmacro ONLY("X")
"""
        text, report = expand(source)
        self.assertEqual(report['expansions'], 1)
        self.assertEqual(text.count('function OnceX'), 1)

    def test_the_call_sites_indentation_is_applied_to_the_body(self):
        source = """
//! textmacro BODY takes A
call Do$A$()
//! endtextmacro
function f takes nothing returns nothing
    //! runtextmacro BODY("It")
endfunction
"""
        text, _ = expand(source)
        self.assertIn('    call DoIt()', text)

    def test_quoted_arguments_may_contain_commas(self):
        source = """
//! textmacro MSG takes TEXT
call BJDebugMsg("$TEXT$")
//! endtextmacro
//! runtextmacro MSG("hello, world")
"""
        text, _ = expand(source)
        self.assertIn('call BJDebugMsg("hello, world")', text)


class TestPlaceholderScanning(unittest.TestCase):
    def test_documentation_mentioning_a_placeholder_is_not_a_problem(self):
        # A library's own API docs routinely spell out `$TYPE$` to describe
        # what a macro generates; counting that reports a problem where there
        # is none. It was the only such hit across every real file here.
        text = '/* method operator [] takes integer key returns $TYPE$ */\n'
        self.assertEqual(unexpanded_placeholders(text), [])
        self.assertEqual(unexpanded_placeholders(text, ignore_comments=False),
                         ['TYPE'])

    def test_a_real_leftover_in_code_is_still_reported(self):
        self.assertEqual(unexpanded_placeholders('set x = $MISSED$\n'), ['MISSED'])

    def test_both_comment_styles_are_stripped(self):
        self.assertNotIn('$A$', strip_comments('code // $A$'))
        self.assertNotIn('$B$', strip_comments('code /* $B$ */ more'))


if __name__ == '__main__':
    unittest.main()
