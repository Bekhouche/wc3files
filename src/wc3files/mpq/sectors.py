"""
Reading one file's data out of an MPQ block, shared by MpqFiles and W3xFile.

The layout, as the MPQ format defines it:

1. A **single-unit** file is one block: encrypted as a whole with the file
   key, and compressed as a whole when its stored size is smaller than its
   real size.
2. Any other file is split into sectors of `512 << SectorSizeShift` bytes,
   `ceil(FileSize / SectorSize)` of them, the last one partial.
   - A **compressed or imploded** file starts with a sector offset table:
     one offset per sector plus the end offset, plus one more entry when the
     file carries a CRC block after its sectors. The table is encrypted with
     `key - 1`, sector i with `key + i`. A sector is compressed only when it
     is stored smaller than its real size; otherwise it is kept as it is.
   - An **uncompressed** file has no offset table at all: its sectors follow
     each other from the start of the block (still encrypted one by one with
     `key + i`). Reading a table there skipped the start of the file and
     read part of the next one instead; a real map's war3map.j, stored
     uncompressed, came out with its first 960 bytes missing and 960 bytes
     of object data appended.

`decompress` handles a sector of a file with the COMPRESS flag (a method
byte, then the data); a file with the older IMPLODE flag is PKWare DCL
throughout, with no method byte.
"""

import struct

from ..data.bin import BinaryDataType
from .data import MPQFileDataType
from .compression import decompress, explode

_COMPRESSED = MPQFileDataType.COMPRESS.value
_IMPLODED = MPQFileDataType.IMPLODE.value
_ENCRYPTED = MPQFileDataType.ENCRYPTED.value
_SINGLE_UNIT = MPQFileDataType.SINGLE_UNIT.value
_SECTOR_CRC = MPQFileDataType.SECTOR_CRC.value


def read_block(reader, block_entry, offset, sector_size, decrypt, key):
     """The data of one file.

     `offset` is the absolute position of the block, `key` the file key (used
     only when the block is encrypted), `decrypt(data, key)` the MPQ cipher,
     `decompress(sector)` the method-byte decompressor and `explode(data)` the
     PKWare DCL one.
     """
     flags = block_entry['Flags']
     file_size = block_entry['FileSize']
     encrypted = bool(flags & _ENCRYPTED)

     def unpack(raw, expected):
          if len(raw) >= expected:
               return raw
          if flags & _COMPRESSED:
               return decompress(raw, expected)
          if flags & _IMPLODED:
               return explode(raw)
          return raw

     if flags & _SINGLE_UNIT:
          reader.seek(offset)
          raw = _read(reader, block_entry['BlockSize'])
          if encrypted:
               raw = decrypt(raw, key)
          return unpack(raw, file_size)[:file_size]

     count = (file_size + sector_size - 1) // sector_size
     pieces = []
     if flags & (_COMPRESSED | _IMPLODED):
          entries = count + 1 + (1 if flags & _SECTOR_CRC else 0)
          reader.seek(offset)
          table = _read(reader, 4 * entries)
          if encrypted:
               table = decrypt(table, key - 1)
          positions = struct.unpack('<%dI' % entries, table)
          reader.seek(offset + positions[0])
          blob = _read(reader, positions[count] - positions[0])
          base = positions[0]
          for i in range(count):
               raw = blob[positions[i] - base:positions[i + 1] - base]
               if encrypted:
                    raw = decrypt(raw, key + i)
               pieces.append(unpack(raw, min(sector_size, file_size - i * sector_size)))
     else:
          reader.seek(offset)
          blob = _read(reader, file_size)
          for i in range(count):
               raw = blob[i * sector_size:(i + 1) * sector_size]
               if encrypted:
                    raw = decrypt(raw, key + i)
               pieces.append(raw)
     return b''.join(pieces)[:file_size]


def _read(reader, size):
     return reader.read(BinaryDataType.BYTES, num=size)
