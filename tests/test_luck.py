import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.luck import LuckError, compile_luck, compile_project, merge_into_script
from wc3files.scripts.checker import check_program
from wc3files.scripts.jass_parser import parse
from wc3files.scripts.jass_vm import JassVM

EXAMPLES = os.path.join(os.path.dirname(__file__), 'samples', 'luck')


def run(jass, entry, *args, natives=None):
    vm = JassVM(parse(jass))
    vm.natives.update(natives or {})
    value = vm.call(entry, *args)
    return value, vm.output


def issues(jass):
    return [f for f in check_program(parse(jass)) if f['severity'] == 'issue']


def body(jass, name):
    lines = [l.strip() for l in jass.splitlines()]
    start = next(i for i, l in enumerate(lines) if l.startswith(f'function {name} '))
    return lines[start + 1:lines.index('endfunction', start)]


class TestExamples(unittest.TestCase):
    """LuckyParser's own example project (tests/samples/luck, MIT)."""

    @classmethod
    def setUpClass(cls):
        cls.result = compile_project([EXAMPLES])

    def test_checks(self):
        self.assertEqual(issues(self.result.jass), [])
        self.assertEqual(self.result.init_function, 'LuckyParser_InitAll')
        self.assertEqual(self.result.warnings, [])

    def test_tour_runs(self):
        _, output = run(self.result.jass, 'LuckyParser_InitAll',
                        natives={'GetTriggerUnit': lambda: None})
        self.assertEqual(output, [
            'priority init', 'Hello LuckyParser', '20 + 22 = 42', 'Clamp result = 100',
            'Grouped parameter sum = 6.000', 'positive', 'while: 4', 'while: 3', 'while: 2',
            'while: 1', 'foreach index: 0', 'foreach index: 1', 'foreach index: 2',
            'foreach index: 3', 'sum = 10', 'Global counter = 1', 'private library helper',
            'public library function', 'private module helper', 'private scope helper',
            'JASS multiply = 42', 'Hello Luck island inside interop.j', 'normal init',
            'load block'])

    def test_structs_variables_and_named_arguments(self):
        jass = self.result.jass
        self.assertEqual(run(jass, 'VariableExamples', 5)[0], 16)
        self.assertEqual(run(jass, 'ImportExample', 2, 3), (5, ['Hello imports']))
        missile, output = run(jass, 'CreateDemoMissile', None)
        self.assertEqual((missile, output), (1, ['Missile speed = 900.000']))
        self.assertEqual(body(jass, 'Missile_accelerate'),
                         ['set Missile_speed[this] = (Missile_speed[this] + amount)'])

    def test_generated_names(self):
        names = [l.split()[1] for l in self.result.jass.splitlines() if l.startswith('function ')]
        for name in ('LibraryExample_LibraryHelper', 'LibraryHello', 'Missile_allocate',
                     'Missile_create', 'InitializationExample_onInit_2', 'LuckyParser_InitAll',
                     'JassMultiply', 'CallLuckFromJass'):
            self.assertIn(name, names)
        # every function after the ones it calls
        self.assertLess(names.index('Greet'), names.index('CallLuckFromJass'))
        self.assertLess(names.index('Missile_create'), names.index('CreateDemoMissile'))


class TestLanguage(unittest.TestCase):
    def test_forms_defaults_and_print(self):
        result = compile_luck(
            'fn Scale(real(x = 1.0, y = 2.0), integer times = 2) -> real:\n'
            '    return (x + y) * times\n\n'
            'nothing Show(boolean flag, real r):\n'
            '    let n = 3\n'
            '    print(flag)\n'
            '    print("n=" + n + " r=" + r + " flag=" + flag)\n'
            '    print(1 + 2)\n'
            '    print(Scale(y = 5.0))\n')
        self.assertEqual(issues(result.jass), [])
        _, output = run(result.jass, 'Show', True, 0.5)
        self.assertEqual(output, ['true', 'n=3 r=0.500 flag=true', '3', '12.000'])

    def test_namespace_globals_and_privacy(self):
        result = compile_luck(
            'library Counter:\n'
            '    global integer count = 0\n'
            '    constant integer STEP = 2\n'
            '    nothing bump():\n'
            '        count += STEP\n'
            '    public integer Next():\n'
            '        bump()\n'
            '        return count\n')
        self.assertIn('integer Counter_count = 0', result.jass)
        self.assertEqual(body(result.jass, 'Counter_bump'),
                         ['set Counter_count = (Counter_count + Counter_STEP)'])
        self.assertEqual(run(result.jass, 'Next')[0], 2)

    def test_loops_and_branches(self):
        result = compile_luck(
            'integer Sum(integer n):\n'
            '    local integer total = 0\n'
            '    for i in 1..n:\n'
            '        if i == 3:\n'
            '            total += 100\n'
            '        elif i > 4:\n'
            '            break\n'
            '        else:\n'
            '            total += i\n'
            '    var k = 0\n'
            '    loop:\n'
            '        k++\n'
            '        if k >= 2:\n'
            '            break\n'
            '    return total + k\n')
        self.assertEqual(run(result.jass, 'Sum', 10)[0], 1 + 2 + 100 + 4 + 2)

    def test_errors(self):
        for source, message in (
                ('nothing F():\n  print("x")\n', 'four spaces'),
                ('nothing F():\n    integer a = 1\n    integer a = 2\n', 'more than once'),
                ('nothing F():\n    break\n', 'inside a loop'),
                ('nothing F():\n    if true:\n        continue\n', 'continue'),
                ('nothing F():\n    G(x = 1)\nnothing G(integer y):\n    return\n', 'no parameter x'),
                ('nothing A():\n    B()\nnothing B():\n    A()\n', 'cannot be ordered')):
            with self.subTest(message=message), self.assertRaises(LuckError) as caught:
                compile_luck(source)
            self.assertIn(message, str(caught.exception))

    def test_reserved_constructs_warn(self):
        result = compile_luck('class Thing:\n    integer x\n\nnothing F():\n    return\n')
        self.assertEqual(len(result.warnings), 1)
        self.assertIn('reserved', result.warnings[0])

    def test_islands(self):
        result = compile_luck('nothing FromLuck():\n    FromJass(2)\n\n'
                              '//! language jass\n'
                              'function FromJass takes integer n returns nothing\n'
                              '    call BJDebugMsg(I2S(n))\nendfunction\n'
                              '//! language vjass\n'
                              'library V\n    function VHello takes nothing returns nothing\n'
                              '        call BJDebugMsg("v")\n    endfunction\nendlibrary\n')
        self.assertEqual(issues(result.jass), [])
        self.assertEqual(run(result.jass, 'FromLuck')[1], ['2'])
        self.assertEqual(run(result.jass, 'VHello')[1], ['v'])


class TestMap(unittest.TestCase):
    SCRIPT = ('globals\n    integer udg_x = 0\nendglobals\n'
              'function main takes nothing returns nothing\n    set udg_x = 1\nendfunction\n'
              'function config takes nothing returns nothing\nendfunction\n')

    def test_merge(self):
        result = compile_luck('onInit:\n    print("hello")\n')
        merged = merge_into_script(self.SCRIPT, result)
        self.assertEqual(body(merged, 'main'), ['set udg_x = 1', 'call LuckyParser_InitAll()'])
        self.assertEqual(run(merged, 'main')[1], ['hello'])
        self.assertEqual(merge_into_script(merged.replace('function onInit_1', 'function x'),
                                           compile_luck('nothing Y():\n    return\n')).count(
            'call LuckyParser_InitAll()'), 1)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'out.j')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['luck', EXAMPLES, '-o', out, '--check']), 0)
            with open(out, encoding='utf-8') as handle:
                self.assertIn('function LuckyParser_InitAll takes nothing returns nothing',
                              handle.read())
            script = os.path.join(tmp, 'war3map.j')
            with open(script, 'w', encoding='utf-8') as handle:
                handle.write(self.SCRIPT)
            built = os.path.join(tmp, 'built.j')
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(['luck', os.path.join(EXAMPLES, 'examples',
                                                            'initialization.luck'),
                                       '--map', script, '-o', built]), 0)
            with open(built, encoding='utf-8') as handle:
                self.assertIn('call LuckyParser_InitAll()', handle.read())


if __name__ == '__main__':
    unittest.main()
