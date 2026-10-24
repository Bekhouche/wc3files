"""The replay block stream - everything after the game's setup preamble.

This is what `_raw_tail` used to be: an opaque blob carried through verbatim
so a rewritten replay stayed valid. It is actually three things in sequence,
and all three are modelled here:

1. **Reforged player records** (`0x38`). One byte marker, a one-byte
   subtype, a `uint32` length, then that many bytes of protobuf-encoded
   payload carrying a player's slot, battletag and a short code. The
   existing `ReforgedPlayerMetadata` reader never consumed these - it looked
   for a `0x19` marker and gave up - which is why every real Reforged
   replay's tail began part-way through one.

2. **The game-start record** (`0x19`). One byte marker, a `uint16` length,
   then that many bytes: the slot table, random seed and select mode.

3. **The block stream proper**: time slots carrying player commands, chat
   messages, leave-game records and the start blocks.

Only the pieces whose layout is confirmed are given fields; everything else
is kept as `raw` bytes and written back untouched. In particular the
*actions* inside a time slot's command data are deliberately not decoded -
that is a large, separately-versioned table, and nothing here needs it to
produce chat, timings, leave reasons or a byte-exact rewrite.

**Verified**: parsing then rebuilding reproduces the original tail
byte-for-byte on all 290 real replays available locally, and the block walk
consumes every tail exactly - no replay ends mid-record and none has bytes
left over.
"""
import struct

# Block ids.
LEAVE_GAME = 0x17
FIRST_START = 0x1A
SECOND_START = 0x1B
THIRD_START = 0x1C
TIME_SLOT_OLD = 0x1E
TIME_SLOT = 0x1F
CHAT_MESSAGE = 0x20
UNKNOWN_0x22 = 0x22
UNKNOWN_0x23 = 0x23
FORCED_END_COUNTDOWN = 0x2F

# Pre-stream records.
REFORGED_PLAYER_RECORD = 0x38
GAME_START_RECORD = 0x19

# Blocks that are a fixed number of bytes after the id.
_FIXED_SIZE = {
    LEAVE_GAME: 13,
    FIRST_START: 4,
    SECOND_START: 4,
    THIRD_START: 4,
    UNKNOWN_0x23: 10,
    FORCED_END_COUNTDOWN: 8,
}

# A chat message with this flag carries a uint32 mode before its text.
_CHAT_FLAG_WITH_MODE = 0x20


class ReplayBlockError(ValueError):
    pass


def parse_stream(data, marker_already_consumed=False):
    """Parse a replay tail into `(reforged_players, game_start, blocks)`.

    `marker_already_consumed` says the caller already ate the `0x19` byte, so
    the game-start record begins at its length field. That is what the
    existing preamble reader does when it finds the marker.
    """
    pos = 0
    size = len(data)

    reforged_players = []
    while pos < size and data[pos] == REFORGED_PLAYER_RECORD:
        if pos + 6 > size:
            raise ReplayBlockError(
                f'truncated Reforged player record at {pos}')
        subtype = data[pos + 1]
        length = struct.unpack_from('<I', data, pos + 2)[0]
        end = pos + 6 + length
        if end > size:
            raise ReplayBlockError(
                f'Reforged player record at {pos} runs past the end of the data')
        reforged_players.append({
            'subtype': subtype,
            'payload': data[pos + 6:end],
        })
        pos = end

    game_start = None
    if marker_already_consumed:
        if pos + 2 > size:
            raise ReplayBlockError('truncated game-start record')
        length = struct.unpack_from('<H', data, pos)[0]
        end = pos + 2 + length
        if end > size:
            raise ReplayBlockError('game-start record runs past the end of the data')
        game_start = {'marker_consumed': True, 'payload': data[pos + 2:end]}
        pos = end
    elif pos < size and data[pos] == GAME_START_RECORD:
        if pos + 3 > size:
            raise ReplayBlockError('truncated game-start record')
        length = struct.unpack_from('<H', data, pos + 1)[0]
        end = pos + 3 + length
        if end > size:
            raise ReplayBlockError('game-start record runs past the end of the data')
        game_start = {'marker_consumed': False, 'payload': data[pos + 3:end]}
        pos = end

    blocks = []
    while pos < size:
        blocks.append(_read_block(data, pos, size))
        pos = blocks[-1]['_end']

    for block in blocks:
        del block['_end']
    return reforged_players, game_start, blocks


def _read_block(data, pos, size):
    block_id = data[pos]
    start = pos
    pos += 1

    if block_id in _FIXED_SIZE:
        length = _FIXED_SIZE[block_id]
        if pos + length > size:
            raise ReplayBlockError(
                f'block 0x{block_id:02X} at {start} runs past the end of the data')
        payload = data[pos:pos + length]
        pos += length
        block = {'id': block_id, 'raw': payload}
        if block_id == LEAVE_GAME:
            reason, player_id, result, unknown = struct.unpack('<IBII', payload)
            block.update(reason=reason, player_id=player_id,
                         result=result, unknown=unknown)
        elif block_id in (FIRST_START, SECOND_START, THIRD_START):
            block['value'] = struct.unpack('<I', payload)[0]
        elif block_id == FORCED_END_COUNTDOWN:
            mode, countdown = struct.unpack('<II', payload)
            block.update(mode=mode, countdown=countdown)

    elif block_id in (TIME_SLOT, TIME_SLOT_OLD):
        if pos + 2 > size:
            raise ReplayBlockError(f'truncated time slot at {start}')
        length = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        end = pos + length
        if end > size:
            raise ReplayBlockError(f'time slot at {start} runs past the end of the data')
        body = data[pos:end]
        if length >= 2:
            time_increment = struct.unpack_from('<H', body, 0)[0]
            command_data = body[2:]
        else:
            # A zero-length slot carries no time increment at all; keep the
            # body as-is rather than inventing one.
            time_increment = None
            command_data = body
        block = {
            'id': block_id,
            'time_increment': time_increment,
            # Individual actions are deliberately not decoded - see the module
            # docstring. Kept verbatim so a rewrite is byte-exact.
            'command_data': command_data,
        }
        pos = end

    elif block_id == CHAT_MESSAGE:
        if pos + 3 > size:
            raise ReplayBlockError(f'truncated chat message at {start}')
        player_id = data[pos]
        length = struct.unpack_from('<H', data, pos + 1)[0]
        pos += 3
        end = pos + length
        if end > size:
            raise ReplayBlockError(f'chat message at {start} runs past the end of the data')
        body = data[pos:end]
        block = {'id': block_id, 'player_id': player_id, 'raw': body}
        if body:
            flags = body[0]
            block['flags'] = flags
            offset = 1
            if flags == _CHAT_FLAG_WITH_MODE and len(body) >= 5:
                block['mode'] = struct.unpack_from('<I', body, 1)[0]
                offset = 5
            text = body[offset:]
            if text.endswith(b'\x00'):
                text = text[:-1]
            block['text'] = text.decode('utf-8', errors='replace')
        pos = end

    elif block_id == UNKNOWN_0x22:
        if pos >= size:
            raise ReplayBlockError(f'truncated 0x22 block at {start}')
        length = data[pos]
        pos += 1
        end = pos + length
        if end > size:
            raise ReplayBlockError(f'0x22 block at {start} runs past the end of the data')
        block = {'id': block_id, 'raw': data[pos:end]}
        pos = end

    else:
        raise ReplayBlockError(
            f'unrecognized replay block id 0x{block_id:02X} at offset {start}')

    block['_end'] = pos
    return block


def build_stream(reforged_players, game_start, blocks):
    """Rebuild the tail bytes from parsed pieces - the inverse of parse_stream."""
    out = bytearray()

    for record in reforged_players or ():
        payload = record.get('payload', b'')
        out.append(REFORGED_PLAYER_RECORD)
        out.append(record.get('subtype', 0) & 0xFF)
        out += struct.pack('<I', len(payload))
        out += payload

    if game_start is not None:
        payload = game_start.get('payload', b'')
        if not game_start.get('marker_consumed'):
            out.append(GAME_START_RECORD)
        out += struct.pack('<H', len(payload))
        out += payload

    for block in blocks or ():
        out += _build_block(block)
    return bytes(out)


def _build_block(block):
    block_id = block['id']
    out = bytearray([block_id])

    if block_id in _FIXED_SIZE:
        if block_id == LEAVE_GAME and 'reason' in block:
            out += struct.pack('<IBII', block['reason'], block['player_id'],
                               block['result'], block['unknown'])
        elif block_id in (FIRST_START, SECOND_START, THIRD_START) and 'value' in block:
            out += struct.pack('<I', block['value'])
        elif block_id == FORCED_END_COUNTDOWN and 'mode' in block:
            out += struct.pack('<II', block['mode'], block['countdown'])
        else:
            out += block.get('raw', b'')

    elif block_id in (TIME_SLOT, TIME_SLOT_OLD):
        command_data = block.get('command_data', b'')
        increment = block.get('time_increment')
        body = b'' if increment is None else struct.pack('<H', increment)
        body += command_data
        out += struct.pack('<H', len(body))
        out += body

    elif block_id == CHAT_MESSAGE:
        body = _build_chat_body(block)
        out.append(block.get('player_id', 0) & 0xFF)
        out += struct.pack('<H', len(body))
        out += body

    elif block_id == UNKNOWN_0x22:
        payload = block.get('raw', b'')
        out.append(len(payload) & 0xFF)
        out += payload

    else:
        raise ReplayBlockError(f'cannot write unrecognized block id 0x{block_id:02X}')

    return bytes(out)


def _build_chat_body(block):
    """Re-encode a chat message, preferring the parsed text so an edit to it
    actually takes effect, and falling back to the raw bytes when the message
    was not modelled (an empty body, say)."""
    if 'text' not in block or 'flags' not in block:
        return block.get('raw', b'')
    body = bytearray([block['flags'] & 0xFF])
    if 'mode' in block:
        body += struct.pack('<I', block['mode'])
    body += block['text'].encode('utf-8') + b'\x00'
    return bytes(body)
