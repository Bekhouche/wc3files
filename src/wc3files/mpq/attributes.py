"""
(attributes) file: per-block-table-entry CRC32/FILETIME/MD5 metadata that
Blizzard's own tools attach to an MPQ archive. Real, documented format,
previously unimplemented anywhere in this package (`(attributes)` generation
was explicitly listed as a gap - see TODO.md) - confirmed structurally
against `w3x2lni`'s own StormLib FFI bindings (`script/ffi/stormlib.lua`),
which build every archive with `dwAttrFlags = 7` (`MPQ_ATTRIBUTE_CRC32 |
MPQ_ATTRIBUTE_FILETIME | MPQ_ATTRIBUTE_MD5`) and `dwMpqVersion = 0`
(format version 1) - i.e. exactly the CRC32+FILETIME+MD5 combination this
module defaults to, not an assumption made from scratch.

Layout: header `dwVersion` (uint32, = 100 - the (attributes) file's own
internal format version, unrelated to the MPQ archive's format version),
`dwFlags` (uint32 bitmask, see MPQAttributesFlags). Then, for each flag bit
that's set, one flat array of that type sized to the archive's
*block-table* entry count (not hash-table entry count - matches deleted/
duplicate slots or a hash table larger than the file count) - one slot per
block-table row, in block-table order: CRC32 as 4-byte values, FILETIME as
8-byte values (Windows FILETIME: 100ns ticks since 1601-01-01), MD5 as
16-byte digests, PATCH_BIT as 1 byte per entry (a bitfield in the real
format, but never set by this module - PATCH_BIT support isn't attempted,
see TODO.md).

The `(attributes)` file's own block-table entry is conventionally *not*
covered by its own arrays (its content is computed before it's added to the
archive, so it doesn't know its own future block index) - every real
(attributes) writer works this way, not just this one.
"""

import hashlib
import struct
import time
import zlib

from .data import MPQAttributesFlags

ATTRIBUTES_VERSION = 100
DEFAULT_FLAGS = (MPQAttributesFlags.CRC32.value | MPQAttributesFlags.FILETIME.value
                 | MPQAttributesFlags.MD5.value)

# 1601-01-01 -> 1970-01-01, in 100ns ticks (Windows FILETIME epoch offset).
_WINDOWS_EPOCH_OFFSET_100NS = 116444736000000000


def to_filetime(timestamp=None):
     """Converts a Unix timestamp (default: now) to a Windows FILETIME
     (100ns ticks since 1601-01-01), the unit real (attributes) files use."""
     timestamp = time.time() if timestamp is None else timestamp
     return int(timestamp * 10_000_000) + _WINDOWS_EPOCH_OFFSET_100NS


def pack_attributes(file_contents, flags=DEFAULT_FLAGS, timestamp=None):
     """`file_contents`: ordered list of plaintext bytes, one per block-table
     entry the attributes file should cover (in block-table order), NOT
     including the `(attributes)` file's own entry. Returns the
     `(attributes)` file's own plaintext content (still needs the normal
     sector-compression/block-table treatment every other file gets)."""
     parts = [struct.pack('<2I', ATTRIBUTES_VERSION, flags)]

     if flags & MPQAttributesFlags.CRC32.value:
          parts.append(b''.join(struct.pack('<I', zlib.crc32(data)) for data in file_contents))

     if flags & MPQAttributesFlags.FILETIME.value:
          filetime = to_filetime(timestamp)
          parts.append(b''.join(struct.pack('<Q', filetime) for _ in file_contents))

     if flags & MPQAttributesFlags.MD5.value:
          parts.append(b''.join(hashlib.md5(data).digest() for data in file_contents))

     if flags & MPQAttributesFlags.PATCH_BIT.value:
          parts.append(bytes(len(file_contents)))

     return b''.join(parts)


def unpack_attributes(data, entry_count):
     """Inverse of pack_attributes: returns {'version', 'flags', and any of
     'crc32'/'filetime'/'md5'/'patch_bit' present per `flags`} - each a list
     of `entry_count` values (bytes objects for md5, ints otherwise)."""
     version, flags = struct.unpack_from('<2I', data, 0)
     result = {'version': version, 'flags': flags}
     offset = 8

     if flags & MPQAttributesFlags.CRC32.value:
          result['crc32'] = list(struct.unpack_from(f'<{entry_count}I', data, offset))
          offset += 4 * entry_count

     if flags & MPQAttributesFlags.FILETIME.value:
          result['filetime'] = list(struct.unpack_from(f'<{entry_count}Q', data, offset))
          offset += 8 * entry_count

     if flags & MPQAttributesFlags.MD5.value:
          result['md5'] = [bytes(data[offset + i * 16:offset + i * 16 + 16]) for i in range(entry_count)]
          offset += 16 * entry_count

     if flags & MPQAttributesFlags.PATCH_BIT.value:
          result['patch_bit'] = list(data[offset:offset + entry_count])
          offset += entry_count

     return result
