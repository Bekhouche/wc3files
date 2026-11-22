import bisect
import struct
from ..data import BinaryDataType
from .data import *
from .cipher import MpqCipher
from .header import MpqHeader

class MpqHashTable:
    """
    Class for handling the MPQ Hash Table, including reading, decrypting,
    and retrieving hash table entries from an MPQ archive.
    """

    def __init__(self):
        """Initialize by clearing any stored MPQ data and setting up the cipher."""
        self.clear()
        self.mpq_cipher = MpqCipher()
        self.mpq_archive_header = MpqHeader()
        self.data_archive_header = {}

    def clear(self):
        """Clear any previously stored MPQ data."""
        self._data = {}

    def set_archive_header(self, mpq_archive_header):
          self.mpq_archive_header = mpq_archive_header
          self.data_archive_header = self.mpq_archive_header.get_data()

    def read(self, reader):
        """
        Read and parse MPQ archive hash table data from the reader.

        Args:
            reader: A binary reader object to read MPQ data.
            header: The MPQ archive header containing offsets and other metadata.
        """
        # Generate the decryption key for the hash table
        key = self.mpq_cipher.hash(MPQKeyType.MPQ_HASH_TABLE.value, MPQHashType.MPQ_HASH_FILE_KEY.value)

        # Move to the hash table offset in the file
        reader.seek(self.data_archive_header[MPQFileHeaderFields.HASH_TABLE_OFFSET.value] + self.data_archive_header['ARCHIVE_OFFSET'])
        
        # Decrypt the hash table entries
        table_size = 16 * self.data_archive_header[MPQFileHeaderFields.HASH_TABLE_ENTRIES.value]
        encrypted_data = reader.read(BinaryDataType.BYTES, num=table_size)
        data = self.mpq_cipher.decrypt(encrypted_data, key)

        # Parse each entry in the hash table
        for i in range(self.data_archive_header[MPQFileHeaderFields.HASH_TABLE_ENTRIES.value]):
            entry_data = data[i*16:i*16+16]
            entry = dict(zip(
                [field.value for field in MPQHashTableEntry],
                struct.unpack(MpqUnpack.HASH_TABLE.value, entry_data)
            ))
            self._data[i] = entry
        self._index = build_hash_index(self._data)

    def get_data(self):
        """Return the parsed MPQ hash table data."""
        return self._data

    def get_hash_table_entry(self, filename, language=0, platform=0, block_table=None):
        """The hash entry of `filename`, found the way the game finds it (see
        find_hash_entry). Pass the block table so that an entry pointing at a
        block that does not exist is skipped, as the game skips it."""
        return find_hash_entry(
            self._data,
            self.mpq_cipher.hash(filename, MPQHashType.MPQ_HASH_TABLE_OFFSET.value),
            self.mpq_cipher.hash(filename, MPQHashType.MPQ_HASH_NAME_A.value),
            self.mpq_cipher.hash(filename, MPQHashType.MPQ_HASH_NAME_B.value),
            language, platform, block_table, getattr(self, '_index', None))


HASH_ENTRY_FREE = 0xFFFFFFFF      # never used: ends a probe
HASH_ENTRY_DELETED = 0xFFFFFFFE   # used, then deleted: a probe goes on past it

_HASH_A = MPQHashTableEntry.FILE_PATH_HASH_A.value
_HASH_B = MPQHashTableEntry.FILE_PATH_HASH_B.value
_LANGUAGE = MPQHashTableEntry.LANGUAGE.value
_PLATFORM = MPQHashTableEntry.PLATFORM.value
_BLOCK_INDEX = MPQHashTableEntry.FILE_BLOCK_INDEX.value


def build_hash_index(entries):
    """An index over a hash table for find_hash_entry: the slots of each
    (hash A, hash B) pair, and the sorted list of free slots.

    A protected map's table is often almost full, so a walk for a name that
    is not there visits every entry - 65,536 of them in a real corpus map, a
    few milliseconds per lookup in Python, times thousands of lookups in a
    DEEP read. With the index a lookup only looks at the slots that carry
    its name, and at where the first free slot ends its walk."""
    by_hash = {}
    free = []
    for slot in range(len(entries)):
        entry = entries[slot]
        index = entry[_BLOCK_INDEX]
        if index == HASH_ENTRY_FREE:
            free.append(slot)
        elif index != HASH_ENTRY_DELETED:
            by_hash.setdefault((entry[_HASH_A], entry[_HASH_B]), []).append(slot)
    return by_hash, free


def find_hash_entry(entries, home_hash, hash_a, hash_b, language=0, platform=0, block_table=None,
                    index=None):
    """Look a name up in a hash table the way the MPQ format, Storm and
    StormLib do: start at `home_hash & (size - 1)` and walk forward, past
    deleted entries, until the name matches or a free entry ends the walk.

    This used to compare the name with every entry of the table, which cost
    a full pass per lookup and could return an entry the game never reaches.
    With `block_table`, a match whose block index is out of range or whose
    block does not exist is skipped and the walk goes on - the decoy entries
    protectors put ahead of a file's real one (and MpqWriter's EXPERIMENTAL
    and AGGRESSIVE levels) rely on exactly that.

    `entries` is indexable by slot (a list, or a dict keyed 0..n-1). An
    exact language and platform match wins; otherwise the first neutral one
    (language 0, platform 0) on the walk.

    `index` (from build_hash_index) gives the same answer without walking:
    only the slots that carry the name, in walk order, up to the first free
    slot."""
    size = len(entries)
    if not size:
        return None
    start = home_hash & (size - 1)
    if index is None:
        slots = []
        slot = start
        for _ in range(size):
            entry = entries[slot]
            block = entry[_BLOCK_INDEX]
            if block == HASH_ENTRY_FREE:
                break
            if block != HASH_ENTRY_DELETED and entry[_HASH_A] == hash_a and entry[_HASH_B] == hash_b:
                slots.append(slot)
            slot = (slot + 1) % size
    else:
        by_hash, free = index
        candidates = by_hash.get((hash_a, hash_b))
        if not candidates:
            return None
        if free:
            at = bisect.bisect_left(free, start)
            limit = (free[at] - start) if at < len(free) else (free[0] + size - start)
        else:
            limit = size
        slots = [slot for distance, slot in sorted(((slot - start) % size, slot) for slot in candidates)
                 if distance < limit]

    exists = MPQFileDataType.EXISTS.value
    best = None
    for slot in slots:
        entry = entries[slot]
        block = entry[_BLOCK_INDEX]
        if block_table is not None and not (block < len(block_table)
                                            and block_table[block]['Flags'] & exists):
            continue
        if entry[_LANGUAGE] == language and entry[_PLATFORM] == platform:
            return entry
        if best is None and entry[_LANGUAGE] in (0, language) and entry[_PLATFORM] in (0, platform):
            best = entry
    return best
