import random
import struct
import zlib
from .data import *
from .cipher import MpqCipher
from . import protection as protection_module
from .attributes import pack_attributes


class MpqWriter:
     """
     Builds a classic (format-0) MPQ archive from an in-memory {filename: bytes} table.
     Mirrors the struct layouts and cipher used by header.py/hash_table.py/block_table.py/files.py
     so a written archive round-trips through this package's own readers (and StormLib/the game).

     `protection` (an `MpqProtectionLevel`, default `NONE`) opts into MPQ-container
     "protection" techniques real map protectors use - see mpq/protection.py for exactly
     what each tier does and its verification status (none of it is confirmed against a
     real WC3 client from this environment).

     `attributes` (bool, default False) additionally writes a real `(attributes)` file
     (per-block CRC32/FILETIME/MD5 - see mpq/attributes.py) - previously unimplemented in
     this package.

     `rng` (a `random.Random`, optional) drives every protection tier's non-deterministic
     choices (decoy hash values, format-version value) - pass a seeded instance for
     reproducible output (tests do this); defaults to a fresh, unseeded `random.Random()`.
     """

     HEADER_SIZE = 32
     LISTFILE = '(listfile)'
     ATTRIBUTES = '(attributes)'

     def __init__(self, sector_size_shift=3, protection=MpqProtectionLevel.NONE,
                  attributes=False, rng=None):
          self.sector_size_shift = sector_size_shift
          self.protection_level = protection
          self.write_attributes = attributes
          self.rng = rng or random.Random()
          self.mpq_cipher = MpqCipher()

     def build(self, files):
          """files: dict[str, bytes]. Returns the archive bytes. A '(listfile)' entry is
          generated from the other keys, unless the protection level omits it entirely."""
          files = {name: (data or b'') for name, data in files.items() if name != self.LISTFILE}
          if protection_module.should_include_listfile(self.protection_level):
               # Blizzard's own tools terminate every entry with \r\n (including the last), not just separate them.
               files[self.LISTFILE] = ''.join(name + '\r\n' for name in files).encode('utf8')

          names = list(files.keys())
          sector_size = 512 << self.sector_size_shift

          block_table = []
          file_data = bytearray()

          def add_file(name, data):
               block_offset = self.HEADER_SIZE + len(file_data)
               packed = self._pack_file(data, sector_size)
               block_table.append({
                    MPQBlockTableEntry.BLOCK_OFFSET.value: block_offset,
                    MPQBlockTableEntry.BLOCK_SIZE.value: len(packed),
                    MPQBlockTableEntry.FILE_SIZE.value: len(data),
                    MPQBlockTableEntry.FLAGS.value: MPQFileDataType.EXISTS.value | MPQFileDataType.COMPRESS.value,
               })
               file_data.extend(packed)
               return len(block_table) - 1

          named_block_indices = []
          attributes_contents = []
          for name in names:
               index = add_file(name, files[name])
               named_block_indices.append((name, index))
               attributes_contents.append(files[name])

          invalid_block_index = None
          if protection_module.decoys_per_file(self.protection_level):
               invalid_block_index = len(block_table)
               block_table.append(protection_module.invalid_block_entry())
               attributes_contents.append(b'')  # the decoy row describes no real content

          if self.write_attributes:
               attributes_data = pack_attributes(attributes_contents)
               index = add_file(self.ATTRIBUTES, attributes_data)
               named_block_indices.append((self.ATTRIBUTES, index))

          hash_table = protection_module.build_hash_table(
               named_block_indices, self.mpq_cipher, level=self.protection_level,
               invalid_block_index=invalid_block_index, rng=self.rng)

          hash_table_offset = self.HEADER_SIZE + len(file_data)
          packed_hash_table = self._pack_table(hash_table, MPQHashTableEntry, MpqUnpack.HASH_TABLE.value, MPQKeyType.MPQ_HASH_TABLE)

          block_table_offset = hash_table_offset + len(packed_hash_table)
          packed_block_table = self._pack_table(block_table, MPQBlockTableEntry, MpqUnpack.BLOCK_ENTRY.value, MPQKeyType.MPQ_BLOCK_TABLE)

          archive_size = block_table_offset + len(packed_block_table)
          format_version = protection_module.format_version_for(self.protection_level, self.rng)
          header = struct.pack(
               MpqUnpack.HEADER.value,
               MpqMagicType.MPQ_ARCHIVE_V1.value, self.HEADER_SIZE, archive_size, format_version, self.sector_size_shift,
               hash_table_offset, block_table_offset, len(hash_table), len(block_table),
          )

          return bytes(header) + bytes(file_data) + packed_hash_table + packed_block_table

     def write(self, files, dst_path):
          with open(dst_path, 'wb') as handle:
               handle.write(self.build(files))

     def _pack_file(self, data, sector_size):
          # Mirrors the "sectors = FileSize // sector_size + 1" convention the readers
          # (mpq/files.py, w3x_file.py) expect - it always includes a trailing guard
          # sector, empty unless FileSize lands exactly on a sector boundary.
          file_size = len(data)
          block_count = file_size // sector_size + 1
          positions = [4 * (block_count + 1)]
          sectors = bytearray()
          remaining = file_size
          read_offset = 0
          for _ in range(block_count):
               take = min(sector_size, remaining)
               chunk = data[read_offset:read_offset + take]
               read_offset += take
               remaining -= take
               stored = self._compress_sector(chunk) if chunk else b''
               sectors += stored
               positions.append(positions[-1] + len(stored))

          position_table = b''.join(struct.pack('<I', pos) for pos in positions)
          return position_table + bytes(sectors)

     @staticmethod
     def _compress_sector(chunk):
          compressed = b'\x02' + zlib.compress(chunk, 9)
          if len(compressed) < len(chunk):
               return compressed
          return bytes(chunk)

     def _pack_table(self, entries, entry_enum, fmt, key_type):
          data = b''.join(
               struct.pack(fmt, *(entry[field.value] for field in entry_enum))
               for entry in entries
          )
          key = self.mpq_cipher.hash(key_type.value, MPQHashType.MPQ_HASH_FILE_KEY.value)
          return self.mpq_cipher.encrypt(bytearray(data), key)
