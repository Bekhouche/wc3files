"""cJass, checked against the examples of the cJass 1.4.0.2 manual."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cjass import CJassError, cjass_to_vjass, compile_cjass
from wc3files.cli.main import main
from wc3files.scripts.checker import check_program
from wc3files.scripts.jass_parser import parse


def lines(source):
    return [line.strip() for line in cjass_to_vjass(source).splitlines() if line.strip()]


def body(source):
    """The lines between the first function header and endfunction."""
    out = lines(source)
    start = next(i for i, line in enumerate(out) if line.startswith('function '))
    end = out.index('endfunction', start)
    return out[start + 1:end]


class TestSyntax(unittest.TestCase):
    def test_increments(self):
        # Manual 2.3.2: a prefix operator runs before the statement, a
        # postfix one after it.
        self.assertEqual(body('nothing f() {\n integer a = 7\n a++\n integer b = ++a\n'
                              ' integer c = a + b--\n}'),
                         ['local integer a = 7', 'local integer b', 'local integer c',
                          'set a = a + 1', 'set a = a + 1', 'set b = a', 'set c = a + b',
                          'set b = b - 1'])

    def test_compound_and_optional_keywords(self):
        self.assertEqual(body('nothing f() { integer a = 3, b = 5, c = 4, d = 6\n'
                              ' a += 7; b -= 4; c *= 5; d /= 3; DoNothing() }'),
                         ['local integer a = 3', 'local integer b = 5', 'local integer c = 4',
                          'local integer d = 6', 'set a = a + 7', 'set b = b - 4',
                          'set c = c * 5', 'set d = d / 3', 'call DoNothing()'])

    def test_locals_move_to_the_top(self):
        # Manual 2.2: a literal initializer stays with the declaration, any
        # other becomes a set where it was.
        self.assertEqual(body('nothing f() { DoNothing()\n integer i = 0\n'
                              ' location l = Location(0,0) }'),
                         ['local integer i = 0', 'local location l', 'call DoNothing()',
                          'set l = Location(0, 0)'])

    def test_braced_if(self):
        # Manual 2.4.
        self.assertEqual(body('integer f(integer i) {\n'
                              ' if (i > 3) { return --i } else { BJDebugMsg("i <= 3") }\n'
                              ' return i\n}'),
                         ['if (i > 3) then', 'set i = i - 1', 'return i', 'else',
                          'call BJDebugMsg("i <= 3")', 'endif', 'return i'])

    def test_loops(self):
        # Manual 2.5: whilenot tests first, do ... whilenot at the end.
        self.assertEqual(body('integer pow(integer num, integer pow) {\n'
                              ' integer i = 0, res = 1\n'
                              ' whilenot (i++ >= pow) { res *= num }\n return res }'),
                         ['local integer i = 0', 'local integer res = 1', 'loop',
                          'exitwhen (i >= pow)', 'set i = i + 1', 'set res = res * num',
                          'endloop', 'return res'])
        self.assertEqual(body('nothing f() { integer i = 0, res = 1\n'
                              ' do { res *= 2 } whilenot (i++ >= 4) }')[2:],
                         ['loop', 'set res = res * 2', 'exitwhen (i >= 4)', 'set i = i + 1',
                          'endloop'])
        self.assertEqual(body('include "cj_types.j"\nvoid f() { int i = 0\n'
                              ' while (i < 3) { i++ }\n loop { break } }')[1:],
                         ['loop', 'exitwhen not (i < 3)', 'set i = i + 1', 'endloop', 'loop',
                          'exitwhen true', 'endloop'])

    def test_logic_operators(self):
        self.assertEqual(body('boolean f(unit u, real x, real y) {\n'
                              ' return (GetUnitX(u) == x) && (GetUnitY(u) == y) || !true }'),
                         ['return (GetUnitX(u) == x) and (GetUnitY(u) == y) or not true'])

    def test_functions_globals_and_vjass_blocks(self):
        out = lines('integer f = 12\n'
                    'library A {\n    private nothing hidden() { }\n}\n'
                    'struct S {\n    integer n = 0\n    static S make() { return S.create() }\n'
                    '    method get takes nothing returns integer\n        return n\n'
                    '    endmethod\n}\n'
                    'function plain takes nothing returns nothing\n    integer k = 1\n'
                    '    k++\nendfunction')
        self.assertEqual(out, ['globals', 'integer f = 12', 'endglobals', 'library A',
                               'private function hidden takes nothing returns nothing',
                               'endfunction', 'endlibrary', 'struct S', 'integer n = 0',
                               'static method make takes nothing returns S',
                               'return S.create()', 'endmethod',
                               'method get takes nothing returns integer', 'return n',
                               'endmethod', 'endstruct',
                               'function plain takes nothing returns nothing',
                               'local integer k = 1', 'set k = k + 1', 'endfunction'])


class TestRealCodeCases(unittest.TestCase):
    """Cases met in two real cJass projects."""

    def test_debug_lines(self):
        source = ('nothing f() {\n debug if (1 > 0) then\n debug BJDebugMsg("d")\n'
                  ' debug endif\n DoNothing() }')
        self.assertEqual(body(source), ['call DoNothing()'])
        self.assertEqual([l.strip() for l in cjass_to_vjass(source, debug=True).splitlines()][1:4],
                         ['if (1 > 0) then', 'call BJDebugMsg("d")', 'endif'])

    def test_same_local_in_two_branches(self):
        self.assertEqual(body('nothing f(boolean b) {\n if (b) { rect r = Rect(0, 0, 1, 1) }\n'
                              ' else { rect r = Rect(0, 0, 2, 2) }\n RemoveRect(r) }'),
                         ['local rect r', 'if (b) then', 'set r = Rect(0, 0, 1, 1)', 'else',
                          'set r = Rect(0, 0, 2, 2)', 'endif', 'call RemoveRect(r)'])

    def test_string_joined_by_backslash(self):
        self.assertEqual(body('nothing f() { BJDebugMsg("one \\\n two") }'),
                         ['call BJDebugMsg("one  two")'])

    def test_textmacro_static_if_and_function_interface(self):
        out = lines('function interface Callback takes integer x returns nothing\n'
                    '//! textmacro MAKE\n    local integer n = 1\n//! endtextmacro\n'
                    'nothing f() {\n static if DEBUG_MODE then\n  DoNothing()\n endif\n'
                    ' while (true) { break } }')
        self.assertEqual(out, ['function interface Callback takes integer x returns nothing',
                               '//! textmacro MAKE', 'local integer n = 1', '//! endtextmacro',
                               'function f takes nothing returns nothing',
                               'static if DEBUG_MODE then', 'call DoNothing()', 'endif', 'loop',
                               'exitwhen not (true)', 'exitwhen true', 'endloop', 'endfunction'])

    def test_struct_methods_with_modifiers(self):
        out = lines('struct S extends array {\n'
                    '    public static thistype create(integer n)\n    {\n'
                    '        thistype data = thistype(n)\n        return data\n    }\n}')
        self.assertEqual(out[:3], ['struct S extends array',
                                   'public static method create takes integer n returns thistype',
                                   'local thistype data'])

    def test_new_with_arguments_and_repeated_includes(self):
        source = ('include "cj_types.j"\ninclude "cj_types_priv.j"\ninclude "cj_typesEx.j"\n'
                  'include "cj_typesEx_priv.j"\n'
                  'void f() { unit u = new unit(Player(0), \'hfoo\')\n'
                  ' location l = new location()\n player p = new player(3) }')
        self.assertEqual(body(source)[3:],
                         ["set u = CreateUnit(Player(0), 'hfoo', 0, 0, 0)",
                          'set l = Location(0, 0)', 'set p = Player(3)'])


class TestPreprocessor(unittest.TestCase):
    def test_define(self):
        # Manual 3.1 and 3.4.
        self.assertEqual(body("#define FOOTMAN = 'hfoo'\n"
                              'define msg(playerid,text) = DisplayTextToPlayer(Player(playerid), '
                              'text, 0, 0)\n'
                              'nothing f() { CreateUnit(GetLocalPlayer(), FOOTMAN, 0, 0, 0)\n'
                              ' msg(1, "x") }'),
                         ["call CreateUnit(GetLocalPlayer(), 'hfoo', 0, 0, 0)",
                          'call DisplayTextToPlayer(Player(1), "x", 0, 0)'])

    def test_private_defines(self):
        # Manual 3.2: each element sees its own private define.
        out = lines('#define msg = "X"\nlibrary A {\n #define private msg = "A"\n'
                    ' nothing a() { BJDebugMsg(msg) }\n}\nscope B {\n'
                    ' #define private msg = "B"\n nothing b() { BJDebugMsg(msg) }\n}\n'
                    'nothing x() { BJDebugMsg(msg) }')
        self.assertEqual([l for l in out if 'BJDebugMsg' in l],
                         ['call BJDebugMsg("A")', 'call BJDebugMsg("B")',
                          'call BJDebugMsg("X")'])

    def test_setdef_undef_and_redefinition(self):
        self.assertEqual(body('#define msg = "text"\nnothing f() { BJDebugMsg(msg)\n'
                              '#setdef msg = "other text"\n BJDebugMsg(msg)\n#undef msg\n'
                              ' BJDebugMsg(msg) }'),
                         ['call BJDebugMsg("text")', 'call BJDebugMsg("other text")',
                          'call BJDebugMsg(msg)'])
        with self.assertRaises(CJassError):
            cjass_to_vjass('#define a = 1\n#define a = 2\n')

    def test_overloads_stringize_concat_counter(self):
        # Manual 3.5, 3.6 and 3.7.
        out = lines('define msg(text) = BJDebugMsg(text)\n'
                    'define msg(text,playerid) = DisplayTextToPlayer(Player(playerid), 0, 0, text)\n'
                    'define register_func(type) = {\n'
                    '    nothing func_##type (type t) { BJDebugMsg(`type`) }\n}\n'
                    'register_func(real)\n'
                    'define unique_name = func_##COUNTER\n'
                    'nothing unique_name () {}\nnothing unique_name () {}\n'
                    'nothing g() { msg("a"); msg("b", 2) }')
        self.assertIn('function func_real takes real t returns nothing', out)
        self.assertIn('call BJDebugMsg("real")', out)
        self.assertIn('function func_0 takes nothing returns nothing', out)
        self.assertIn('function func_1 takes nothing returns nothing', out)
        self.assertIn('call BJDebugMsg("a")', out)
        self.assertIn('call DisplayTextToPlayer(Player(2), 0, 0, "b")', out)

    def test_hook(self):
        # Manual 3.8.1: ## keeps the name from being replaced again.
        self.assertEqual(body('define RemoveUnit(u) = { BJDebugMsg("removing")\n'
                              ' Remove##Unit(u) }\nnothing f(unit u) { RemoveUnit(u) }'),
                         ['call BJDebugMsg("removing")', 'call RemoveUnit(u)'])

    def test_conditions_and_error(self):
        source = ('#define MY_FLAG = true\n#define msg\n#if MY_FLAG\n#setdef msg = "on"\n'
                  '#else\n#setdef msg = "off"\n#endif\n'
                  '#if DEBUG == 1\n#define mode = 1\n#else\n#define mode = 0\n#endif\n'
                  'nothing f() { BJDebugMsg(msg); BJDebugMsg(I2S(mode)) }')
        self.assertEqual(body(source), ['call BJDebugMsg("on")', 'call BJDebugMsg(I2S(0))'])
        self.assertIn('BJDebugMsg(I2S(1))', cjass_to_vjass(source, debug=True))
        with self.assertRaises(CJassError) as caught:
            cjass_to_vjass('#if 1 == 1\n#error "stop here"\n#endif\n')
        self.assertIn('stop here', str(caught.exception))

    def test_enum(self):
        # Manual 5.1: unnamed enums share one counter, a named one has its own.
        out = body('enum { FOOTMAN, MAGE }\nenum (color) { RED, GREEN, BLUE }\n'
                   'enum { WARDEN, ARCHER }\nenum (side) LEFT RIGHT endenum\n'
                   'nothing f() { BJDebugMsg(I2S(MAGE + BLUE + ARCHER + RIGHT)) }')
        self.assertEqual(out, ['call BJDebugMsg(I2S(1 + 2 + 3 + 1))'])

    def test_include_and_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'lib.j'), 'w', encoding='utf-8') as handle:
                handle.write('#guard my_lib_j\n#define GREETING = "hello"\n')
            out = cjass_to_vjass('include "lib.j"\ninclude "lib.j"\n'
                                 'nothing f() { BJDebugMsg(GREETING) }', include_paths=[tmp])
            self.assertIn('call BJDebugMsg("hello")', out)
        with self.assertRaises(CJassError):
            cjass_to_vjass('include "nowhere.j"\n')

    def test_new_and_angle_names(self):
        self.assertEqual(body('include "cj_typesEx.j"\n'
                              'define <GetPlayableMapRect()> = bj_mapInitialPlayableArea\n'
                              'nothing f() { timer t = new timer\n rect r = GetPlayableMapRect() }'),
                         ['local timer t', 'local rect r', 'set t = CreateTimer()',
                          'set r = bj_mapInitialPlayableArea'])


class TestCompile(unittest.TestCase):
    SOURCE = ('include "cj_types.j"\n'
              'struct Counter {\n    int count = 0\n'
              '    void add(int n) { count += n }\n}\n'
              'int pow(int num, int p) { int res = 1\n'
              '    whilenot (p-- <= 0) { res *= num }\n    return res }\n'
              'void main_test() { Counter c = Counter.create()\n    c.add(pow(2, 3))\n'
              '    if (c.count == 8) { BJDebugMsg("ok") } }\n')

    def test_through_vjass_and_checker(self):
        jass = compile_cjass(self.SOURCE)
        self.assertEqual([f for f in check_program(parse(jass)) if f['severity'] == 'issue'], [])
        self.assertIn('function s__Counter_add takes integer this, integer n returns nothing',
                      jass)

    def test_runs(self):
        from wc3files.scripts.jass_vm import run_script
        jass = compile_cjass(self.SOURCE)
        result = run_script(jass, entry='main_test')
        self.assertIn('ok', result.output)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'code.j')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(self.SOURCE)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['cjass', path, '--check']), 0)
            self.assertIn('function pow takes integer num, integer p returns integer',
                          out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                main(['cjass', path, '--no-vjass'])
            self.assertIn('struct Counter', out.getvalue())


if __name__ == '__main__':
    unittest.main()
