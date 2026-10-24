"""PopcornFX files - the particle middleware Reforged uses for effects.

Reforged ships three PopcornFX file types alongside its own formats:

- `.pkcf` - a bake *config*, plain text. Fully read and written here.
- `.pkmm` - a baked *mesh*: a name table followed by vertex data. The name
  table is read; the vertex data is not.
- `.pkb` - a baked *effect*. Only the header is read.

PopcornFX is third-party middleware with no published binary spec, and
nothing in Warcraft III modding needs to author these - so the aim here is
to **identify** a file and surface the parts that are verifiable, not to
pretend to a full parse. Every reader keeps the original bytes, so
`to_bytes()` reproduces the file exactly.

The `.pkcf` text format is PopcornFX's own config syntax::

    Version = 2.5.0.63166;
    COvenBakeConfig_Mesh	$D857A09F
    {
        ImportAnimation = true;
    }
"""
import re
import struct

PKB_MAGIC = bytes([0x11, 0x0B, 0x00, 0xCA])


def _load(path_or_data):
    if isinstance(path_or_data, (bytes, bytearray)):
        return bytes(path_or_data)
    with open(path_or_data, 'rb') as handle:
        return handle.read()


class PopcornError(ValueError):
    pass


# ---------------------------------------------------------------- pkcf ----

_ASSIGN = re.compile(r'^\s*([A-Za-z_][\w.]*)\s*=\s*(.*?);\s*$')
_HEADER = re.compile(r'^\s*([A-Za-z_]\w*)\s*(\$[0-9A-Fa-f]+)?\s*$')


class PkcfFile(object):
    """A PopcornFX bake config (`.pkcf`), read and written in full.

    `settings` holds the top-level `Key = value;` pairs; `sections` holds
    each `TypeName $HASH { ... }` block as
    {'type': ..., 'hash': ..., 'settings': {...}}.
    """

    def __init__(self):
        self.settings = {}
        self.sections = []

    def read(self, path_or_text):
        if isinstance(path_or_text, str) and (
                '\n' in path_or_text or len(path_or_text) >= 4096):
            text = path_or_text
        else:
            text = _load(path_or_text).decode('utf-8-sig', errors='replace')
        self.settings = {}
        self.sections = []

        pending = None
        current = None
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('//'):
                continue
            if line == '{':
                if pending is None:
                    raise PopcornError(f'line {number}: "{{" with no section header')
                current = pending
                self.sections.append(current)
                pending = None
                continue
            if line == '}':
                if current is None:
                    raise PopcornError(f'line {number}: unbalanced "}}"')
                current = None
                continue

            match = _ASSIGN.match(line)
            if match:
                target = current['settings'] if current else self.settings
                target[match.group(1)] = match.group(2)
                continue

            match = _HEADER.match(line.replace('\t', ' '))
            if match:
                pending = {'type': match.group(1), 'hash': match.group(2),
                           'settings': {}}
                continue
            raise PopcornError(f'line {number}: cannot read {line!r}')

        if current is not None:
            raise PopcornError('a section is never closed with "}"')
        return self

    def to_text(self):
        lines = [f'{key} = {value};' for key, value in self.settings.items()]
        for section in self.sections:
            header = section['type']
            if section.get('hash'):
                header += '\t' + section['hash']
            lines.append(header)
            lines.append('{')
            lines += [f'\t{key} = {value};'
                      for key, value in section['settings'].items()]
            lines.append('}')
        return '\n'.join(lines) + '\n'

    def write(self, path):
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(self.to_text())

    def get_data(self):
        return {'settings': dict(self.settings), 'sections': self.sections}


# ---------------------------------------------------------------- pkmm ----

class PkmmFile(object):
    """A baked PopcornFX mesh (`.pkmm`) - name table only.

    The file opens with a zero dword, a name count, and that many
    length-prefixed names: the material, the mesh, then one per vertex
    stream (`Position`, `Normal`, `Color`, `Texcoord`, `UvStream1`). The
    vertex data that follows is kept as `body` and not interpreted.

    Verified against all six `.pkmm` files the game ships - in each the
    declared count matches exactly the names that decode.
    """

    def __init__(self):
        self.names = []
        self.body = b''
        self._raw = b''

    def read(self, path_or_data):
        data = _load(path_or_data)
        self._raw = data
        if len(data) < 8:
            raise PopcornError('too short to be a .pkmm file')
        leading, count = struct.unpack_from('<II', data, 0)
        if leading != 0:
            raise PopcornError(
                f'expected a zero dword at the start of a .pkmm, found '
                f'{leading:#x}; this reader only knows the shipped layout')
        offset = 8
        self.names = []
        for index in range(count):
            if offset >= len(data):
                raise PopcornError(f'name {index} runs past the end of the file')
            length = data[offset]
            offset += 1
            if offset + length > len(data):
                raise PopcornError(f'name {index} runs past the end of the file')
            self.names.append(data[offset:offset + length].decode('latin-1'))
            offset += length
        self.body = data[offset:]
        return self

    @property
    def material(self):
        return self.names[0] if self.names else None

    @property
    def mesh(self):
        return self.names[1] if len(self.names) > 1 else None

    @property
    def streams(self):
        """The vertex streams the mesh carries, e.g. Position, Normal."""
        return self.names[2:]

    def to_bytes(self):
        return self._raw

    def write(self, path):
        with open(path, 'wb') as handle:
            handle.write(self.to_bytes())

    def get_data(self):
        return {'material': self.material, 'mesh': self.mesh,
                'streams': list(self.streams), 'unparsed_bytes': len(self.body)}


# ----------------------------------------------------------------- pkb ----

class PkbFile(object):
    """A baked PopcornFX effect (`.pkb`) - header and type table.

    Every shipped `.pkb` starts with the same twelve bytes
    (`11 0B 00 CA 02 05 01 01 D7 F7 00 00`), then three dwords. The third
    is an offset to a table of the node types the effect instantiates
    (`CParticleEffect`, `CParticleRendererBillboard`, ...), encoded the same
    way as a `.pkmm` name table: a dword count, then length-prefixed names.

    The other two dwords are file-specific and what they count is not known,
    so they stay in `header_values` rather than being given invented names;
    the particle data itself is not decoded. The whole file is kept in
    `data`, so `to_bytes()` is exact.
    """

    def __init__(self):
        self.signature = b''
        self.header_values = ()
        self.type_names = []
        self.data = b''

    def read(self, path_or_data):
        data = _load(path_or_data)
        if len(data) < 24 or not data.startswith(PKB_MAGIC):
            raise PopcornError('not a PopcornFX .pkb file (bad magic)')
        self.data = data
        self.signature = data[:12]
        self.header_values = struct.unpack_from('<III', data, 12)
        self.type_names = self._read_type_names(data, self.header_values[2])
        return self

    @staticmethod
    def _read_type_names(data, offset):
        """The type table, or [] if the offset does not point at one.

        A bad offset means this file is laid out differently from the ones
        this was verified against, and reporting no names is honest where
        decoding whatever happens to be there would not be.
        """
        if not 0 < offset < len(data) - 4:
            return []
        count = struct.unpack_from('<I', data, offset)[0]
        position = offset + 4
        names = []
        for _ in range(count):
            if position >= len(data):
                return []
            length = data[position]
            position += 1
            if length == 0 or position + length > len(data):
                return []
            chunk = data[position:position + length]
            if not all(32 <= byte < 127 for byte in chunk):
                return []
            names.append(chunk.decode('latin-1'))
            position += length
        return names

    def to_bytes(self):
        return self.data

    def write(self, path):
        with open(path, 'wb') as handle:
            handle.write(self.to_bytes())

    def get_data(self):
        return {'signature': self.signature.hex(' '),
                'header_values': list(self.header_values),
                'type_names': list(self.type_names),
                'size': len(self.data)}
