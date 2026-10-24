"""Putting names to what a replay's actions say.

Three different things in an action need naming, and they come from three
different places:

* the **action id** itself (`0x10` -> "Unit/building ability");
* a **numeric order id** - the `?? ?? 0D 00` ItemID pattern, which is a
  command rather than an object (`0x0D0003` -> "rightclick"). 264 of these
  are documented, generated into `_names_data.py` straight from the
  specification rather than typed out;
* an **object rawcode** (`hfoo`, `u00T`), which needs the base game's
  strings or, for a map's own objects, that map's data -
  `wc3files.w3o.ObjectNames` does that part.

Sources: `w3g_actions.txt` v1.01 (blue & nagger, 2006, w3g.deepnode.de) for
the action and order tables, extended with the later actions from
`PBug90/w3gjs`'s `ActionParser.ts`, which are marked below.
"""
import struct

from ._names_data import ORDER_NAMES, SPEC_ACTION_NAMES

# Actions the 2006 specification predates. Named from w3gjs's ActionParser,
# cross-checked against what the layouts actually carry.
_LATER_ACTION_NAMES = {
    0x14: 'Unit/building ability (two orders, e.g. build in place)',
    0x15: 'Unit/building ability (two orders, with target object)',
    0x1F: 'Remove unit from building queue',
    0x50: 'Change ally options',
    0x63: 'Unknown (scenario related)',
    0x64: 'Unknown (with object)',
    0x65: 'Unknown (with object)',
    0x6B: 'SyncStoredInteger',
    0x6C: 'SyncStoredReal',
    0x6D: 'SyncStoredBoolean',
    0x6E: 'SyncStoredUnit',
    0x70: 'FlushGameCache / cache operation',
    0x71: 'Cache operation',
    0x72: 'Cache operation',
    0x73: 'Cache operation',
    0x76: 'Game cache / UI event',
    0x77: 'Arrow key or trigger command',
    0x78: 'Sync stored identifier and value',
    0x79: 'Trigger event with value and text',
    0x7A: 'Unknown (20 bytes)',
    0x7B: 'Unknown (16 bytes)',
    0xA0: 'Cheat action',
    0xA1: 'Cheat action',
}

ACTION_NAMES = dict(SPEC_ACTION_NAMES)
ACTION_NAMES.update(_LATER_ACTION_NAMES)

# A numeric ItemID always has this shape: the low two bytes vary, then 0D 00.
_ORDER_MARKER = 0x0D


def action_name(action_id, default=None):
    """The human description of an action id."""
    return ACTION_NAMES.get(action_id, default or f'action 0x{action_id:02X}')


def is_order_id(value):
    """Whether an ItemID field holds a numeric *order* rather than an object.

    Accepts what the action parser produces: a four-character rawcode
    (never an order), or the hex of the raw bytes when they are not
    printable.
    """
    raw = _raw_bytes(value)
    return raw is not None and len(raw) == 4 and raw[2] == _ORDER_MARKER and raw[3] == 0


def order_value(value):
    """The numeric order id as an integer, or None if this is not one."""
    raw = _raw_bytes(value)
    if raw is None or len(raw) != 4:
        return None
    number = struct.unpack('<I', raw)[0]
    return number if is_order_id(value) else None


def order_name(value, default=None):
    """What a numeric order does, e.g. 'rightclick' or 'attack'."""
    number = order_value(value)
    if number is None:
        return default
    return ORDER_NAMES.get(number, default or f'unknown order 0x{number:06X}')


def _raw_bytes(value):
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        return None
    if len(value) == 4 and value.isprintable():
        # A readable rawcode. The parser reverses the bytes on read, so
        # reverse back to recover what was on disk.
        return bytes(reversed(value.encode('latin-1')))
    if len(value) == 8:
        try:
            return bytes.fromhex(value)
        except ValueError:
            return None
    return None


def describe_item_id(value, object_names=None):
    """Describe an ItemID field: an order's name, or an object's.

    Falls back to the raw rawcode when no name is known, so the result is
    always something a reader can act on rather than an empty string.
    """
    if value is None:
        return None
    if is_order_id(value):
        return order_name(value)
    if object_names is not None:
        name = object_names.get(value)
        if name:
            return f'{name} ({value})'
    return value


def describe_action(action, object_names=None, player=None):
    """A one-line readable form of a parsed action."""
    parts = [action_name(action.get('id', -1))]

    for field in ('order_id', 'order_id_2', 'item_id'):
        value = action.get(field)
        if value is not None:
            parts.append(describe_item_id(value, object_names))

    if action.get('text'):
        parts.append(repr(action['text']))
    if action.get('identifier'):
        parts.append(f"{action['identifier']}={action.get('value')!r}")
    if 'group_number' in action:
        parts.append(f"group {action['group_number']}")
    if action.get('units'):
        parts.append(f"{len(action['units'])} unit(s)")
    if 'gold' in action:
        parts.append(f"{action['gold']} gold, {action['lumber']} lumber")

    line = ' | '.join(str(part) for part in parts if part)
    return f'player {player}: {line}' if player is not None else line
