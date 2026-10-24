"""Real tests for wc3files.scripts (the JASS lexer/parser/printer/checker).

Two tiers:
- Guaranteed, portable: the checked-in tests/samples/scripts/sample.j plus the real
  bundled common.j/blizzard.j (scripts/gamedata/) - always available, no
  personal data needed.
- Best-effort real-world sweep: whatever war3map.j fixtures happen to exist
  locally under outputs/ (gitignored map-extraction output) - skipped
  entirely if none are present, and known-bad ones (documented in TODO.md:
  truncated extractions, vJass source, binary garbage) are expected to fail
  and don't count against the suite.
"""
import glob
import os
import unittest

import _helpers
from wc3files.scripts.jass_parser import parse, JassParseError
from wc3files.scripts.jass_lexer import JassLexError
from wc3files.scripts.jass_printer import print_program
from wc3files.scripts.checker import check_program, NativeSignatures
from wc3files.scripts.optimizer import find_reachable, remove_dead_code
from wc3files.scripts.obfuscator import build_rename_table, apply_rename
from wc3files.scripts import ir

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GAMEDATA_DIR = os.path.join(_REPO_ROOT, 'src', 'wc3files', 'scripts', 'gamedata')

# Substrings of war3map.j fixture paths to leave out of the real-world sweep
# below: stale or corrupted extractions and out-of-scope vJass source, which
# are not parser bugs. Empty by design - it used to name ten folders of a
# personal map collection, which does not belong in the repo. The sweep only
# runs against local fixtures that are not part of this project anyway, so add
# your own entries here if you keep some and need to exclude any.
KNOWN_BAD_FIXTURE_SUBSTRINGS = ()


def _round_trip(source):
    program = parse(source)
    text = print_program(program)
    reparsed = parse(text)
    return program, program == reparsed


class TestJassRoundTrip(unittest.TestCase):
    def test_sample_j_round_trips(self):
        with open(_helpers.sample_path('sample.j'), 'r', encoding='utf-8') as fh:
            source = fh.read()
        program, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertEqual(len(program.functions), 1)
        self.assertEqual(program.functions[0].name, 'Foo')

    def test_bundled_common_j_round_trips(self):
        path = os.path.join(_GAMEDATA_DIR, 'common.j')
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            source = fh.read()
        program, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertGreater(len(program.natives), 1000)
        self.assertGreater(len(program.type_defs), 100)

    def test_bundled_blizzard_j_round_trips(self):
        path = os.path.join(_GAMEDATA_DIR, 'blizzard.j')
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            source = fh.read()
        program, equal = _round_trip(source)
        self.assertTrue(equal)
        self.assertGreater(len(program.functions), 900)

    def test_lexer_reports_position_on_bad_input(self):
        with self.assertRaises(JassLexError) as ctx:
            parse('function F takes nothing returns nothing\n    set x = @\nendfunction')
        self.assertEqual(ctx.exception.line, 2)

    def test_parser_raises_rather_than_misparse(self):
        with self.assertRaises(JassParseError):
            parse('function F takes nothing returns nothing\n    call\nendfunction')


class TestJassChecker(unittest.TestCase):
    """Confirms the checker isn't trivially permissive (catches real problems)
    and isn't trivially noisy (silent on JASS's real integer->real widening) -
    see TODO.md's phase-2 entry for how the widening rule was discovered."""

    def setUp(self):
        self.natives = NativeSignatures.default()

    def test_catches_unrecognized_call(self):
        findings = check_program(parse(
            'function T takes nothing returns nothing\n'
            '    call ThisFunctionDoesNotExist()\n'
            'endfunction'
        ), natives=self.natives)
        self.assertTrue(any('ThisFunctionDoesNotExist' in f['message'] for f in findings))

    def test_catches_wrong_argument_count(self):
        findings = check_program(parse(
            'function T takes nothing returns nothing\n'
            '    local unit u = null\n'
            '    call KillUnit(u, u)\n'
            'endfunction'
        ), natives=self.natives)
        self.assertTrue(any('takes 1 argument' in f['message'] for f in findings))

    def test_catches_incompatible_type(self):
        findings = check_program(parse(
            'function T takes nothing returns nothing\n'
            '    call KillUnit("not a unit")\n'
            'endfunction'
        ), natives=self.natives)
        self.assertTrue(any('incompatible type string' in f['message'] for f in findings))

    def test_does_not_flag_integer_to_real_widening(self):
        findings = check_program(parse(
            'function T takes nothing returns nothing\n'
            '    local unit u = null\n'
            '    local integer i = 5\n'
            '    call SetUnitX(u, i)\n'
            'endfunction'
        ), natives=self.natives)
        self.assertFalse(any('SetUnitX' in f['message'] for f in findings))

    def test_accepts_subtype_via_handle_hierarchy(self):
        # unit extends widget extends agent extends handle (common.j) - a
        # native declaring a widget/agent/handle param must accept a unit.
        self.assertTrue(self.natives.is_ancestor('widget', 'unit'))
        self.assertTrue(self.natives.is_ancestor('handle', 'unit'))
        self.assertFalse(self.natives.is_ancestor('item', 'unit'))

    def test_zero_findings_against_real_bundled_functions(self):
        # blizzard.j's own BJ-wrapper functions call natives extensively -
        # a correct checker should find nothing wrong with Blizzard's own code.
        path = os.path.join(_GAMEDATA_DIR, 'blizzard.j')
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            program = parse(fh.read())
        findings = check_program(program, natives=self.natives)
        issues = [f for f in findings if f['severity'] == 'issue']
        self.assertEqual(issues, [])


class TestJassRealWorldSweep(unittest.TestCase):
    """Best-effort: sweeps whatever real war3map.j fixtures exist locally
    under outputs/. Entirely skipped if none are present (fresh clone, CI,
    another machine) - this is a bonus check, not a hard requirement."""

    @classmethod
    def setUpClass(cls):
        pattern = os.path.join(_REPO_ROOT, 'outputs', '**', '*ar3map.j')
        cls.files = sorted(set(glob.glob(pattern, recursive=True)))
        if not cls.files:
            raise unittest.SkipTest('no local war3map.j fixtures under outputs/')

    def test_known_good_fixtures_round_trip_and_pass_the_checker(self):
        natives = NativeSignatures.default()
        checked = 0
        for path in self.files:
            if any(bad in path for bad in KNOWN_BAD_FIXTURE_SUBSTRINGS):
                continue
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            if not source.strip():
                continue
            with self.subTest(path=path):
                program, equal = _round_trip(source)
                self.assertTrue(equal, f'{path}: structural mismatch after round-trip')
                issues = [f for f in check_program(program, natives=natives)
                          if f['severity'] == 'issue']
                self.assertEqual(issues, [], f'{path}: {issues}')
            checked += 1
        if checked == 0:
            raise unittest.SkipTest('every local fixture is in the known-bad list')


_OPTIMIZER_SAMPLE = '''
globals
    integer udg_Count = 0
    real udg_Unused = 0
    trigger gg_trg_Something = null
endglobals

function UnusedFunction takes nothing returns nothing
    call BJDebugMsg("never called")
endfunction

function DynamicTarget takes nothing returns nothing
    call BJDebugMsg("called via ExecuteFunc")
endfunction

function Helper takes integer x returns integer
    local integer i = x
    set udg_Count = udg_Count + i
    return udg_Count
endfunction

function config takes nothing returns nothing
    call SetMapName("Test")
endfunction

function main takes nothing returns nothing
    local integer result = Helper(5)
    call TriggerRegisterVariableEvent(gg_trg_Something, "udg_Count", EQUAL, 0)
    call ExecuteFunc("DynamicTarget")
    call BJDebugMsg(I2S(result))
endfunction
'''


class TestJassOptimizerAndObfuscator(unittest.TestCase):
    """Phase 3: reachability-based dead-code elimination + identifier renaming,
    porting the design (not the code) of w3x2lni's simplify.lua/confuser.lua."""

    def setUp(self):
        self.natives = NativeSignatures.default()
        self.native_and_bj_names = set(self.natives._natives.keys()) | set(self.natives._functions.keys())

    def test_removes_unreachable_function_and_global(self):
        program = parse(_OPTIMIZER_SAMPLE)
        reachable = find_reachable(program)
        remove_dead_code(program, reachable)
        self.assertNotIn('UnusedFunction', [f.name for f in program.functions])
        self.assertNotIn('udg_Unused', [g.name for g in program.globals])
        self.assertIn('main', [f.name for f in program.functions])
        self.assertIn('config', [f.name for f in program.functions])

    def test_executefunc_protects_reachability_and_name(self):
        program = parse(_OPTIMIZER_SAMPLE)
        reachable = find_reachable(program)
        self.assertIn('DynamicTarget', reachable['reachable_functions'])
        self.assertIn('DynamicTarget', reachable['protected_functions'])
        rename_table = build_rename_table(program, reachable, natives=self.native_and_bj_names)
        self.assertNotIn('DynamicTarget', rename_table)

    def test_a_local_shadowing_a_renamed_function_keeps_its_own_name(self):
        """A local may share a name with a function, and real maps do.

        In JASS the local wins for variable references while a call still
        reaches the function, so renaming the function must not rewrite the
        local's reads and writes - their `local` declaration stays put, and
        rewriting the references produced a script referring to an undeclared
        variable. Caught by validating output with the game's own pjass, which
        this package's own parser could not: it accepted its own bad output.
        """
        source = """
function gFF takes nothing returns nothing
endfunction

function Caller takes nothing returns nothing
    local integer gFF
    set gFF = 0
    set gFF = gFF + 1
    call gFF()
endfunction

function main takes nothing returns nothing
    call Caller()
endfunction
"""
        program = parse(source)
        reachable = find_reachable(program)
        rename_table = build_rename_table(program, reachable,
                                          natives=self.native_and_bj_names)
        self.assertIn('gFF', rename_table)  # the function is renamed
        new_name = rename_table['gFF']
        # Caller is renamed too, so find it by its new name afterwards.
        caller_name = rename_table.get('Caller', 'Caller')

        apply_rename(program, rename_table)
        caller = [f for f in program.functions if f.name == caller_name][0]

        local_names = [s.name for s in caller.body if isinstance(s, ir.Local)]
        self.assertEqual(local_names, ['gFF'])

        # Every variable read/write in Caller must still say gFF...
        sets = [s for s in caller.body if isinstance(s, ir.SetStmt)]
        self.assertTrue(sets)
        for statement in sets:
            self.assertEqual(statement.target, 'gFF')

        # ...while the call is renamed, because a local cannot shadow a call.
        calls = [s for s in caller.body if isinstance(s, ir.CallStmt)]
        self.assertEqual([c.name for c in calls], [new_name])

    def test_variable_event_protects_global_name(self):
        program = parse(_OPTIMIZER_SAMPLE)
        reachable = find_reachable(program)
        self.assertIn('udg_Count', reachable['protected_globals'])
        rename_table = build_rename_table(program, reachable, natives=self.native_and_bj_names)
        self.assertNotIn('udg_Count', rename_table)

    def test_rename_generates_distinct_names_and_stays_checker_clean(self):
        program = parse(_OPTIMIZER_SAMPLE)
        reachable = find_reachable(program)
        remove_dead_code(program, reachable)
        rename_table = build_rename_table(program, reachable, natives=self.native_and_bj_names)

        # Helper/config/main/gg_trg_Something are all renameable and not protected.
        self.assertIn('Helper', rename_table)
        self.assertIn('config', rename_table)
        self.assertIn('main', rename_table)
        self.assertIn('gg_trg_Something', rename_table)
        self.assertEqual(len(set(rename_table.values())), len(rename_table))  # all distinct

        apply_rename(program, rename_table)
        obfuscated_source = print_program(program)
        reparsed = parse(obfuscated_source)  # must still be valid JASS
        issues = [f for f in check_program(reparsed, natives=self.natives) if f['severity'] == 'issue']
        self.assertEqual(issues, [])

    def test_generated_name_never_collides_with_a_local_or_param(self):
        # A local/param named exactly what the first generated name would be
        # ('a') must not be silently shadowed by a renamed global/function.
        source = '''
        function main takes nothing returns nothing
            local integer a = 5
            call BJDebugMsg(I2S(a))
        endfunction
        function config takes nothing returns nothing
        endfunction
        function Foo takes nothing returns nothing
            call BJDebugMsg("hi")
        endfunction
        '''
        program = parse(source)
        # Make Foo reachable via a call from main so it's a rename candidate.
        program.functions[0].body.insert(0, ir.CallStmt('Foo'))
        reachable = find_reachable(program)
        rename_table = build_rename_table(program, reachable, natives=self.native_and_bj_names)
        self.assertNotIn('a', rename_table.values())

    def test_real_world_sweep_full_pipeline(self):
        pattern = os.path.join(_REPO_ROOT, 'outputs', '**', '*ar3map.j')
        files = sorted(set(glob.glob(pattern, recursive=True)))
        if not files:
            raise unittest.SkipTest('no local war3map.j fixtures under outputs/')

        checked = 0
        for path in files:
            if any(bad in path for bad in KNOWN_BAD_FIXTURE_SUBSTRINGS):
                continue
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                source = fh.read()
            if not source.strip():
                continue
            try:
                program = parse(source)
            except (JassParseError, JassLexError):
                continue

            with self.subTest(path=path):
                reachable = find_reachable(program)
                remove_dead_code(program, reachable)
                rename_table = build_rename_table(program, reachable, natives=self.native_and_bj_names)
                apply_rename(program, rename_table)
                obfuscated_source = print_program(program)
                reparsed = parse(obfuscated_source)  # must still be valid JASS
                issues = [f for f in check_program(reparsed, natives=self.natives)
                          if f['severity'] == 'issue']
                self.assertEqual(issues, [], f'{path}: {issues}')
            checked += 1
        if checked == 0:
            raise unittest.SkipTest('every local fixture is in the known-bad list')


if __name__ == '__main__':
    unittest.main()
