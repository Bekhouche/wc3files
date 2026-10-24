"""The local index (`Data/data/*.idx`) - where in the installed archives a
given encoded key actually lives.

An installed CASC storage keeps its own index, separate from the CDN's
per-archive `.index` files: 16 buckets, each with a numbered `.idx` whose
highest-numbered version is the live one. Each entry maps the first few
bytes of an encoded key to (archive number, offset, size).

Layout, read off the real local Warcraft III install rather than taken on
faith from a spec:

    0x00 uint32  header hash size (16)
    0x04 uint32  header hash
    0x08 uint16  version (7)
    0x0A uint8   bucket index
    0x0B uint8   extra bytes
    0x0C uint8   encoded size length   (4)
    0x0D uint8   storage offset length (5)
    0x0E uint8   encoding key length   (9)
    0x0F uint8   file offset bits      (30)
    0x10 uint64  segment size
    ...  padding to a 16-byte boundary
    0x20 uint32  entries size (bytes)
    0x24 uint32  entries hash
    0x28 entries

Each entry is `encoding key length` + `storage offset length` +
`encoded size length` bytes - 18 on this install. The storage offset is
big-endian and packs two values: the top bits are the archive number and
the low `file offset bits` are the byte offset into `data.NNN`.

The field lengths are read from each file's own header rather than
hardcoded, so an install that uses different ones still parses.
"""
import os
import re
import struct

_IDX_NAME = re.compile(r'^(?P<bucket>[0-9a-f]{2})(?P<version>[0-9a-f]{8})\.idx$', re.I)


class CascIndexError(Exception):
    pass


class IndexEntry(object):
    __slots__ = ('key', 'archive', 'offset', 'size')

    def __init__(self, key, archive, offset, size):
        self.key = key
        self.archive = archive
        self.offset = offset
        self.size = size

    def __repr__(self):
        return (f'<IndexEntry {self.key.hex()} archive={self.archive} '
                f'offset={self.offset} size={self.size}>')


class CascIndex(object):
    """Every local index bucket, merged into one encoded-key lookup."""

    def __init__(self):
        self.entries = {}
        self.key_length = None

    def read_directory(self, data_dir):
        """Load every bucket's live `.idx` from a `Data/data` directory.

        Buckets are versioned: several `.idx` files can share a bucket number
        and only the highest version is current, so loading all of them
        blindly would resurrect stale locations.
        """
        live = {}
        for name in os.listdir(data_dir):
            match = _IDX_NAME.match(name)
            if not match:
                continue
            bucket = int(match.group('bucket'), 16)
            version = int(match.group('version'), 16)
            if bucket not in live or version > live[bucket][0]:
                live[bucket] = (version, os.path.join(data_dir, name))

        if not live:
            raise CascIndexError(f'no .idx files found in {data_dir}')

        for _, path in sorted(live.values()):
            self.read_file(path)
        return self

    def read_file(self, path):
        with open(path, 'rb') as handle:
            data = handle.read()
        if len(data) < 0x28:
            raise CascIndexError(f'{path} is too small to be an index')

        header_hash_size = struct.unpack_from('<I', data, 0)[0]
        (version, bucket, extra_bytes, encoded_size_length,
         storage_offset_length, encoding_key_length,
         file_offset_bits) = struct.unpack_from('<HBBBBBB', data, 8)

        if version != 7:
            raise CascIndexError(
                f'{path}: unsupported local index version {version} (expected 7)'
            )
        if not 0 < encoding_key_length <= 16:
            raise CascIndexError(
                f'{path}: implausible encoding key length {encoding_key_length}'
            )
        if not 0 < file_offset_bits < storage_offset_length * 8:
            raise CascIndexError(
                f'{path}: file offset bits {file_offset_bits} does not fit in a '
                f'{storage_offset_length}-byte storage offset'
            )

        if self.key_length is None:
            self.key_length = encoding_key_length
        elif self.key_length != encoding_key_length:
            raise CascIndexError(
                f'{path}: encoding key length {encoding_key_length} differs from '
                f'{self.key_length} seen in an earlier bucket'
            )

        # Entries start on the first 16-byte boundary after the header.
        pos = (0x08 + header_hash_size + 0x0F) & ~0x0F
        entries_size = struct.unpack_from('<I', data, pos)[0]
        pos += 8  # skip the entries size and its hash

        entry_size = encoding_key_length + storage_offset_length + encoded_size_length
        if entries_size % entry_size:
            raise CascIndexError(
                f'{path}: entries block of {entries_size} bytes is not a whole '
                f'number of {entry_size}-byte entries'
            )
        if pos + entries_size > len(data):
            raise CascIndexError(f'{path}: entries block runs past end of file')

        offset_mask = (1 << file_offset_bits) - 1
        for _ in range(entries_size // entry_size):
            key = data[pos:pos + encoding_key_length]
            pos += encoding_key_length

            raw_offset = int.from_bytes(data[pos:pos + storage_offset_length], 'big')
            pos += storage_offset_length

            size = int.from_bytes(data[pos:pos + encoded_size_length], 'little')
            pos += encoded_size_length

            self.entries[key] = IndexEntry(
                key=key,
                archive=raw_offset >> file_offset_bits,
                offset=raw_offset & offset_mask,
                size=size,
            )
        return self

    def find(self, encoded_key):
        """Locate an encoded key. Accepts a full 16-byte key or hex string;
        the index only stores a prefix of each key, so it is truncated to
        whatever length this index uses."""
        if isinstance(encoded_key, str):
            encoded_key = bytes.fromhex(encoded_key)
        if self.key_length is None:
            raise CascIndexError('no index files have been read yet')
        return self.entries.get(encoded_key[:self.key_length])

    def __len__(self):
        return len(self.entries)
