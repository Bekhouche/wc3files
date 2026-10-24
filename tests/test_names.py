"""Tests for putting names to replay actions and object rawcodes.

Three separate things need naming and come from three places: the action id,
a numeric order id (a command, not an object), and an object rawcode - which
needs the base game's strings or, for a map's own objects, that map's data.

The map-side tests build a small map in memory with this package's own
writers, so no game files are needed.
"""
import os
import struct
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.w3g import names
from wc3files.w3o.data import ObjectFileType
from wc3files.w3o.object_names import ObjectNames


class TestActionNames(unittest.TestCase):
    def test_known_action_ids_have_descriptions(self):
        self.assertIn('ability', names.action_name(0x10).lower())
        self.assertEqual(names.action_name(0x6B), 'SyncStoredInteger')
        self.assertIn('Change Selection', names.action_name(0x16))

    def test_an_unknown_id_degrades_to_its_number(self):
        self.assertEqual(names.action_name(0xEE), 'action 0xEE')

    def test_the_tables_came_out_of_the_specification(self):
        # Generated rather than typed; a truncated table would show up here.
        self.assertGreaterEqual(len(names.ORDER_NAMES), 264)
        self.assertGreater(len(names.ACTION_NAMES), 40)


class TestOrderIds(unittest.TestCase):
    """A numeric ItemID is a command (`?? ?? 0D 00`), not an object."""

    RIGHT_CLICK = '03000d00'
    ATTACK = '0f000d00'

    def test_recognizes_a_numeric_order(self):
        self.assertTrue(names.is_order_id(self.RIGHT_CLICK))
        self.assertEqual(names.order_name(self.RIGHT_CLICK), 'rightclick')
        self.assertEqual(names.order_name(self.ATTACK), 'attack')

    def test_a_rawcode_is_not_an_order(self):
        self.assertFalse(names.is_order_id('hfoo'))
        self.assertIsNone(names.order_value('hfoo'))
        self.assertIsNone(names.order_name('hfoo'))

    def test_an_undocumented_order_still_reports_its_value(self):
        # The low bytes vary; an id the 2006 table lacks must not come back
        # empty, or a caller cannot tell it apart from a parse failure.
        # Pick a value the 2006 table does not list.
        undocumented = 'ee0f0d00'
        self.assertNotIn(names.order_value(undocumented), names.ORDER_NAMES)
        self.assertIn('0D0FEE', names.order_name(undocumented).upper())

    def test_describe_item_id_picks_the_right_namespace(self):
        self.assertEqual(names.describe_item_id(self.RIGHT_CLICK), 'rightclick')
        self.assertEqual(names.describe_item_id('abcd'), 'abcd')


class TestBaseGameObjectNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lookup = ObjectNames.base_game()

    def test_units_items_abilities_and_upgrades_all_resolve(self):
        self.assertEqual(self.lookup.get('hfoo'), 'Footman')
        self.assertEqual(self.lookup.get('Obla'), 'Blademaster')
        self.assertEqual(self.lookup.get('AOsf'), 'Feral Spirit')
        self.assertEqual(self.lookup.get('wneg'), 'Wand of Negation')

    def test_a_multi_level_name_gives_the_objects_own_name(self):
        # Upgrade names are comma-separated per level.
        self.assertEqual(self.lookup.get('Resm'), 'Strength of the Moon')

    def test_an_unknown_rawcode_returns_the_default(self):
        self.assertIsNone(self.lookup.get('zzzz'))
        self.assertEqual(self.lookup.get('zzzz', 'unknown'), 'unknown')


class TestMapObjectNames(unittest.TestCase):
    """Names for a map's own objects, from its object data and strings."""

    @classmethod
    def setUpClass(cls):
        from wc3files.w3o.object_file import ObjectFile
        from wc3files.table.wts_file import WtsFile

        # Two custom units derived from the Footman: one renamed through a
        # TRIGSTR reference, one that does not override its name at all.
        data = {
            'w3u': {
                'original': {},
                'custom': {
                    'u000': {'ID': 'hfoo',
                             'unam': {'value': 'TRIGSTR_007', 'type': 3}},
                    'u001': {'ID': 'hfoo'},
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            obj_path = os.path.join(tmp, 'war3map.w3u')
            obj = ObjectFile()
            obj.import_data(data)
            obj.write(obj_path, skin=False)
            with open(obj_path, 'rb') as handle:
                cls.w3u = handle.read()

            wts_path = os.path.join(tmp, 'war3map.wts')
            wts = WtsFile()
            wts.import_data({7: 'Elite Footman'})
            wts.write(wts_path)
            with open(wts_path, 'rb') as handle:
                cls.wts = handle.read()

    def _lookup(self, base_game=True):
        lookup = ObjectNames()
        if base_game:
            lookup.load_base_game()
        lookup.load_map_files({'war3map.w3u': self.w3u,
                               'war3map.wts': self.wts})
        return lookup

    def test_a_trigstr_name_is_resolved_through_the_maps_strings(self):
        self.assertEqual(self._lookup().get('u000'), 'Elite Footman')

    def test_a_custom_object_without_its_own_name_inherits_its_templates(self):
        # u001 derives from hfoo and overrides nothing, so it is a Footman.
        self.assertEqual(self._lookup().get('u001'), 'Footman')

    def test_the_category_of_a_map_object_is_recorded(self):
        self.assertEqual(self._lookup().category('u000'), ObjectFileType.Units)

    def test_base_game_objects_still_resolve_alongside_map_objects(self):
        lookup = self._lookup()
        self.assertEqual(lookup.get('hfoo'), 'Footman')
        self.assertEqual(lookup.get('u000'), 'Elite Footman')

    def test_without_the_base_game_a_template_name_is_simply_unknown(self):
        # Nothing should be invented when the base tables are not loaded.
        lookup = self._lookup(base_game=False)
        self.assertEqual(lookup.get('u000'), 'Elite Footman')
        self.assertIsNone(lookup.get('u001'))

    def test_a_missing_strings_file_leaves_the_placeholder_unresolved(self):
        lookup = ObjectNames()
        lookup.load_map_files({'war3map.w3u': self.w3u})
        # The raw TRIGSTR is kept rather than guessed at or dropped.
        self.assertEqual(lookup.get('u000'), 'TRIGSTR_007')

    def test_an_unreadable_object_file_does_not_break_the_lookup(self):
        lookup = ObjectNames().load_base_game()
        lookup.load_map_files({'war3map.w3u': b'not an object file at all',
                               'war3map.wts': self.wts})
        self.assertEqual(lookup.get('hfoo'), 'Footman')


class TestDescribeAction(unittest.TestCase):
    def test_describes_an_ability_with_its_object_name(self):
        lookup = ObjectNames.base_game()
        line = names.describe_action(
            {'id': 0x10, 'order_id': 'hfoo'}, object_names=lookup, player='Alice')
        self.assertIn('Alice', line)
        self.assertIn('Footman (hfoo)', line)

    def test_describes_an_order_rather_than_pretending_it_is_an_object(self):
        line = names.describe_action({'id': 0x11, 'order_id': '03000d00'})
        self.assertIn('rightclick', line)

    def test_includes_chat_and_resource_details(self):
        self.assertIn("'-mode ar'",
                      names.describe_action({'id': 0x60, 'text': '-mode ar'}))
        self.assertIn('500 gold',
                      names.describe_action({'id': 0x51, 'slot': 1, 'gold': 500,
                                             'lumber': 100}))


if __name__ == '__main__':
    unittest.main()
