"""Real tests for war3map.wtg / war3map.wct (MapTriggersFile / MapCustomTextTriggersFile)
and the bundled real TriggerData.txt."""
import os
import tempfile
import unittest

import _helpers
from wc3files import MapTriggersFile, MapCustomTextTriggersFile
from wc3files.wtg.data import TriggerItemType
from wc3files.wtg.trigger_data import TriggerData, EVENT


class TestWtgRoundTrip(unittest.TestCase):
    def setUp(self):
        self.wtg = MapTriggersFile()
        self.wtg.read(_helpers.sample_path('sample.wtg'))

    def test_known_fields(self):
        data = self.wtg.get_data()
        self.assertEqual(data['format_version'], 7)
        self.assertEqual(data['subversion'], -2147483647)
        categories = [i for i in data['items'] if i['item_type'] == TriggerItemType.CATEGORY.value]
        triggers = [i for i in data['items'] if i['item_type'] == TriggerItemType.GUI.value]
        self.assertEqual(categories[0]['name'], 'Sample Category')
        self.assertEqual(triggers[0]['name'], 'Sample Trigger')
        self.assertEqual(data['variables'][0]['name'], 'udg_SampleInt')

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.wtg'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.wtg')
            self.wtg.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)


class TestWctRoundTrip(unittest.TestCase):
    def setUp(self):
        self.wct = MapCustomTextTriggersFile()
        self.wct.read(_helpers.sample_path('sample.wct'))

    def test_known_fields(self):
        data = self.wct.get_data()
        self.assertEqual(len(data['triggers']), 1)
        self.assertIn('BJDebugMsg', data['triggers'][0])

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.wct'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.wct')
            self.wct.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)


class TestTriggerData(unittest.TestCase):
    """The bundled real TriggerData.txt - the file whose function-argument-count
    lookup this format's own binary layout depends on (see MapTriggersFile's docstring)."""

    def test_default_loads_and_knows_real_functions(self):
        trigger_data = TriggerData.default()
        # MapInitializationEvent is a real, extremely common GUI event with zero
        # parameters - a good sentinel for "the nothing-vs-one-arg distinction
        # documented in TODO.md is handled correctly" (a 'nothing' arg type must
        # not be miscounted as one real parameter).
        self.assertEqual(trigger_data.arg_count(EVENT, 'MapInitializationEvent'), 0)

    def test_unknown_function_raises(self):
        trigger_data = TriggerData.default()
        with self.assertRaises(KeyError):
            trigger_data.arg_count(EVENT, 'ThisIsNotARealTriggerFunction')

    def test_a_native_or_comparison_missing_from_the_file_still_has_a_count(self):
        # Extended editors expose natives as GUI functions with the native's
        # own parameters, and every OperatorCompare* condition takes 3.
        from wc3files.wtg.trigger_data import ACTION, CALL, CONDITION
        trigger_data = TriggerData.default()
        for function_type, name in ((CALL, 'GetHandleId'), (ACTION, 'BlzLoadTOCFile'),
                                    (CONDITION, 'OperatorCompareAttackType')):
            self.assertNotIn(name, trigger_data._arg_counts[
                {CALL: 'TriggerCalls', ACTION: 'TriggerActions',
                 CONDITION: 'TriggerConditions'}[function_type]])
        self.assertEqual(trigger_data.arg_count(CALL, 'GetHandleId'), 1)
        self.assertEqual(trigger_data.arg_count(ACTION, 'BlzLoadTOCFile'), 1)
        self.assertEqual(trigger_data.arg_count(ACTION, 'BlzFrameSetSize'), 3)
        self.assertEqual(trigger_data.arg_count(CONDITION, 'OperatorCompareAttackType'), 3)

    def test_triggers_using_them_round_trip(self):
        # Azeroth Roleplay v2.11.2.0609.w3x and Enfos_DR_Beta_V5_850.w3x
        # failed to read on exactly these functions.
        from wc3files.wtg.trigger_data import ACTION, CALL, CONDITION

        def param(kind, value, function=None):
            return {'type': kind, 'value': value, 'function': function, 'array_indexer': None}

        def function(kind, name, parameters):
            return {'type': kind, 'branch': None, 'name': name, 'enabled': True,
                    'parameters': parameters, 'children': []}

        handle_id = function(CALL, 'GetHandleId', [param(1, 'udg_Frame')])
        functions = [
            function(CONDITION, 'OperatorCompareAttackType',
                     [param(1, 'udg_Attack'), param(0, 'OperatorEqualENE'), param(0, 'ATTACK_TYPE_HERO')]),
            function(ACTION, 'BlzFrameSetSize',
                     [param(2, 'GetHandleId', handle_id), param(3, '0.10'), param(3, '0.20')]),
        ]
        triggers = MapTriggersFile()
        triggers.read(_helpers.sample_path('sample.wtg'))
        triggers.get_triggers()[0]['functions'] = functions
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'war3map.wtg')
            triggers.write(path)
            again = MapTriggersFile()
            again.read(path)
            self.assertEqual(again.get_triggers()[0]['functions'], functions)
            rewritten = os.path.join(tmp, 'again.wtg')
            again.write(rewritten)
            self.assertEqual(_helpers.read_bytes(rewritten), _helpers.read_bytes(path))


if __name__ == '__main__':
    unittest.main()
