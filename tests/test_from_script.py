"""Tests for recovering GUI triggers from GUI-generated JASS.

The World Editor compiles every GUI trigger to a fixed JASS shape, so the
reverse is tractable for the parts it generated and nothing else. These use
real TriggerData layouts with scripts written here; the corpus figures (10
Blizzard campaign maps, 63.1% of entries recovered) are in TODO.md.
"""
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.wtg import trigger_text as tt
from wc3files.wtg.from_script import reconstruct_from_jass
from wc3files.wtg.trigger_data import ACTION, CONDITION, EVENT


SIMPLE = '''
function Trig_Example_Actions takes nothing returns nothing
    call AddHeroXPSwapped( 1000, GetTriggerUnit(), false )
endfunction
function InitTrig_Example takes nothing returns nothing
    set gg_trg_Example = CreateTrigger(  )
    call TriggerRegisterTimerEventPeriodic( gg_trg_Example, 2.00 )
    call TriggerAddAction( gg_trg_Example, function Trig_Example_Actions )
endfunction
'''


def only_trigger(source):
    result = reconstruct_from_jass(source)
    assert len(result['triggers']) == 1, result['triggers']
    return result['triggers'][0], result['coverage']


class TestTriggerRecovery(unittest.TestCase):
    def test_an_init_function_becomes_one_named_trigger(self):
        trigger, _ = only_trigger(SIMPLE)
        self.assertEqual(trigger['name'], 'Example')

    def test_trigger_register_calls_become_events(self):
        trigger, _ = only_trigger(SIMPLE)
        events = [f for f in trigger['functions'] if f['type'] == EVENT]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['name'], 'TriggerRegisterTimerEventPeriodic')
        # The trigger handle itself is not one of the event's parameters.
        self.assertEqual(len(events[0]['parameters']), 1)

    def test_the_action_function_is_followed_and_its_calls_recovered(self):
        trigger, _ = only_trigger(SIMPLE)
        actions = [f for f in trigger['functions'] if f['type'] == ACTION]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['name'], 'AddHeroXPSwapped')
        self.assertEqual(len(actions[0]['parameters']), 3)

    def test_conditions_are_followed_through_the_condition_wrapper(self):
        source = '''
function Trig_T_Conditions takes nothing returns boolean
    call DoNothing(  )
    return true
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddCondition( gg_trg_T, Condition( function Trig_T_Conditions ) )
endfunction
'''
        trigger, _ = only_trigger(source)
        conditions = [f for f in trigger['functions'] if f['type'] == CONDITION]
        self.assertTrue(conditions)
        self.assertEqual(conditions[0]['name'], 'DoNothing')

    def test_a_disabled_trigger_is_marked_initially_off(self):
        source = SIMPLE.replace(
            '    call TriggerAddAction',
            '    call DisableTrigger( gg_trg_Example )\n    call TriggerAddAction')
        trigger, _ = only_trigger(source)
        self.assertFalse(trigger['is_initially_on'])

    def test_a_script_with_no_init_functions_yields_no_triggers(self):
        result = reconstruct_from_jass('function main takes nothing returns nothing\n'
                                       'endfunction\n')
        self.assertEqual(result['triggers'], [])
        self.assertEqual(result['coverage']['ratio'], 0.0)


class TestParameterConversion(unittest.TestCase):
    def _action(self, source):
        trigger, _ = only_trigger(source)
        return [f for f in trigger['functions'] if f['type'] == ACTION][0]

    def test_a_preset_is_chosen_by_the_parameters_declared_type(self):
        # 'false' is the script value of 48 different presets; only the
        # declared type (showhideoption) picks the right one. Naming the
        # wrong preset would be worse than naming none, because the output
        # would look correct.
        action = self._action(SIMPLE)
        show_hide = action['parameters'][2]
        self.assertEqual(show_hide['type'], tt.PRESET)
        self.assertEqual(show_hide['value'], 'ShowHideHide')
        self.assertEqual(
            tt.TriggerText.default().describe_parameter(show_hide), 'Hide')

    def test_a_nested_gui_call_becomes_a_function_parameter(self):
        action = self._action(SIMPLE)
        hero = action['parameters'][1]
        self.assertEqual(hero['type'], tt.FUNCTION)
        self.assertEqual(hero['function']['name'], 'GetTriggerUnit')

    def test_a_literal_becomes_a_plain_value(self):
        action = self._action(SIMPLE)
        self.assertEqual(action['parameters'][0]['type'], tt.STRING)
        self.assertEqual(action['parameters'][0]['value'], '1000')

    def test_a_user_variable_becomes_a_variable_parameter(self):
        source = '''
function Trig_V_Actions takes nothing returns nothing
    call AddHeroXPSwapped( udg_amount, GetTriggerUnit(), false )
endfunction
function InitTrig_V takes nothing returns nothing
    set gg_trg_V = CreateTrigger(  )
    call TriggerAddAction( gg_trg_V, function Trig_V_Actions )
endfunction
'''
        parameter = self._action(source)['parameters'][0]
        self.assertEqual(parameter['type'], tt.VARIABLE)
        # Without its `udg_` prefix: that belongs to the script, and the
        # editor shows and stores the bare name.
        self.assertEqual(parameter['value'], 'amount')


class TestPartialRecovery(unittest.TestCase):
    """What the editor never generated becomes custom script, not a guess."""

    SOURCE = '''
function Trig_Mixed_Actions takes nothing returns nothing
    call AddHeroXPSwapped( 1000, GetTriggerUnit(), false )
    call SomeHandWrittenHelper( 5 )
    set udg_counter = udg_counter + 1
endfunction
function InitTrig_Mixed takes nothing returns nothing
    set gg_trg_Mixed = CreateTrigger(  )
    call TriggerAddAction( gg_trg_Mixed, function Trig_Mixed_Actions )
endfunction
'''

    def test_an_unknown_call_becomes_custom_script(self):
        trigger, coverage = only_trigger(self.SOURCE)
        actions = [f for f in trigger['functions'] if f['type'] == ACTION]
        self.assertEqual(len(actions), 3)
        self.assertFalse(actions[0].get('custom'))      # a known GUI action
        self.assertTrue(actions[1].get('custom'))       # a hand-written helper
        self.assertFalse(actions[2].get('custom'))      # `set udg_x = ...`

    def test_custom_script_keeps_the_original_source(self):
        trigger, _ = only_trigger(self.SOURCE)
        texts = [f['parameters'][0]['value'] for f in trigger['functions']
                 if f.get('custom')]
        self.assertTrue(any('SomeHandWrittenHelper' in t for t in texts))

    def test_setting_a_gui_variable_is_recognized_not_dumped_as_script(self):
        # `set udg_x = ...` is the GUI's "Set Variable" action. Treating it as
        # raw script was the single biggest source of unrecovered entries on
        # the benchmark map.
        trigger, _ = only_trigger(self.SOURCE)
        actions = [f for f in trigger['functions'] if f['type'] == ACTION]
        set_variable = actions[2]
        self.assertEqual(set_variable['name'], 'SetVariable')
        self.assertEqual(set_variable['parameters'][0]['type'], tt.VARIABLE)
        self.assertEqual(set_variable['parameters'][0]['value'], 'counter')

    def test_coverage_is_reported_rather_than_implied(self):
        trigger, coverage = only_trigger(self.SOURCE)
        self.assertEqual(trigger['coverage'], {'recovered': 2, 'total': 3})
        self.assertAlmostEqual(coverage['ratio'], 2 / 3)

    def test_an_unprintable_expression_does_not_leak_a_class_name(self):
        # A parameter that is not a known GUI call is rendered as its source;
        # falling back to the node's class name produced output reading
        # "CallExpr", which tells a reader nothing.
        source = '''
function Trig_E_Actions takes nothing returns nothing
    call AddHeroXPSwapped( 1000, SomeUnknownCall( 3 ), false )
endfunction
function InitTrig_E takes nothing returns nothing
    set gg_trg_E = CreateTrigger(  )
    call TriggerAddAction( gg_trg_E, function Trig_E_Actions )
endfunction
'''
        trigger, _ = only_trigger(source)
        action = [f for f in trigger['functions'] if f['type'] == ACTION][0]
        rendered = action['parameters'][1]['value']
        self.assertIn('SomeUnknownCall', rendered)
        self.assertNotIn('CallExpr', rendered)


class TestVariableRecovery(unittest.TestCase):
    """GUI variables live in the script's `globals` block, prefixed `udg_`.

    Verified against a real map shipping both files: 1,717 variables in its
    .wtg, 1,717 recovered from its script with names matching exactly - see
    TODO.md.
    """

    SOURCE = """
globals
    integer udg_counter = 0
    unit array udg_heroes
    string udg_label = "hi"
    trigger gg_trg_Something = null
    integer not_a_gui_variable = 3
endglobals
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
endfunction
"""

    def setUp(self):
        self.result = reconstruct_from_jass(self.SOURCE)
        self.by_name = {v['name']: v for v in self.result['variables']}

    def test_the_udg_prefix_is_stripped(self):
        # The editor stores the bare name; the compiler adds the prefix.
        self.assertIn('counter', self.by_name)
        self.assertNotIn('udg_counter', self.by_name)

    def test_only_gui_variables_are_taken(self):
        # gg_trg_* is the editor's own, and a plain global is the map's.
        self.assertEqual(set(self.by_name), {'counter', 'heroes', 'label'})

    def test_type_and_array_flag_survive(self):
        self.assertEqual(self.by_name['counter']['type'], 'integer')
        self.assertFalse(self.by_name['counter']['is_array'])
        self.assertEqual(self.by_name['heroes']['type'], 'unit')
        self.assertTrue(self.by_name['heroes']['is_array'])

    def test_an_initial_value_is_kept_and_its_absence_recorded(self):
        self.assertTrue(self.by_name['counter']['is_initialized'])
        self.assertEqual(self.by_name['counter']['initial_value'], '0')
        self.assertFalse(self.by_name['heroes']['is_initialized'])

    def test_the_count_is_reported(self):
        self.assertEqual(self.result['coverage']['variables'], 3)

    def test_a_script_with_no_globals_yields_no_variables(self):
        result = reconstruct_from_jass(SIMPLE)
        self.assertEqual(result['variables'], [])


class TestPlacedObjectRecovery(unittest.TestCase):
    """Regions, cameras and sounds the editor placed on the map.

    Each also has its own file (war3map.w3r/.w3c/.w3s), so this matters most
    when those are gone - a protector commonly strips them and leaves the
    script. Verified against a real map that still has both: 116 regions in
    its .w3r, 116 recovered from its script, names matching and bounds equal
    to the float (see TODO.md).
    """

    SOURCE = """
globals
    rect gg_rct_CENTER = null
    camerasetup gg_cam_Intro = null
    sound gg_snd_Hint = null
endglobals
function CreateRegions takes nothing returns nothing
    set gg_rct_CENTER = Rect( -608.0, -4096.0, 672.0, -2176.0 )
    set gg_rct_SIDE = Rect( 100.0, 200.0, 300.0, 400.0 )
endfunction
function CreateCameras takes nothing returns nothing
    set gg_cam_Intro = CreateCameraSetup(  )
endfunction
function InitSounds takes nothing returns nothing
    set gg_snd_Hint = CreateSound( "Sound/Hint.wav", false, false, true, 10, 10, "" )
endfunction
"""

    def setUp(self):
        self.result = reconstruct_from_jass(self.SOURCE)

    def test_regions_keep_their_bounds_in_the_right_order(self):
        # Rect takes (minx, miny, maxx, maxy) - left, bottom, right, top.
        regions = {r['name']: r for r in self.result['regions']}
        self.assertEqual(set(regions), {'CENTER', 'SIDE'})
        centre = regions['CENTER']
        self.assertEqual((centre['left'], centre['bottom'],
                          centre['right'], centre['top']),
                         (-608.0, -4096.0, 672.0, -2176.0))

    def test_negative_coordinates_survive(self):
        # They arrive as a unary minus around a literal, not as a negative
        # literal, so they need handling rather than silently dropping out.
        centre = [r for r in self.result['regions'] if r['name'] == 'CENTER'][0]
        self.assertLess(centre['left'], 0)
        self.assertLess(centre['bottom'], 0)

    def test_cameras_and_sounds_are_recovered(self):
        self.assertEqual([c['name'] for c in self.result['cameras']], ['Intro'])
        sounds = self.result['sounds']
        self.assertEqual(len(sounds), 1)
        self.assertEqual(sounds[0]['name'], 'Hint')
        self.assertEqual(sounds[0]['path'], 'Sound/Hint.wav')

    def test_counts_are_reported(self):
        coverage = self.result['coverage']
        self.assertEqual(coverage['regions'], 2)
        self.assertEqual(coverage['cameras'], 1)
        self.assertEqual(coverage['sounds'], 1)

    def test_a_script_without_placed_objects_yields_none(self):
        result = reconstruct_from_jass(SIMPLE)
        self.assertEqual(result['regions'], [])
        self.assertEqual(result['cameras'], [])
        self.assertEqual(result['sounds'], [])


class TestTriggerFileAccessors(unittest.TestCase):
    """Reading triggers out of a .wtg without knowing its layout.

    Format 7 puts categories, triggers, comments and variables in one
    `items` list tagged by `item_type`; older files keep a separate
    `triggers` list. Reading `get_data()['triggers']` on a current map
    therefore yields nothing at all rather than failing - which is exactly
    what happened while benchmarking, making a 522-trigger map look empty.
    """

    def setUp(self):
        from wc3files.wtg.wtg_file import MapTriggersFile

        self.reader = MapTriggersFile()
        self.reader.read(_helpers.sample_path('sample.wtg'))

    def test_get_triggers_works_on_the_unified_item_list(self):
        triggers = self.reader.get_triggers()
        self.assertTrue(triggers)
        for trigger in triggers:
            # Either layout yields trigger-shaped entries.
            self.assertIn('name', trigger)

    def test_categories_are_separable_from_triggers(self):
        from wc3files.wtg.wtg_file import MapTriggersFile

        categories = self.reader.get_categories()
        for category in categories:
            self.assertEqual(category['item_type'], MapTriggersFile.ITEM_CATEGORY)
        trigger_ids = {id(t) for t in self.reader.get_triggers()}
        self.assertFalse(trigger_ids & {id(c) for c in categories})

    def test_an_old_style_triggers_list_is_used_when_present(self):
        from wc3files.wtg.wtg_file import MapTriggersFile

        reader = MapTriggersFile()
        reader._data = {'triggers': [{'name': 'Legacy'}], 'items': []}
        self.assertEqual([t['name'] for t in reader.get_triggers()], ['Legacy'])


class TestConditionIdioms(unittest.TestCase):
    """A GUI condition compiles to a wrapper the reconstruction must see through."""

    SOURCE = """
function Trig_C_Conditions takes nothing returns boolean
    if ( not ( GetUnitTypeId(GetTriggerUnit()) == 'hfoo' ) ) then
        return false
    endif
    if ( not ( udg_ready == true ) ) then
        return false
    endif
    return true
endfunction
function InitTrig_C takes nothing returns nothing
    set gg_trg_C = CreateTrigger(  )
    call TriggerAddCondition( gg_trg_C, Condition( function Trig_C_Conditions ) )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.conditions = [f for f in self.trigger['functions']
                           if f['type'] == CONDITION]

    def test_the_trailing_return_is_not_counted_as_a_condition(self):
        # `return true` closes the generated function; counting it added one
        # phantom condition per trigger (298 of them on the benchmark map).
        self.assertEqual(len(self.conditions), 2)

    def test_a_typed_comparison_is_named_from_what_it_compares(self):
        # `GetUnitTypeId` is declared in TriggerData.txt as returning
        # `unitcode`, and `OperatorCompareUnitCode` is the comparison that
        # takes `unitcode`, so the editor's own name is recoverable - it is
        # not reported as raw script.
        condition = self.conditions[0]
        self.assertEqual(condition['name'], 'OperatorCompareUnitCode')
        self.assertFalse(condition.get('custom'))

    def test_the_wrapper_is_stripped_down_to_the_test(self):
        # The operand survives as structure rather than text: the `if`
        # scaffolding and the `not` the wrapper adds are both gone.
        operand = self.conditions[0]['parameters'][0]
        self.assertEqual(operand['function']['name'], 'GetUnitTypeId')
        inner = operand['function']['parameters'][0]
        self.assertEqual(inner['function']['name'], 'GetTriggerUnit')

    def test_an_untypable_comparison_is_still_reported_as_script(self):
        # `udg_ready` is a bare variable, so nothing declares its GUI type
        # and there is no honest way to name the comparison. It stays raw
        # rather than being guessed at.
        condition = self.conditions[1]
        self.assertEqual(condition['name'], 'CustomScriptCode')
        text = condition['parameters'][0]['value']
        self.assertIn('udg_ready', text)
        self.assertNotIn('return false', text)
        self.assertNotIn('not', text.replace('nothing', ''))

    def test_each_condition_is_its_own_entry(self):
        self.assertEqual(self.conditions[0]['name'], 'OperatorCompareUnitCode')
        self.assertIn('udg_ready',
                      self.conditions[1]['parameters'][0]['value'])


class TestIfThenElseIsRebuiltAsTheGuiConstruct(unittest.TestCase):
    SOURCE = """
function Trig_T_Func001C takes nothing returns boolean
    if ( not ( GetUnitTypeId(GetTriggerUnit()) == 104 ) ) then
        return false
    endif
    return true
endfunction
function Trig_T_Actions takes nothing returns nothing
    if ( Trig_T_Func001C() ) then
        call KillUnit( GetTriggerUnit() )
    else
        call RemoveUnit( GetTriggerUnit() )
    endif
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.actions = [f for f in self.trigger['functions']
                        if f['type'] == ACTION]

    def test_an_if_in_an_actions_body_is_not_raw_script(self):
        self.assertEqual(len(self.actions), 1)
        self.assertEqual(self.actions[0]['name'], 'IfThenElseMultiple')
        self.assertFalse(self.actions[0].get('custom'))

    def test_children_carry_the_branch_the_editor_stores(self):
        # 0 conditions, 1 then-actions, 2 else-actions - the editor's own
        # tagging, taken from a real .wtg.
        children = self.actions[0]['children']
        self.assertEqual([(c['branch'], c['name']) for c in children], [
            (0, 'OperatorCompareUnitCode'),
            (1, 'KillUnit'),
            (2, 'RemoveUnit'),
        ])

    def test_the_generated_condition_function_is_followed_through(self):
        # The test is lifted into Trig_T_Func001C; reporting that call
        # instead of what it checks would lose the condition.
        condition = self.actions[0]['children'][0]
        self.assertEqual(condition['type'], CONDITION)
        self.assertEqual(condition['parameters'][0]['function']['name'],
                         'GetUnitTypeId')


class TestTheEditorsOwnScriptNamesAreFollowed(unittest.TestCase):
    """TriggerData.txt's `_X_ScriptName=Y` lines are the only record of the
    GUI functions the editor emits under a different name."""

    SOURCE = """
function Trig_T_Func002 takes nothing returns nothing
    call RemoveUnit( GetEnumUnit() )
    call KillUnit( GetEnumUnit() )
endfunction
function Trig_T_Actions takes nothing returns nothing
    call SetHeroLevelBJ( GetTriggerUnit(), 5, true )
    call ForGroupBJ( udg_g, function Trig_T_Func002 )
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.actions = [f for f in self.trigger['functions']
                        if f['type'] == ACTION]

    def test_a_renamed_action_comes_back_under_its_gui_name(self):
        # The editor stores SetHeroLevel and emits SetHeroLevelBJ.
        self.assertEqual(self.actions[0]['name'], 'SetHeroLevel')

    def test_an_enumeration_inlines_the_function_it_runs(self):
        enumeration = self.actions[1]
        self.assertEqual(enumeration['name'], 'ForGroupMultiple')
        # All on branch 0 - unlike if/then/else, which splits its children.
        self.assertEqual([(c['branch'], c['name'])
                          for c in enumeration['children']],
                         [(0, 'RemoveUnit'), (0, 'KillUnit')])

    def test_the_function_reference_is_not_left_as_a_parameter(self):
        # It became the children; repeating it as a parameter would be the
        # compiled form leaking into the rebuilt GUI one.
        enumeration = self.actions[1]
        self.assertEqual(len(enumeration['parameters']), 1)

    def test_a_group_built_with_a_filter_is_not_mistaken_for_the_callback(self):
        # The callback is passed directly. Looking inside nested calls too
        # swallowed the group itself whenever it was built by something
        # taking a filter, leaving the enumeration with no arguments.
        source = """
function Trig_T_Func002 takes nothing returns nothing
    call RemoveUnit( GetEnumUnit() )
endfunction
function Trig_T_Actions takes nothing returns nothing
    call ForGroupBJ( GetUnitsInRectMatching(gg_rct_A, Condition(function Trig_T_Filter)), function Trig_T_Func002 )
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""
        trigger, _ = only_trigger(source)
        enumeration = [f for f in trigger['functions']
                       if f['type'] == ACTION][0]
        self.assertEqual(enumeration['name'], 'ForGroupMultiple')
        self.assertEqual(len(enumeration['parameters']), 1)
        self.assertEqual([c['name'] for c in enumeration['children']],
                         ['RemoveUnit'])


class TestGuiCommentsAreRecovered(unittest.TestCase):
    """A GUI Comment action compiles to a plain `//` line and leaves nothing
    else behind, so it is recoverable only if comments survive lexing."""

    SOURCE = """
function Trig_T_Actions takes nothing returns nothing
    // set things up
    call KillUnit( GetTriggerUnit() )
    // and clean up
    call RemoveUnit( GetTriggerUnit() )
    // trailing note
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)

    def test_comments_come_back_in_the_order_they_appear(self):
        names = [(f['name'], f['parameters'][0]['value'] if
                  f['name'] == 'CommentString' else None)
                 for f in self.trigger['functions'] if f['type'] == ACTION]
        self.assertEqual(names, [
            ('CommentString', 'set things up'),
            ('KillUnit', None),
            ('CommentString', 'and clean up'),
            ('RemoveUnit', None),
            ('CommentString', 'trailing note'),
        ])

    def test_a_comment_after_the_last_statement_is_not_dropped(self):
        actions = [f for f in self.trigger['functions'] if f['type'] == ACTION]
        self.assertEqual(actions[-1]['name'], 'CommentString')


class TestCommentsDoNotDisturbTheParser(unittest.TestCase):
    def test_structural_equality_ignores_the_recorded_lines(self):
        # Line numbers are kept off ir.Node._fields so the parse -> print ->
        # re-parse round-trip check, which compares structurally, is
        # unaffected by them.
        from wc3files.scripts import parse_jass
        from wc3files.scripts.jass_printer import print_program
        source = """
function F takes nothing returns nothing
    // a comment that shifts every line below it
    call KillUnit( null )
endfunction
"""
        first = parse_jass(source)
        self.assertEqual(parse_jass(print_program(first)), first)


class TestCountedLoopsAreRebuilt(unittest.TestCase):
    SOURCE = """
function Trig_T_Actions takes nothing returns nothing
    set bj_forLoopAIndex = 1
    set bj_forLoopAIndexEnd = 9
    loop
        exitwhen bj_forLoopAIndex > bj_forLoopAIndexEnd
        call KillUnit( null )
        set bj_forLoopAIndex = bj_forLoopAIndex + 1
    endloop
    set udg_i = 2
    loop
        exitwhen udg_i > 7
        call RemoveUnit( null )
        set udg_i = udg_i + 1
    endloop
    loop
        exitwhen udg_done
        call KillUnit( null )
    endloop
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.actions = [f for f in self.trigger['functions']
                        if f['type'] == ACTION]

    def test_the_integer_a_loop_is_rebuilt_with_its_bounds(self):
        loop = self.actions[0]
        self.assertEqual(loop['name'], 'ForLoopAMultiple')
        self.assertEqual([p['value'] for p in loop['parameters']], ['1', '9'])
        self.assertEqual([(c['branch'], c['name']) for c in loop['children']],
                         [(0, 'KillUnit')])

    def test_a_loop_over_a_user_variable_names_that_variable(self):
        loop = self.actions[1]
        self.assertEqual(loop['name'], 'ForLoopVarMultiple')
        self.assertEqual(loop['parameters'][0]['value'], 'i')
        self.assertEqual([p['value'] for p in loop['parameters'][1:]],
                         ['2', '7'])

    def test_a_hand_written_loop_is_not_forced_into_a_gui_shape(self):
        # No counter, no increment - the author wrote this one, and
        # reporting it as a GUI loop would be an invention.
        self.assertTrue(self.actions[2].get('custom'))
        self.assertEqual(self.actions[2]['name'], 'CustomScriptCode')


class TestParametersMatchHowTheEditorStoresThem(unittest.TestCase):
    SOURCE = """
function Trig_T_Actions takes nothing returns nothing
    call SetPlayerStateBJ( Player(10), PLAYER_STATE_RESOURCE_GOLD, 1 )
    call SetUnitMoveSpeed( udg_HeroArray[1], 1.0 )
    set udg_Counter[2] = 5
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddAction( gg_trg_T, function Trig_T_Actions )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.actions = [f for f in self.trigger['functions']
                        if f['type'] == ACTION]

    def test_a_preset_spelled_as_a_call_is_recognised(self):
        # The editor's Player10 is Player(10) in the script, which
        # TriggerData.txt records as that preset's script text.
        parameter = self.actions[0]['parameters'][0]
        self.assertEqual(parameter['type'], tt.PRESET)
        self.assertEqual(parameter['value'], 'Player10')

    def test_a_real_is_written_with_two_decimals(self):
        self.assertEqual(self.actions[1]['parameters'][1]['value'], '1.00')

    def test_a_variable_loses_its_udg_prefix(self):
        parameter = self.actions[1]['parameters'][0]
        self.assertEqual(parameter['value'], 'HeroArray')

    def test_an_array_index_is_an_integer_not_a_preset(self):
        # Without saying the index is an integer, a bare `1` matched
        # whichever preset happens to compile to 1.
        indexer = self.actions[1]['parameters'][0]['array_indexer']
        self.assertEqual(indexer['value'], '1')
        self.assertEqual(indexer['type'], tt.STRING)

    def test_a_value_of_unknown_type_is_not_guessed_into_a_preset(self):
        # A Set Variable's value has no declared type, and `5` is the script
        # value of more than one preset, so naming one would be a coin toss.
        value = self.actions[2]['parameters'][1]
        self.assertEqual(value['type'], tt.STRING)
        self.assertEqual(value['value'], '5')


class TestGroupedConditionsAreRebuilt(unittest.TestCase):
    SOURCE = """
function Trig_T_Func001C takes nothing returns boolean
    if ( ( GetUnitTypeId(GetTrainedUnit()) == 104 ) ) then
        return true
    endif
    if ( ( GetSpellAbilityId() == 105 ) ) then
        return true
    endif
    return false
endfunction
function Trig_T_Conditions takes nothing returns boolean
    if ( not ( Trig_T_Func001C() ) ) then
        return false
    endif
    return true
endfunction
function InitTrig_T takes nothing returns nothing
    set gg_trg_T = CreateTrigger(  )
    call TriggerAddCondition( gg_trg_T, Condition( function Trig_T_Conditions ) )
endfunction
"""

    def setUp(self):
        self.trigger, _ = only_trigger(self.SOURCE)
        self.conditions = [f for f in self.trigger['functions']
                           if f['type'] == CONDITION]

    def test_an_or_block_is_recognised_by_its_shape(self):
        # `if (x) then return true endif ... return false` is how the editor
        # compiles "any of these", the mirror of the ordinary conditions
        # function. Confirmed against a real map.
        self.assertEqual(len(self.conditions), 1)
        self.assertEqual(self.conditions[0]['name'], 'OrMultiple')

    def test_the_conditions_inside_it_are_recovered(self):
        # Reported as children, as the editor stores them - keeping the
        # operands as raw text lost these entirely.
        self.assertEqual([(c['branch'], c['name'])
                          for c in self.conditions[0]['children']],
                         [(0, 'OperatorCompareUnitCode'),
                          (0, 'OperatorCompareAbilityId')])

    def test_a_grouped_block_carries_no_flat_parameters(self):
        self.assertEqual(self.conditions[0]['parameters'], [])


class TestMapInitializationEventIsRecovered(unittest.TestCase):
    SOURCE = """
function Trig_Melee_Initialization_Actions takes nothing returns nothing
    call KillUnit( null )
endfunction
function InitTrig_Melee_Initialization takes nothing returns nothing
    set gg_trg_Melee_Initialization = CreateTrigger(  )
    call TriggerAddAction( gg_trg_Melee_Initialization, function Trig_Melee_Initialization_Actions )
endfunction
function RunInitializationTriggers takes nothing returns nothing
    call ConditionalTriggerExecute( gg_trg_Melee_Initialization )
endfunction
"""

    def test_the_event_is_taken_from_the_initialisation_runner(self):
        # "Map initialization" compiles to no TriggerRegister call at all;
        # being run from RunInitializationTriggers is the only trace left.
        trigger, _ = only_trigger(self.SOURCE)
        events = [f for f in trigger['functions'] if f['type'] == EVENT]
        self.assertEqual([f['name'] for f in events],
                         ['MapInitializationEvent'])

    def test_a_trigger_the_runner_does_not_name_has_no_such_event(self):
        source = self.SOURCE.replace(
            'call ConditionalTriggerExecute( gg_trg_Melee_Initialization )', '')
        trigger, _ = only_trigger(source)
        self.assertEqual(
            [f for f in trigger['functions'] if f['type'] == EVENT], [])


class TestRendersThroughTriggerText(unittest.TestCase):
    def test_a_recovered_trigger_renders_as_a_sentence(self):
        trigger, _ = only_trigger(SIMPLE)
        lines = tt.describe_trigger(trigger, tt.TriggerText.default())
        joined = '\n'.join(lines)
        self.assertIn('Example', joined)
        # Two decimals, as the editor writes every real.
        self.assertIn('Every 2.00 seconds of game time', joined)
        self.assertIn('Add 1000 experience to (Triggering unit), Hide level-up graphics',
                      joined)


if __name__ == '__main__':
    unittest.main()
