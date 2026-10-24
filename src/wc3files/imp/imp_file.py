import struct
from ..data import BinaryDataType
from .data import ImpFileType
from wc3files.files import BinaryFile


class ImpFile(object):
     """
     Reader/writer for war3map.imp / war3campaign.imp (the imported-file list: every file
     path imported into the map, plus a per-file byte of flags whose individual bits aren't
     documented - see ImportedFileFlags in War3Net.Build.Import, which this was cross-checked
     against).

     Format is version(int32, always 1) + count(int32) + that many (flags:byte, path:z-string)
     entries. Verified byte-for-byte round-trip against every real war3map.imp fixture that
     wasn't already known (from the war3mapUnits.doo/war3map.wtg work) to be a stale,
     sector-padded extraction.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {'version': 1, 'files': []}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in ImpFileType]:
                    raise ValueError(f"Invalid imported-files file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     @staticmethod
     def _read_i32(reader):
          return struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_i32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', value))

     def _read(self, reader):
          version = self._read_i32(reader)
          count = self._read_i32(reader)

          files = []
          for _ in range(count):
               flags = reader.read(BinaryDataType.BYTES, num=1)[0]
               path = reader.read(BinaryDataType.STRING)
               files.append({'flags': flags, 'path': path})

          return {'version': version, 'files': files}

     def _write(self, writer, data):
          self._write_i32(writer, data.get('version', 1))

          files = data.get('files', [])
          self._write_i32(writer, len(files))
          for entry in files:
               writer.write(BinaryDataType.BYTES, bytes([entry.get('flags', 0) & 0xFF]))
               writer.write(BinaryDataType.STRING, entry.get('path', ''))
