"""Tests for wc3files.scripts.lua_checker: war3map.lua checked against the
bundled common.j/blizzard.j and the Lua 5.3 standard library.

Each case is one of two kinds: a real mistake the checker must report (with
the line it is on), or valid Lua that looks suspicious to a naive checker and
must stay silent (shadowing locals, hooks, feature tests, metatable
operators, Lua's own number/string conversions).
"""

import contextlib
import io
import os
import tempfile
import unittest

import _helpers  # noqa: F401  (puts src/ on the path)

from wc3files.cli.main import main as cli_main
from wc3files.scripts import ScriptFile, check_lua, parse_lua


def findings(source):
     return check_lua(parse_lua(source))


def issues(source):
     return [f for f in findings(source) if f['severity'] == 'issue']


def notes(source):
     return [f for f in findings(source) if f['severity'] == 'note']


class TestReportsMistakes(unittest.TestCase):

     def test_string_object_id_suggests_fourcc(self):
          found = issues('local p = Player(0)\nCreateUnit(p, "hfoo", 0, 0, 270)\n')
          self.assertEqual(len(found), 1)
          self.assertEqual(found[0]['line'], 2)
          self.assertIn("argument 2 (unitid)", found[0]['message'])
          self.assertIn("FourCC('hfoo')", found[0]['message'])

     def test_function_where_boolexpr_is_expected(self):
          found = issues('local t = CreateTrigger()\n'
                         'local function cond() return true end\n'
                         'TriggerAddCondition(t, cond)\n')
          self.assertEqual(len(found), 1)
          self.assertIn('Condition() or Filter()', found[0]['message'])
          self.assertEqual(issues('local t = CreateTrigger()\n'
                                  'TriggerAddCondition(t, Condition(function() return true end))\n'),
                           [])

     def test_global_function_where_boolexpr_is_expected(self):
          found = issues('function Cond() return true end\n'
                         'TriggerAddCondition(CreateTrigger(), Cond)\n')
          self.assertEqual(len(found), 1)

     def test_handle_type_from_a_game_call(self):
          found = issues('local t = CreateTrigger()\nKillUnit(t)\n')
          self.assertEqual(len(found), 1)
          self.assertIn('expects a unit, got a trigger', found[0]['message'])
          # a subtype is fine: a unit is a widget
          self.assertEqual(issues('local u = CreateUnit(Player(0), FourCC("hfoo"), 0, 0, 0)\n'
                                  'SetWidgetLife(u, 10)\n'), [])

     def test_wrong_argument_count(self):
          found = issues('BlzFrameSetVertexColor(f, 255, 204, 0, 220)\n')
          self.assertEqual(len(found), 1)
          self.assertIn("'BlzFrameSetVertexColor' takes 2 argument(s), got 5",
                        found[0]['message'])
          self.assertEqual(len(issues('SetUnitX(u)\n')), 1)   # a missing real is not nil-safe

     def test_missing_handle_arguments_are_a_note(self):
          source = ('TriggerRegisterPlayerUnitEvent(CreateTrigger(), Player(0), '
                    'EVENT_PLAYER_UNIT_DEATH)\n')
          self.assertEqual(issues(source), [])
          self.assertIn('passed as nil', notes(source)[0]['message'])

     def test_result_of_a_function_returning_nothing(self):
          found = issues('local u\nSetUnitPosition(u, KillUnit(u), 0)\n')
          self.assertEqual(len(found), 1)
          self.assertIn('returns nothing', found[0]['message'])
          # as the last argument it passes no value at all
          found = issues('local u\nSetUnitX(u, KillUnit(u))\n')
          self.assertIn('takes 2 argument(s), got 1', found[0]['message'])

     def test_non_integer_float_literal(self):
          self.assertEqual(len(issues('local p = Player(1.5)\n')), 1)
          self.assertEqual(issues('local p = Player(2.0)\n'), [])

     def test_undefined_global_call(self):
          found = issues('local function f()\n  Foo()\nend\n')
          self.assertEqual(len(found), 1)
          self.assertEqual(found[0]['line'], 2)
          self.assertIn("undefined global 'Foo'", found[0]['message'])

     def test_undefined_global_read_is_a_note(self):
          self.assertEqual(issues('local x = udg_Missing\n'), [])
          self.assertIn("'udg_Missing'", notes('local x = udg_Missing\n')[0]['message'])

     def test_unknown_library_member(self):
          found = issues('local x = math.flor(2.5)\n')
          self.assertEqual(len(found), 1)
          self.assertIn("'math.flor'", found[0]['message'])

     def test_lua51_names_are_notes(self):
          source = 'local a = unpack({})\nlocal b = math.pow(2, 3)\n'
          self.assertEqual(issues(source), [])
          self.assertEqual(len(notes(source)), 2)

     def test_fourcc(self):
          self.assertEqual(len(issues('local id = FourCC("hfo")\n')), 1)
          self.assertEqual(issues('local id = FourCC("hfoo")\n'), [])

     def test_common_ai_native_is_a_note(self):
          source = 'local u\nif not UnitAlive(u) then end\n'
          self.assertEqual(issues(source), [])
          self.assertIn('common.ai', notes(source)[0]['message'])


class TestStaysSilent(unittest.TestCase):

     def test_bundled_sample(self):
          script = ScriptFile().read(_helpers.sample_path('sample.lua'))
          self.assertEqual([f for f in script.validate() if f['severity'] == 'issue'], [])

     def test_game_api_and_globals(self):
          source = ('function InitTrig()\n'
                    '  gg_trg_A = CreateTrigger()\n'
                    '  TriggerRegisterPlayerUnitEvent(gg_trg_A, Player(0), '
                    'EVENT_PLAYER_UNIT_DEATH, nil)\n'
                    '  TriggerAddAction(gg_trg_A, function()\n'
                    '    local u = GetTriggerUnit()\n'
                    '    SetUnitState(u, UNIT_STATE_LIFE, GetUnitState(u, UNIT_STATE_MAX_LIFE) / 2)\n'
                    '    bj_lastCreatedUnit = u\n'
                    '    udg_arr = __jarray(0)\n'
                    '  end)\n'
                    'end\n')
          self.assertEqual(findings(source), [])

     def test_local_shadows_a_native(self):
          self.assertEqual(issues('local CreateUnit = function(a) return a end\n'
                                  'CreateUnit("x")\n'), [])

     def test_hooked_native_uses_the_scripts_definition(self):
          self.assertEqual(issues('local old = CreateUnit\n'
                                  'function CreateUnit(id, ...) return old(Player(id), ...) end\n'
                                  'CreateUnit(0, FourCC("hfoo"), 0, 0, 0)\n'), [])

     def test_reassigned_local_has_no_known_type(self):
          self.assertEqual(issues('local x = CreateTrigger()\nx = CreateUnit(Player(0), 1, 0, 0, 0)\n'
                                  'KillUnit(x)\n'), [])

     def test_global_defined_later_or_inside_a_function(self):
          self.assertEqual(issues('function main() Helper() end\nfunction Helper() end\n'), [])
          self.assertEqual(issues('local function f() G = 1 end\nprint(G)\n'), [])

     def test_feature_tests(self):
          source = ('local unpack = table.unpack or unpack\n'
                    'local atan2 = math.atan2 or math.atan\n'
                    'if Debug then Debug.beginFile("x") end\n'
                    'local exports = {}\n'
                    'exports.Thing = Thing or {}\n'
                    'if type(Missing) == "function" then Missing() end\n')
          self.assertEqual(findings(source), [])

     def test_library_extensions(self):
          self.assertEqual(issues('function string.trim(s) return s end\n'
                                  'math.clamp = function(x) return x end\n'
                                  'print(string.trim(" a "), math.clamp(1))\n'), [])
          # through a local alias, as the CSharp.lua runtime does
          self.assertEqual(issues('do local math = math\nlocal Math = math\n'
                                  'Math.Abs = math.abs end\n'
                                  'print(math.Abs(-1))\n'), [])
          # handed to a function that may extend it
          self.assertEqual(issues('local function mixin(t) end\nmixin(math)\n'
                                  'print(math.Anything(1))\n'), [])
          self.assertEqual(len(issues('for k in pairs(math) do end\n'
                                      'print(math.Anything(1))\n')), 1)

     def test_dynamic_globals_turn_off_name_checks(self):
          found = findings('for k, v in pairs(t) do _G[k] = v end\nAnything()\n')
          self.assertEqual([f['severity'] for f in found], ['note'])
          self.assertIn('dynamically', found[0]['message'])

     def test_metatable_operators(self):
          # `/` and `..` may be overloaded, so their result type is unknown
          self.assertEqual(issues('GetPlayerId(C / "x")\nCreateUnit(C .. "y", 1, 0, 0, 0)\n'), [])

     def test_lua_conversions(self):
          self.assertEqual(issues('SetUnitX(u, "10")\nlocal i = S2I(5)\n'), [])

     def test_unknown_result_count_skips_the_count_check(self):
          self.assertEqual(issues('local u\nlocal function GetXY() return 1, 2 end\n'
                                  'SetUnitPosition(u, GetXY())\n'
                                  'local function f(...) SetUnitPosition(...) end\n'), [])
          self.assertEqual(len(issues('local u\nSetUnitPosition(u, GetUnitX(u))\n')), 1)

     def test_scoping(self):
          source = ('for i = 1, 3 do print(i) end\n'
                    'for k, v in pairs({}) do print(k, v) end\n'
                    'repeat local done = true until done\n'
                    'local obj = {}\n'
                    'function obj:m() return self end\n'
                    'local function rec(n) if n > 0 then return rec(n - 1) end end\n')
          self.assertEqual(findings(source), [])


class TestScriptFileAndCli(unittest.TestCase):

     def test_cli_check(self):
          with tempfile.TemporaryDirectory() as folder:
               path = os.path.join(folder, 'war3map.lua')
               with open(path, 'w', encoding='utf-8') as handle:
                    handle.write('local t = CreateTrigger()\nKillUnit(t)\n')
               out, err = io.StringIO(), io.StringIO()
               with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(cli_main(['jass', path, '--check']), 1)
               self.assertIn(f'{path}:2: issue:', out.getvalue())
               self.assertIn('1 issue(s), 0 note(s)', err.getvalue())

               with open(path, 'w', encoding='utf-8') as handle:
                    handle.write('KillUnit(CreateUnit(Player(0), FourCC("hfoo"), 0, 0, 0))\n')
               out = io.StringIO()
               with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli_main(['jass', path, '--check']), 0)
               self.assertEqual(out.getvalue(), '')


if __name__ == '__main__':
     unittest.main()
