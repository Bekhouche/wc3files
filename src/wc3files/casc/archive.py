"""Reading a blob out of an installed archive (`Data/data/data.NNN`).

An archive is a plain concatenation of blocks. Each block starts with a
30-byte header before its BLTE payload:

    16 bytes  the block's encoded key, byte-reversed
     4 bytes  total block size including this header (little-endian)
     2 bytes  flags
     8 bytes  checksum

The index gives (archive, offset, size); this skips that header and hands
the BLTE payload on. The reversed key is useful on its own - it is checked
against the key that was looked up, which catches a bad index read
immediately instead of letting a wrong offset decode into nonsense.
"""
import os
import struct

from . import blte

BLOCK_HEADER_SIZE = 30


class CascArchiveError(Exception):
    pass


def archive_path(data_dir, archive_number):
    return os.path.join(data_dir, f'data.{archive_number:03d}')


def read_block(data_dir, entry, verify_key=True):
    """Read and BLTE-decode one block, given an index entry."""
    path = archive_path(data_dir, entry.archive)
    if not os.path.exists(path):
        raise CascArchiveError(
            f'archive data.{entry.archive:03d} is not installed locally '
            f'(looked in {data_dir})'
        )

    with open(path, 'rb') as handle:
        handle.seek(entry.offset)
        raw = handle.read(entry.size)

    if len(raw) < BLOCK_HEADER_SIZE:
        raise CascArchiveError(
            f'block at data.{entry.archive:03d}+{entry.offset} is only '
            f'{len(raw)} bytes, too small for a block header'
        )

    stored_key = bytes(reversed(raw[:16]))
    block_size = struct.unpack_from('<I', raw, 16)[0]

    if verify_key and not stored_key.startswith(entry.key):
        raise CascArchiveError(
            f'block at data.{entry.archive:03d}+{entry.offset} holds key '
            f'{stored_key.hex()}, but the index pointed here for '
            f'{entry.key.hex()} - the index read is wrong'
        )
    if block_size != entry.size:
        raise CascArchiveError(
            f'block at data.{entry.archive:03d}+{entry.offset} declares size '
            f'{block_size}, index says {entry.size}'
        )

    return blte.decode(raw[BLOCK_HEADER_SIZE:])
