"""BLTE - the container every file inside a CASC storage is wrapped in.

A BLTE "file" is a chunk table plus the chunks themselves, each chunk
independently compressed. The modes that matter in practice:

  'N'  stored raw, copy the chunk out as-is
  'Z'  zlib
  'F'  another whole BLTE frame, decoded recursively
  'E'  encrypted (Salsa20, keyed by a TACT key the storage does not carry)

Only 'E' can't be handled locally: the keys ship with Blizzard's own client,
not with the data, so an encrypted chunk is reported as such rather than
silently returned as garbage. Nothing in a Warcraft III install has needed
one so far (see TODO.md for the measured figure).

Verified against the real local Warcraft III CASC install rather than from
the spec alone: a decoded file's MD5 must equal the content key the build
config/encoding table says it should have, which is an exact, self-checking
test - a wrong chunk table or a missed compression mode cannot accidentally
produce the right MD5.
"""
import struct
import zlib


class BlteError(Exception):
    pass


class BlteEncryptedError(BlteError):
    """A chunk is Salsa20-encrypted and no TACT key is available for it."""


BLTE_MAGIC = b'BLTE'


def decode(data, max_depth=8):
    """Decode a BLTE container, returning the raw file bytes.

    `data` must start at the BLTE magic (callers reading from a local archive
    are responsible for skipping the 30-byte block header first - see
    casc/archive.py, which knows that layout).
    """
    if len(data) < 8 or data[:4] != BLTE_MAGIC:
        raise BlteError(f'not a BLTE container (magic {data[:4]!r})')
    if max_depth <= 0:
        raise BlteError('BLTE recursion limit reached')

    header_size = struct.unpack_from('>I', data, 4)[0]

    if header_size == 0:
        # No chunk table at all: the remainder is one single chunk.
        return _decode_chunk(data[8:], max_depth)

    flags, count_hi, count_mid, count_lo = struct.unpack_from('>4B', data, 8)
    chunk_count = (count_hi << 16) | (count_mid << 8) | count_lo
    if chunk_count == 0:
        raise BlteError('BLTE header declares a chunk table with no chunks')

    table_end = 12 + chunk_count * 24
    if table_end > header_size or table_end > len(data):
        raise BlteError(
            f'BLTE chunk table for {chunk_count} chunks overruns the '
            f'{header_size}-byte header'
        )

    out = bytearray()
    offset = header_size
    for i in range(chunk_count):
        compressed_size, decompressed_size = struct.unpack_from('>II', data, 12 + i * 24)
        if offset + compressed_size > len(data):
            raise BlteError(
                f'BLTE chunk {i} runs past the end of the data '
                f'(needs {offset + compressed_size}, have {len(data)})'
            )
        chunk = _decode_chunk(data[offset:offset + compressed_size], max_depth)
        if len(chunk) != decompressed_size:
            raise BlteError(
                f'BLTE chunk {i} decoded to {len(chunk)} bytes, header says '
                f'{decompressed_size}'
            )
        out += chunk
        offset += compressed_size

    return bytes(out)


def _decode_chunk(chunk, max_depth):
    if not chunk:
        raise BlteError('empty BLTE chunk')
    mode = chunk[0:1]
    payload = chunk[1:]

    if mode == b'N':
        return payload
    if mode == b'Z':
        return zlib.decompress(payload)
    if mode == b'F':
        return decode(payload, max_depth - 1)
    if mode == b'E':
        raise BlteEncryptedError(
            'BLTE chunk is Salsa20-encrypted; the TACT key for it is not part '
            'of the installed data and this package has no key ring'
        )
    raise BlteError(f'unrecognized BLTE chunk mode {mode!r}')
