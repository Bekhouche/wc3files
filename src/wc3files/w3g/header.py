import struct
from ..data import BinaryDataType

class ReplayHeader:
     """
     Class for Replay Header
     """

     def __init__(self):
          """Initialize by clearing any stored Replay data."""
          self.clear()

     def clear(self):
          """Clear any previously stored Replay data."""
          self._data = {}

     def read(self, reader):
          """Read and parse ."""
          self._data['name'] = reader.read(BinaryDataType.BYTES, num=28)
          self._data['fileoffset'] = reader.read(BinaryDataType.INT)
          self._data['size_compressed'] = reader.read(BinaryDataType.INT)
          self._data['header_version'] = reader.read(BinaryDataType.INT)
          self._data['size_decompressed'] = reader.read(BinaryDataType.INT)
          self._data['number_compressed'] = reader.read(BinaryDataType.INT)
          self._data['sub'] = {}

          if self._data['header_version'] == 0:
               # Pre-Reforged header layout (ROC/original TFT) - no build number field at
               # all, so it can't be Reforged.
               self._data['sub']['unknown'] = reader.read(BinaryDataType.INT, num=2)
               self._data['sub']['version'] = reader.read(BinaryDataType.INT, num=2)
               self._data['sub']['build'] = reader.read(BinaryDataType.INT, num=2)
               self._data['sub']['flags'] = reader.read(BinaryDataType.INT, num=2)
               self._data['sub']['replay_length'] = reader.read(BinaryDataType.INT)
               self._data['sub']['checksum'] = reader.read(BinaryDataType.INT)
               self._data['reforged'] = False
          else:
               self._data['sub']['version_identifier'] = reader.convert(reader.read(BinaryDataType.BYTES), BinaryDataType.BYTES, BinaryDataType.STRING)
               self._data['sub']['version_number'] = reader.read(BinaryDataType.INT)
               self._data['sub']['build_number'] = reader.read(BinaryDataType.INT, 2)
               self._data['sub']['flags'] = reader.read(BinaryDataType.INT, 2)
               self._data['sub']['replay_length'] = reader.read(BinaryDataType.INT)
               self._data['sub']['checksum'] = reader.read(BinaryDataType.INT)
               self._data['reforged'] = self._data['sub']['build_number'] >= 6089
          
     def get_data(self):
        """Return the parsed MPQ data."""
        return self._data

     def write(self, writer, data=None):
          """
          Write the header back out. BinaryFile.write() for BinaryDataType.INT always packs
          a full 4 bytes, but several fields here (unknown/version/build/flags/build_number)
          are 2 bytes on read - those are packed manually to match.
          """
          data = self._data if data is None else data

          # BinaryFile.read() defaults to signed=False, so every field here was read as
          # unsigned - pack with '<I'/'<H' (not '<i'/'<h') or a large checksum/size value
          # overflows struct.pack.
          writer.write(BinaryDataType.BYTES, data['name'])
          writer.write(BinaryDataType.BYTES, struct.pack('<I', data['fileoffset']))
          writer.write(BinaryDataType.BYTES, struct.pack('<I', data['size_compressed']))
          writer.write(BinaryDataType.BYTES, struct.pack('<I', data['header_version']))
          writer.write(BinaryDataType.BYTES, struct.pack('<I', data['size_decompressed']))
          writer.write(BinaryDataType.BYTES, struct.pack('<I', data['number_compressed']))

          sub = data['sub']
          if data['header_version'] == 0:
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['unknown']))
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['version']))
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['build']))
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['flags']))
               writer.write(BinaryDataType.BYTES, struct.pack('<I', sub['replay_length']))
               writer.write(BinaryDataType.BYTES, struct.pack('<I', sub['checksum']))
          else:
               version_identifier = sub['version_identifier']
               if isinstance(version_identifier, str):
                    version_identifier = version_identifier.encode('utf-8')
               writer.write(BinaryDataType.BYTES, version_identifier[:4].ljust(4, b'\x00'))
               writer.write(BinaryDataType.BYTES, struct.pack('<I', sub['version_number']))
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['build_number']))
               writer.write(BinaryDataType.BYTES, struct.pack('<H', sub['flags']))
               writer.write(BinaryDataType.BYTES, struct.pack('<I', sub['replay_length']))
               writer.write(BinaryDataType.BYTES, struct.pack('<I', sub['checksum']))


          