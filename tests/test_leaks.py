import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.scripts.jass_parser import parse
from wc3files.scripts.leaks import find_leaks

HEAD = 'globals\n    location udg_P = null\n    group udg_G = null\n    trigger gg_trg_Init = null\n' \
       '    trigger gg_trg_Spell = null\n    rect gg_rct_A = null\nendglobals\n'
MAIN = ('function main takes nothing returns nothing\n'
        '    set gg_rct_A = Rect(0, 0, 64, 64)\n'
        '    call InitCustomTriggers()\n'
        '    call ConditionalTriggerExecute(gg_trg_Init)\n'
        'endfunction\n')


def leaks(body):
    return [f['message'] for f in find_leaks(parse(HEAD + body + MAIN))]


def fn(name, *lines, takes='nothing', returns='nothing'):
    inner = ''.join('    ' + line + '\n' for line in lines)
    return f'function {name} takes {takes} returns {returns}\n{inner}endfunction\n'


class TestLeaks(unittest.TestCase):
    def test_dropped(self):
        found = leaks(fn('Act',
                         "call CreateUnitAtLoc(Player(0), 'hfoo', GetRectCenter(gg_rct_A), 0)",
                         'call DisplayTextToForce(GetForceOfPlayer(Player(0)), "x")',
                         'call AddSpecialEffect("a.mdl", 0, 0)',
                         'if GetUnitLoc(GetTriggerUnit()) == null then',
                         'endif'))
        self.assertEqual(found, ['in Act: location from GetRectCenter is dropped without being '
                                 'destroyed',
                                 'in Act: force from GetForceOfPlayer is dropped without being '
                                 'destroyed',
                                 'in Act: effect from AddSpecialEffect is dropped without being '
                                 'destroyed',
                                 'in Act: location from GetUnitLoc is dropped without being '
                                 'destroyed'])

    def test_locals(self):
        found = leaks(fn('Act',
                         'local location a = GetUnitLoc(GetTriggerUnit())',
                         'local location b = Location(0, 0)',
                         'local group g = CreateGroup()',
                         'local location c',
                         'local timer t = CreateTimer()',
                         'local hashtable h = InitHashtable()',
                         'set c = PolarProjectionBJ(b, 100, 0)',
                         'call RemoveLocation(b)',
                         'call SaveLocationHandle(h, 0, 0, c)',
                         "call TimerStart(t, 1, false, null)",
                         'call GroupEnumUnitsInRect(g, gg_rct_A, null)'))
        self.assertEqual(found, ['in Act: location a from GetUnitLoc is never destroyed',
                                 'in Act: group g from CreateGroup is never destroyed'])

    def test_returned_and_script_creators(self):
        found = leaks(fn('MakeLoc', 'local location l = Location(1, 2)', 'return l',
                         returns='location')
                      + fn('Act', 'call SetUnitPositionLoc(GetTriggerUnit(), MakeLoc())',
                           'call Consume(MakeLoc())')
                      + fn('Consume', 'call RemoveLocation(l)', takes='location l'))
        self.assertEqual(found, ['in Act: location from MakeLoc is dropped without being '
                                 'destroyed'])

    def test_gui_idioms(self):
        found = leaks(fn('Act',
                         'set bj_wantDestroyGroup = true',
                         'call ForGroupBJ(GetUnitsInRectAll(gg_rct_A), null)',
                         'call ForGroupBJ(GetUnitsInRectAll(gg_rct_A), null)',
                         'call AddSpecialEffectLocBJ(udg_P, "a.mdl")',
                         'call DestroyEffect(GetLastCreatedEffectBJ())',
                         'call CreateTextTagLocBJ("x", udg_P, 0, 10, 100, 100, 100, 0)',
                         'call SetTextTagPermanentBJ(GetLastCreatedTextTag(), false)',
                         'call AddLightningLoc("CLPB", udg_P, udg_P)',
                         'set udg_P = GetUnitLoc(GetTriggerUnit())',
                         'call RemoveLocation(udg_P)'))
        # bj_wantDestroyGroup covers one ForGroupBJ; the lightning is never
        # destroyed through bj_lastCreatedLightning.
        self.assertEqual(found, ['in Act: group from GetUnitsInRectAll is dropped without being '
                                 'destroyed',
                                 'in Act: lightning from AddLightningLoc is dropped without being '
                                 'destroyed'])

    def test_last_created_in_order(self):
        # bj_lastCreatedEffect holds only the latest effect.
        found = leaks(fn('Act',
                         'call AddSpecialEffectLocBJ(udg_P, "a.mdl")',
                         'call AddSpecialEffectLocBJ(udg_P, "b.mdl")',
                         'call DestroyEffect(GetLastCreatedEffectBJ())',
                         'if udg_P == null then',
                         '    call AddSpecialEffectLocBJ(udg_P, "c.mdl")',
                         'else',
                         '    call AddSpecialEffectLocBJ(udg_P, "d.mdl")',
                         'endif',
                         'call DestroyEffectBJ(GetLastCreatedEffectBJ())',
                         'call AddSpecialEffectLocBJ(udg_P, "e.mdl")'))
        lines = [f['line'] for f in find_leaks(parse(HEAD + fn('Act',
                 'call AddSpecialEffectLocBJ(udg_P, "a.mdl")',
                 'call AddSpecialEffectLocBJ(udg_P, "b.mdl")',
                 'call DestroyEffect(GetLastCreatedEffectBJ())') + MAIN))]
        self.assertEqual(len(found), 2)
        self.assertEqual(lines, [9])

    def test_called_code_takes_last_created(self):
        found = leaks(fn('Act', 'call AddSpecialEffectLocBJ(udg_P, "a.mdl")',
                         'call TriggerExecute(gg_trg_Spell)',
                         'call AddSpecialEffectLocBJ(udg_P, "b.mdl")', 'call Cleanup()')
                      + fn('Cleanup', 'call DestroyEffect(GetLastCreatedEffectBJ())'))
        self.assertEqual(found, [])

    def test_globals_and_run_once(self):
        init_trigger = fn('InitTrig_Init', 'set gg_trg_Init = CreateTrigger()',
                          'call TriggerAddAction(gg_trg_Init, function Trig_Init_Actions)')
        spell_trigger = fn('InitTrig_Spell', 'set gg_trg_Spell = CreateTrigger()',
                           'call TriggerRegisterAnyUnitEventBJ(gg_trg_Spell, '
                           'EVENT_PLAYER_UNIT_SPELL_EFFECT)',
                           'call TriggerAddAction(gg_trg_Spell, function Trig_Spell_Actions)')
        found = leaks(fn('Trig_Init_Actions', 'set udg_G = CreateGroup()',
                         'call AddSpecialEffect("decor.mdl", 0, 0)',
                         'call CreateUnitAtLoc(Player(0), \'hfoo\', GetRectCenter(gg_rct_A), 0)')
                      + fn('Trig_Spell_Actions', 'set udg_P = GetSpellTargetLoc()')
                      + init_trigger + spell_trigger
                      + fn('InitCustomTriggers', 'call InitTrig_Init()', 'call InitTrig_Spell()'))
        # The init trigger runs once, so nothing it loses is reported; the
        # spell trigger runs on every cast.
        self.assertEqual(found, ['in Trig_Spell_Actions: location udg_P from GetSpellTargetLoc is '
                                 'never destroyed by any function'])

    def test_execute_func_initializer(self):
        found = leaks('globals\n    rect L__r = null\nendglobals\n'
                      + fn('L__Init', 'set L__r = Rect(0, 0, 1, 1)')
                      + fn('main', 'call ExecuteFunc("L__Init")') + fn('config'))
        self.assertEqual(found, [])

    def test_check_cli(self):
        script = HEAD + fn('Act', 'call AddSpecialEffect("a.mdl", 0, 0)') + MAIN \
            + fn('InitCustomTriggers')
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'war3map.j')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(script)
            for extra, expected in (([], 1), (['--no-leaks'], 0)):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err):
                    self.assertEqual(main(['jass', path, '--check'] + extra), 0)
                self.assertEqual(out.getvalue().count('warning: in Act: effect'), expected)
            self.assertIn('1 warning(s)', main_err(path))


def main_err(path):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        main(['jass', path, '--check'])
    return err.getvalue()


if __name__ == '__main__':
    unittest.main()
