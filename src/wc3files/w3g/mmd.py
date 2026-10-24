"""W3MMD - Warcraft 3 Map Meta Data, as carried inside a replay.

W3MMD is the de-facto standard maps use to report what actually happened in
a game - who played, who won, and whatever statistics the map tracks - to
replay parsers and hosting bots. Without it a replay says who was in the
lobby and little else; with it, a Legion TD replay can tell you each
player's kills, leaks and final standing.

**How it rides in the replay.** A map emits messages through
`SyncStoredInteger`, which lands in the action stream as

    0x6B, <filename>\\0, <mission_key>\\0, <key>\\0, <uint32 value>

with `filename` always `MMD.Dat`. The mission key is `val:<n>` for a message
and `chk:<n>` for its checksum, where `<n>` is the message id, starting at 0
and incrementing. The `key` field carries the message itself (or, for a
checksum message, the id repeated). The trailing dword is a weak checksum the
game client uses to spot forgeries; a parser is explicitly not expected to
verify it.

Messages are located by that surrounding byte pattern rather than by walking
the action stream, which the standard itself endorses ("Messages can be
identified by the surrounding pattern"). That matters here: walking actions
would need the full, separately-versioned action table, and a single
unmodelled action id would silently desynchronise everything after it.

**Security model, and why this reports rather than judges.** The standard
assumes clients can lie: each message is sent by one or more randomly chosen
players, honest clients re-send a message when their checksum disagrees, and
a player sending two different messages for the same id is by definition
cheating. This parser therefore records *disagreements* instead of silently
picking a winner - `MmdData.conflicts` lists any id for which different
contents were seen, so a caller can apply whatever policy it wants rather
than inheriting one.

Source: the W3MMD standard, version 1.00 (PBug90/w3gPlus, `W3MMD.spec`).

**Verified** against the local replay corpus: 154 of 290 real replays carry
W3MMD, and their messages decode to coherent, gap-free id sequences with
matching checksum messages - see TODO.md for the measured figures.
"""
import re
import struct

SYNC_STORED_INTEGER = 0x6B
MMD_FILENAME = b'MMD.Dat'

_VALUE_PREFIX = 'val:'
_CHECK_PREFIX = 'chk:'

# Flags a map may set on a player, per the standard.
FLAGS = ('winner', 'loser', 'drawer', 'leaver', 'practicing')


class MmdError(ValueError):
    pass


def _split_arguments(text):
    """Split a message into arguments on non-escaped spaces.

    The standard defines exactly two escapes: '\\ ' for a space and '\\\\'
    for a backslash.
    """
    args = []
    current = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '\\' and index + 1 < length:
            nxt = text[index + 1]
            if nxt == ' ':
                current.append(' ')
                index += 2
                continue
            if nxt == '\\':
                current.append('\\')
                index += 2
                continue
            current.append(char)
            index += 1
            continue
        if char == ' ':
            if current:
                args.append(''.join(current))
                current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        args.append(''.join(current))
    return args


def _escape_argument(value):
    return str(value).replace('\\', '\\\\').replace(' ', '\\ ')


def iter_raw_messages(data):
    """Yield `(kind, message_id, contents, checksum)` for every W3MMD record
    found in `data`, in order.

    `kind` is 'val' or 'chk'. Malformed or truncated records are skipped
    rather than raising: the surrounding bytes are an action stream this
    module deliberately does not model, so a false positive on the signature
    is possible and must not take down the parse.
    """
    position = 0
    size = len(data)
    needle = MMD_FILENAME + b'\x00'

    while True:
        found = data.find(needle, position)
        if found == -1:
            return
        position = found + len(needle)

        # The action byte sits immediately before the filename.
        if found == 0 or data[found - 1] != SYNC_STORED_INTEGER:
            continue

        mission_end = data.find(b'\x00', position)
        if mission_end == -1:
            continue
        key_end = data.find(b'\x00', mission_end + 1)
        if key_end == -1:
            continue
        if key_end + 5 > size:
            continue

        mission_key = data[position:mission_end].decode('utf-8', errors='replace')
        contents = data[mission_end + 1:key_end].decode('utf-8', errors='replace')
        checksum = struct.unpack_from('<I', data, key_end + 1)[0]

        if mission_key.startswith(_VALUE_PREFIX):
            kind = 'val'
            raw_id = mission_key[len(_VALUE_PREFIX):]
        elif mission_key.startswith(_CHECK_PREFIX):
            kind = 'chk'
            raw_id = mission_key[len(_CHECK_PREFIX):]
        else:
            continue

        try:
            message_id = int(raw_id)
        except ValueError:
            continue

        yield kind, message_id, contents, checksum


class MmdData(object):
    """Everything a map reported about a game.

    `players` maps a W3MMD player id to a name, `variables` holds the
    per-player values the map tracked, `flags` the winner/loser/leaver style
    markers, and `events` the logged occurrences. `conflicts` records any
    message id for which different contents were seen - see the module
    docstring on why those are surfaced rather than resolved here.
    """

    def __init__(self):
        self.version = None            # (minimum, current)
        self.players = {}              # pid -> name
        self.definitions = {}          # variable name -> definition dict
        self.variables = {}            # pid -> {name: value}
        self.flags = {}                # pid -> set of flags
        self.event_definitions = {}    # event name -> definition dict
        self.events = []               # list of {'name':..., 'args': [...]}
        self.messages = {}             # message id -> contents
        self.checksums = {}            # message id -> contents of chk message
        self.conflicts = {}            # message id -> [differing contents]
        self.unknown = []              # messages whose type is not in the standard

    # -- construction -----------------------------------------------------

    @classmethod
    def from_bytes(cls, data):
        instance = cls()
        instance.read(data)
        return instance

    def read(self, data):
        for kind, message_id, contents, _checksum in iter_raw_messages(data):
            if kind == 'chk':
                self.checksums.setdefault(message_id, contents)
                continue

            existing = self.messages.get(message_id)
            if existing is None:
                self.messages[message_id] = contents
            elif existing != contents:
                # Two different messages under one id: by the standard, a
                # player is cheating. Record it and keep the first.
                self.conflicts.setdefault(message_id, [existing]).append(contents)

        for message_id in sorted(self.messages):
            self._apply(self.messages[message_id])
        return self

    # -- message handling -------------------------------------------------

    def _apply(self, contents):
        args = _split_arguments(contents)
        if not args:
            return
        kind = args[0]

        if kind == 'init':
            self._apply_init(args[1:])
        elif kind == 'DefVarP':
            self._apply_def_var(args[1:])
        elif kind == 'VarP':
            self._apply_var(args[1:])
        elif kind == 'FlagP':
            self._apply_flag(args[1:])
        elif kind == 'DefEvent':
            self._apply_def_event(args[1:])
        elif kind == 'Event':
            self._apply_event(args[1:])
        elif kind.lower() == 'blank':
            # Deliberately nothing: the standard defines 'blank' as a
            # non-message, used to drown out cheaters pre-sending ids.
            return
        else:
            self.unknown.append(contents)

    def _apply_init(self, args):
        if not args:
            return
        if args[0] == 'pid' and len(args) >= 3:
            try:
                self.players[int(args[1])] = args[2]
            except ValueError:
                self.unknown.append('init ' + ' '.join(args))
        elif args[0] == 'version' and len(args) >= 3:
            try:
                self.version = (int(args[1]), int(args[2]))
            except ValueError:
                self.unknown.append('init ' + ' '.join(args))

    def _apply_def_var(self, args):
        if len(args) < 4:
            return
        name, value_type, goal_type, suggestion = args[0], args[1], args[2], args[3]
        self.definitions[name] = {
            'type': value_type,
            'goal': goal_type,
            'suggestion': suggestion,
        }

    def _apply_var(self, args):
        if len(args) < 4:
            return
        try:
            pid = int(args[0])
        except ValueError:
            return
        name, operation, raw_value = args[1], args[2], args[3]

        definition = self.definitions.get(name, {})
        value_type = definition.get('type', 'int')
        value = self._coerce(raw_value, value_type)

        per_player = self.variables.setdefault(pid, {})
        if operation == '=':
            per_player[name] = value
        elif operation in ('+=', '-=') and not isinstance(value, str):
            current = per_player.get(name, 0)
            if isinstance(current, str):
                current = 0
            per_player[name] = current + value if operation == '+=' else current - value
        else:
            per_player[name] = value

    @staticmethod
    def _coerce(raw_value, value_type):
        if value_type == 'string':
            return raw_value
        try:
            return float(raw_value) if value_type == 'real' else int(raw_value)
        except ValueError:
            try:
                return float(raw_value)
            except ValueError:
                return raw_value

    def _apply_flag(self, args):
        if len(args) < 2:
            return
        try:
            pid = int(args[0])
        except ValueError:
            return
        flag = args[1]
        flags = self.flags.setdefault(pid, set())
        if flag in ('winner', 'loser', 'drawer'):
            # Only the last outcome counts, per the standard.
            flags.difference_update({'winner', 'loser', 'drawer'})
        flags.add(flag)

    def _apply_def_event(self, args):
        if len(args) < 2:
            return
        name = args[0]
        try:
            count = int(args[1])
        except ValueError:
            return
        argument_names = args[2:2 + count]
        fmt = args[2 + count] if len(args) > 2 + count else ''
        self.event_definitions[name] = {
            'arguments': argument_names,
            'format': fmt,
        }

    def _apply_event(self, args):
        if not args:
            return
        self.events.append({'name': args[0], 'args': args[1:]})

    # -- convenience ------------------------------------------------------

    def winners(self):
        return sorted(pid for pid, flags in self.flags.items() if 'winner' in flags)

    def losers(self):
        return sorted(pid for pid, flags in self.flags.items() if 'loser' in flags)

    def name_of(self, pid):
        return self.players.get(pid)

    def missing_message_ids(self):
        """Ids absent from an otherwise contiguous run starting at 0.

        A gap means messages were lost or never sent; the standard expects
        ids to start at 0 and increase by one.
        """
        if not self.messages:
            return []
        highest = max(self.messages)
        return [i for i in range(highest + 1) if i not in self.messages]

    def describe_event(self, event):
        """Render an event through its definition's format string.

        `{0}`-style placeholders are replaced by the event's arguments. An
        argument resolves to a player name in either of the two ways the
        standard allows: the placeholder itself says so (`{0:player}`), or
        the argument was *declared* as a player id by prefixing its name with
        `pid:` in the DefEvent (`DefEvent kill 2 pid:killer pid:victim ...`).
        """
        definition = self.event_definitions.get(event['name'])
        if not definition or not definition.get('format'):
            return f"{event['name']} " + ' '.join(event['args'])

        declared = definition.get('arguments') or []

        def resolve_player(value):
            try:
                return self.players.get(int(value), value)
            except ValueError:
                return value

        def substitute(match):
            index = int(match.group(1))
            suffix = match.group(2)
            if index >= len(event['args']):
                return match.group(0)
            value = event['args'][index]
            is_player = suffix == ':player' or (
                index < len(declared) and declared[index].startswith('pid:'))
            return resolve_player(value) if is_player else value

        return re.sub(r'\{(\d+)(:player)?\}', substitute, definition['format'])

    def __repr__(self):
        return (f'<MmdData players={len(self.players)} messages={len(self.messages)} '
                f'events={len(self.events)} conflicts={len(self.conflicts)}>')


def from_replay(replay_data):
    """Extract W3MMD from the dict `ReplayFile.get_data()` returns."""
    from . import blocks as _blocks

    compressed = replay_data.get('compressed_data', replay_data)
    command_bytes = bytearray()
    for block in compressed.get('blocks', ()):
        if block.get('id') in (_blocks.TIME_SLOT, _blocks.TIME_SLOT_OLD):
            command_bytes += block.get('command_data', b'')
    if not command_bytes and compressed.get('_raw_tail'):
        # A replay whose block stream did not parse can still be scanned.
        command_bytes = bytearray(compressed['_raw_tail'])
    return MmdData.from_bytes(bytes(command_bytes))
