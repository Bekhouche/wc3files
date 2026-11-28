r"""
The `.log` file of a desync folder (#258).

The file is text, a series of blocks:

    =======...
    [Desync - <category> - Turn(<turn>) = <checksum>]
    \t#<id>#: 0x<value>
    \t#<id>#
    \t\t#<id>#: 0x<value>

The category is a four-character code written as a number (`gwar`,
`rand`, `cust`, ...). Tabs give the depth: `#id#` alone opens a group and
`#id#: 0x...` is a value. Ids are kept as numbers; `code_name` shows the
four characters where they are printable, since many ids read so (`plys`,
`tech`), but what they stand for is not documented.

A desync writes one record: the blocks of the three turns up to the desync
turn, newest first. The file is named by the start of the game session and
grows with each desync, so it can hold several records, oldest first. A
line of any other shape raises ValueError, since the format is not
documented and the reader is only checked on the files it was measured on.
"""

import re
import struct

_HEADER = re.compile(r'^\[Desync - (\d+) - Turn\((\d+)\) = (\d+)\]$')
_LINE = re.compile(r'^(\t+)#(-?\d+)#(?:: 0x([0-9A-Fa-f]+))?$')
_RULE = re.compile(r'^=+$')
# The turns a record covers: the desync turn and the two before it.
RECORD_TURNS = 3


def code_name(number):
    """The four characters of a code written as a number, as 'gwar', or
    None when they are not all printable."""
    if not 0 <= number < 2 ** 32:
        return None
    raw = struct.pack('>I', number)
    return raw.decode('ascii') if all(32 < b < 127 for b in raw) else None


class DesyncLog(object):
    """Reader of a desync `.log` file. `blocks` lists, in file order,
    `{'category', 'turn', 'checksum', 'items'}`, where an item is
    `{'id', 'value'}` or `{'id', 'items'}` for a group."""

    def __init__(self):
        self.blocks = []

    def read(self, source):
        """Read a path, or the file's bytes or text."""
        if isinstance(source, (bytes, bytearray)):
            text = bytes(source).decode('utf-8', 'replace')
        elif isinstance(source, str) and '\n' in source:
            text = source
        else:
            with open(source, 'rb') as handle:
                text = handle.read().decode('utf-8', 'replace')
        self.blocks = self._parse(text)
        return self

    @staticmethod
    def _parse(text):
        blocks = []
        stack = None  # the item lists open, from the block down
        for number, line in enumerate(text.split('\n'), 1):
            line = line.rstrip('\r')
            if not line or _RULE.match(line):
                continue
            header = _HEADER.match(line)
            if header:
                block = {'category': int(header.group(1)), 'turn': int(header.group(2)),
                         'checksum': int(header.group(3)), 'items': []}
                blocks.append(block)
                stack = [block['items']]
                continue
            match = _LINE.match(line)
            if not match or stack is None:
                raise ValueError(f'desync log line {number}: not a known shape: {line[:80]!r}')
            depth = len(match.group(1))
            if depth > len(stack):
                raise ValueError(f'desync log line {number}: depth {depth} under depth '
                                 f'{len(stack) - 1}')
            del stack[depth:]
            if match.group(3) is None:
                group = {'id': int(match.group(2)), 'items': []}
                stack[-1].append(group)
                stack.append(group['items'])
            else:
                stack[-1].append({'id': int(match.group(2)), 'value': int(match.group(3), 16)})
        return blocks

    def records(self):
        """The blocks grouped by desync: a list of `{'turn', 'turns',
        'blocks'}`, where `turn` is the desync turn (the newest)."""
        records = []
        for block in self.blocks:
            current = records[-1] if records else None
            first = current['blocks'][0] if current else None
            if current is None or not (current['turn'] - RECORD_TURNS < block['turn']
                                       <= current['turn']) \
                    or (block['category'], block['turn']) == (first['category'], first['turn']):
                current = {'turn': block['turn'], 'blocks': []}
                records.append(current)
            current['blocks'].append(block)
        for record in records:
            record['turns'] = sorted({block['turn'] for block in record['blocks']})
        return records

    def to_dict(self):
        """The blocks with each category and id named where it reads as
        four characters, for JSON."""
        def items(nodes):
            out = []
            for node in nodes:
                entry = {'id': node['id']}
                name = code_name(node['id'])
                if name:
                    entry['name'] = name
                if 'items' in node:
                    entry['items'] = items(node['items'])
                else:
                    entry['value'] = f"0x{node['value']:08X}"
                out.append(entry)
            return out
        return {'records': [
            {'turn': record['turn'], 'turns': record['turns'], 'blocks': [
                {'category': code_name(block['category']) or block['category'],
                 'turn': block['turn'], 'checksum': block['checksum'],
                 'items': items(block['items'])}
                for block in record['blocks']]}
            for record in self.records()]}


def _keyed(log):
    """{(turn, category, n): block}: the n-th block of a category in a
    turn, which is how two players' logs line up."""
    out = {}
    for block in log.blocks:
        n = 0
        while (block['turn'], block['category'], n) in out:
            n += 1
        out[(block['turn'], block['category'], n)] = block
    return out


def _first_item_difference(a, b, path=()):
    """The path to the first item that differs between two item lists, and
    the two items there (None for a missing one), or None."""
    for index in range(max(len(a), len(b))):
        left = a[index] if index < len(a) else None
        right = b[index] if index < len(b) else None
        here = path + (((left or right)['id'], index),)
        if left is None or right is None or left['id'] != right['id'] \
                or ('items' in left) != ('items' in right):
            return here, left, right
        if 'items' in left:
            found = _first_item_difference(left['items'], right['items'], here)
            if found:
                return found
        elif left['value'] != right['value']:
            return here, left, right
    return None


def diff_logs(a, b):
    """The differences between two players' logs of the same game, by turn:
    a list of `{'turn', 'category', 'index', 'checksums', 'path', 'values'}`
    for each block whose checksum or items differ, oldest turn first, and
    within a turn in the order of the first log. `path` lists (id, position)
    pairs down to the first item that differs; `values` holds that item's
    value in each log (None where it is missing or a group). Blocks only
    one log has are listed with the other checksum None."""
    left, right = _keyed(a), _keyed(b)
    order = {key: n for n, key in enumerate(list(left) + [k for k in right if k not in left])}
    out = []
    for key in sorted(order, key=lambda k: (k[0], order[k])):
        one, two = left.get(key), right.get(key)
        if one is not None and two is not None and one['checksum'] == two['checksum'] \
                and one['items'] == two['items']:
            continue
        found = _first_item_difference(one['items'], two['items']) if one and two else None
        path, values = (), (None, None)
        if found:
            path = found[0]
            values = tuple(item.get('value') if item else None for item in found[1:])
        out.append({'turn': key[0], 'category': key[1], 'index': key[2],
                    'checksums': (one['checksum'] if one else None,
                                  two['checksum'] if two else None),
                    'path': list(path), 'values': values})
    return out
