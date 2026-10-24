"""war3map.doo - doodad and destructable placement.

This is a different format from war3mapUnits.doo, which lives next to it and
shares both the `.doo` extension and the `W3do` magic. Before this existed,
`war3map.doo` was being handed to `MapUnitsFile` on the strength of its
extension alone, and failed on every one of the 48 files in the benchmark
corpus.

Record layout, derived from that corpus and checked by requiring the parse
to land exactly on the end of the file:

    char[4]  id                 doodad or destructable rawcode
    int      variation
    float[3] position
    float    rotation           radians
    float[3] scale
    char[4]  skin               Reforged; present when the next byte looks
                                printable, the same test MapUnitsFile uses
    int      unknown_v13        format 13 only
    byte     flags
    byte     life               percent
    int      item_table_pointer index into the map's item tables, or -1
    int      item_set_count     each set: count, then (char[4], int) pairs
    int      unknown_v13        format 13 only
    int      editor_id
    float    roll               format 13 only
    float    pitch              format 13 only
    int      light_count        format 13 only
    byte[36] lights[light_count]

Then a second section of "special" doodads - cliff and terrain decoration -
with its own version and count, each entry a rawcode and three ints.

**The light block has nothing marking it.** In format 13 a minority of
records carry 36 further bytes holding what decodes as a BGRA colour and
three floats; across the corpus they belong to doodads such as `LObz` and
`LOtz`, and the game evidently knows which types have one from its own
doodad table rather than from anything in the file. So presence is decided
structurally: if the bytes that follow do not begin a record but the bytes
36 further on do, the block is there. That is a heuristic, which is why
every read is checked by writing the parse back and comparing it against
the bytes it came from - see `verify`. A file that does not reproduce is
refused rather than returned, so this can report nothing wrong and be wrong.
"""
import struct

from loguru import logger

from ..data import BinaryDataType
from wc3files.files import BinaryFile

MAGIC = b'W3do'

# A skin rawcode's first byte is always printable; the flags byte that would
# otherwise sit there never is. Same threshold MapUnitsFile uses.
PEEK_PRINTABLE_THRESHOLD = 0x20

LIGHT_BLOCK_SIZE = 36


class MapDoodadsFile(object):
    """Reader/writer for war3map.doo."""

    def __init__(self):
        self.clear()

    def clear(self):
        self._data = {'version': 8, 'subversion': 11, 'doodads': [],
                      'special_version': 0, 'specials': []}

    # -- reading ----------------------------------------------------------

    def read(self, file_path, verify=True):
        if isinstance(file_path, (bytes, bytearray)):
            raw = bytes(file_path)
        else:
            with open(file_path, 'rb') as handle:
                raw = handle.read()

        self._data = self._read(raw)

        if verify:
            rebuilt = self.to_bytes()
            if rebuilt != raw:
                raise ValueError(
                    f'war3map.doo: parsed, but writing it back gave '
                    f'{len(rebuilt)} bytes against the original\'s {len(raw)}, '
                    f'so the parse is not trustworthy and is refused. '
                    f'Format {self._data["version"]}/'
                    f'{self._data["subversion"]}; see this module\'s docstring '
                    f'on the unmarked light block.')
        return self

    def _read(self, raw):
        if raw[:4] != MAGIC:
            raise ValueError(f'Invalid doodad file header. Got: {raw[:4]!r}')
        version, subversion, count = struct.unpack_from('<iii', raw, 4)
        offset = 16

        doodads = []
        for _index in range(count):
            doodad, offset = self._read_doodad(raw, offset, version, subversion)
            doodads.append(doodad)

        special_version, special_count = struct.unpack_from('<ii', raw, offset)
        offset += 8
        specials = []
        for _ in range(special_count):
            specials.append({
                'id': raw[offset:offset + 4].decode('latin-1'),
                'variation': struct.unpack_from('<i', raw, offset + 4)[0],
                'x': struct.unpack_from('<i', raw, offset + 8)[0],
                'y': struct.unpack_from('<i', raw, offset + 12)[0],
            })
            offset += 16

        if offset != len(raw):
            raise ValueError(
                f'war3map.doo: {len(raw) - offset} bytes left over after '
                f'{count} doodads and {special_count} special doodads')

        return {'version': version, 'subversion': subversion,
                'doodads': doodads, 'special_version': special_version,
                'specials': specials}

    def _read_doodad(self, raw, offset, version, subversion):
        doodad = {
            'id': raw[offset:offset + 4].decode('latin-1'),
            'variation': struct.unpack_from('<i', raw, offset + 4)[0],
            'position': struct.unpack_from('<fff', raw, offset + 8),
            'rotation': struct.unpack_from('<f', raw, offset + 20)[0],
            'scale': struct.unpack_from('<fff', raw, offset + 24)[0:3],
        }
        offset += 36

        has_skin = raw[offset] >= PEEK_PRINTABLE_THRESHOLD
        doodad['has_skin'] = has_skin
        if has_skin:
            doodad['skin'] = raw[offset:offset + 4].decode('latin-1')
            offset += 4
        else:
            doodad['skin'] = doodad['id']

        if version >= 13:
            doodad['unknown_v13'] = struct.unpack_from('<i', raw, offset)[0]
            offset += 4

        doodad['flags'] = raw[offset]
        doodad['life'] = raw[offset + 1]
        offset += 2

        doodad['item_table_pointer'] = struct.unpack_from('<i', raw, offset)[0]
        offset += 4
        set_count = struct.unpack_from('<i', raw, offset)[0]
        offset += 4
        item_sets = []
        for _ in range(set_count):
            item_count = struct.unpack_from('<i', raw, offset)[0]
            offset += 4
            items = []
            for _ in range(item_count):
                items.append({
                    'id': raw[offset:offset + 4].decode('latin-1'),
                    'chance': struct.unpack_from('<i', raw, offset + 4)[0],
                })
                offset += 8
            item_sets.append(items)
        doodad['item_sets'] = item_sets

        if version >= 13:
            doodad['unknown_v13_b'] = struct.unpack_from('<i', raw, offset)[0]
            offset += 4

        doodad['editor_id'] = struct.unpack_from('<i', raw, offset)[0]
        offset += 4

        if version >= 13:
            # roll, pitch, then a count of lights and that many light blocks.
            # This was previously read as three unidentified ints and the
            # light data detected by looking ahead for the next record, which
            # worked but was a guess; the third int is simply the count.
            doodad['roll'] = struct.unpack_from('<f', raw, offset)[0]
            doodad['pitch'] = struct.unpack_from('<f', raw, offset + 4)[0]
            light_count = struct.unpack_from('<I', raw, offset + 8)[0]
            offset += 12
            if light_count > (len(raw) - offset) // LIGHT_BLOCK_SIZE:
                raise ValueError(
                    f'doodad {doodad["id"]!r} declares {light_count} lights, '
                    f'which runs past the end of the file')
            lights = []
            for _ in range(light_count):
                lights.append(raw[offset:offset + LIGHT_BLOCK_SIZE])
                offset += LIGHT_BLOCK_SIZE
            doodad['lights'] = lights

        return doodad, offset

    # -- writing ----------------------------------------------------------

    def to_bytes(self):
        data = self._data
        version = data.get('version', 8)
        subversion = data.get('subversion', 11)
        doodads = data.get('doodads', [])

        out = bytearray(MAGIC)
        out += struct.pack('<iii', version, subversion, len(doodads))
        for doodad in doodads:
            out += self._write_doodad(doodad, version, subversion)

        specials = data.get('specials', [])
        out += struct.pack('<ii', data.get('special_version', 0), len(specials))
        for special in specials:
            out += special['id'].encode('latin-1')[:4].ljust(4, b'\0')
            out += struct.pack('<iii', special.get('variation', 0),
                               special.get('x', 0), special.get('y', 0))
        return bytes(out)

    def _write_doodad(self, doodad, version, subversion):
        out = bytearray()
        out += doodad['id'].encode('latin-1')[:4].ljust(4, b'\0')
        out += struct.pack('<i', doodad.get('variation', 0))
        out += struct.pack('<fff', *doodad.get('position', (0.0, 0.0, 0.0)))
        out += struct.pack('<f', doodad.get('rotation', 0.0))
        out += struct.pack('<fff', *doodad.get('scale', (1.0, 1.0, 1.0)))

        if doodad.get('has_skin', doodad.get('skin') != doodad['id']):
            out += doodad.get('skin', doodad['id']).encode('latin-1')[:4].ljust(4, b'\0')

        if version >= 13:
            out += struct.pack('<i', doodad.get('unknown_v13', -1))

        out += bytes([doodad.get('flags', 2) & 0xFF,
                      doodad.get('life', 100) & 0xFF])

        out += struct.pack('<i', doodad.get('item_table_pointer', -1))
        item_sets = doodad.get('item_sets', [])
        out += struct.pack('<i', len(item_sets))
        for items in item_sets:
            out += struct.pack('<i', len(items))
            for item in items:
                out += item['id'].encode('latin-1')[:4].ljust(4, b'\0')
                out += struct.pack('<i', item.get('chance', 0))

        if version >= 13:
            out += struct.pack('<i', doodad.get('unknown_v13_b', -1))

        out += struct.pack('<i', doodad.get('editor_id', 0))

        if version >= 13:
            out += struct.pack('<f', doodad.get('roll', 0.0))
            out += struct.pack('<f', doodad.get('pitch', 0.0))
            lights = doodad.get('lights') or []
            out += struct.pack('<I', len(lights))
            for light in lights:
                out += bytes(light)
        return bytes(out)

    def write(self, file_path):
        with open(file_path, 'wb') as handle:
            handle.write(self.to_bytes())

    # -- data in and out --------------------------------------------------

    def get_data(self):
        return self._data

    def import_data(self, data):
        self._data = data
        return self

    def __repr__(self):
        data = self._data
        return (f'<MapDoodadsFile {data.get("version")}/'
                f'{data.get("subversion")} {len(data.get("doodads", []))} '
                f'doodads, {len(data.get("specials", []))} special>')
