"""The encoding table - content key to encoded key.

CASC addresses a file two ways: by what it *is* (CKey, the MD5 of the
decompressed bytes) and by what is *stored* (EKey, identifying the BLTE
blob). The root names files by CKey, the index locates blobs by EKey, so
this table is the join between them.

Format (header is big-endian, which the rest of this package's formats are
not - CASC inherited it from Blizzard's CDN tooling):

    'EN'            magic
    uint8           version (1)
    uint8           CKey hash size (16)
    uint8           EKey hash size (16)
    uint16          CKey page size in KB
    uint16          EKey page size in KB
    uint32          CKey page count
    uint32          EKey page count
    uint8           unused
    uint32          ESpec string block size

then the ESpec block, a page index (first key + page MD5 per page), and
the pages themselves. Each CKey page holds entries of:

    uint8           EKey count
    uint40          file size
    CKey[16]
    EKey[16] * count

Only the CKey pages are parsed: the EKey-spec half describes how each blob
was compressed, which matters for *writing* a CASC storage and nothing this
package does reads it.
"""
import struct


class CascEncodingError(Exception):
    pass


HEADER_SIZE = 22


class EncodingTable(object):
    def __init__(self):
        self.ckey_to_ekey = {}
        self.sizes = {}

    def read(self, data):
        if len(data) < HEADER_SIZE or data[:2] != b'EN':
            raise CascEncodingError(f'not an encoding file (magic {data[:2]!r})')

        (version, ckey_size, ekey_size, ckey_page_kb, ekey_page_kb,
         ckey_page_count, ekey_page_count, _unused,
         espec_size) = struct.unpack_from('>BBBHHIIBI', data, 2)

        if version != 1:
            raise CascEncodingError(f'unsupported encoding version {version}')
        if ckey_size != 16 or ekey_size != 16:
            raise CascEncodingError(
                f'unexpected key sizes (ckey {ckey_size}, ekey {ekey_size}); '
                f'this package assumes 16-byte MD5 keys'
            )

        page_size = ckey_page_kb * 1024
        # Skip the ESpec string block and the page index (one first-key plus
        # one page MD5 per page) to reach the pages themselves.
        pos = HEADER_SIZE + espec_size + ckey_page_count * (ckey_size + 16)
        end = pos + ckey_page_count * page_size
        if end > len(data):
            raise CascEncodingError(
                f'encoding CKey pages run past end of file '
                f'(need {end}, have {len(data)})'
            )

        for page_start in range(pos, end, page_size):
            offset = page_start
            page_end = page_start + page_size
            while offset + 6 + ckey_size <= page_end:
                key_count = data[offset]
                if key_count == 0:
                    break  # rest of the page is padding
                file_size = int.from_bytes(data[offset + 1:offset + 6], 'big')
                offset += 6

                ckey = data[offset:offset + ckey_size]
                offset += ckey_size

                needed = ekey_size * key_count
                if offset + needed > page_end:
                    raise CascEncodingError(
                        'encoding entry claims more keys than fit in its page'
                    )
                # The first EKey is the one actually stored; extra keys are
                # alternate encodings of the same content.
                self.ckey_to_ekey[ckey] = data[offset:offset + ekey_size]
                self.sizes[ckey] = file_size
                offset += needed

        return self

    def find(self, content_key):
        if isinstance(content_key, str):
            content_key = bytes.fromhex(content_key)
        return self.ckey_to_ekey.get(content_key)

    def size_of(self, content_key):
        if isinstance(content_key, str):
            content_key = bytes.fromhex(content_key)
        return self.sizes.get(content_key)

    def __len__(self):
        return len(self.ckey_to_ekey)
