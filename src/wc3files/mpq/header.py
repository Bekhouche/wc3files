import struct

from loguru import logger
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
               elif header[MPQFileHeaderFields.FORMAT_VERSION.value] != 0:
                    # Read as format 0, as Warcraft III does whatever this field says.
                    # Map protectors set it to junk (41 of 1,025 benchmark maps, e.g.
                    # 37103), so raising here would refuse maps the game plays. The
                    # extended fields of v3/v4 archives (format 2 and 3) are not read.
                    logger.debug('MPQ format version {} read as format 0',
                                 header[MPQFileHeaderFields.FORMAT_VERSION.value])
               self._data.update(header)
          else:
               raise ValueError("Invalid MPQ file header. Got:", magic)
          
     def get_data(self):
          """Return the parsed MPQ data."""
          return self._data
