"""Tests for wc3files.w3g.actions - the player actions inside time slots.

A time slot's command data is a run of per-player command blocks, each
declaring how many bytes of actions it holds. The strict test that matters
is that parsing consumes exactly that many bytes: no overrun, nothing left
unread. These build the bytes synthetically so no replay is needed; the
corpus figures are in TODO.md (290 of 290 real replays parse exactly, over
30 million actions).

Sources: w3g_actions.txt v1.01 (blue & nagger) and PBug90/w3gjs's
ActionParser.ts, cross-checked - see the module docstring for where they
disagree and which real data supported.
"""
import os
import struct
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.w3g import actions
from wc3files.w3g import blocks


def command_block(player_id, *action_bytes):
    """One command block: player id, uint16 length, then the action bytes."""
    body = b''.join(action_bytes)
    return bytes([player_id]) + struct.pack('<H', len(body)) + body


def four_cc(text):
    """A four-character code as it sits in the file (reversed on read)."""
    return bytes(reversed(text.encode('latin-1')))


def net_tag(a, b):
    return struct.pack('<II', a, b)


def vec2(x, y):
    return struct.pack('<ff', x, y)


def zstring(text):
    return text.encode('utf-8') + b'\x00'


class TestCommandFraming(unittest.TestCase):
    def test_several_players_blocks_in_one_slot(self):
        data = (command_block(1, b'\x02')
                + command_block(2, b'\x1a')
                + command_block(3, b'\x61'))
        parsed = actions.parse_command_data(data)
        self.assertEqual([c['player_id'] for c in parsed], [1, 2, 3])
        self.assertEqual([c['actions'][0]['id'] for c in parsed], [0x02, 0x1A, 0x61])

    def test_several_actions_in_one_block(self):
        data = command_block(1, b'\x1a', b'\x1a', b'\x02', b'\x61')
        parsed = actions.parse_command_data(data)
        self.assertEqual(len(parsed), 1)
        self.assertEqual([a['id'] for a in parsed[0]['actions']],
                         [0x1A, 0x1A, 0x02, 0x61])

    def test_a_block_that_declares_more_than_it_has_is_refused(self):
        data = bytes([1]) + struct.pack('<H', 50) + b'\x1a\x1a'
        with self.assertRaises(actions.ActionError):
            actions.parse_command_data(data)

    def test_leftover_bytes_in_a_block_are_an_error_not_ignored(self):
        # 0x1A takes no data, so a 3-byte body holding one is wrong; silently
        # ignoring the remainder is how a parser drifts out of sync.
        data = bytes([1]) + struct.pack('<H', 3) + b'\x1a\xff\xff'
        with self.assertRaises(actions.ActionError):
            actions.parse_command_data(data)

    def test_non_strict_mode_reports_a_bad_block_and_keeps_going(self):
        data = (command_block(1, b'\x1a')
                + bytes([2]) + struct.pack('<H', 2) + b'\xff\xff'
                + command_block(3, b'\x61'))
        parsed = actions.parse_command_data(data, strict=False)
        self.assertEqual(len(parsed), 3)
        self.assertIn('error', parsed[1])
        self.assertEqual(parsed[1]['raw'], b'\xff\xff')
        self.assertEqual(parsed[2]['actions'][0]['id'], 0x61)

    def test_pause_carries_no_data(self):
        # The one point the two references disagree on; 151 real command
        # blocks consist of nothing but this id, which settles it.
        parsed = actions.parse_command_data(command_block(1, b'\x01'))
        self.assertEqual(parsed[0]['actions'], [{'id': 0x01}])


class TestActionLayouts(unittest.TestCase):
    def _one(self, *action_bytes, **kwargs):
        parsed = actions.parse_command_data(command_block(1, *action_bytes), **kwargs)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(len(parsed[0]['actions']), 1)
        return parsed[0]['actions'][0]

    def test_unit_ability_without_target(self):
        action = self._one(b'\x10', struct.pack('<H', 0x0040), four_cc('A000'),
                           b'\x00' * 8)
        self.assertEqual(action['id'], 0x10)
        self.assertEqual(action['ability_flags'], 0x0040)
        self.assertEqual(action['order_id'], 'A000')

    def test_unit_ability_with_target_position(self):
        action = self._one(b'\x11', struct.pack('<H', 0), four_cc('smar'),
                           b'\x00' * 8, vec2(1024.0, -512.5))
        self.assertEqual(action['order_id'], 'smar')
        self.assertAlmostEqual(action['target'][0], 1024.0)
        self.assertAlmostEqual(action['target'][1], -512.5)

    def test_unit_ability_with_target_object(self):
        action = self._one(b'\x12', struct.pack('<H', 0), four_cc('smar'),
                           b'\x00' * 8, vec2(0.0, 0.0), net_tag(7, 9))
        self.assertEqual(action['object'], (7, 9))

    def test_give_or_drop_item(self):
        action = self._one(b'\x13', struct.pack('<H', 0), four_cc('smar'),
                           b'\x00' * 8, vec2(0.0, 0.0),
                           net_tag(1, 2), net_tag(3, 4))
        self.assertEqual(action['unit'], (1, 2))
        self.assertEqual(action['item'], (3, 4))

    def test_change_selection_lists_every_unit(self):
        action = self._one(b'\x16', b'\x01', struct.pack('<H', 3),
                           net_tag(1, 1), net_tag(2, 2), net_tag(3, 3))
        self.assertEqual(action['select_mode'], 1)
        self.assertEqual(action['units'], [(1, 1), (2, 2), (3, 3)])

    def test_assign_and_select_hotkey_group(self):
        assign = self._one(b'\x17', b'\x02', struct.pack('<H', 1), net_tag(5, 5))
        self.assertEqual(assign['group_number'], 2)
        self.assertEqual(assign['units'], [(5, 5)])

        select = self._one(b'\x18', b'\x03', b'\x00')
        self.assertEqual(select['group_number'], 3)

    def test_transfer_resources(self):
        action = self._one(b'\x51', b'\x02', struct.pack('<I', 500),
                           struct.pack('<I', 250))
        self.assertEqual((action['slot'], action['gold'], action['lumber']),
                         (2, 500, 250))

    def test_minimap_ping(self):
        action = self._one(b'\x68', vec2(100.0, 200.0), struct.pack('<f', 3.0))
        self.assertAlmostEqual(action['position'][0], 100.0)
        self.assertAlmostEqual(action['duration'], 3.0)

    def test_sync_stored_integer_carries_the_cache_triple(self):
        action = self._one(b'\x6b', zstring('MMD.Dat'), zstring('val:0'),
                           zstring('init version 1 1'), struct.pack('<I', 0xFFFFFFFF))
        self.assertEqual(action['id'], 0x6B)
        self.assertEqual(action['cache']['filename'], 'MMD.Dat')
        self.assertEqual(action['cache']['mission_key'], 'val:0')
        self.assertEqual(action['cache']['key'], 'init version 1 1')
        self.assertEqual(action['value'], 0xFFFFFFFF)

    def test_map_trigger_chat_command(self):
        action = self._one(b'\x60', b'\x00' * 8, zstring('-mode ar'))
        self.assertEqual(action['text'], '-mode ar')

    def test_a_cheat_action_is_flagged(self):
        action = self._one(b'\x21', b'\x00' * 8)
        self.assertTrue(action.get('cheat'))

    def test_an_unknown_action_id_is_refused_rather_than_skipped(self):
        with self.assertRaises(actions.ActionError):
            actions.parse_command_data(command_block(1, b'\x45'))


class TestShiftThreshold(unittest.TestCase):
    """Newer replay formats shifted action ids at and above 0x77 up by one."""

    def test_shifted_id_maps_to_the_newer_action(self):
        # Raw 0x77 under the shift is the identifier/value action, which is
        # what real Reforged replays carry ("SyncModeSelect"/"confirm").
        raw = command_block(1, b'\x77', zstring('SyncModeSelect'),
                            zstring('confirm'), b'\x00' * 4)
        action = actions.parse_command_data(raw, shift_threshold=0x77)[0]['actions'][0]
        self.assertEqual(action['id'], 0x78)
        self.assertEqual(action['raw_id'], 0x77)
        self.assertEqual(action['identifier'], 'SyncModeSelect')
        self.assertEqual(action['value'], 'confirm')

    def test_the_same_bytes_fail_under_the_unshifted_mapping(self):
        raw = command_block(1, b'\x77', zstring('SyncModeSelect'),
                            zstring('confirm'), b'\x00' * 4)
        with self.assertRaises(actions.ActionError):
            actions.parse_command_data(raw, shift_threshold=None)

    def test_ids_below_the_threshold_are_untouched(self):
        raw = command_block(1, b'\x62', b'\x00' * 12)
        for threshold in (0x77, None):
            action = actions.parse_command_data(
                raw, shift_threshold=threshold)[0]['actions'][0]
            self.assertEqual(action['id'], 0x62)
            self.assertNotIn('raw_id', action)

    def test_detection_picks_the_mapping_that_parses(self):
        slot = {'id': blocks.TIME_SLOT, 'time_increment': 100,
                'command_data': command_block(1, b'\x77', zstring('a'),
                                              zstring('b'), b'\x00' * 4)}
        self.assertEqual(actions.detect_shift_threshold([slot]), 0x77)

    def test_detection_raises_when_nothing_parses(self):
        slot = {'id': blocks.TIME_SLOT, 'time_increment': 100,
                'command_data': command_block(1, b'\x45\x45\x45')}
        with self.assertRaises(actions.ActionError):
            actions.detect_shift_threshold([slot])


class TestIterationAndCounts(unittest.TestCase):
    def _slots(self):
        return [
            {'id': blocks.TIME_SLOT, 'time_increment': 100,
             'command_data': command_block(
                 1, b'\x10', struct.pack('<H', 0), four_cc('A000'), b'\x00' * 8)},
            {'id': blocks.CHAT_MESSAGE, 'player_id': 1, 'flags': 0x20,
             'mode': 0, 'text': 'ignored'},
            {'id': blocks.TIME_SLOT, 'time_increment': 100,
             'command_data': (command_block(1, b'\x1a')
                              + command_block(2, b'\x18', b'\x01', b'\x00'))},
        ]

    def test_iteration_covers_time_slots_and_skips_other_blocks(self):
        found = list(actions.iter_actions(self._slots()))
        self.assertEqual([(pid, a['id']) for pid, a in found],
                         [(1, 0x10), (1, 0x1A), (2, 0x18)])

    def test_counts_are_per_player(self):
        self.assertEqual(actions.action_counts(self._slots()), {1: 2, 2: 1})

    def test_apm_counting_excludes_non_command_actions(self):
        # 0x1A (pre-subselection) is not a player command; 0x10 and 0x18 are.
        self.assertEqual(actions.action_counts(self._slots(), apm_only=True),
                         {1: 1, 2: 1})

    def test_change_selection_is_deliberately_not_counted_for_apm(self):
        # w3g_actions.txt marks it [APM?] with conditional rules this layer
        # has no context for, so counting it would inflate APM.
        self.assertFalse(actions.is_apm_action({'id': 0x16}))
        self.assertTrue(actions.is_apm_action({'id': 0x10}))


_REPLAY_ENV_VAR = 'WC3FILES_TEST_REPLAY'


@unittest.skipUnless(os.environ.get(_REPLAY_ENV_VAR), (
    f'set {_REPLAY_ENV_VAR}=/path/to/a/real/replay.w3g to exercise this against '
    'real game bytes - the synthetic tests above cover the parser without it'
))
class TestActionsAgainstRealReplay(unittest.TestCase):
    def test_every_command_block_consumes_its_declared_length(self):
        from wc3files import ReplayFile

        replay = ReplayFile()
        replay.read(os.environ[_REPLAY_ENV_VAR])
        parsed = replay.get_data()['compressed_data']
        slots = [b for b in parsed.get('blocks', ())
                 if b['id'] in (blocks.TIME_SLOT, blocks.TIME_SLOT_OLD)
                 and b.get('command_data')]
        self.assertTrue(slots, 'a real replay should contain time slots')

        threshold = actions.detect_shift_threshold(parsed['blocks'])
        total = 0
        for slot in slots:
            # strict=True means any leftover or overrun raises.
            for command in actions.parse_command_data(
                    slot['command_data'], shift_threshold=threshold, strict=True):
                total += len(command['actions'])
        self.assertGreater(total, 0)


class TestSearchingByObjectId(unittest.TestCase):
    """Finding actions by object id.

    Two different things are called an object id. A *rawcode* ('UH01') names
    a type; a *net tag* names one specific instance on the map. Both can be
    searched, but they cannot be linked - see find_actions' docstring.
    """

    def _slots(self):
        train = (b'\x10', struct.pack('<H', 0), four_cc('UH01'), b'\x00' * 8)
        other = (b'\x10', struct.pack('<H', 0), four_cc('hfoo'), b'\x00' * 8)
        subgroup = (b'\x19', four_cc('UH01'), net_tag(500, 600))
        select = (b'\x16', b'\x01', struct.pack('<H', 2),
                  net_tag(500, 600), net_tag(7, 8))
        # A numeric order id - a command, not an object - must never match a
        # rawcode search.
        right_click = (b'\x11', struct.pack('<H', 0),
                       bytes([0x00, 0x0D, 0x00, 0x03]), b'\x00' * 8,
                       vec2(0.0, 0.0))
        return [
            {'id': blocks.TIME_SLOT, 'time_increment': 100,
             'command_data': (command_block(1, *train)
                              + command_block(2, *other))},
            {'id': blocks.TIME_SLOT, 'time_increment': 100,
             'command_data': (command_block(1, *subgroup)
                              + command_block(1, *select)
                              + command_block(3, *right_click))},
        ]

    def test_find_every_action_for_a_rawcode(self):
        hits = list(actions.find_actions(self._slots(), rawcode='UH01'))
        self.assertEqual(len(hits), 2)
        self.assertEqual(sorted(a['id'] for _, a in hits), [0x10, 0x19])

    def test_a_rawcode_search_can_be_narrowed_to_one_player(self):
        hits = list(actions.find_actions(self._slots(), rawcode='UH01', player_id=2))
        self.assertEqual(hits, [])
        hits = list(actions.find_actions(self._slots(), rawcode='UH01', player_id=1))
        self.assertEqual(len(hits), 2)

    def test_find_every_action_touching_one_instance(self):
        # The net tag appears both as 0x19's object and inside 0x16's unit list.
        hits = list(actions.find_actions(self._slots(), net_tag=(500, 600)))
        self.assertEqual(sorted(a['id'] for _, a in hits), [0x16, 0x19])

    def test_a_numeric_order_id_is_not_treated_as_an_object(self):
        # 0x03 0x00 0x0D 0x00 is "right-click", a command - searching for
        # objects must not surface it, and it must not look like a rawcode.
        right_click = [a for _, a in actions.iter_actions(self._slots())
                       if a['id'] == 0x11][0]
        self.assertEqual(actions.action_rawcodes(right_click), set())

    def test_rawcode_and_net_tag_helpers_report_what_an_action_names(self):
        subgroup = [a for _, a in actions.iter_actions(self._slots())
                    if a['id'] == 0x19][0]
        self.assertEqual(actions.action_rawcodes(subgroup), {'UH01'})
        self.assertEqual(actions.action_net_tags(subgroup), {(500, 600)})

    def test_filters_combine(self):
        hits = list(actions.find_actions(self._slots(), rawcode='UH01',
                                         action_id=0x19))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1]['object'], (500, 600))


if __name__ == '__main__':
    unittest.main()
