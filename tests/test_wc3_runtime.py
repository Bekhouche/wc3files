"""Tests for wc3files.scripts.jass_to_lua and wc3files.scripts.wc3_runtime.

The translator is checked by running its output: each case is JASS whose
meaning differs from the obvious Lua (integer division, string +, arrays,
loops). The runtime is checked with small map scripts that use what real
war3map.lua files do at start-up: config/main, players, triggers, timers,
waits, groups, hashtables, and text output; and on its two reports, errors
(with the Lua position) and argument warnings.
"""

import contextlib
import io
import os
import tempfile
import unittest

import _helpers  # noqa: F401  (puts src/ on the path)

from wc3files.cli.main import main as cli_main
from wc3files.scripts import parse_jass
from wc3files.scripts.jass_to_lua import jass_to_lua
from wc3files.scripts.lua_vm import LuaVM
from wc3files.scripts.wc3_runtime import MapRuntime, run_map_script


class TestJassToLua(unittest.TestCase):

     def test_integer_division_truncates(self):
          vm = LuaVM(version='5.3')
          vm.execute(jass_to_lua(parse_jass(
               'globals\ninteger a = 0\ninteger b = 0\nreal c = 0\nendglobals\n'
               'function main takes nothing returns nothing\n'
               'set a = -7 / 2\nset b = 7 / 2\nset c = -7 / 2.0\nendfunction\n')), 't')
          vm.call(vm.globals.get('main'), [])
          self.assertEqual([vm.globals.get(n) for n in 'abc'], [-3, 3, -3.5])

     def test_strings_arrays_loops_rawcodes(self):
          vm = LuaVM(version='5.3')
          vm.execute(jass_to_lua(parse_jass(
               'globals\nstring s = null\ninteger array counts\ninteger total = 0\n'
               'integer id = 0\nboolean neg = false\nendglobals\n'
               'function main takes nothing returns nothing\n'
               '    local integer i = 0\n'
               '    set s = s + "a" + null\n'
               '    loop\n        exitwhen i == 4\n        set counts[i] = i * 2\n'
               '        set i = i + 1\n    endloop\n'
               '    set total = counts[3] + counts[9]\n'
               "    set id = 'hfoo'\n"
               '    set neg = not (1 == 2)\n'
               'endfunction\n')), 't')
          vm.call(vm.globals.get('main'), [])
          self.assertEqual([vm.globals.get(n) for n in ('s', 'total', 'id', 'neg')],
                           ['a', 6, 0x68666F6F, True])

     def test_lua_keyword_names(self):
          vm = LuaVM(version='5.3')
          vm.execute(jass_to_lua(parse_jass(
               'function f takes integer end returns integer\nreturn end + 1\nendfunction\n')), 't')
          self.assertEqual(vm.call(vm.globals.get('f'), [2]), [3])


class TestRuntime(unittest.TestCase):

     def test_start_up_runs(self):
          report = run_map_script('''
               function config() SetPlayers(2) end
               function main()
                    InitBlizzard()
                    local t = CreateTrigger()
                    TriggerRegisterTimerEvent(t, 0.5, false)
                    TriggerAddAction(t, function()
                         DisplayTextToPlayer(GetLocalPlayer(), 0, 0, "trigger at " .. R2S(TimerGetElapsed(bj_gameStartedTimer)))
                         TriggerSleepAction(1.0)
                         udg_after_wait = true
                    end)
                    local g = CreateGroup()
                    for i = 0, 2 do GroupAddUnit(g, CreateUnit(Player(0), FourCC("hfoo"), i * 10, 0, 0)) end
                    udg_sum = 0
                    ForGroup(g, function() udg_sum = udg_sum + GetUnitX(GetEnumUnit()) end)
                    local h = InitHashtable()
                    SaveInteger(h, 1, 2, 42)
                    udg_loaded = LoadInteger(h, 1, 2)
                    TimerStart(CreateTimer(), 0.0, false, function() udg_timer = GetPlayers() end)
               end
          ''', seconds=3)
          self.assertEqual(report.errors, [])
          self.assertTrue(any(m.startswith('trigger at') for m in report.messages))

     def test_values_after_run(self):
          runtime = MapRuntime()
          runtime.run('''
               function config() SetPlayers(3) end
               function main()
                    udg_sum = 0
                    local g = CreateGroup()
                    for i = 1, 3 do GroupAddUnit(g, CreateUnit(Player(0), FourCC("hfoo"), i, 0, 0)) end
                    ForGroup(g, function() udg_sum = udg_sum + GetUnitX(GetEnumUnit()) end)
                    local h = InitHashtable()
                    SaveInteger(h, 1, 2, 42)
                    udg_loaded = LoadInteger(h, 1, 2)
                    TimerStart(CreateTimer(), 0.25, false, function() udg_timer = GetPlayers() end)
                    local t = CreateTrigger()
                    TriggerAddAction(t, function() TriggerSleepAction(2.0); udg_woke = true end)
                    TriggerExecute(t)
               end
          ''', seconds=1)
          g = runtime.vm.globals
          self.assertEqual([g.get('udg_sum'), g.get('udg_loaded'), g.get('udg_timer'),
                            g.get('udg_woke')], [6.0, 42, 3, None])
          runtime.advance(1.5)
          self.assertTrue(g.get('udg_woke'))

     def test_errors_and_warnings(self):
          report = run_map_script('''
               function main()
                    KillUnit(CreateTrigger())
                    TimerStart(CreateTimer(), 1.0, false, function()
                         NotDefinedAnywhere()
                    end)
               end
          ''', 'war3map.lua', seconds=2)
          self.assertEqual(len(report.errors), 1)
          self.assertIn("war3map.lua:5: attempt to call a nil value (global 'NotDefinedAnywhere')",
                        report.errors[0])
          self.assertEqual(len(report.warnings), 1)
          self.assertIn("'KillUnit' argument 1 (whichUnit): unit expected, got trigger",
                        report.warnings[0])

     def test_no_debug_library(self):
          # maps' own code tests for it (the CSharp.lua runtime does)
          report = run_map_script('function main() udg_has_debug = debug ~= nil end')
          self.assertEqual(report.errors, [])

     def test_weak_values_survive_until_a_collection(self):
          # Found running a real (obfuscated) map: a fresh table stored in a
          # weak-valued table was gone on the next line.
          vm = LuaVM(version='5.3')
          self.assertEqual(vm.execute('''
               local t = setmetatable({}, {__mode = "v"})
               t.k = {}
               local before = t.k ~= nil
               collectgarbage()
               return before, t.k == nil
          ''', 't'), [True, True])

     def test_cli_runs_a_jass_script(self):
          with tempfile.TemporaryDirectory() as folder:
               path = os.path.join(folder, 'war3map.j')
               with open(path, 'w', encoding='utf-8') as handle:
                    handle.write('function main takes nothing returns nothing\n'
                                 '    call DisplayTextToPlayer(Player(0), 0, 0, "hi " + I2S(7 / 2))\n'
                                 'endfunction\n')
               out = io.StringIO()
               with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    status = cli_main(['run-map', path, '--messages'])
               self.assertEqual(status, 0)
               self.assertIn('hi 3', out.getvalue())


if __name__ == '__main__':
     unittest.main()
