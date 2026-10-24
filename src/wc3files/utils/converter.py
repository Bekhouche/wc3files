import struct
from wc3files.data import BinaryDataType

def convert_bytes_to(bytes, data_type):
     """Decode a raw byte buffer per BinaryDataType, matching BinaryFile.read()'s
     own conventions (little-endian unsigned int, single-precision float, utf-8
     string, unreal rounded to 7 decimal digits)."""
     if data_type == BinaryDataType.INT:
          return int.from_bytes(bytes, 'little')
     elif data_type == BinaryDataType.REAL:
          return struct.unpack('f', bytes)[0]
     elif data_type == BinaryDataType.UNREAL:
          return float(format(struct.unpack('f', bytes)[0], '.7f'))
     elif data_type == BinaryDataType.STRING:
          return bytes.decode('utf-8')
     elif data_type == BinaryDataType.BYTES:
          return bytes
     return None