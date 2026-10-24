from ..data import BinaryDataType
from .data import ShadowMapFileType
from wc3files.files import BinaryFile


class ShadowMapFile(object):
     """
     Reader/writer for war3map.shd (terrain shadow map).

     No magic number, no version field, no header at all - the whole file is
     `width * height` raw flag bytes, same grid as war3map.wpm (4x4 cells per
     terrain tile, row-major from the bottom-left). Because there's no self-
     describing header, `width`/`height` must be supplied by the caller (from an
     already-read `war3map.w3e`/`war3map.wpm`) rather than inferred.

     Cross-checked against War3Net.Build.Core's MapShadowMap ("`// True = 0xff,
     false = 0x00`"), HiveWE's shadow_map.ixx, and mdx-m3-viewer, which all agree
     on a boolean-as-byte format. Verified against 22 of 23 real local fixtures:
     every one is cleanly 100% `0x00`/`0xFF` (one fixture has exactly two distinct
     values, `0x00`/`0xFF`; every other is a single value, `0x00`, i.e. no shadow
     anywhere - both fully consistent with the documented format). The one
     remaining fixture is a **newly
     identified, separate corrupted extraction, not a format variant**: it's
     24,774,700 bytes - two orders of magnitude larger than every other real
     fixture's shd (which top out around 590KB) and structurally implausible for
     any real WC3 map's terrain size, with byte values spread continuously across
     the full 0-255 range rather than the clean binary pattern every other real
     fixture shows. Excluded from the "confirmed matches the format" count for
     that reason, not because the boolean format itself is in doubt - checking
     per-fixture (not just aggregating byte counts across all fixtures) is what
     caught this: the aggregate looked "86% non-binary" purely because this one
     oversized outlier dominated the total byte count.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path, width, height):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in ShadowMapFileType]:
                    raise ValueError(f"Invalid shadow map file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               actual_size = reader.size()
               expected_size = width * height
               if actual_size != expected_size:
                    raise ValueError(
                         f"war3map.shd size mismatch: expected {expected_size} bytes "
                         f"for a {width}x{height} grid, file is {actual_size} bytes"
                    )
               cells = reader.read(BinaryDataType.BYTES, num=expected_size)

          self._data = {'width': width, 'height': height, 'cells': cells}

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               writer.write(BinaryDataType.BYTES, bytes(self._data.get('cells', b'')))

     def get_data(self):
          return self._data

     @staticmethod
     def is_shadowed(byte_value):
          """Real fixtures only ever use 0x00/0xFF, but treat any nonzero byte as
          shadowed rather than requiring an exact 0xFF match - a stricter check
          isn't supported by anything in the format itself."""
          return byte_value != 0

     def to_bool_grid(self):
          return [self.is_shadowed(b) for b in self._data.get('cells', b'')]

     @classmethod
     def from_bool_grid(cls, width, height, bools):
          instance = cls()
          instance._data = {
               'width': width, 'height': height,
               'cells': bytes(0xFF if v else 0x00 for v in bools),
          }
          return instance
