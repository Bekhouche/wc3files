"""Tests for rendering GUI triggers the way the World Editor shows them.

The layouts come from the real bundled TriggerData.txt, so these exercise
the genuine data; only the trigger instances are constructed here. That
split is deliberate - no local map turned out to have intact GUI triggers
to test against (see TODO.md), so the trigger side has to be synthetic.
"""
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.wtg import trigger_text as tt
from wc3files.wtg.trigger_data import ACTION, CALL, CONDITION, EVENT


def call(name, *parameters):
    return {'type': ACTION, 'name': name, 'parameters': list(parameters)}


def string(value):
    return {'type': tt.STRING, 'value': value}


def preset(value):
    return {'type': tt.PRESET, 'value': value}


def variable(name, indexer=None):
    return {'type': tt.VARIABLE, 'value': name, 'array_indexer': indexer}


def function(name, *parameters):
    return {'type': tt.FUNCTION, 'value': '',
            'function': {'type': CALL, 'name': name, 'parameters': list(parameters)}}


class TestLayoutParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = tt.TriggerText.default()

    def test_the_bundled_tables_loaded(self):
        self.assertGreater(len(self.text.layouts), 1000)
        self.assertGreater(len(self.text._presets), 1000)
        self.assertGreater(len(self.text.hints), 100)

    def test_a_layout_splits_into_literals_and_placeholders(self):
        layout = self.text.layouts['AddHeroXPSwapped']
        self.assertEqual(layout,
                         ['Add ', '~Quantity', ' experience to ', '~Hero', ', ',
                          '~Show/Hide', ' level-up graphics'])

    def test_commas_inside_quoted_text_do_not_split_the_layout(self):
        # ", " is one literal piece above, not two empty ones - the quoting
        # in the file exists precisely so display text can contain a comma.
        self.assertIn(', ', self.text.layouts['AddHeroXPSwapped'])

    def test_display_names_resolve_through_the_world_edit_strings(self):
        self.assertEqual(self.text.display_name('AddHeroXPSwapped'), 'Add Experience')

    def test_hints_are_available_for_functions_that_have_them(self):
        hint = self.text.hint('SetSoundFacialAnimationLabel')
        self.assertTrue(hint and 'Label' in hint)
        self.assertIsNone(self.text.hint('NoSuchFunctionAnywhere'))


class TestRendering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = tt.TriggerText.default()

    def test_a_full_action_reads_like_the_editor_shows_it(self):
        rendered = self.text.describe(call(
            'AddHeroXPSwapped',
            string('1000'),
            function('GetTriggerUnit'),
            preset('ShowHideHide')))
        self.assertEqual(
            rendered,
            'Add 1000 experience to (Triggering unit), Hide level-up graphics')

    def test_a_preset_shows_its_display_text_not_its_script_value(self):
        # ShowHideHide's script value is 'false'; the editor shows 'Hide'.
        # Reading the wrong field produced the former.
        self.assertEqual(self.text.describe_parameter(preset('ShowHideHide')), 'Hide')

    def test_a_nested_function_parameter_is_rendered_in_parentheses(self):
        self.assertEqual(self.text.describe_parameter(function('GetTriggerUnit')),
                         '(Triggering unit)')

    def test_a_variable_renders_by_name(self):
        self.assertEqual(self.text.describe_parameter(variable('udg_counter')),
                         'udg_counter')

    def test_an_array_variable_shows_its_index(self):
        self.assertEqual(
            self.text.describe_parameter(variable('udg_list', string('3'))),
            'udg_list[3]')

    def test_a_function_with_no_layout_falls_back_to_a_plain_call(self):
        # Not every function in a map is a GUI function; showing the call is
        # honest, inventing a sentence for it would not be.
        rendered = self.text.describe(call('NotAGuiFunction', string('1'), string('2')))
        self.assertEqual(rendered, 'NotAGuiFunction(1, 2)')

    def test_a_missing_parameter_leaves_its_placeholder_visible(self):
        rendered = self.text.describe(call('AddHeroXPSwapped', string('1000')))
        self.assertIn('~Hero', rendered)
        self.assertIn('Add 1000 experience to ', rendered)

    def test_extra_parameters_are_appended_rather_than_dropped(self):
        rendered = self.text.describe(call(
            'AddHeroXPSwapped', string('1000'), function('GetTriggerUnit'),
            preset('ShowHideHide'), string('EXTRA')))
        self.assertIn('EXTRA', rendered)

    def test_recursion_is_bounded(self):
        # A parameter that points back at its own function must not hang.
        looping = {'type': tt.FUNCTION, 'value': ''}
        looping['function'] = {'type': CALL, 'name': 'Loop', 'parameters': [looping]}
        self.assertIsInstance(self.text.describe_parameter(looping), str)

    def test_an_empty_function_renders_as_its_name(self):
        self.assertEqual(self.text.describe(call('GetTriggerUnit')), 'Triggering unit')


class TestDescribeTrigger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = tt.TriggerText.default()

    def _trigger(self):
        return {
            'name': 'Example Trigger',
            'description': 'a demo',
            'functions': [
                {'type': EVENT, 'name': 'TriggerRegisterTimerEventPeriodic',
                 'parameters': [string('2.00')]},
                {'type': ACTION, 'name': 'AddHeroXPSwapped',
                 'parameters': [string('1000'), function('GetTriggerUnit'),
                                preset('ShowHideHide')]},
            ],
        }

    def test_groups_events_conditions_and_actions(self):
        lines = tt.describe_trigger(self._trigger(), self.text)
        self.assertEqual(lines[0], 'Example Trigger')
        self.assertIn('// a demo', lines[1])
        joined = '\n'.join(lines)
        self.assertIn('Events:', joined)
        self.assertIn('Actions:', joined)
        self.assertNotIn('Conditions:', joined)  # none present, so no header
        self.assertIn('Every 2.00 seconds of game time', joined)
        self.assertIn('Hide level-up graphics', joined)

    def test_a_trigger_with_no_functions_still_names_itself(self):
        lines = tt.describe_trigger({'name': 'Empty', 'functions': []}, self.text)
        self.assertEqual(lines, ['Empty'])

    def test_an_unnamed_trigger_is_labelled_rather_than_blank(self):
        lines = tt.describe_trigger({'functions': []}, self.text)
        self.assertEqual(lines, ['(unnamed trigger)'])


if __name__ == '__main__':
    unittest.main()
