"""wc3files.w3g.summary and `replay-info` (#263) on a synthetic replay: a
game start record with three slots, timed chat and a leave, written with
this package's writer and read back. No personal replay is needed."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_w3g import _player_record, _replay_data
from wc3files import ReplayFile
from wc3files.w3g import blocks as B
from wc3files.w3g.summary import color_name, race_name, slots, summarize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _slot(player_id, team, color, race, computer=0, ai=1, handicap=100, status=2):
    return bytes([player_id, 100, status, computer, team, color, race, ai, handicap])


def _game_start(*slot_records, seed=0x1DB17CCF, select_mode=3, start_spots=24):
    payload = bytes([len(slot_records)]) + b''.join(slot_records)
    payload += struct.pack('<I', seed) + bytes([select_mode, start_spots])
    return {'marker_consumed': False, 'payload': payload}


def _time(ms):
    return {'id': B.TIME_SLOT, 'time_increment': ms, 'command_data': b''}


def _replay():
    """Host in slot 0 (human, red, team 0), a normal computer in slot 1
    (orc, blue, team 1), an observer in slot 2 (team 24 of 24 slots)."""
    players = [_player_record(1, 'HostPlayer', host=True), _player_record(2, 'Watcher')]
    game_start = _game_start(_slot(1, 0, 0, 0x01), _slot(0, 1, 1, 0x02, computer=1),
                             _slot(2, 24, 24, 0x20),
                             *[_slot(0, 0, 0, 0, status=0)] * 21)
    blocks = [
        _time(1000),
        {'id': B.CHAT_MESSAGE, 'player_id': 1, 'flags': 0x20, 'mode': 0, 'text': 'glhf'},
        _time(2500),
        {'id': B.CHAT_MESSAGE, 'player_id': 2, 'flags': 0x20, 'mode': 1, 'text': 'to allies'},
        {'id': B.CHAT_MESSAGE, 'player_id': 1, 'flags': 0x20, 'mode': 3 + 2, 'text': 'to slot 2'},
        _time(500),
        {'id': B.LEAVE_GAME, 'reason': 0x01, 'player_id': 2, 'result': 0x0D, 'unknown': 0},
        _time(250),
    ]
    data = _replay_data(players=players, tail=B.build_stream([], game_start, blocks))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'synthetic.w3g')
        writer = ReplayFile()
        writer.import_data(data)
        writer.write(path)
        reader = ReplayFile()
        reader.read(path)
        with open(path, 'rb') as handle:
            raw = handle.read()
        return reader.get_data(), raw


class TestReplaySummary(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data, cls.raw = _replay()
        cls.summary = summarize(cls.data)

    def test_the_game(self):
        s = self.summary
        self.assertEqual((s['game_name'], s['host'], s['slots'], s['unfinished']),
                         ('Test Game', 'TestCreator', 24, False))
        self.assertEqual(s['length_ms'], 123456)                 # from the header
        self.assertEqual(s['time_slots_ms'], 4250)               # from the time slots
        self.assertEqual(s['select_mode'], 'team and race not selectable')

    def test_the_players(self):
        players = self.summary['players']
        self.assertEqual([p['slot'] for p in players], [0, 1, 2])   # only used slots
        host, ai, watcher = players
        self.assertEqual((host['name'], host['team'], host['color_name'], host['race'],
                          host['host'], host['observer']),
                         ('HostPlayer', 0, 'Red', 'human', True, False))
        self.assertEqual((ai['name'], ai['computer'], ai['player_id'], ai['race'],
                          ai['color_name']),
                         ('Computer (normal)', True, None, 'orc', 'Blue'))
        self.assertEqual((watcher['name'], watcher['observer'], watcher['race']),
                         ('Watcher', True, 'random'))

    def test_chat_and_leaves_are_timed(self):
        chat = self.summary['chat']
        self.assertEqual([(m['time_ms'], m['name'], m['to'], m['to_slot'], m['text'])
                          for m in chat],
                         [(1000, 'HostPlayer', 'all', None, 'glhf'),
                          (3500, 'Watcher', 'allies', None, 'to allies'),
                          (3500, 'HostPlayer', 'player', 2, 'to slot 2')])
        leave, = self.summary['leaves']
        self.assertEqual((leave['time_ms'], leave['name'], leave['slot'], leave['reason'],
                          leave['result_code']), (4000, 'Watcher', 2, 'remote', 0x0D))

    def test_chat_can_be_left_out(self):
        self.assertNotIn('chat', summarize(self.data, chat=False))

    def test_helpers(self):
        self.assertEqual((color_name(0), color_name(12), color_name(23)),
                         ('Red', 'Maroon', 'Peanut'))
        self.assertEqual((race_name(0x01), race_name(0x48), race_name(0x60), race_name(0)),
                         ('human', 'undead', 'random', None))
        # A record that is not the 9-byte layout gives no slots rather than
        # a misreading.
        self.assertEqual(slots({'payload': b'\x02' + bytes(9)}), ([], {}))
        self.assertEqual(slots(None), ([], {}))

    def test_the_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'synthetic.w3g')
            with open(path, 'wb') as handle:
                handle.write(self.raw)
            env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'),
                       PYTHONIOENCODING='utf-8')
            text = subprocess.run([sys.executable, '-m', 'wc3files', 'replay-info', path],
                                  capture_output=True, text=True, encoding='utf-8', env=env)
            self.assertEqual(text.returncode, 0, text.stderr)
            for expected in ('Test Game', 'HostPlayer', 'Computer (normal)', 'obs',
                             '[allies] Watcher: to allies', 'result 0x0D'):
                self.assertIn(expected, text.stdout)
            out = subprocess.run([sys.executable, '-m', 'wc3files', 'replay-info', path,
                                  '--json', '--no-chat'],
                                 capture_output=True, text=True, encoding='utf-8', env=env)
            parsed = json.loads(out.stdout)
            self.assertEqual(len(parsed['players']), 3)
            self.assertNotIn('chat', parsed)


class TestMcpReplayInfo(unittest.TestCase):
    """The MCP replay_info tool gives the same summary (#263)."""

    def test_replay_info(self):
        from wc3files.mcp import tools as T
        _data, raw = _replay()
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'game.w3g'), 'wb') as handle:
                handle.write(raw)
            roots = T.Roots([tmp])
            result = T.replay_info(roots, 'game.w3g')
            self.assertEqual([p['name'] for p in result['players']],
                             ['HostPlayer', 'Computer (normal)', 'Watcher'])
            self.assertEqual(len(result['chat']), 3)
            self.assertNotIn('chat', T.replay_info(roots, 'game.w3g', chat=False))
            with self.assertRaises(ValueError):
                T.replay_info(roots, 'game.w3g', max_chars=100)


if __name__ == '__main__':
    unittest.main()
