from ..data import BinaryDataType
from .data import PathingFileType
from wc3files.files import BinaryFile


class PathingFile(object):
     """
     Reader/writer for war3map.wpm (pathing map: per-cell walkability/buildability
     flags, 4x4 cells per terrain tile).

     Header: `"MP3W"` magic, `version` (always 0 in every real fixture checked),
     `width`/`height` - confirmed against a real sibling war3map.w3e to be exactly
     `4 * (w3e_width - 1)` / `4 * (w3e_height - 1)` (one tile has 4x4 pathing cells;
     w3e's width/height are tile *corners*, one more than the tile count). Then
     `width * height` raw flag bytes, row-major from the bottom-left - same grid
     convention as war3map.shd.

     Flag bits are all *negative*-sense (a set bit means "not allowed"), cross-checked
     against War3Net.Build.Core's `PathingType` and HiveWE's `pathing_map.ixx`/wiki:

     - `0x02` no_walk, `0x04` no_fly, `0x08` no_build, `0x20` blight, `0x40` no_water -
       all four sources agree.
     - `0x10` "harvestable" per HiveWE (code + wiki, with a worked example of peon
       carry routes) - War3Net's own `PathingType` enum omits this bit entirely. **Not
       independently confirmed**: this bit was never set in any of the 2.7 million real
       cells scanned across 16 real local fixtures, so its meaning rests on HiveWE's
       documentation alone, not an observed example.
     - `0x80` "amphibious" per HiveWE; War3Net leaves it as an unnamed `UNK` bit. This
       one *is* common in real data (set in ~25% of cells scanned) and always
       co-occurs with the documented "cliff edge/solid doodad" and "map boundary" byte
       values (`0xCA`/`0xCE`) - consistent with *some* real structural meaning, but
       that correlation alone doesn't independently confirm "amphibious" specifically
       as the right name for it, so treat this name as HiveWE's best guess, not a
       verified fact.

     Common observed byte values (matches the documented examples exactly): `0x00`
     bridge doodad, `0x08` shallow water, `0x0A` deep water, `0x40` normal ground,
     `0x48` unbuildable ground, `0xCA` cliff edges/solid doodads, `0xCE` map boundary.
     """

     FLAG_BITS = {
          0x02: 'no_walk',
          0x04: 'no_fly',
          0x08: 'no_build',
          0x10: 'harvestable',
          0x20: 'blight',
          0x40: 'no_water',
          0x80: 'amphibious',
     }

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in PathingFileType]:
                    raise ValueError(f"Invalid pathing file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     @classmethod
     def decode_flags(cls, byte_value):
          return {name: bool(byte_value & bit) for bit, name in cls.FLAG_BITS.items()}

     @classmethod
     def encode_flags(cls, named_flags):
          value = 0
          for bit, name in cls.FLAG_BITS.items():
               if named_flags.get(name):
                    value |= bit
          return value

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          magic = reader.read(BinaryDataType.BYTES, num=4)
          if magic != b'MP3W':
               raise ValueError(f"Invalid pathing file header. Got: {magic!r}")

          version = reader.read(BinaryDataType.INT)
          if version != 0:
               raise ValueError(f"Unsupported war3map.wpm version: {version} (only 0 is implemented)")

          width = reader.read(BinaryDataType.INT)
          height = reader.read(BinaryDataType.INT)
          cells = reader.read(BinaryDataType.BYTES, num=width * height)

          return {'version': version, 'width': width, 'height': height, 'cells': cells}

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          writer.write(BinaryDataType.BYTES, b'MP3W')
          writer.write(BinaryDataType.INT, data.get('version', 0))
          writer.write(BinaryDataType.INT, data.get('width', 0))
          writer.write(BinaryDataType.INT, data.get('height', 0))
          writer.write(BinaryDataType.BYTES, bytes(data.get('cells', b'')))
