from ..data import BinaryDataType
from .data import *

class MpqOptionalData:
     """
     Class for handling MPQ archives that may not start at the beginning of the file. 
     Installation packages or patch executables often append MPQ archives at the end 
     of an EXE file. If the MPQ doesn't start at offset 0, it must be aligned to a 512-byte (0x200) boundary.
     """

     def __init__(self):
          """Initialize by clearing any stored MPQ data."""
          self.clear()

     def clear(self):
          """Clear any previously stored MPQ data."""
          self._data = {}

     def read(self, reader):
          """Read and parse MPQ archive data from the reader."""
          current_offset = 0
          reader.seek(current_offset)  # Rewind reader to the beginning
          magic = reader.read(BinaryDataType.BYTES)
          self._data['ARCHIVE_OFFSET'] = current_offset  # Default to 0, assuming no valid MPQ archive yet

          # Check for custom archive type (HM3W archive)
          if magic == MpqMagicType.HM3W_ARCHIVE.value:
               self._data['MAGIC'] = magic
               self._data['UNKNOWN'] = reader.read(BinaryDataType.INT)  # Read unknown integer field
               self._data['MAP_NAME'] = reader.read(BinaryDataType.STRING)  # Read the map name
               self._data['MAP_FLAGS'] = reader.read(BinaryDataType.INT)  # Read map flags
               self._data['MAX_PLAYERS'] = reader.read(BinaryDataType.INT)  # Read max number of players
               
               # Align to 512-byte boundary by skipping remaining bytes
               remaining_bytes = 512 - (4 + 4 + len(self._data['MAP_NAME']) + 4 + 4)
               if remaining_bytes > 0:
                    reader.read(remaining_bytes)  # Skip extra bytes (assumed to be padding)
               
               # Optionally, read footer if it exists
               footer_id = reader.read(BinaryDataType.BYTES, num=4)
               if footer_id == MpqMagicType.NGIS_ARCHIVE.value:
                    self._data['FOOTER_ID'] = footer_id
                    self._data['AUTH_DATA'] = reader.read(256)  # Read 256 bytes of authentication data
               else:
                    reader.seek(reader.tell() - 5)  # Rewind if footer ID is not valid

               # Continue reading potential MPQ archive types
               while True:
                    # Start by reading the first byte
                    next_magic = reader.read(BinaryDataType.BYTES, num=1)
                    
                    if next_magic == b'M':
                         # Found 'M', now check for 'P'
                         next_magic = reader.read(BinaryDataType.BYTES, num=1)
                         
                         if next_magic == b'P':
                              # Found 'P', now check for 'Q'
                              next_magic = reader.read(BinaryDataType.BYTES, num=1)
                              
                              if next_magic == b'Q':
                                   # Found 'Q', so we found the MPQ sequence
                                   self._data['ARCHIVE_OFFSET'] = reader.tell()  # Store offset of valid MPQ archive
                                   break  # Exit the loop after confirming MPQ order
               self._data['ARCHIVE_OFFSET'] -= 3

     def get_data(self):
          """Return the parsed MPQ data."""
          return self._data
    
     @staticmethod
     def parse_flags(value):
          flags = {
               0x0001: "hide minimap in preview screens",
               0x0002: "modify ally priorities",
               0x0004: "melee map",
               0x0008: "playable map size was large and has never been reduced to medium",
               0x0010: "masked areas are partially visible",
               0x0020: "fixed player setting for custom forces",
               0x0040: "use custom forces",
               0x0080: "use custom techtree",
               0x0100: "use custom abilities",
               0x0200: "use custom upgrades",
               0x0400: "map properties menu opened at least once since map creation",
               0x0800: "show water waves on cliff shores",
               0x1000: "show water waves on rolling shores"
          }
          
          # Create a dictionary to store the result
          result = {desc: bool(value & flag) for flag, desc in flags.items()}
          
          return result
