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

    def get_data(self):
        """Return the parsed MPQ hash table data."""
        return self._data

    def get_hash_table_entry(self, filename, language=0, platform=0):
        hash_a = self.mpq_cipher.hash(filename, MPQHashType.MPQ_HASH_NAME_A.value)
        hash_b = self.mpq_cipher.hash(filename, MPQHashType.MPQ_HASH_NAME_B.value)
        best = None
        for idx in range(len(self._data)):
            entry = self._data[idx]
            if (entry[MPQHashTableEntry.FILE_PATH_HASH_A.value] == hash_a and entry[MPQHashTableEntry.FILE_PATH_HASH_B.value] == hash_b):
                if language == entry[MPQHashTableEntry.LANGUAGE.value] and platform == entry[MPQHashTableEntry.PLATFORM.value]:
                        return entry
                if (entry[MPQHashTableEntry.LANGUAGE.value] == 0 or language == entry[MPQHashTableEntry.LANGUAGE.value]) and \
                        (entry[MPQHashTableEntry.PLATFORM.value] == 0 or platform == entry[MPQHashTableEntry.PLATFORM.value]):
                        best = entry
        return best
