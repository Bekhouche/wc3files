"""Real test for wc3files.w3g (ReplayFile) - read-only, against a real replay.

Coverage here comes in two halves. ReplayFile.import_data() can load a
replay from the same dict get_data() returns, so TestSyntheticReplay builds
small valid replays from scratch and exercises the read and write paths on
every run - no game data needed. What that cannot show is that the byte
layout matches what Warcraft III really writes, since those bytes come from
this package's own writer; the tests gated behind WC3FILES_TEST_REPLAY cover
that against real replays. TODO.md documents
that even the read side was only ever verified against real replays supplied
by the maintainer (157 of them, none checked into this repo - they're
personal, multi-KB-to-MB BattleNet replay files). Rather than fabricate a
synthetic one that might not reflect real byte layout, this test is
skipped unless a real replay is pointed at via WC3FILES_TEST_REPLAY.
"""
import os
import struct
import tempfile
import unittest

import _helpers
from wc3files import ReplayFile
from wc3files.w3g import blocks as blocks_mod

_REPLAY_ENV_VAR = 'WC3FILES_TEST_REPLAY'


@unittest.skipUnless(os.environ.get(_REPLAY_ENV_VAR), (
    f'set {_REPLAY_ENV_VAR}=/path/to/a/real/replay.w3g to exercise this against '
    'real game bytes - the synthetic tests above cover the read/write paths '
    'without it, see module docstring'
))
class TestReplayRead(unittest.TestCase):
    def test_reads_without_crashing_and_has_players(self):
        replay = ReplayFile()
        replay.read(os.environ[_REPLAY_ENV_VAR])
        data = replay.get_data()
        self.assertIn('header', data)
        self.assertIn('compressed_data', data)
        self.assertGreater(len(data['compressed_data'].get('Players', [])), 0)


class TestReplayImportDataValidation(unittest.TestCase):
    """import_data()'s guard rails - the only part of this format testable
    without a real replay, since a valid one must carry the raw action-block
    stream verbatim and that cannot be synthesized."""

    def test_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            ReplayFile().import_data(['not', 'a', 'dict'])

    def test_rejects_data_missing_a_required_section(self):
        for partial in ({}, {'header': {}}, {'compressed_data': {}}):
            with self.subTest(partial=sorted(partial)):
                with self.assertRaises(ValueError):
                    ReplayFile().import_data(partial)


@unittest.skipUnless(os.environ.get(_REPLAY_ENV_VAR), (
    f'set {_REPLAY_ENV_VAR}=/path/to/a/real/replay.w3g to exercise this against '
    'real game bytes - the synthetic tests above cover the read/write paths '
    'without it, see module docstring'
))
class TestReplayBuildFromData(unittest.TestCase):
    """Building a replay from dumped data used to be a silent no-op: write()
    only ever rebuilt from whatever a prior read() left on the instance, so
    the CLI declared this format unbuildable rather than mislead anyone.
    import_data() closes that.

    Verified while adding it, across 210 real replays: this path produces
    byte-identical output to the direct read -> write path in all 210, and the
    only field that differs from the original in all 210 is
    header.size_compressed - the block stream is recompressed on write, so the
    new file is a different size and its own header correctly says so.
    """

    def setUp(self):
        self.source = os.environ[_REPLAY_ENV_VAR]
        self.original = ReplayFile()
        self.original.read(self.source)
        self.data = self.original.get_data()

    def _build_and_reread(self, data):
        import tempfile
        rebuilt = ReplayFile()
        rebuilt.import_data(data)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'rebuilt.w3g')
            rebuilt.write(out)
            reread = ReplayFile()
            reread.read(out)
            return reread.get_data()

    def test_a_fresh_object_loaded_only_from_dumped_data_round_trips(self):
        result = self._build_and_reread(self.data)
        self.assertEqual(result['compressed_data'], self.data['compressed_data'])
        self.assertEqual(result['reforged_player_metadata'],
                         self.data['reforged_player_metadata'])
        # size_compressed is the one field expected to change (see class docstring).
        differing = {k for k, v in self.data['header'].items()
                     if result['header'].get(k) != v}
        self.assertLessEqual(differing, {'size_compressed'})

    def test_a_real_replays_block_stream_is_parsed(self):
        """The tail of a real replay is the block stream, not an opaque blob.

        Measured across all 290 local replays while building this: every tail
        parses and rebuilds byte-for-byte, totalling 13,796,119 time slots,
        44,671 chat messages and 3,010 leave-game records.
        """
        compressed = self.data['compressed_data']
        self.assertIn('blocks', compressed,
                      'a real replay tail should parse, not fall back to raw bytes')
        self.assertNotIn('_raw_tail', compressed)

        ids = {block['id'] for block in compressed['blocks']}
        self.assertIn(blocks_mod.TIME_SLOT, ids)

        # Rebuilding the stream must reproduce it exactly.
        rebuilt = blocks_mod.build_stream(compressed.get('reforged_players'),
                                          compressed.get('game_start'),
                                          compressed['blocks'])
        reparsed = blocks_mod.parse_stream(
            rebuilt,
            marker_already_consumed=bool(
                compressed.get('game_start')
                and compressed['game_start'].get('marker_consumed')),
        )
        self.assertEqual(blocks_mod.build_stream(*reparsed), rebuilt)

    def test_an_edit_to_dumped_data_actually_takes_effect(self):
        self.data['compressed_data']['Players'][0]['name'] = 'EditedName'
        self.data['compressed_data']['MapName'] = 'Maps' + chr(92) + 'EditedMap.w3x'
        result = self._build_and_reread(self.data)
        self.assertEqual(result['compressed_data']['Players'][0]['name'], 'EditedName')
        self.assertEqual(result['compressed_data']['MapName'],
                         'Maps' + chr(92) + 'EditedMap.w3x')


MAGIC = b'Warcraft III recorded game\x1a\x00'
HOST_FLAG = 0x00
PLAYER_RECORD_MARKER = 0x16


def _player_record(player_id, name, host=False):
    """Build one player record.

    Byte 0 is the host flag: 0x00 marks the host record carried in the
    preamble, and 0x16 introduces each additional joined player. Byte 1 is the
    id, then a null-terminated name, then a mode flag - 0x08 means ladder,
    anything else means custom and gives the number of bytes to skip.
    """
    lead = HOST_FLAG if host else PLAYER_RECORD_MARKER
    raw = bytes([lead, player_id]) + name.encode('utf-8') + b'\x00' + bytes([0x01, 0x00])
    return {
        'player_id': player_id,
        'name': name,
        'race': 'none',
        'is_host': host,
        'runtime': 0,
        'raw': raw.hex(),
        'size': len(raw),
    }


def _replay_data(players=None, game_name='Test Game',
                 map_name='Maps/Test/TestMap.w3x', creator='TestCreator',
                 tail=b'\x00' * 64, header_version=1, build_number=6114):
    """A complete, minimal replay in the shape get_data() returns."""
    if players is None:
        players = [_player_record(1, 'HostPlayer', host=True),
                   _player_record(2, 'SecondPlayer')]

    if header_version == 0:
        sub = {'unknown': 0, 'version': 1, 'build': build_number,
               'flags': 0x8000, 'replay_length': 123456, 'checksum': 0}
        file_offset = 64
    else:
        sub = {'version_identifier': 'W3XP', 'version_number': 10032,
               'build_number': build_number, 'flags': 0x8000,
               'replay_length': 123456, 'checksum': 0}
        file_offset = 68

    return {
        'header': {
            'name': MAGIC,
            'fileoffset': file_offset,
            # Recomputed by write() from what it actually emits.
            'size_compressed': 0,
            'size_decompressed': 0,
            'number_compressed': 0,
            'header_version': header_version,
            'sub': sub,
            'reforged': header_version != 0 and build_number >= 6089,
        },
        'compressed_data': {
            '_leading_bytes': b'\x10\x01\x00\x00',
            'Players': players,
            'GameName': game_name,
            'GamePassword': '',
            '_raw_settings': bytes(13),
            'GameSettings': {},
            'MapName': map_name,
            'CreatorName': creator,
            '_settings_tail': b'',
            'PlayerCount': len(players),
            'GameType': {'GameType': 0x09, 'privacy': 0x00},
            'LanguageID': b'\x00\x00\x00\x00',
            '_raw_tail': tail,
        },
        'reforged_player_metadata': [],
    }


class TestSyntheticReplay(unittest.TestCase):
    """Replay coverage that does not need a real .w3g.

    Every other test in this module is gated behind WC3FILES_TEST_REPLAY,
    because a real replay is personal data that cannot be checked in - which
    left this format with no CI coverage at all. Now that write() can build a
    replay from a plain data dict, a small valid one can be constructed here,
    so the read and write paths are exercised on every run.

    What this cannot show is that the layout matches what Warcraft III really
    writes - these bytes come from this package's own writer. The gated tests
    cover that against real replays, and TODO.md records those measurements.
    """

    def _write_and_read(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'synthetic.w3g')
            writer = ReplayFile()
            writer.import_data(data)
            writer.write(path)
            self.assertGreater(os.path.getsize(path), 0)
            reader = ReplayFile()
            reader.read(path)
            return reader.get_data()

    def test_a_synthetic_replay_round_trips(self):
        result = self._write_and_read(_replay_data())
        compressed = result['compressed_data']
        self.assertEqual([p['name'] for p in compressed['Players']],
                         ['HostPlayer', 'SecondPlayer'])
        self.assertEqual(compressed['GameName'], 'Test Game')
        self.assertEqual(compressed['MapName'], 'Maps/Test/TestMap.w3x')
        self.assertEqual(compressed['CreatorName'], 'TestCreator')

    def test_the_unparsed_tail_survives_byte_for_byte(self):
        # The action-block/command stream is not parsed - it is carried
        # through verbatim, and losing any of it would corrupt the replay.
        tail = bytes(range(256)) * 3
        result = self._write_and_read(_replay_data(tail=tail))
        self.assertEqual(result['compressed_data']['_raw_tail'], tail)

    def test_every_joined_player_is_preserved(self):
        players = [_player_record(1, 'Host', host=True)]
        players += [_player_record(i, f'Player{i}') for i in range(2, 9)]
        result = self._write_and_read(_replay_data(players=players))
        got = result['compressed_data']['Players']
        self.assertEqual(len(got), 8)
        self.assertEqual([p['player_id'] for p in got], list(range(1, 9)))
        self.assertEqual(got[-1]['name'], 'Player8')

    def test_a_pre_reforged_header_round_trips_too(self):
        # header_version 0 is the ROC/original-TFT layout, a separate branch
        # in both the reader and the writer.
        data = _replay_data(header_version=0, build_number=6059)
        result = self._write_and_read(data)
        self.assertEqual(result['header']['header_version'], 0)
        self.assertFalse(result['header']['reforged'])
        self.assertEqual(result['header']['sub']['build'], 6059)
        self.assertEqual(result['compressed_data']['GameName'], 'Test Game')

    def test_an_edited_field_is_written_back(self):
        data = _replay_data()
        data['compressed_data']['Players'][0]['name'] = 'Renamed'
        data['compressed_data']['MapName'] = 'Maps/Other/Other.w3x'
        result = self._write_and_read(data)
        self.assertEqual(result['compressed_data']['Players'][0]['name'], 'Renamed')
        self.assertEqual(result['compressed_data']['MapName'], 'Maps/Other/Other.w3x')

    def test_header_size_fields_are_recomputed_not_trusted(self):
        # They are written as 0 above; write() must replace them with the real
        # sizes of what it emitted, or the file would not read back at all.
        result = self._write_and_read(_replay_data())
        header = result['header']
        self.assertGreater(header['size_decompressed'], 0)
        self.assertGreater(header['number_compressed'], 0)
        self.assertGreater(header['size_compressed'], header['fileoffset'])

    def test_a_replay_with_no_players_is_rejected(self):
        data = _replay_data()
        data['compressed_data']['Players'] = []
        replay = ReplayFile()
        replay.import_data(data)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                replay.write(os.path.join(tmp, 'empty.w3g'))


class TestReplayBlockStream(unittest.TestCase):
    """The block stream that used to be carried as an opaque `_raw_tail`.

    It is three things in sequence: Reforged player records (0x38), the
    game-start record (0x19), then the blocks proper - time slots, chat,
    leave-game and the start blocks. Measured on the 290 real replays
    available locally, parsing and rebuilding reproduces every tail
    byte-for-byte, and the walk consumes each one exactly: 13,796,119 time
    slots, 44,671 chat messages and 3,010 leave-game records.

    Actions *inside* a time slot are deliberately still not decoded - that is
    a large, separately-versioned table, and none of the above needs it.
    """

    def _round_trip(self, reforged, game_start, blocks):
        raw = blocks_mod.build_stream(reforged, game_start, blocks)
        marker_consumed = bool(game_start and game_start.get('marker_consumed'))
        again = blocks_mod.parse_stream(raw, marker_already_consumed=marker_consumed)
        self.assertEqual(blocks_mod.build_stream(*again), raw)
        return again

    def test_a_chat_message_is_readable_and_rebuilds(self):
        block = {'id': blocks_mod.CHAT_MESSAGE, 'player_id': 3, 'flags': 0x20,
                 'mode': 0, 'text': 'gg wp'}
        _, _, parsed = self._round_trip([], None, [block])
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['text'], 'gg wp')
        self.assertEqual(parsed[0]['player_id'], 3)
        self.assertEqual(parsed[0]['mode'], 0)

    def test_an_edited_chat_message_takes_effect(self):
        block = {'id': blocks_mod.CHAT_MESSAGE, 'player_id': 1, 'flags': 0x20,
                 'mode': 0, 'text': 'original'}
        block['text'] = 'replaced'
        _, _, parsed = self._round_trip([], None, [block])
        self.assertEqual(parsed[0]['text'], 'replaced')

    def test_a_time_slot_keeps_its_increment_and_command_bytes(self):
        command = bytes([1, 2, 3, 4, 5])
        block = {'id': blocks_mod.TIME_SLOT, 'time_increment': 250,
                 'command_data': command}
        _, _, parsed = self._round_trip([], None, [block])
        self.assertEqual(parsed[0]['time_increment'], 250)
        self.assertEqual(parsed[0]['command_data'], command)

    def test_a_leave_game_block_exposes_its_fields(self):
        block = {'id': blocks_mod.LEAVE_GAME, 'reason': 0x0C, 'player_id': 4,
                 'result': 0x09, 'unknown': 2}
        _, _, parsed = self._round_trip([], None, [block])
        self.assertEqual(parsed[0]['reason'], 0x0C)
        self.assertEqual(parsed[0]['player_id'], 4)
        self.assertEqual(parsed[0]['result'], 0x09)

    def test_start_blocks_and_countdown_round_trip(self):
        source = [
            {'id': blocks_mod.FIRST_START, 'value': 1},
            {'id': blocks_mod.SECOND_START, 'value': 1},
            {'id': blocks_mod.THIRD_START, 'value': 1},
            {'id': blocks_mod.FORCED_END_COUNTDOWN, 'mode': 1, 'countdown': 30},
        ]
        _, _, parsed = self._round_trip([], None, source)
        self.assertEqual([b['id'] for b in parsed], [b['id'] for b in source])
        self.assertEqual(parsed[3]['countdown'], 30)

    def test_reforged_player_records_are_consumed_not_left_in_the_stream(self):
        # These are what used to make every real Reforged tail start
        # part-way through a record: the old reader looked only for a 0x19
        # marker and never consumed the 0x38 records before it.
        reforged = [
            {'subtype': 3, 'payload': b'\x08\x01\x12\x04Name'},
            {'subtype': 4, 'payload': b'\x08\x02'},
        ]
        blocks = [{'id': blocks_mod.FIRST_START, 'value': 1}]
        parsed_reforged, game_start, parsed_blocks = self._round_trip(
            reforged, None, blocks)
        self.assertEqual(len(parsed_reforged), 2)
        self.assertEqual(parsed_reforged[0]['subtype'], 3)
        self.assertEqual(parsed_reforged[0]['payload'], b'\x08\x01\x12\x04Name')
        self.assertIsNone(game_start)
        self.assertEqual(len(parsed_blocks), 1)

    def test_the_game_start_record_round_trips_both_ways(self):
        payload = bytes(range(40))
        for marker_consumed in (False, True):
            with self.subTest(marker_consumed=marker_consumed):
                game_start = {'marker_consumed': marker_consumed, 'payload': payload}
                _, parsed_start, _ = self._round_trip(
                    [], game_start, [{'id': blocks_mod.FIRST_START, 'value': 7}])
                self.assertIsNotNone(parsed_start)
                self.assertEqual(parsed_start['payload'], payload)

    def test_an_unrecognized_block_id_is_refused_rather_than_guessed(self):
        with self.assertRaises(blocks_mod.ReplayBlockError):
            blocks_mod.parse_stream(bytes([0x99, 0x00, 0x00]))

    def test_a_truncated_block_is_refused(self):
        # A time slot whose declared length runs past the data.
        raw = bytes([blocks_mod.TIME_SLOT]) + struct.pack('<H', 500) + b'\x00' * 4
        with self.assertRaises(blocks_mod.ReplayBlockError):
            blocks_mod.parse_stream(raw)

    def test_a_replay_with_an_unparsable_tail_still_round_trips(self):
        # Nothing should become unreadable just because its tail is not
        # recognized - it falls back to being carried verbatim.
        tail = b'\xff' * 32
        data = _replay_data(tail=tail)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'odd.w3g')
            writer = ReplayFile()
            writer.import_data(data)
            writer.write(path)
            reader = ReplayFile()
            reader.read(path)
            compressed = reader.get_data()['compressed_data']
        self.assertNotIn('blocks', compressed)
        self.assertEqual(compressed['_raw_tail'], tail)

    def test_blocks_survive_a_full_replay_write_and_reread(self):
        data = _replay_data(tail=b'')
        data['compressed_data'].pop('_raw_tail')
        data['compressed_data']['reforged_players'] = []
        data['compressed_data']['game_start'] = None
        data['compressed_data']['blocks'] = [
            {'id': blocks_mod.FIRST_START, 'value': 1},
            {'id': blocks_mod.CHAT_MESSAGE, 'player_id': 2, 'flags': 0x20,
             'mode': 0, 'text': 'hello from a test'},
            {'id': blocks_mod.TIME_SLOT, 'time_increment': 100,
             'command_data': b'\x01\x02'},
            {'id': blocks_mod.LEAVE_GAME, 'reason': 1, 'player_id': 2,
             'result': 0, 'unknown': 0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'blocks.w3g')
            writer = ReplayFile()
            writer.import_data(data)
            writer.write(path)
            reader = ReplayFile()
            reader.read(path)
            compressed = reader.get_data()['compressed_data']

        got = compressed['blocks']
        self.assertEqual([b['id'] for b in got],
                         [blocks_mod.FIRST_START, blocks_mod.CHAT_MESSAGE,
                          blocks_mod.TIME_SLOT, blocks_mod.LEAVE_GAME])
        self.assertEqual(got[1]['text'], 'hello from a test')
        self.assertEqual(got[2]['time_increment'], 100)
        self.assertEqual(got[3]['player_id'], 2)


@unittest.skipUnless(os.environ.get(_REPLAY_ENV_VAR), (
    f'set {_REPLAY_ENV_VAR}=/path/to/a/real/replay.w3g to exercise this against '
    'a real replay'))
class TestByteExactRoundTrip(unittest.TestCase):
    """A replay read and written back unchanged must be the same bytes.

    It used not to be: write() recompressed the block stream at zlib level 9,
    which the game does not use, so every rebuild came out 5-7% smaller and
    the header had to describe the new size. Two further fields were also
    being written back as zero rather than as found - the byte between the
    privacy byte and the language id (0x49 in 20 of 25 real replays), and the
    settings block, whose encoder is not a byte-exact inverse of its decoder.
    Verified across 290 real replays.
    """

    def setUp(self):
        self.path = os.environ[_REPLAY_ENV_VAR]
        with open(self.path, 'rb') as handle:
            self.original = handle.read()

    def _rebuild(self):
        replay = ReplayFile()
        replay.read(self.path)
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'out.w3g')
            replay.write(target)
            with open(target, 'rb') as handle:
                return replay, handle.read()

    def test_writing_an_unedited_replay_reproduces_it(self):
        _replay, rebuilt = self._rebuild()
        self.assertEqual(self.original, rebuilt)

    def test_the_decompressed_stream_itself_reproduces(self):
        replay = ReplayFile()
        replay.read(self.path)
        store = replay.compressed_data
        self.assertEqual(store.build(), store._source_decompressed)

    def test_the_original_blocks_are_kept_not_recompressed(self):
        replay = ReplayFile()
        replay.read(self.path)
        self.assertTrue(replay.compressed_data._raw_blocks)

    def test_editing_the_data_still_produces_a_readable_replay(self):
        # Once the decoded stream changes the stored blocks are stale, so
        # write() recompresses - the result must still read back.
        replay = ReplayFile()
        replay.read(self.path)
        players = replay.compressed_data.get_data().get('Players')
        if not players:
            self.skipTest('replay has no players to edit')
        replay.compressed_data.get_data()['GameName'] = 'edited name'
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, 'edited.w3g')
            replay.write(target)
            again = ReplayFile()
            again.read(target)
        self.assertEqual(again.compressed_data.get_data()['GameName'],
                         'edited name')


if __name__ == '__main__':
    unittest.main()
