"""Tests for wc3files.w3g.mmd - W3MMD, the map meta data standard.

W3MMD is how a map reports what actually happened in a game: who played, who
won, and whatever statistics it tracks. It rides in the replay's action
stream as `SyncStoredInteger` calls (action 0x6B) with the filename
`MMD.Dat`.

These build the records byte-for-byte the way the standard defines them, so
no replay is needed. The real-data figures are in TODO.md; measured while
building this, 154 of 290 local replays carry W3MMD, every id gap in them is
genuinely absent rather than missed by the parser, and no replay had
conflicting messages.

Source: the W3MMD standard, version 1.00 (PBug90/w3gPlus, W3MMD.spec).
"""
import os
import struct
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files.w3g import mmd


def record(mission_key, contents, checksum=0xFFFFFFFF, action=mmd.SYNC_STORED_INTEGER):
    """One SyncStoredInteger action carrying a W3MMD message."""
    out = bytearray()
    if action is not None:
        out.append(action)
    out += mmd.MMD_FILENAME + b'\x00'
    out += mission_key.encode('utf-8') + b'\x00'
    out += contents.encode('utf-8') + b'\x00'
    out += struct.pack('<I', checksum)
    return bytes(out)


def stream(*messages):
    """A run of `val:` messages, each followed by its `chk:` message, with
    filler between them so the parser has to locate records rather than
    assume they are adjacent."""
    out = bytearray()
    for index, contents in enumerate(messages):
        out += b'\x00\x11\x22'
        out += record(f'val:{index}', contents)
        out += b'\x33'
        out += record(f'chk:{index}', str(index))
    return bytes(out)


class TestMmdArgumentSplitting(unittest.TestCase):
    """The standard defines exactly two escapes: '\\ ' for a space and
    '\\\\' for a backslash."""

    def test_plain_arguments_split_on_spaces(self):
        self.assertEqual(mmd._split_arguments('init version 1 1'),
                         ['init', 'version', '1', '1'])

    def test_an_escaped_space_stays_inside_one_argument(self):
        self.assertEqual(mmd._split_arguments(r'Event winround Team\ One 3'),
                         ['Event', 'winround', 'Team One', '3'])

    def test_an_escaped_backslash_is_literal(self):
        self.assertEqual(mmd._split_arguments(r'VarP 0 path = C:\\dir'),
                         ['VarP', '0', 'path', '=', r'C:\dir'])

    def test_runs_of_spaces_do_not_make_empty_arguments(self):
        self.assertEqual(mmd._split_arguments('a   b'), ['a', 'b'])


class TestMmdParsing(unittest.TestCase):
    def test_init_messages_give_version_and_players(self):
        data = mmd.MmdData.from_bytes(stream(
            'init version 1 1',
            'init pid 0 Strilanc',
            'init pid 1 Someone\\ Else',
        ))
        self.assertEqual(data.version, (1, 1))
        self.assertEqual(data.players, {0: 'Strilanc', 1: 'Someone Else'})
        self.assertEqual(data.name_of(1), 'Someone Else')

    def test_checksum_messages_are_not_treated_as_content(self):
        data = mmd.MmdData.from_bytes(stream('init version 1 1'))
        self.assertEqual(len(data.messages), 1)
        self.assertEqual(data.checksums, {0: '0'})

    def test_variables_apply_assignment_and_arithmetic_in_order(self):
        data = mmd.MmdData.from_bytes(stream(
            'init version 1 1',
            'DefVarP kills int high leaderboard',
            'VarP 0 kills = 5',
            'VarP 0 kills += 3',
            'VarP 0 kills -= 2',
        ))
        self.assertEqual(data.definitions['kills']['type'], 'int')
        self.assertEqual(data.definitions['kills']['goal'], 'high')
        self.assertEqual(data.variables[0]['kills'], 6)

    def test_a_real_variable_keeps_its_fractional_value(self):
        data = mmd.MmdData.from_bytes(stream(
            'DefVarP ratio real high track',
            'VarP 2 ratio = 13.134',
        ))
        self.assertAlmostEqual(data.variables[2]['ratio'], 13.134)

    def test_a_string_variable_is_not_coerced_to_a_number(self):
        data = mmd.MmdData.from_bytes(stream(
            'DefVarP team string none none',
            'VarP 1 team = Alliance',
        ))
        self.assertEqual(data.variables[1]['team'], 'Alliance')

    def test_only_the_last_outcome_flag_counts(self):
        # Per the standard, a player does not get two wins for two 'winner'
        # flags, and a later outcome replaces an earlier one.
        data = mmd.MmdData.from_bytes(stream(
            'init pid 0 A', 'init pid 1 B',
            'FlagP 0 loser',
            'FlagP 0 winner',
            'FlagP 1 loser',
            'FlagP 1 leaver',
        ))
        self.assertEqual(data.winners(), [0])
        self.assertEqual(data.losers(), [1])
        # 'leaver' is independent of the outcome flags and must survive.
        self.assertIn('leaver', data.flags[1])
        self.assertIn('loser', data.flags[1])

    def test_blank_messages_are_ignored(self):
        data = mmd.MmdData.from_bytes(stream('blank', 'blank', 'init pid 0 A'))
        self.assertEqual(data.players, {0: 'A'})
        self.assertEqual(data.unknown, [])

    def test_an_unrecognized_message_is_recorded_not_dropped(self):
        data = mmd.MmdData.from_bytes(stream('SomethingNew 1 2 3'))
        self.assertEqual(data.unknown, ['SomethingNew 1 2 3'])

    def test_events_render_through_their_definition(self):
        data = mmd.MmdData.from_bytes(stream(
            'init pid 0 Alice',
            'init pid 5 Bob',
            r'DefEvent kill 2 pid:killer pid:victim {0}\ killed\ {1}',
            'Event kill 0 5',
        ))
        self.assertEqual(len(data.events), 1)
        self.assertEqual(data.describe_event(data.events[0]), 'Alice killed Bob')

    def test_an_event_without_a_definition_still_reports_its_arguments(self):
        data = mmd.MmdData.from_bytes(stream('Event mystery a b'))
        self.assertEqual(data.describe_event(data.events[0]), 'mystery a b')

    def test_conflicting_messages_for_one_id_are_recorded_not_resolved(self):
        # By the standard, two different messages under one id means a client
        # is lying. The parser surfaces it rather than silently picking one.
        data = bytearray()
        data += record('val:0', 'FlagP 0 winner')
        data += record('val:0', 'FlagP 1 winner')
        parsed = mmd.MmdData.from_bytes(bytes(data))
        self.assertIn(0, parsed.conflicts)
        self.assertEqual(len(parsed.conflicts[0]), 2)

    def test_gaps_in_the_message_ids_are_reported(self):
        data = bytearray()
        for index in (0, 1, 3):
            data += record(f'val:{index}', 'blank')
        parsed = mmd.MmdData.from_bytes(bytes(data))
        self.assertEqual(parsed.missing_message_ids(), [2])

    def test_a_signature_without_the_action_byte_is_ignored(self):
        # The filename could occur by chance inside the action stream this
        # module does not decode; the 0x6B action byte must precede it.
        payload = record('val:0', 'init pid 0 Ghost', action=None)
        self.assertEqual(mmd.MmdData.from_bytes(payload).messages, {})

    def test_a_truncated_record_does_not_abort_the_parse(self):
        good = record('val:0', 'init pid 0 Real')
        truncated = bytes([mmd.SYNC_STORED_INTEGER]) + mmd.MMD_FILENAME + b'\x00val:1'
        parsed = mmd.MmdData.from_bytes(good + truncated)
        self.assertEqual(parsed.players, {0: 'Real'})


class TestMmdFromReplayData(unittest.TestCase):
    """`from_replay` pulls the command bytes out of the parsed block stream."""

    def test_reads_messages_out_of_time_slot_command_data(self):
        from wc3files.w3g import blocks

        payload = stream('init version 1 1', 'init pid 0 Alice', 'FlagP 0 winner')
        half = len(payload) // 2
        replay_data = {
            'compressed_data': {
                'blocks': [
                    # Split across two time slots to confirm the command data
                    # is joined before scanning.
                    {'id': blocks.TIME_SLOT, 'time_increment': 100,
                     'command_data': payload[:half]},
                    {'id': blocks.TIME_SLOT, 'time_increment': 100,
                     'command_data': payload[half:]},
                ],
            },
        }
        data = mmd.from_replay(replay_data)
        self.assertEqual(data.version, (1, 1))
        self.assertEqual(data.players, {0: 'Alice'})
        self.assertEqual(data.winners(), [0])

    def test_falls_back_to_a_raw_tail_when_blocks_did_not_parse(self):
        replay_data = {'compressed_data': {'_raw_tail': stream('init pid 2 Bob')}}
        self.assertEqual(mmd.from_replay(replay_data).players, {2: 'Bob'})

    def test_a_replay_without_mmd_yields_nothing(self):
        replay_data = {'compressed_data': {'blocks': []}}
        data = mmd.from_replay(replay_data)
        self.assertEqual(data.messages, {})
        self.assertEqual(data.players, {})


_REPLAY_ENV_VAR = 'WC3FILES_TEST_REPLAY'


@unittest.skipUnless(os.environ.get(_REPLAY_ENV_VAR), (
    f'set {_REPLAY_ENV_VAR}=/path/to/a/real/replay.w3g to exercise this against '
    'real game bytes - the synthetic tests above cover the parser without it'
))
class TestMmdAgainstRealReplay(unittest.TestCase):
    def test_a_real_replay_parses_without_error(self):
        from wc3files import ReplayFile

        replay = ReplayFile()
        replay.read(os.environ[_REPLAY_ENV_VAR])
        data = mmd.from_replay(replay.get_data())

        # Not every replay carries W3MMD, so the assertion is about
        # coherence rather than presence.
        if data.messages:
            self.assertEqual(data.conflicts, {},
                             'conflicting messages mean a client sent forgeries')
            for pid in data.variables:
                self.assertIn(pid, data.players)


if __name__ == '__main__':
    unittest.main()
