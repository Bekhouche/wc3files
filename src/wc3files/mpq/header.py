import struct
from ..data import BinaryDataType
from .data import *

class MpqHeader:
     """
     Class for MPQ Header
     """

     def __init__(self):
          """Initialize by clearing any stored MPQ data."""
          self.clear()

     def clear(self):
          """Clear any previously stored MPQ data."""
          self._data = {}

     def read(self, reader):
          """Read and parse MPQ archive data from the reader."""
          current_offset = reader.tell()
          magic = reader.read(BinaryDataType.BYTES)
          reader.seek(current_offset)
          self._data['ARCHIVE_OFFSET'] = current_offset
          
          if magic == MpqMagicType.MPQ_ARCHIVE_V1.value:
               data = reader.read(BinaryDataType.BYTES, num=32)
               header = dict(zip([field.value for field in MPQFileHeaderFields], struct.unpack(MpqUnpack.HEADER.value, data)))
               if header[MPQFileHeaderFields.FORMAT_VERSION.value] == 1:
                    data = reader.read(BinaryDataType.BYTES, num=12)
                    extended_header = dict(zip([field.value for field in MPQFileHeaderExtendedFieldsFormatV2], struct.unpack(MpqUnpack.HEADER_v2.value, data)))
                    header.update(extended_header)
               elif header[MPQFileHeaderFields.FORMAT_VERSION.value] == 2:
                    print('Format 3 (WoW - Cataclysm beta or newer)')
               elif header[MPQFileHeaderFields.FORMAT_VERSION.value] == 3:
                    print('Format 4 (WoW - Cataclysm beta or newer)')
               self._data.update(header)
          else:
               raise ValueError("Invalid MPQ file header. Got:", magic)
          
     def get_data(self):
          """Return the parsed MPQ data."""
          return self._data
