"""Tests for the vJass / Zinc compiler (wc3files.vjass).

The behavioural tests compare against JassHelper itself. Each fixture in
samples/vjass/ was compiled by the JassHelper that ships with the game
(`--scriptonly`, release and `--debug`), and what its output prints when run
in wc3files.scripts.jass_vm was recorded in expected.json. Here the same
fixture is compiled by this package - release, debug, and with the inliner -
run the same way, and has to print exactly the same.

The rest pin down the pieces those runs do not show: name mangling, library
order, errors, the preprocessor, Zinc translation, the inliner's safety
rules, and the API and command line around them.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.scripts import ScriptFile, check_jass
from wc3files.scripts.jass_parser import parse as parse_jass
from wc3files.scripts.jass_vm import JassVM, run_script
from wc3files.vjass import (VJassCompiler, CompileOptions, VJassError,
                            compile_vjass, preprocess)
from wc3files.vjass.zinc import translate_zinc

SAMPLES = os.path.join(_helpers.SAMPLES_DIR, 'vjass')
with open(os.path.join(SAMPLES, 'expected.json')) as _fh:
    EXPECTED = json.load(_fh)

MAIN = '\nfunction main takes nothing returns nothing\nendfunction\n'


def run(jass, entry='test'):
    vm = JassVM(parse_jass(jass))
    vm.call('main')
    if entry and entry in vm.functions:
        vm.call(entry)
    return vm.output


def compile_text(source, **options):
    return VJassCompiler(**options).compile(source).jass


class TestAgainstJassHelper(unittest.TestCase):
    """Every fixture behaves as JassHelper's own output does."""

    def check(self, name, debug, optimize):
        path = os.path.join(SAMPLES, name + '.j')
        result = VJassCompiler(debug=debug, optimize=optimize).compile_file(path)
        mode = 'debug' if debug else 'release'
        self.assertEqual(run(result.jass), EXPECTED[name][mode],
                         f'{name} ({mode}{", optimized" if optimize else ""})')
        # And the output is JASS the checker accepts.
        issues = [f for f in check_jass(parse_jass(result.jass))
                  if f['severity'] == 'issue']
        self.assertEqual(issues, [], name)

    def test_every_fixture(self):
        for name in sorted(EXPECTED):
            for debug in (False, True):
                for optimize in (False, True):
                    with self.subTest(name=name, debug=debug, optimize=optimize):
                        self.check(name, debug, optimize)


class TestNaming(unittest.TestCase):
    """Generated names follow JassHelper's, so code that names them works."""

    def test_scope_prefixes(self):
        jass = compile_text('''
library Lib
    globals
        private integer hidden = 1
        public integer shown = 2
        integer plain = 3
    endglobals
    private function f takes nothing returns nothing
    endfunction
    public function g takes nothing returns nothing
        call f()
    endfunction
endlibrary
scope Outer
    scope Inner
        public function h takes nothing returns nothing
        endfunction
    endscope
    function k takes nothing returns nothing
        call Inner_h()
    endfunction
endscope
''' + MAIN)
        for name in ('integer Lib__hidden = 1', 'integer Lib_shown = 2',
                     'integer plain = 3', 'function Lib__f takes',
                     'function Lib_g takes', 'call Lib__f()',
                     'function Outer_Inner_h takes', 'call Outer_Inner_h()'):
            self.assertIn(name, jass)
        self.assertIn('constant boolean LIBRARY_Lib = true', jass)

    def test_struct_member_names(self):
        jass = compile_text('''
module M
    private static integer secret = 0
    static method bump takes nothing returns nothing
        set secret = secret + 1
    endmethod
endmodule
struct S
    implement M
    integer x
    static integer count = 0
    method operator [] takes integer i returns integer
        return i
    endmethod
    method operator y takes nothing returns integer
        return 1
    endmethod
    method operator y= takes integer v returns nothing
    endmethod
endstruct
''' + MAIN)
        for name in ('constant integer si__S = 1', 'integer array s__S_x',
                     'integer s__S_count = 0', 'integer s__S_M__secret = 0',
                     'function s__S_bump takes', 'function s__S__getindex',
                     'function s__S__get_y', 'function s__S__set_y',
                     'set s__S_M__secret = s__S_M__secret + 1'):
            self.assertIn(name, jass)

    def test_struct_and_module_may_share_a_name(self):
        jass = compile_text('''
struct Catalog
endstruct
module Catalog
    static integer n = 0
endmodule
struct User
    implement Catalog
endstruct
''' + MAIN)
        self.assertIn('integer s__User_n = 0', jass)


class TestSourceMap(unittest.TestCase):
    """A finding on the compiled JASS points back at the vJass line."""

    def test_checker_findings_map_to_source_lines(self):
        source = ('library L\n'
                  '    function Hello takes nothing returns nothing\n'
                  '        local trigger t = CreateTrigger()\n'
                  '        call KillUnit(t)\n'
                  '    endfunction\n'
                  'endlibrary\n'
                  'struct S\n'
                  '    method m takes nothing returns nothing\n'
                  '        local timer x = CreateTimer()\n'
                  '        call SetUnitX(x, 1.0)\n'
                  '    endmethod\n'
                  'endstruct\n')
        result = VJassCompiler().compile(source, 'map.j')
        lines = sorted(result.source_location(f['line']).line for f in result.validate()
                       if f['severity'] == 'issue')
        self.assertEqual(lines, [4, 10])
        self.assertEqual(result.source_location(1), None)


class TestLibraryOrder(unittest.TestCase):

    def order(self, source):
        jass = compile_text(source + MAIN)
        return [line.split()[1] for line in jass.splitlines()
                if line.startswith('//library ') and line.endswith('begins')]

    def test_requirements_come_first(self):
        self.assertEqual(self.order('''
library C requires B
endlibrary
library B requires A
endlibrary
library A
endlibrary
'''), ['A', 'B', 'C'])

    def test_unrelated_libraries_sort_alphabetically(self):
        # JassHelper's order, which real maps lean on: a library calling one
        # it never `requires` works because the callee sorts first.
        self.assertEqual(self.order('''
library Zeta
endlibrary
library Skin
endlibrary
library Ascii
endlibrary
'''), ['Ascii', 'Skin', 'Zeta'])

    def test_layers_are_not_refreshed_until_exhausted(self):
        # Z has no requirements; B needs A. JassHelper writes every library
        # with no pending requirement (A, Z) before re-checking, so Z comes
        # before B even though B is alphabetically first.
        self.assertEqual(self.order('''
library B requires A
endlibrary
library Z
endlibrary
library A
endlibrary
'''), ['A', 'Z', 'B'])

    def test_comma_free_requirement_lists(self):
        self.assertEqual(self.order('''
library L uses optional A optional B
endlibrary
library A
endlibrary
library B
endlibrary
'''), ['A', 'B', 'L'])


class TestErrors(unittest.TestCase):

    def assertError(self, source, fragment, **options):
        with self.assertRaises(VJassError) as caught:
            compile_text(source + MAIN, **options)
        self.assertIn(fragment, str(caught.exception))
        return caught.exception

    def test_missing_requirement(self):
        error = self.assertError('library A requires Nope\nendlibrary\n',
                                 'Missing requirement: Nope')
        self.assertEqual(error.location.line, 1)

    def test_requirement_cycle(self):
        self.assertError('library A requires B\nendlibrary\n'
                         'library B requires A\nendlibrary\n',
                         'Library requirements cycle')

    def test_redeclared_function(self):
        self.assertError('function f takes nothing returns nothing\n'
                         'endfunction\n'
                         'function f takes nothing returns nothing\n'
                         'endfunction\n', 'redeclared')

    def test_private_outside_a_library(self):
        self.assertError('globals\n    private integer x\nendglobals\n',
                         'private outside library/scope')

    def test_missing_interface_method(self):
        self.assertError('interface I\n    method f takes nothing returns '
                         'nothing\nendinterface\nstruct S extends I\n'
                         'endstruct\n', 'Missing method : f')

    def test_wrong_ondestroy(self):
        self.assertError('struct S\n    method onDestroy takes integer x '
                         'returns nothing\n    endmethod\nendstruct\n',
                         'Wrong onDestroy definition')

    def test_overriding_a_plain_method(self):
        self.assertError('struct A\n    method f takes nothing returns nothing\n'
                         '    endmethod\nendstruct\nstruct B extends A\n'
                         '    method f takes nothing returns nothing\n'
                         '    endmethod\nendstruct\n', 'only stub')

    def test_undeclared_names_are_warnings_unless_strict(self):
        source = 'function f takes nothing returns nothing\n' \
                 '    set udg_x = 1\nendfunction\n'
        result = VJassCompiler().compile(source + MAIN)
        self.assertIn('set udg_x = 1', result.jass)
        self.assertTrue(any('udg_x' in w.message for w in result.warnings))
        self.assertError(source, 'Undeclared identifier: udg_x', strict=True)

    def test_calling_a_function_above_its_declaration(self):
        source = ('function a takes nothing returns nothing\n    call b()\n'
                  'endfunction\nfunction b takes nothing returns nothing\n'
                  'endfunction\n')
        self.assertError(source, 'b is called before it is declared',
                         strict=True)

    def test_a_syntax_error_says_where(self):
        error = self.assertError('function f takes nothing returns nothing\n'
                                 '    set = 1\nendfunction\n', 'expected')
        self.assertEqual(error.location.line, 2)
        self.assertEqual(error.phase, 'parse')


class TestRuntimeShape(unittest.TestCase):
    """How the generated code is organized, where it matters."""

    def test_method_called_from_above_goes_through_a_trigger(self):
        jass = compile_text('''
struct S
    static method a takes nothing returns integer
        return thistype.b()
    endmethod
    static method b takes nothing returns integer
        return 7
    endmethod
endstruct
function test takes nothing returns nothing
    call BJDebugMsg(I2S(S.a()))
endfunction
''' + MAIN)
        self.assertIn('sc___prototype1_evaluate(1)', jass)
        self.assertEqual(run(jass), ['7'])

    def test_init_goes_after_initblizzard(self):
        jass = compile_text('''
library L initializer i
    private function i takes nothing returns nothing
    endfunction
endlibrary
function main takes nothing returns nothing
    call SetCameraBounds(0, 0, 0, 0, 0, 0, 0, 0)
    call InitBlizzard()
    call InitGlobals()
endfunction
''')
        body = jass[jass.index('function main'):]
        lines = [l.strip() for l in body.splitlines()[1:5]]
        self.assertEqual(lines[1], 'call InitBlizzard()')
        self.assertEqual(lines[2], 'call ExecuteFunc("L__i")')

    def test_big_arrays_use_accessors(self):
        jass = compile_text('''
globals
    integer array big[20000]
endglobals
function test takes nothing returns nothing
    set big[19000] = 5
    call BJDebugMsg(I2S(big[19000]))
endfunction
''' + MAIN)
        self.assertIn('integer array big__2', jass)
        self.assertIn('call big__set(19000, 5)', jass)
        self.assertEqual(run(jass), ['5'])

    def test_bigger_arrays_with_a_larger_array_size(self):
        jass = compile_text('''
globals
    integer array big[20000]
endglobals
''' + MAIN, max_array_size=32768)
        self.assertNotIn('big__get', jass)

    def test_code_value_above_its_declaration(self):
        # JASS needs a function declared before `function f` names it, and a
        # code value cannot go through a trigger; a copy goes above instead.
        jass = compile_text('''
struct S
    static method a takes nothing returns nothing
        local trigger t = CreateTrigger()
        call TriggerAddAction(t, function thistype.b)
        call TriggerExecute(t)
    endmethod
    static method b takes nothing returns nothing
        call BJDebugMsg("b ran")
    endmethod
endstruct
function test takes nothing returns nothing
    call S.a()
endfunction
''' + MAIN)
        self.assertIn('function sf__s__S_b takes', jass)
        self.assertLess(jass.index('function sf__s__S_b'),
                        jass.index('function s__S_a'))
        self.assertEqual(run(jass), ['b ran'])

    def test_code_where_a_boolexpr_belongs(self):
        jass = compile_text('''
function pick takes nothing returns boolean
    return true
endfunction
function test takes nothing returns nothing
    local boolexpr b = function pick
    call GroupEnumUnitsInRange(CreateGroup(), 0, 0, 100, function pick)
endfunction
''' + MAIN)
        self.assertIn('local boolexpr b = Condition(function pick)', jass)
        self.assertIn('100, Condition(function pick))', jass)

    def test_a_function_name_as_a_value_is_its_pointer(self):
        jass = compile_text('''
function interface Op takes nothing returns integer
function seven takes nothing returns integer
    return 7
endfunction
function test takes nothing returns nothing
    local integer id = seven
    local Op op = id
    call BJDebugMsg(I2S(op.evaluate()))
endfunction
''' + MAIN)
        self.assertEqual(run(jass), ['7'])

    def test_keyword_inside_globals(self):
        jass = compile_text('''
library L
    globals
        private keyword later
    endglobals
    private function later takes nothing returns nothing
    endfunction
endlibrary
''' + MAIN)
        self.assertIn('function L__later takes', jass)

    def test_debug_lines(self):
        source = '''
function test takes nothing returns nothing
    debug if true then
        debug call BJDebugMsg("in debug")
    debug endif
    call BJDebugMsg("always")
endfunction
struct S
    debug method check takes nothing returns nothing
        call BJDebugMsg("checking")
    endmethod
endstruct
''' + MAIN
        self.assertEqual(run(compile_text(source)), ['always'])
        self.assertEqual(run(compile_text(source, debug=True)),
                         ['in debug', 'always'])
        self.assertNotIn('s__S_check', compile_text(source))
        self.assertIn('s__S_check', compile_text(source, debug=True))


class TestPreprocessor(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder)

    def write(self, name, text):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(text)
        return path

    def test_imports_resolve_relative_and_only_once(self):
        self.write('lib/a.j', 'library A\n    function fa takes nothing returns '
                              'nothing\n    endfunction\nendlibrary\n')
        main = self.write('main.j', '//! import "lib/a.j"\n//! import "lib/a.j"'
                                    '\n' + MAIN)
        result = VJassCompiler().compile_file(main)
        self.assertEqual(result.jass.count('function fa takes'), 1)
        self.assertEqual(len(result.imports), 1)

    def test_lookup_folders(self):
        self.write('libs/b.j', 'library B\nendlibrary\n')
        main = self.write('main.j', '//! import "b.j"\n' + MAIN)
        with self.assertRaises(VJassError):
            VJassCompiler().compile_file(main)
        result = VJassCompiler(lookup_folders=[os.path.join(self.folder,
                                                            'libs')]
                               ).compile_file(main)
        self.assertIn('LIBRARY_B', result.jass)

    def test_conf_lookup_folders(self):
        self.write('libs/c.j', 'library C\nendlibrary\n')
        conf = self.write('jasshelper.conf', '[lookupfolders]\n"libs"\n'
                                             '[noimplicitthis]\n')
        compiler = VJassCompiler(conf=conf)
        self.assertFalse(compiler.options.implicit_this)
        main = self.write('main.j', '//! import "c.j"\n' + MAIN)
        self.assertIn('LIBRARY_C', compiler.compile_file(main).jass)

    def test_zinc_import(self):
        self.write('z.zn', 'library Z { public function zf() {} }\n')
        main = self.write('main.j', '//! import zinc "z.zn"\n' + MAIN)
        self.assertIn('function zf takes', VJassCompiler().compile_file(main).jass)

    def test_textmacros(self):
        jass = compile_text('''
//! textmacro GETTER takes NAME, TYPE
function Get$NAME$ takes nothing returns $TYPE$
    return 0
endfunction
//! endtextmacro
//! runtextmacro GETTER("Gold", "integer")
//! runtextmacro GETTER("Wood", "integer")
//! runtextmacro optional MISSING()
''' + MAIN)
        self.assertIn('function GetGold takes nothing returns integer', jass)
        self.assertIn('function GetWood takes', jass)
        with self.assertRaises(VJassError):
            compile_text('//! runtextmacro MISSING()\n' + MAIN)

    def test_novjass_and_externals(self):
        result = VJassCompiler().compile('''
//! novjass
this is not code at all
//! endnovjass
//! external ObjectMerger w3a AHhb A000 anam "Test"
//! frobnicate
''' + MAIN)
        self.assertEqual(result.externals[0][1], 'ObjectMerger')
        self.assertTrue(any('frobnicate' in w.message for w in result.warnings))

    def test_block_comments_join_lines(self):
        jass = compile_text('''
library A /* a long
   description */ requires /*
   */ B
endlibrary
library B
endlibrary
''' + MAIN)
        self.assertLess(jass.index('LIBRARY_B'), jass.index('LIBRARY_A'))

    def test_preprocess_reports_macros(self):
        pre = preprocess('//! textmacro X\n//! endtextmacro\n' + MAIN)
        self.assertIn('X', pre.macros)


class TestZinc(unittest.TestCase):

    def translate(self, body):
        pre = preprocess('//! zinc\n' + body + '\n//! endzinc\n')
        kind, lines = pre.segments[0]
        self.assertEqual(kind, 'zinc')
        return '\n'.join(l.text.strip() for l in translate_zinc(
            lines, CompileOptions()))

    def test_library_members_are_private_by_default(self):
        text = self.translate('library L { integer a; public integer b; '
                              'function f() {} public function g() {} '
                              'function onInit() {} }')
        self.assertIn('library L initializer onInit', text)
        self.assertIn('private integer a', text)
        self.assertIn('\ninteger b', text)
        self.assertIn('private function f takes nothing returns nothing', text)
        self.assertIn('\nfunction g takes', text)

    def test_struct_members_are_public_by_default(self):
        text = self.translate('library L { struct S { integer x; private '
                              'integer y; static method m() -> integer '
                              '{ return 1; } } }')
        self.assertIn('\ninteger x', text)
        self.assertIn('private integer y', text)
        self.assertIn('static method m takes nothing returns integer', text)

    def test_compound_assignment_keeps_its_meaning(self):
        text = self.translate('library L { function f() { integer i = 5; '
                              'i *= 2 + 1; i -= 1 - 1; } }')
        self.assertIn('set i = i * (2 + 1)', text)
        self.assertIn('set i = i - (1 - 1)', text)

    def test_not_is_parenthesized(self):
        text = self.translate('library L { function f() -> boolean { '
                              'return !true == false; } }')
        self.assertIn('return (not true) == false', text)

    def test_for_loops(self):
        text = self.translate('library L { function f() { integer i; '
                              'for (0 <= i < 10) {} for (10 > i >= 0) {} } }')
        self.assertIn('set i = 0', text)
        self.assertIn('exitwhen i >= 10', text)
        self.assertIn('set i = (10) - 1', text)
        self.assertIn('exitwhen i < 0', text)

    def test_anonymous_functions(self):
        text = self.translate('library L { function f() { '
                              'TimerStart(CreateTimer(), 1, false, function() '
                              '{ BJDebugMsg("x"); }); } }')
        self.assertIn('private function anon__1 takes nothing returns nothing',
                      text)
        self.assertIn('function anon__1)', text)
        self.assertLess(text.index('anon__1 takes'), text.index('function f '))

    def test_zinc_and_vjass_mix(self):
        jass = compile_text('''
//! zinc
library Z {
    public module Counted { static integer n = 0; }
}
//! endzinc
struct S
    implement Counted
endstruct
''' + MAIN)
        self.assertIn('integer s__S_n = 0', jass)


class TestInliner(unittest.TestCase):

    def test_accessors_are_inlined(self):
        source = '''
struct P
    real px
    method operator x takes nothing returns real
        return this.px
    endmethod
endstruct
function test takes nothing returns nothing
    local P p = P.create()
    set p.px = 2.5
    call BJDebugMsg(R2S(p.x))
endfunction
''' + MAIN
        jass = compile_text(source, optimize=True)
        self.assertIn('R2S(s__P_px[p])', jass)
        self.assertEqual(run(jass), ['2.500'])

    def test_evaluation_order_is_kept(self):
        # The body reads a global before using its argument; inlining would
        # read it before the argument's call changed it.
        source = '''
globals
    integer g = 0
endglobals
function bump takes nothing returns integer
    set g = g + 10
    return 1
endfunction
function f takes integer a returns integer
    return g + a
endfunction
function test takes nothing returns nothing
    call BJDebugMsg(I2S(f(bump())))
endfunction
''' + MAIN
        jass = compile_text(source, optimize=True)
        self.assertIn('f(bump())', jass)
        self.assertEqual(run(jass), ['11'])

    def test_integer_arguments_to_real_parameters(self):
        source = '''
function half takes real x returns real
    return x / 2
endfunction
function test takes nothing returns nothing
    local integer three = 3
    call BJDebugMsg(R2S(half(three)))
endfunction
''' + MAIN
        jass = compile_text(source, optimize=True)
        self.assertIn('I2R(three)', jass)
        self.assertEqual(run(jass), ['1.500'])

    def test_a_local_never_captures_a_global(self):
        source = '''
globals
    integer n = 5
endglobals
function getn takes nothing returns integer
    return n
endfunction
function test takes nothing returns nothing
    local integer n = 1
    call BJDebugMsg(I2S(getn()))
endfunction
''' + MAIN
        jass = compile_text(source, optimize=True)
        self.assertEqual(run(jass), ['5'])


class TestApi(unittest.TestCase):

    def test_compile_vjass_shorthand(self):
        self.assertIn('LIBRARY_A', compile_vjass('library A\nendlibrary\n' +
                                                 MAIN))

    def test_result_program_and_validate(self):
        result = VJassCompiler().compile('struct S\nendstruct\n' + MAIN)
        self.assertTrue(any(g.name == 'si__S' for g in result.program.globals))
        self.assertEqual([f for f in result.validate()
                          if f['severity'] == 'issue'], [])

    def test_script_file_reads_vjass(self):
        path = os.path.join(SAMPLES, 'structs.j')
        script = ScriptFile().read_vjass(path, optimize=True)
        names = [f.name for f in script.get_data().functions]
        self.assertIn('s__P_create', names)
        self.assertIsNotNone(script.vjass_result)

    def test_compile_files_in_order(self):
        folder = tempfile.mkdtemp()
        try:
            a = os.path.join(folder, 'a.j')
            b = os.path.join(folder, 'b.j')
            with open(a, 'w') as fh:
                fh.write('library A\nendlibrary\n')
            with open(b, 'w') as fh:
                fh.write('library B requires A\nendlibrary\n' + MAIN)
            jass = VJassCompiler().compile_files([a, b]).jass
            self.assertIn('LIBRARY_B', jass)
        finally:
            shutil.rmtree(folder)

    def test_compile_map(self):
        from wc3files.w3x import W3xFile
        folder = tempfile.mkdtemp()
        try:
            path = os.path.join(folder, 'test.w3x')
            with open(os.path.join(SAMPLES, 'inherit.j')) as fh:
                source = fh.read()
            W3xFile().build_from_files({'war3map.j': source.encode(),
                                        'war3map.w3i': b'\0' * 4}, path)
            out = os.path.join(folder, 'out.w3x')
            VJassCompiler().compile_map(path, out)
            w3x = W3xFile()
            w3x.read(out)
            compiled = w3x.files['war3map.j'].decode()
            self.assertNotIn('endstruct', compiled)
            self.assertEqual(run(compiled), EXPECTED['inherit']['release'])
            self.assertEqual(w3x.files['war3map.w3i'], b'\0' * 4)
        finally:
            shutil.rmtree(folder)


class TestCommandLine(unittest.TestCase):

    def cli(self, *argv):
        from wc3files.cli.main import main
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_compile_to_stdout(self):
        code, out, _ = self.cli('vjass', os.path.join(SAMPLES, 'misc.j'), '-O')
        self.assertEqual(code, 0)
        self.assertEqual(run(out), EXPECTED['misc']['release'])

    def test_compile_to_a_file_with_check(self):
        folder = tempfile.mkdtemp()
        try:
            target = os.path.join(folder, 'out.j')
            code, _, _ = self.cli('vjass', os.path.join(SAMPLES, 'zinc_basic.j'),
                                  '--debug', '--check', '-o', target)
            self.assertEqual(code, 0)
            with open(target) as fh:
                self.assertEqual(run(fh.read()),
                                 EXPECTED['zinc_basic']['debug'])
        finally:
            shutil.rmtree(folder)

    def test_preprocess(self):
        code, out, _ = self.cli('vjass', os.path.join(SAMPLES, 'zinc_basic.j'),
                                '--preprocess')
        self.assertEqual(code, 0)
        self.assertIn('library ZTest initializer onInit requires ZDep', out)

    def test_an_error_is_reported_not_raised(self):
        folder = tempfile.mkdtemp()
        try:
            bad = os.path.join(folder, 'bad.j')
            with open(bad, 'w') as fh:
                fh.write('library A requires Nope\nendlibrary\n')
            code, _, err = self.cli('vjass', bad)
            self.assertEqual(code, 1)
            self.assertIn('Missing requirement: Nope', err)
        finally:
            shutil.rmtree(folder)


class TestJassVM(unittest.TestCase):
    """The interpreter the tests above stand on."""

    def test_integer_semantics(self):
        vm = run_script('''
function main takes nothing returns nothing
    call BJDebugMsg(I2S(-7 / 2) + " " + I2S(2147483647 + 1) + " " + I2S('A000'))
    call BJDebugMsg(R2S(7 / 2) + " " + R2S(7 / 2.0))
endfunction
''')
        self.assertEqual(vm.output, ['-3 -2147483648 1093677104',
                                     '3.000 3.500'])

    def test_triggers_and_execute_func(self):
        vm = run_script('''
function cond takes nothing returns boolean
    call BJDebugMsg("cond")
    return true
endfunction
function act takes nothing returns nothing
    call BJDebugMsg("act")
endfunction
function main takes nothing returns nothing
    local trigger t = CreateTrigger()
    call TriggerAddCondition(t, Condition(function cond))
    call TriggerAddAction(t, function act)
    if TriggerEvaluate(t) then
        call TriggerExecute(t)
    endif
    call ExecuteFunc("act")
endfunction
''')
        self.assertEqual(vm.output, ['cond', 'act', 'act'])


if __name__ == '__main__':
    unittest.main()
