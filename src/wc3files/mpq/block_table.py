import struct
from io import BytesIO
from ..data import BinaryDataType
from .data import *
from .cipher import MpqCipher
from .header import MpqHeader
from .hash_table import MpqHashTable


class MpqBlockTable:
    """
    Class for Block Table
    """

    def __init__(self):
        """Initialize by clearing any stored MPQ data."""
        self.clear()
        self.mpq_cipher = MpqCipher()
        self.mpq_hash_table = MpqHashTable()
        self.mpq_archive_header = MpqHeader()
        self.data_hash_table = {}
        self.data_archive_header = {}

    def clear(self):
        """Clear any previously stored MPQ data."""
        self._data = {}

    def set_archive_header(self, mpq_archive_header):
        self.mpq_archive_header = mpq_archive_header
        self.data_archive_header = self.mpq_archive_header.get_data()

    def set_hash_table(self, mpq_hash_table):
        self.mpq_hash_table = mpq_hash_table
        self.data_hash_table = self.mpq_hash_table.get_data()

    def read(self, reader):
        self._data = {}
        key = self.mpq_cipher.hash(
            MPQKeyType.MPQ_BLOCK_TABLE.value, MPQHashType.MPQ_HASH_FILE_KEY.value)

        reader.seek(
            self.data_archive_header[MPQFileHeaderFields.BLOCK_TABLE_OFFSET.value] + self.data_archive_header['ARCHIVE_OFFSET'])

        data = self.mpq_cipher.decrypt(reader.read(
            BinaryDataType.BYTES, num=16*self.data_archive_header[MPQFileHeaderFields.BLOCK_TABLE_ENTRIES.value]), key)
        for i in range(self.data_archive_header[MPQFileHeaderFields.BLOCK_TABLE_ENTRIES.value]):
            entry = dict(zip([field.value for field in MPQBlockTableEntry], struct.unpack(
                MpqUnpack.BLOCK_ENTRY.value, data[i*16:i*16+16])))
            self._data[i] = entry

    def get_data(self):
        """Return the parsed MPQ data."""
        return self._data
