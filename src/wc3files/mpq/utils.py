import bz2
import zlib
import struct
from io import BytesIO
from ..data import BinaryDataType
from .data import *

def _read_header(reader, offset=None):
     if offset:
          reader.seek(offset)

     data = reader.read(BinaryDataType.BYTES, num=32)
     header = dict(zip([field.value for field in MPQFileHeaderFields], struct.unpack('<4s2I2H4I', data)))

     if header['FormatVersion'] == 1:
          data = reader.read(BinaryDataType.BYTES, num=12)
          extended_header = dict(zip([field.value for field in MPQFileHeaderExtendedFieldsFormatV2], struct.unpack('q2h', data)))
          header.update(extended_header)
     elif header['FormatVersion'] == 2:
          'Format 3 (WoW - Cataclysm beta or newer)'
     elif header['FormatVersion'] == 3:
          'Format 4 (WoW - Cataclysm beta or newer)'
     return header


def _read_header_user_data(reader):
     data = reader.read(BinaryDataType.BYTES, num=16)
     header = dict(zip([field.value for field in MPQUserDataFields], struct.unpack('<4s3I', data)))
     header['Data'] = reader.read(BinaryDataType.BYTES, num=header['USER_DATA_SIZE'])
     return header

def _read_table(reader, header, table_type):
     if table_type != 'Hash' and table_type != 'Block':
          raise ValueError("Invalid table type.")

     table_offset = header['%sTableOffset' % table_type]
     table_entries = header['%sTableOffset' % table_type]
     
     key = _hash('(%s table)' % table_type, 'TABLE')

     reader.seek(table_offset + header['ArchiveOffset'])
     data = reader.read(BinaryDataType.BYTES, num=table_entries * 16)
     data = _decrypt(data, key)
     return [_unpack_entry(data, table_type, i) for i in range(table_entries)]

def _unpack_entry(data, table_type, position):
     entry_data = data[position*16:position*16+16]
     if table_type == 'Hash':
          entry_class = MPQHashTableEntry
     elif table_type == 'Block':
          entry_class = MPQBlockTableEntry
     else:
          raise ValueError("Invalid table type.")
     return entry_class._make(
                struct.unpack(entry_class.struct_format, entry_data))._asdict()

def _hash(string, hash_type):
     encryption_table = _prepare_encryption_table()

     hash_types = {
          'TABLE_OFFSET': 0,
          'HASH_A': 1,
          'HASH_B': 2,
          'TABLE': 3
     }
     seed1 = 0x7FED7FED
     seed2 = 0xEEEEEEEE

     for ch in string.upper():
          if not isinstance(ch, int):
               ch = ord(ch)

          value = encryption_table[(hash_types[hash_type] << 8) + ch]
          seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
          seed2 = ch + seed1 + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

     return seed1

def _prepare_encryption_table():
     seed = 0x00100001
     crypt_table = {}
     for i in range(256):
          index = i
          for j in range(5):
               seed = (seed * 125 + 3) % 0x2AAAAB
               temp1 = (seed & 0xFFFF) << 0x10
               seed = (seed * 125 + 3) % 0x2AAAAB
               temp2 = (seed & 0xFFFF)
               crypt_table[index] = (temp1 | temp2)
               index += 0x100
     return crypt_table

def _decrypt(data, key):
     encryption_table = _prepare_encryption_table()

     seed1 = key
     seed2 = 0xEEEEEEEE
     result = BytesIO()

     for i in range(len(data) // 4):
          seed2 += encryption_table[0x400 + (seed1 & 0xFF)]
          seed2 &= 0xFFFFFFFF
          value = struct.unpack("<I", data[i*4:i*4+4])[0]
          value = (value ^ (seed1 + seed2)) & 0xFFFFFFFF

          seed1 = ((~seed1 << 0x15) + 0x11111111) | (seed1 >> 0x0B)
          seed1 &= 0xFFFFFFFF
          seed2 = value + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

          result.write(struct.pack("<I", value))

     return result.getvalue()

def _get_hash_table_entry(hash_table, filename):
        hash_a = _hash(filename, 'HASH_A')
        hash_b = _hash(filename, 'HASH_B')
        for entry in hash_table:
            if (entry['hash_a'] == hash_a and entry['hash_b'] == hash_b):
                return entry
     
def _decompress(data):
     compression_type = ord(data[0:1])
     if compression_type == 0:
          return data
     elif compression_type == 2:
          return zlib.decompress(data[1:], 15)
     elif compression_type == 16:
          return bz2.decompress(data[1:])
     else:
          msg = "Unsupported compression type: {}".format(compression_type)
          raise RuntimeError(msg)
