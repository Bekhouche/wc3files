"""Decoding the player actions inside a replay's time slots.

A time slot's `command_data` is a run of command blocks, each

    uint8   player id
    uint16  length of this player's action bytes
    ...     that many bytes of actions

and each action is a one-byte id followed by a layout that depends on it.
This is the last layer of a replay that was still opaque: with it you can see
what every player actually did - abilities used, units selected, items
dropped, resources traded, chat-triggered commands - not just that a time
slot went by.

**This layer is read-only on purpose.** `blocks.py` keeps every time slot's
`command_data` verbatim, so writing a replay never depends on anything here;
decoding actions cannot put a byte-exact rewrite at risk. Call
`parse_command_data()` when you want to look at actions, and nothing changes
for callers that do not.

**Sources.** Two independent ones, cross-checked, because neither covers the
whole range alone:

* `w3g_actions.txt` v1.01 (blue & nagger, 2006, w3g.deepnode.de) - the
  canonical description, exhaustive for the classic actions but written
  before the `0x6B`-and-up cache/sync actions existed.
* `PBug90/w3gjs`'s `ActionParser.ts` - actively maintained through Reforged,
  and the source for the cache actions and for the fact that in the
  post-2.0.2 replay format every action id above `0x77` shifts up by one.

Where the two disagree on a classic action's size, the modern one wins and
the disagreement is noted at the case in question - the test that settles it
is empirical anyway: every command block must consume its declared length
exactly, across every real replay available.

**Verified**: see TODO.md for the measured pass rate over the local replay
corpus. A block that does not parse is reported rather than guessed at, so a
caller can tell "no actions here" from "actions this package cannot read".
"""
import struct

# Actions that move the game on but carry nothing worth modelling get an
# entry here giving how many bytes follow the id.
_SKIP_ONLY = {
    # 0x01 (pause) carries no data. This is the one place the two references
    # disagree - w3gjs skips a byte here, the canonical w3g_actions.txt says
    # the action is one byte total - and real replays settle it: 151 command
    # blocks in the local corpus consist of nothing but this action id, which
    # is only possible if it takes no data.
    0x01: 0,
    0x02: 0,
    0x04: 0,
    0x05: 0,
    0x07: 4,
    0x1A: 0,
    0x20: 0,
    0x21: 8,
    0x22: 0, 0x23: 0, 0x24: 0, 0x25: 0, 0x26: 0,
    0x27: 5, 0x28: 5,
    0x29: 0, 0x2A: 0, 0x2B: 0, 0x2C: 0,
    0x2D: 5,
    0x2E: 4,
    0x2F: 0,
    0x61: 0,
    0x62: 12,
    0x63: 8,
    0x66: 0, 0x67: 0,
    0x69: 16, 0x6A: 16,
    0x7A: 20,
    0x7B: 16,
    0xA0: 14,
    0xA1: 9,
}

# Cheat actions, per the references. Kept so they parse rather than being
# treated as corruption, and flagged so a caller can notice them.
CHEAT_ACTIONS = frozenset(range(0x20, 0x30)) | {0xA0, 0xA1}

SYNC_ACTIONS = frozenset({0x6B, 0x6C, 0x6D, 0x6E, 0x70, 0x71, 0x72, 0x73})


class ActionError(ValueError):
    pass


class _Reader(object):
    __slots__ = ('data', 'pos', 'end')

    def __init__(self, data, pos=0, end=None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def need(self, count):
        if self.pos + count > self.end:
            raise ActionError(
                f'action data ends early: wanted {count} bytes at {self.pos}, '
                f'only {self.end - self.pos} left')

    def u8(self):
        self.need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self):
        self.need(2)
        value = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return value

    def u32(self):
        self.need(4)
        value = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return value

    def f32(self):
        self.need(4)
        value = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return value

    def skip(self, count):
        self.need(count)
        self.pos += count

    def four_cc(self):
        self.need(4)
        raw = self.data[self.pos:self.pos + 4]
        self.pos += 4
        # Order ids read naturally when reversed; keep the raw bytes too so a
        # caller is never stuck with a lossy decode.
        text = bytes(reversed(raw)).decode('latin-1')
        return text if text.isprintable() else raw.hex()

    def net_tag(self):
        return (self.u32(), self.u32())

    def vec2(self):
        return (self.f32(), self.f32())

    def zstring(self):
        index = self.data.find(b'\x00', self.pos, self.end)
        if index == -1:
            raise ActionError(f'unterminated string at {self.pos}')
        value = self.data[self.pos:index].decode('utf-8', errors='replace')
        self.pos = index + 1
        return value

    def cache_desc(self):
        return {'filename': self.zstring(),
                'mission_key': self.zstring(),
                'key': self.zstring()}

    def cache_item(self):
        return {'item_id': self.four_cc(), 'charges': self.u32(), 'flags': self.u32()}

    def ability(self):
        return {'id': self.four_cc(), 'level': self.u32()}

    def cache_hero_data(self):
        data = {
            'xp': self.u32(), 'level': self.u32(), 'skill_points': self.u32(),
            'proper_name_id': self.u32(),
            'strength': self.u32(), 'strength_bonus': self.f32(),
            'agility': self.u32(), 'speed_mod': self.f32(),
            'cooldown_mod': self.f32(), 'agility_bonus': self.f32(),
            'intelligence': self.u32(), 'intelligence_bonus': self.f32(),
        }
        data['abilities'] = [self.ability() for _ in range(self.u32())]
        data['max_life'] = self.f32()
        data['max_mana'] = self.f32()
        data['sight'] = self.f32()
        data['damage'] = [self.u32() for _ in range(self.u32())]
        data['defense'] = self.f32()
        data['control_groups'] = self.u16()
        return data

    def cache_unit(self):
        unit_id = self.four_cc()
        items = [self.cache_item() for _ in range(self.u32())]
        return {'unit_id': unit_id, 'items': items,
                'hero_data': self.cache_hero_data()}


def _parse_action(reader, shift_threshold):
    raw_id = reader.u8()
    action_id = raw_id
    # Newer replay formats shifted the upper action ids up by one. The
    # threshold is a parameter rather than a constant because it is the one
    # thing the two references disagree on, and real replays settle it: see
    # parse_command_data's note.
    if shift_threshold is not None and action_id >= shift_threshold:
        action_id += 1

    action = {'id': action_id}
    if raw_id != action_id:
        action['raw_id'] = raw_id

    if action_id in _SKIP_ONLY:
        reader.skip(_SKIP_ONLY[action_id])
        if action_id in CHEAT_ACTIONS:
            action['cheat'] = True
        return action

    if action_id == 0x03:
        action['game_speed'] = reader.u8()
    elif action_id == 0x06:
        action['name'] = reader.zstring()
        action['path'] = reader.zstring()
        reader.u8()
    elif action_id in (0x10, 0x11, 0x12, 0x13):
        action['ability_flags'] = reader.u16()
        action['order_id'] = reader.four_cc()
        reader.skip(8)  # agent tag
        if action_id >= 0x11:
            action['target'] = reader.vec2()
        if action_id == 0x12:
            action['object'] = reader.net_tag()
        elif action_id == 0x13:
            action['unit'] = reader.net_tag()
            action['item'] = reader.net_tag()
    elif action_id in (0x14, 0x15):
        action['ability_flags'] = reader.u16()
        action['order_id'] = reader.four_cc()
        reader.skip(8)  # agent tag
        action['target_a'] = reader.vec2()
        action['order_id_2'] = reader.four_cc()
        action['flags'] = reader.u32()
        action['category'] = reader.u32()
        action['owner'] = reader.u8()
        action['target_b'] = reader.vec2()
        if action_id == 0x15:
            action['object'] = reader.net_tag()
    elif action_id in (0x16, 0x17):
        key = 'select_mode' if action_id == 0x16 else 'group_number'
        action[key] = reader.u8()
        count = reader.u16()
        action['units'] = [reader.net_tag() for _ in range(count)]
    elif action_id == 0x18:
        action['group_number'] = reader.u8()
        reader.skip(1)
    elif action_id == 0x19:
        action['item_id'] = reader.four_cc()
        action['object'] = reader.net_tag()
    elif action_id in (0x1B, 0x1C):
        reader.skip(1)
        action['object'] = reader.net_tag()
    elif action_id == 0x1D:
        action['hero'] = reader.net_tag()
    elif action_id in (0x1E, 0x1F):
        action['slot_number'] = reader.u8()
        action['item_id'] = reader.four_cc()
    elif action_id == 0x50:
        action['slot'] = reader.u8()
        action['flags'] = reader.u32()
    elif action_id == 0x51:
        action['slot'] = reader.u8()
        action['gold'] = reader.u32()
        action['lumber'] = reader.u32()
    elif action_id == 0x60:
        reader.skip(8)
        action['text'] = reader.zstring()
    elif action_id in (0x64, 0x65):
        action['object'] = reader.net_tag()
    elif action_id == 0x68:
        action['position'] = reader.vec2()
        action['duration'] = reader.f32()
    elif action_id in SYNC_ACTIONS:
        action['cache'] = reader.cache_desc()
        if action_id == 0x6B:
            action['value'] = reader.u32()
        elif action_id == 0x6C:
            action['value'] = reader.f32()
        elif action_id == 0x6D:
            action['value'] = reader.u8()
        elif action_id == 0x6E:
            action['value'] = reader.cache_unit()
    elif action_id == 0x75:
        action['arrow_key'] = reader.u8()
    elif action_id == 0x76:
        action['event_id'] = reader.u8()
        action['position'] = reader.vec2()
        action['button'] = reader.u8()
    elif action_id == 0x77:
        action['command_id'] = reader.u32()
        action['data'] = reader.u32()
        length = reader.u32()
        reader.need(length)
        action['buffer'] = reader.data[reader.pos:reader.pos + length].decode(
            'utf-8', errors='replace')
        reader.pos += length
    elif action_id == 0x78:
        action['identifier'] = reader.zstring()
        action['value'] = reader.zstring()
        reader.skip(4)
    elif action_id == 0x79:
        reader.skip(8)
        action['event_id'] = reader.u32()
        action['value'] = reader.f32()
        action['text'] = reader.zstring()
    else:
        raise ActionError(
            f'unrecognized action id 0x{action_id:02X} at offset {reader.pos - 1}')

    return action


def parse_command_data(data, shift_threshold=0x77, strict=True):
    """Parse one time slot's command data into per-player command blocks.

    Returns a list of `{'player_id': int, 'actions': [...]}`. With
    `strict=False` a block whose actions cannot be decoded is returned with
    its raw bytes under `'raw'` and an `'error'` message, instead of raising
    - useful when sweeping a corpus, where one odd block should not hide
    everything else.

    `shift_threshold` is the id at and above which action ids shifted up by
    one in newer replay formats. The default of `0x77` is what every replay
    in the local corpus needs - 290 of 290, spanning builds 6117, 7000 and
    7003. Pass `None` for the unshifted classic mapping; that path is
    **untested against real data**, because no pre-Reforged replay was
    available to check it. `detect_shift_threshold()` picks between them by
    trying each.
    """
    results = []
    pos = 0
    size = len(data)

    while pos < size:
        if pos + 3 > size:
            if strict:
                raise ActionError(f'truncated command block header at {pos}')
            results.append({'player_id': None, 'actions': [],
                            'raw': bytes(data[pos:]),
                            'error': 'truncated command block header'})
            break

        player_id = data[pos]
        length = struct.unpack_from('<H', data, pos + 1)[0]
        body_start = pos + 3
        body_end = body_start + length
        if body_end > size:
            if strict:
                raise ActionError(
                    f'command block at {pos} declares {length} bytes but only '
                    f'{size - body_start} remain')
            results.append({'player_id': player_id, 'actions': [],
                            'raw': bytes(data[body_start:]),
                            'error': 'command block runs past the end'})
            break

        reader = _Reader(data, body_start, body_end)
        actions = []
        try:
            while reader.pos < body_end:
                actions.append(_parse_action(reader, shift_threshold))
            if reader.pos != body_end:
                raise ActionError(
                    f'command block at {pos} left {body_end - reader.pos} bytes unread')
        except ActionError:
            if strict:
                raise
            results.append({'player_id': player_id, 'actions': actions,
                            'raw': bytes(data[body_start:body_end]),
                            'error': 'actions could not be fully decoded'})
            pos = body_end
            continue

        results.append({'player_id': player_id, 'actions': actions})
        pos = body_end

    return results


def iter_actions(blocks, shift_threshold='auto', strict=False):
    """Yield `(player_id, action)` for every action in a parsed block list.

    `blocks` is what `ReplayFile.get_data()['compressed_data']['blocks']`
    holds; time slots are picked out and everything else skipped.
    """
    from . import blocks as _blocks

    if shift_threshold == 'auto':
        shift_threshold = detect_shift_threshold(blocks)

    for block in blocks or ():
        if block.get('id') not in (_blocks.TIME_SLOT, _blocks.TIME_SLOT_OLD):
            continue
        command_data = block.get('command_data') or b''
        if not command_data:
            continue
        for command in parse_command_data(command_data,
                                          shift_threshold=shift_threshold,
                                          strict=strict):
            for action in command['actions']:
                yield command['player_id'], action


# Actions that represent a real player command, for actions-per-minute. Taken
# from w3g_actions.txt's own [APM+] markings: ability use, selection changes,
# hotkey assignment, item handling and build-queue cancellation count;
# pausing, game-speed changes, ally/resource housekeeping, minimap pings and
# the sync/cache traffic maps generate do not.
APM_ACTIONS = frozenset({
    0x10, 0x11, 0x12, 0x13, 0x14, 0x15,   # unit/building abilities
    0x17, 0x18,                           # assign and select hotkey group
    0x1C, 0x1D, 0x1E, 0x1F,               # ground item, revival, build queue
    0x61,                                 # ESC
    0x66, 0x67,                           # hero skill / build submenus
})


def is_apm_action(action):
    """Whether an action counts towards actions-per-minute.

    `0x16` (change selection) is deliberately excluded: w3g_actions.txt marks
    it `[APM?]` and sets out conditional rules - a deselection does not count,
    and a selection immediately following one is not counted twice - which
    need surrounding context this function does not have. Counting it
    unconditionally would inflate APM, so callers wanting that nuance should
    implement those rules over `iter_actions()`.
    """
    return action.get('id') in APM_ACTIONS


def detect_shift_threshold(blocks, candidates=(0x77, None)):
    """Work out which action-id mapping a replay uses, by trying each.

    Returns the first candidate under which every time slot parses cleanly,
    or raises if none does. Cheap in practice: a wrong mapping almost always
    fails within the first few slots.
    """
    from . import blocks as _blocks

    slots = [b for b in blocks or ()
             if b.get('id') in (_blocks.TIME_SLOT, _blocks.TIME_SLOT_OLD)
             and b.get('command_data')]

    last_error = None
    for candidate in candidates:
        try:
            for slot in slots:
                parse_command_data(slot['command_data'],
                                   shift_threshold=candidate, strict=True)
            return candidate
        except ActionError as exc:
            last_error = exc
    raise ActionError(
        f'no known action-id mapping parses this replay cleanly: {last_error}')


def action_counts(blocks, shift_threshold='auto', apm_only=False):
    """Count actions per player id, for a quick activity or APM figure."""
    counts = {}
    for player_id, action in iter_actions(blocks, shift_threshold=shift_threshold):
        if apm_only and not is_apm_action(action):
            continue
        counts[player_id] = counts.get(player_id, 0) + 1
    return counts


# Fields naming a *type* (a rawcode such as 'hfoo' or 'UH01') and fields
# naming one specific *instance* on the map (a net tag).
RAWCODE_FIELDS = ('order_id', 'order_id_2', 'item_id')
NET_TAG_FIELDS = ('object', 'unit', 'item', 'hero')


def action_rawcodes(action):
    """Every object rawcode an action names, as a set.

    Only stringencoded ItemIDs count: the same fields also carry *numeric*
    order ids (the byte pattern `?? ?? 0D 00`, e.g. right-click or attack),
    which are commands rather than objects and are excluded here.
    """
    found = set()
    for field in RAWCODE_FIELDS:
        value = action.get(field)
        if isinstance(value, str) and len(value) == 4 and value.isprintable():
            found.add(value)
    return found


def action_net_tags(action):
    """Every specific instance an action names, as a set of (tag1, tag2)."""
    found = set()
    for field in NET_TAG_FIELDS:
        value = action.get(field)
        if isinstance(value, tuple) and len(value) == 2:
            found.add(value)
    for unit in action.get('units') or ():
        if isinstance(unit, tuple) and len(unit) == 2:
            found.add(unit)
    return found


def find_actions(blocks, rawcode=None, net_tag=None, player_id=None,
                 action_id=None, shift_threshold='auto'):
    """Search a replay's actions.

    Yields `(player_id, action)` for every action matching all of the given
    filters. `rawcode` matches an object *type* ('UH01'); `net_tag` matches
    one specific instance as a `(tag1, tag2)` pair.

    A caveat worth knowing before relying on `net_tag`: a replay records only
    player commands, never game state, so units the engine creates, kills or
    transforms never appear. Action `0x19` is the only one carrying a rawcode
    and a net tag together, and measured on a real replay it tied 432
    instances to a type of which **181 mapped to more than one rawcode** - so
    an instance cannot be resolved to its type from a replay alone.
    """
    if net_tag is not None:
        net_tag = tuple(net_tag)

    for found_player, action in iter_actions(blocks, shift_threshold=shift_threshold):
        if player_id is not None and found_player != player_id:
            continue
        if action_id is not None and action.get('id') != action_id:
            continue
        if rawcode is not None and rawcode not in action_rawcodes(action):
            continue
        if net_tag is not None and net_tag not in action_net_tags(action):
            continue
        yield found_player, action
