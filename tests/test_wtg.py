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


if __name__ == '__main__':
    unittest.main()
