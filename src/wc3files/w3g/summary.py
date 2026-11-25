"""What happened in a replay (#263): the game, its players with team, colour
and race, the chat with timestamps, who left and when, and the W3MMD results
the map records.

Field meanings follow the replay format specification by blue and nagger
(w3g_format.txt, document version 1.18), checked against the 296 replays of
the corpus:

- the slot table of the game start record is a count and 9 bytes per slot
  (player id, map download, status, computer flag, team, colour, race flags,
  AI strength, handicap), then the random seed, select mode and start spot
  count. That layout fits all 294 corpus replays that have the record;
- chat modes are 0 all, 1 allies, 2 observers and referees, and 3 + N for
  player N, by slot;
- a leave record's reason is 0x01 (closed by the remote game) or 0x0C
  (closed by the local game, the one that saved the replay). Its result is
  given as the raw code: the specification reads it differently by reason
  and by whether it is the last leave, warns against using several of its
  values for winner detection, and does not document 0x04 or 0x0D, which the
  corpus has (0x0D in 1,399 leave records). The W3MMD results are the
  reliable account of who won, when a map records them.

The specification marks observers with team 12, from when a game had 12
slots; in the 24-slot Reforged replays of the corpus they have team 24.

Chat and leave times are the sum of the time slots before them. The header's
game length is given as it is; in 49 of the 270 finished corpus replays with
leaves, the time slots run on past it by more than a minute (mostly by 2 to
20 percent, in three by hours), so the summary gives both: length_ms from the
header and time_slots_ms from the slots. An unfinished replay, whose header
the game never wrote, has only the second.
"""
import re

from . import mmd as _mmd

SLOT_SIZE = 9
STATUS = {0: 'empty', 1: 'closed', 2: 'used'}
RACES = ((0x01, 'human'), (0x02, 'orc'), (0x04, 'night elf'), (0x08, 'undead'),
         (0x20, 'random'))
AI_STRENGTH = {0: 'easy', 1: 'normal', 2: 'insane'}
CHAT_MODES = {0: 'all', 1: 'allies', 2: 'observers'}
LEAVE_REASONS = {0x01: 'remote', 0x0C: 'local', 0x0E: 'unknown'}
SELECT_MODES = {0x00: 'team and race selectable', 0x01: 'team not selectable',
                0x03: 'team and race not selectable', 0x04: 'race fixed to random',
                0xCC: 'automated match making'}

_TIME_SLOTS = (0x1E, 0x1F)
_CHAT = 0x20
_LEAVE = 0x17
_COLOR_CODE = re.compile(r'\|c[0-9a-fA-F]{8}|\|r')
_colors = None


def color_name(index):
    """The World Editor's name for player colour `index` (0 Red, 1 Blue, ...,
    23 Peanut), from the bundled WorldEditStrings, or None."""
    global _colors
    if _colors is None:
        from wc3files.w3o.world_edit_strings import WorldEditStrings
        strings = WorldEditStrings.default()
        _colors = {}
        for i in range(25):
            key = f'WESTRING_UNITCOLOR_{i:02d}'
            text = strings.resolve(key)
            if text != key:
                _colors[i] = _COLOR_CODE.sub('', text)
    return _colors.get(index)


def _payload(game_start):
    payload = game_start.get('payload') if isinstance(game_start, dict) else None
    if isinstance(payload, str) and payload.startswith(("b'", 'b"')):
        import ast
        payload = ast.literal_eval(payload)
    return payload if isinstance(payload, (bytes, bytearray)) else None


def slots(game_start):
    """The slot table of a game start record: (slots, extra) where slots is
    a list of dicts and extra has the random seed, select mode and start spot
    count; ([], {}) when the record does not have the 9-byte layout."""
    payload = _payload(game_start)
    if not payload:
        return [], {}
    count = payload[0]
    end = 1 + SLOT_SIZE * count
    if len(payload) != end + 6:
        return [], {}
    table = []
    for index in range(count):
        (player_id, download, status, computer, team, color, race, ai,
         handicap) = payload[1 + SLOT_SIZE * index:1 + SLOT_SIZE * (index + 1)]
        table.append({'slot': index, 'player_id': player_id, 'download': download,
                      'status': STATUS.get(status, status), 'computer': bool(computer),
                      'team': team, 'color': color, 'race_flags': race,
                      'ai_strength': ai, 'handicap': handicap})
    seed = int.from_bytes(payload[end:end + 4], 'little')
    return table, {'random_seed': seed, 'select_mode': payload[end + 4],
                   'start_spots': payload[end + 5]}


def race_name(flags):
    for bit, name in RACES:
        if flags & bit:
            return name
    return None


def _is_observer(team, slot_count):
    return team == 24 or (team == 12 and slot_count <= 12)


def summarize(data, chat=True):
    """A summary of a read replay (`ReplayFile.get_data()`), as plain data."""
    header = data.get('header') or {}
    sub = header.get('sub') or {}
    game = data.get('compressed_data') or {}
    names = {p.get('player_id'): p.get('name') for p in game.get('Players') or []}
    hosts = {p.get('player_id') for p in game.get('Players') or [] if p.get('is_host')}
    table, extra = slots(game.get('game_start'))
    players = []
    for slot in table:
        if slot['status'] != 'used':
            continue
        entry = {
            'slot': slot['slot'],
            'player_id': None if slot['computer'] else slot['player_id'],
            'name': (f'Computer ({AI_STRENGTH.get(slot["ai_strength"], slot["ai_strength"])})'
                     if slot['computer'] else names.get(slot['player_id'])),
            'computer': slot['computer'],
            'team': slot['team'],
            'observer': _is_observer(slot['team'], len(table)),
            'color': slot['color'],
            'color_name': color_name(slot['color']),
            'race': race_name(slot['race_flags']),
            'race_selectable': bool(slot['race_flags'] & 0x40),
            'handicap': slot['handicap'],
            'host': (not slot['computer']) and slot['player_id'] in hosts,
        }
        players.append(entry)
    slot_of = {p['player_id']: p['slot'] for p in players if p['player_id'] is not None}

    time = 0
    messages, leaves = [], []
    for block in game.get('blocks') or []:
        kind = block.get('id')
        if kind in _TIME_SLOTS:
            time += block.get('time_increment') or 0
        elif kind == _CHAT and chat:
            mode = block.get('mode')
            to = None
            if mode is None:
                label = 'lobby' if block.get('flags') == 0x10 else None
            elif mode in CHAT_MODES:
                label = CHAT_MODES[mode]
            else:
                label, to = 'player', mode - 3
            messages.append({'time_ms': time, 'player_id': block.get('player_id'),
                             'name': names.get(block.get('player_id')), 'to': label,
                             'to_slot': to, 'text': block.get('text', '')})
        elif kind == _LEAVE:
            player_id = block.get('player_id')
            leaves.append({'time_ms': time, 'player_id': player_id,
                           'name': names.get(player_id), 'slot': slot_of.get(player_id),
                           'reason': LEAVE_REASONS.get(block.get('reason')),
                           'reason_code': block.get('reason'),
                           'result_code': block.get('result')})

    length = sub.get('replay_length')
    unfinished = not length
    summary = {
        'game_name': game.get('GameName'),
        'map': game.get('MapName'),
        'host': game.get('CreatorName'),
        'length_ms': time if unfinished else length,
        'time_slots_ms': time,
        'unfinished': unfinished,
        'version': sub.get('version_number'),
        'build': sub.get('build_number'),
        'reforged': header.get('reforged'),
        'slots': len(table),
        'select_mode': SELECT_MODES.get(extra.get('select_mode'), extra.get('select_mode')),
        'players': players,
        'leaves': leaves,
    }
    if chat:
        summary['chat'] = messages
    stats = _mmd.from_replay(data)
    if stats.messages:
        summary['w3mmd'] = {'players': stats.players, 'winners': stats.winners(),
                            'losers': stats.losers()}
    return summary
