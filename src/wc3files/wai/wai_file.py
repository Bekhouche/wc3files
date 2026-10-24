"""WAI - the AI script format the World Editor's AI Editor saves.

A `.wai` holds a melee AI: the race it plays, its base unit types, and the
condition/action tree that drives its build order. The game also compiles
these to `.ai` JASS scripts, which is the form `wc3files.scripts` already
reads.

**This reader is deliberately partial.** The format is undocumented, and
only six samples ship with the game, which is not enough to pin down the
condition tree that makes up the bulk of the file. Reading the header is
verified against all six; everything past it is kept as `tail` bytes and
not interpreted, so `write()` still reproduces the original file exactly
when the header is unchanged.

Header, little-endian and unaligned (the format packs strings and integers
with no padding)::

    u32            version, always 2
    cstring        name          "Grunt Master", "Navy"
    u32            race          1 human, 2 orc, 3 undead
    u32            id            differs per script
    u32            count         always 4 in the shipped files
    char[4]*count  base units    worker, worker, town hall, town hall

What `id` counts is unknown - it is not a file length or an offset, and the
two scripts that share a value (`reforged_u08_blue` and `_red`) differ
elsewhere, so it is exposed as a number without a claimed meaning.
"""
import struct

RACES = {1: 'human', 2: 'orc', 3: 'undead', 4: 'nightelf'}


class WaiError(ValueError):
    pass


class WaiFile(object):
    """An AI-editor script. Header fields are parsed; the body is kept raw."""

    def __init__(self):
        self.version = 2
        self.name = ''
        self.race = 0
        self.id = 0
        self.base_units = []
        self.tail = b''

    def read(self, path_or_data):
        if isinstance(path_or_data, (bytes, bytearray)):
            data = bytes(path_or_data)
        else:
            with open(path_or_data, 'rb') as handle:
                data = handle.read()

        if len(data) < 8:
            raise WaiError('too short to be a .wai file')
        self.version = struct.unpack_from('<I', data, 0)[0]
        if self.version != 2:
            raise WaiError(
                f'unsupported .wai version {self.version}; only version 2 has '
                f'been seen, and guessing at another layout would mis-parse')

        end = data.find(b'\0', 4)
        if end < 0:
            raise WaiError('the name is not terminated')
        self.name = data[4:end].decode('latin-1')

        offset = end + 1
        if offset + 12 > len(data):
            raise WaiError('truncated before the header counts')
        self.race, self.id, count = struct.unpack_from('<III', data, offset)
        offset += 12

        if count > (len(data) - offset) // 4:
            raise WaiError(f'base-unit count {count} runs past the end of the file')
        self.base_units = [data[offset + i * 4: offset + i * 4 + 4].decode('latin-1')
                           for i in range(count)]
        offset += count * 4

        self.tail = data[offset:]
        return self

    def write(self, path):
        with open(path, 'wb') as handle:
            handle.write(self.to_bytes())

    def to_bytes(self):
        out = bytearray()
        out += struct.pack('<I', self.version)
        out += self.name.encode('latin-1') + b'\0'
        out += struct.pack('<III', self.race, self.id, len(self.base_units))
        for code in self.base_units:
            out += code.encode('latin-1')[:4].ljust(4, b'\0')
        out += self.tail
        return bytes(out)

    @property
    def race_name(self):
        return RACES.get(self.race, f'unknown ({self.race})')

    def strings(self):
        """The null-terminated text in the unparsed body.

        The body names the AI's condition and action functions
        (`GetBooleanAnd`, `OperatorCompareInteger`) along with its build-order
        labels. Listing them says a lot about what a script does without
        claiming to understand the tree they sit in - which is why this is a
        separate helper and not part of `read()`.
        """
        out = []
        current = bytearray()
        for byte in self.tail:
            if 32 <= byte < 127:
                current.append(byte)
            else:
                if len(current) >= 3:
                    out.append(current.decode('latin-1'))
                current.clear()
        if len(current) >= 3:
            out.append(current.decode('latin-1'))
        return out

    def get_data(self):
        return {
            'version': self.version,
            'name': self.name,
            'race': self.race,
            'race_name': self.race_name,
            'id': self.id,
            'base_units': list(self.base_units),
            'unparsed_bytes': len(self.tail),
        }
