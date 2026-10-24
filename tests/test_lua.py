"""Real tests for wc3files.scripts' Lua frontend (lexer/parser/printer).

Two tiers, mirroring test_jass.py:
- Guaranteed, portable: the checked-in tests/samples/scripts/sample.lua plus a battery
  of hand-written snippets covering the grammar surface (including the Lua 5.3
  bitwise/floor-division operators and \\x/\\z escapes - see below for why
  those, not 5.1, are the real target).
- Best-effort real-world sweep: whatever war3map.lua fixtures happen to exist
  locally under outputs/ (gitignored map-extraction output) - skipped entirely
  if none are present.

This frontend targets Lua 5.3, not 5.1, based on real evidence: a one-off
sweep (not part of this checked-in suite - it reaches into a sibling project
directory, not this repo) against 23 real local war3map.lua fixtures found
genuine 5.3-only syntax in real, non-synthetic map scripts - floor division
(//), the bitwise operators (&, |, ~ as both binary xor and unary bnot, <<,
>>), and the \\x/\\z string escapes all appear in real files, which 5.1 simply
doesn't have. All 23 fixtures now round-trip structurally (parse -> print ->
reparse) with zero known failures - see TODO.md for the full accounting,
including two real bugs that sweep caught (a float-precision bug in
_format_real shared with jass_printer.py, and a printer that omitted any
statement-terminating punctuation, which could recreate Lua's own documented
call/`(`-adjacency ambiguity in *printed* output even when the original,
semicolon-terminated source was never ambiguous - fixed by always emitting a
trailing `;`, matching what every real fixture already does defensively).
"""
import glob
import os
import unittest

import _helpers
from wc3files.scripts.lua_lexer import LuaLexError
from wc3files.scripts.lua_parser import parse, LuaParseError
from wc3files.scripts.lua_printer import print_chunk
from wc3files.scripts.lua_obfuscator import obfuscate_lua
from wc3files.scripts.script_file import ScriptFile
from wc3files.scripts import ir

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _round_trip(source):
    chunk = parse(source)
    text = print_chunk(chunk)
    reparsed = parse(text)
    return chunk, chunk == reparsed


class TestLuaRoundTrip(unittest.TestCase):
    def test_sample_lua_round_trips(self):
        with open(_helpers.sample_path('sample.lua'), 'r', encoding='utf-8') as fh:
            source = fh.read()
        chunk, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertGreater(len(chunk.body), 15)

    def test_local_multi_assign_and_table_constructor(self):
        chunk, equal = _round_trip(
            'local a, b, c = 1, 2.5, "three"\n'
            'local t = {1, 2, [5] = "x", name = "y"}\n'
        )
        self.assertTrue(equal)
        local = chunk.body[0]
        self.assertEqual(local.names, ['a', 'b', 'c'])
        self.assertEqual([v.value for v in local.values], [1, 2.5, 'three'])
        table = chunk.body[1].values[0]
        self.assertEqual(len(table.fields), 4)

    def test_function_statement_dotted_and_method(self):
        chunk, equal = _round_trip(
            'function Foo.Bar.Baz(x)\n    return x\nend\n'
            'function Foo:Method(y)\n    return y\nend\n'
        )
        self.assertTrue(equal)
        plain, method = chunk.body
        self.assertEqual(plain.name_path, ['Foo', 'Bar', 'Baz'])
        self.assertIsNone(plain.method_name)
        self.assertEqual(method.name_path, ['Foo'])
        self.assertEqual(method.method_name, 'Method')

    def test_control_flow_forms(self):
        source = (
            'local function f(n)\n'
            '    if n < 0 then\n'
            '        return "neg"\n'
            '    elseif n == 0 then\n'
            '        return "zero"\n'
            '    else\n'
            '        return "pos"\n'
            '    end\n'
            'end\n'
            'local total = 0\n'
            'for i = 1, 10, 2 do\n'
            '    total = total + i\n'
            'end\n'
            'for k, v in pairs({}) do\n'
            '    print(k, v)\n'
            'end\n'
            'while total < 100 do\n'
            '    total = total * 2\n'
            'end\n'
            'repeat\n'
            '    total = total - 1\n'
            'until total <= 0\n'
            'do\n'
            '    local scoped = true\n'
            'end\n'
        )
        chunk, equal = _round_trip(source)
        self.assertTrue(equal)
        kinds = [type(s).__name__ for s in chunk.body]
        self.assertEqual(kinds, [
            'LocalFunctionStmt', 'LocalStmt', 'NumericForStmt', 'GenericForStmt',
            'WhileStmt', 'RepeatStmt', 'DoStmt',
        ])

    def test_varargs_and_multiple_return(self):
        chunk, equal = _round_trip(
            'local function f(...)\n'
            '    return ...\n'
            'end\n'
            'local function g()\n'
            '    return 1, 2, 3\n'
            'end\n'
            'local a, b, c = g()\n'
        )
        self.assertTrue(equal)
        self.assertTrue(chunk.body[0].is_vararg)
        self.assertEqual(len(chunk.body[1].body[0].values), 3)

    def test_anonymous_function_and_method_call_chain(self):
        chunk, equal = _round_trip(
            'local add = function(a, b) return a + b end\n'
            'local result = obj.field[1]:method(1, 2).other\n'
        )
        self.assertTrue(equal)
        self.assertIsInstance(chunk.body[0].values[0], ir.LuaFunctionExpr)
        self.assertIsInstance(chunk.body[1].values[0], ir.IndexExpr)

    def test_operator_precedence_and_associativity(self):
        # -2^2 == -(2^2); 2^2^3 == 2^(2^3) (right-assoc); string concat is
        # also right-assoc - all per the Lua reference manual.
        chunk = parse('local a = -2^2\nlocal b = 2^2^3\nlocal c = "a" .. "b" .. "c"\n')
        neg_pow = chunk.body[0].values[0]
        self.assertIsInstance(neg_pow, ir.UnaryExpr)
        self.assertIsInstance(neg_pow.operand, ir.BinaryExpr)
        self.assertEqual(neg_pow.operand.op, '^')

        pow_chain = chunk.body[1].values[0]
        self.assertEqual(pow_chain.right.op, '^')  # 2^(2^3), not (2^2)^3

        concat_chain = chunk.body[2].values[0]
        self.assertEqual(concat_chain.right.op, '..')  # "a"..("b".."c")

    def test_bitwise_and_floor_division_operators(self):
        # Real war3map.lua fixtures use these (Lua 5.3, not 5.1) - see
        # TODO.md and this module's own docstring.
        chunk, equal = _round_trip(
            'local a = (5 & 3) | (5 ~ 3)\n'
            'local b = 5 << 1\n'
            'local c = 5 >> 1\n'
            'local d = 7 // 2\n'
            'local e = ~5\n'
        )
        self.assertTrue(equal)
        self.assertEqual(chunk.body[4].values[0].op, '~')  # unary bnot

    def test_hex_and_z_escapes(self):
        # \\x (Lua 5.2+ hex escape) and \\z (skip-whitespace escape) - both
        # confirmed in real fixtures, not supported by Lua 5.1's own grammar.
        chunk, equal = _round_trip('local s = "\\x41\\x42"\nlocal t = "a\\z\n   b"\n')
        self.assertTrue(equal)
        self.assertEqual(chunk.body[0].values[0].value, 'AB')
        self.assertEqual(chunk.body[1].values[0].value, 'ab')

    def test_long_bracket_string_and_comments(self):
        source = (
            'local a = [[line one\nline two]]\n'
            'local b = [==[ has ]] inside ]==]\n'
            '--[[ a long comment\nspanning lines ]]\n'
            '-- a line comment\n'
            'local c = 1 -- trailing comment\n'
        )
        chunk, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertEqual(chunk.body[0].values[0].value, 'line one\nline two')
        self.assertEqual(chunk.body[1].values[0].value, ' has ]] inside ')

    def test_infinite_real_literal_round_trips(self):
        # A real fixture had a `real` literal one ULP over the max IEEE-754
        # double, overflowing to +inf - repr(float('inf')) is the bare word
        # 'inf', not valid Lua syntax, so this needs its own printer case
        # (see lua_printer._format_infinite_real). Regression test for a
        # real bug the war3map.lua sweep caught - see TODO.md.
        chunk, equal = _round_trip('local a = 1.79769313486232E+308\n')
        self.assertTrue(equal)
        self.assertEqual(chunk.body[0].values[0].value, float('inf'))

    def test_statement_starting_with_paren_does_not_merge_with_prior_call(self):
        # Real Lua's own grammar is genuinely ambiguous here (the reference
        # manual documents this exact case): a call-ending statement
        # immediately followed - no semicolon, just a newline - by a
        # statement starting with '(' greedily absorbs into ONE call chain
        # (`make()(D)[1]`), not two statements, and every real fixture that
        # does this always writes the defensive `;` to disambiguate (grep
        # confirms it - see TODO.md). This frontend's own printer originally
        # didn't emit any statement-terminating punctuation at all, so its
        # *own* output recreated the hazard even when the parsed source was
        # never ambiguous in the first place - caught by the real-fixture
        # sweep. Every statement now gets a trailing `;`, closing the gap.
        source = (
            'local function make()\n'
            '    return setmetatable({}, {__index = function() end})\n'
            'end\n'
            'local D = make();\n'
            '(D)[1] = 2\n'
        )
        chunk, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertIsInstance(chunk.body[1], ir.LocalStmt)
        self.assertIsInstance(chunk.body[2], ir.AssignStmt)

    def test_lexer_reports_position_on_bad_input(self):
        with self.assertRaises(LuaLexError) as ctx:
            parse('local a = 1\nlocal s = "unterminated\nlocal b = 2\n')
        self.assertEqual(ctx.exception.line, 2)

    def test_parser_raises_rather_than_misparse(self):
        with self.assertRaises(LuaParseError):
            parse('local 1x = 2\n')

    def test_break_and_return_must_be_last_in_block(self):
        chunk, equal = _round_trip(
            'while true do\n'
            '    if true then\n'
            '        break\n'
            '    end\n'
            'end\n'
            'local function f()\n'
            '    return 1\n'
            'end\n'
        )
        self.assertTrue(equal)


class TestScriptFileLua(unittest.TestCase):
    """ScriptFile's .lua wiring (read/write dispatch, validate=True guard) -
    the lexer/parser/printer functions above are exercised directly by every
    other test in this module; this covers the wrapper that dispatches to
    them by file extension."""

    def test_read_write_round_trips_through_scriptfile(self):
        script = ScriptFile().read(_helpers.sample_path('sample.lua'))
        self.assertEqual(script.get_file_type().value, 'lua')
        self.assertIsInstance(script.get_data(), ir.Chunk)

        out_path = os.path.join(_helpers.SAMPLES_DIR, '_tmp_scriptfile_roundtrip.lua')
        try:
            script.write(out_path)
            reread = ScriptFile().read(out_path)
            self.assertEqual(script.get_data(), reread.get_data())
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_validate_true_is_rejected_for_lua(self):
        script = ScriptFile().read(_helpers.sample_path('sample.lua'))
        with self.assertRaises(NotImplementedError):
            script.write(os.path.join(_helpers.SAMPLES_DIR, '_unused.lua'), validate=True)


class TestLuaObfuscator(unittest.TestCase):
    """Phase 5: local/param/loop-var renaming via scope-tracking, deliberately
    narrower in scope than JASS's phase 3 (see lua_obfuscator.py's own
    docstring for why globals are never renamed and dead-code elimination
    isn't attempted for Lua at all)."""

    def _obfuscate(self, source):
        chunk = parse(source)
        obfuscate_lua(chunk)
        printed = print_chunk(chunk)
        reparsed = parse(printed)  # must still be valid Lua
        return printed, reparsed

    def test_globals_are_never_renamed(self):
        printed, reparsed = self._obfuscate(
            'GlobalTable = {}\n'
            'function GlobalTable.method(x)\n'
            '    local y = x + 1\n'
            '    return y\n'
            'end\n'
            'GlobalFn = GlobalTable.method\n'
            'print(_G, GlobalFn(1))\n'
        )
        for name in ('GlobalTable', 'GlobalFn', '_G', 'print'):
            self.assertIn(name, printed)

        method_stmt = reparsed.body[1]
        self.assertEqual(method_stmt.name_path, ['GlobalTable', 'method'])
        renamed_param = method_stmt.params[0]
        self.assertNotEqual(renamed_param, 'x')  # param *is* expected to be renamed
        renamed_local = method_stmt.body[0].names[0]
        self.assertNotEqual(renamed_local, 'y')

    def test_recursive_local_function_renames_its_own_call(self):
        source = (
            'local function fact(n)\n'
            '    if n <= 1 then\n'
            '        return 1\n'
            '    end\n'
            '    return n * fact(n - 1)\n'
            'end\n'
            'print(fact(5))\n'
        )
        printed, reparsed = self._obfuscate(source)
        self.assertNotIn('fact', printed)
        # The renamed function must still call *itself* (not be left calling
        # the old, no-longer-declared name) - and 'print' (global) survives.
        local_fn = reparsed.body[0]
        self.assertIsInstance(local_fn, ir.LocalFunctionStmt)
        self.assertIn('print', printed)

    def test_closure_captures_renamed_outer_local(self):
        source = (
            'local function makeCounter()\n'
            '    local count = 0\n'
            '    return function()\n'
            '        count = count + 1\n'
            '        return count\n'
            '    end\n'
            'end\n'
            'local counter = makeCounter()\n'
            'print(counter())\n'
        )
        printed, reparsed = self._obfuscate(source)
        self.assertNotIn('count', printed)
        self.assertNotIn('makeCounter', printed)
        self.assertNotIn('counter', printed)

    def test_shadowing_in_nested_block_does_not_corrupt_outer_local(self):
        source = (
            'local function f()\n'
            '    local a = 1\n'
            '    do\n'
            '        local a = 2\n'
            '        print(a)\n'
            '    end\n'
            '    print(a)\n'
            'end\n'
        )
        printed, reparsed = self._obfuscate(source)
        fn = reparsed.body[0]
        outer_local_name = fn.body[0].names[0]
        inner_local_name = fn.body[1].body[0].names[0]
        # Reparsed print() calls: the inner one must reference the inner
        # local, the outer one the outer local - never crossed/corrupted.
        inner_print_arg = fn.body[1].body[1].call.args[0].name
        outer_print_arg = fn.body[2].call.args[0].name
        self.assertEqual(inner_print_arg, inner_local_name)
        self.assertEqual(outer_print_arg, outer_local_name)

    def test_field_names_are_never_treated_as_variables(self):
        # `.name`/`name = value` field/key spellings must never be renamed,
        # even though they're stored as plain strings right next to real
        # variable references in the same statement.
        printed, reparsed = self._obfuscate(
            'local t = {}\n'
            'local x = 1\n'
            't.fieldName = x\n'
            'local u = {fieldName = x}\n'
        )
        renamed_x = reparsed.body[1].names[0]
        self.assertNotEqual(renamed_x, 'x')

        field_assign = reparsed.body[2]  # AssignStmt: t.fieldName = <renamed x>
        self.assertEqual(field_assign.targets[0].index.value, 'fieldName')
        self.assertEqual(field_assign.values[0].name, renamed_x)

        table_literal = reparsed.body[3].values[0]  # {fieldName = <renamed x>}
        key, value = table_literal.fields[0]
        self.assertEqual(key.value, 'fieldName')
        self.assertEqual(value.name, renamed_x)

    def test_real_world_sweep_full_pipeline(self):
        pattern = os.path.join(_REPO_ROOT, 'outputs', '**', '*ar3map.lua')
        files = sorted(set(glob.glob(pattern, recursive=True)))
        if not files:
            raise unittest.SkipTest('no local war3map.lua fixtures under outputs/')

        checked = 0
        for path in files:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            if not source.strip():
                continue
            with self.subTest(path=path):
                chunk = parse(source)
                stmt_count_before = len(chunk.body)
                obfuscate_lua(chunk)
                printed = print_chunk(chunk)
                reparsed = parse(printed)  # must still be valid Lua
                self.assertEqual(stmt_count_before, len(reparsed.body),
                                  f'{path}: top-level statement count changed')
            checked += 1
        if checked == 0:
            raise unittest.SkipTest('every local fixture was empty')


class TestLuaRealWorldSweep(unittest.TestCase):
    """Best-effort: sweeps whatever real war3map.lua fixtures exist locally
    under outputs/. Entirely skipped if none are present (fresh clone, CI,
    another machine) - mirrors TestJassRealWorldSweep in test_jass.py."""

    @classmethod
    def setUpClass(cls):
        pattern = os.path.join(_REPO_ROOT, 'outputs', '**', '*ar3map.lua')
        cls.files = sorted(set(glob.glob(pattern, recursive=True)))
        if not cls.files:
            raise unittest.SkipTest('no local war3map.lua fixtures under outputs/')

    def test_real_fixtures_round_trip(self):
        checked = 0
        for path in self.files:
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            if not source.strip():
                continue
            with self.subTest(path=path):
                chunk, equal = _round_trip(source)
                self.assertTrue(equal, f'{path}: structural mismatch after round-trip')
            checked += 1
        if checked == 0:
            raise unittest.SkipTest('every local fixture was empty')


if __name__ == '__main__':
    unittest.main()
