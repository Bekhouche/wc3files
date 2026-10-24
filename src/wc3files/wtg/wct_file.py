import struct
from ..data import BinaryDataType
from .data import WctFileType
from wc3files.files import BinaryFile


class MapCustomTextTriggersFile(object):
     """
     Reader/writer for war3map.wct (the JASS/Lua text for triggers marked "Custom Text",
     plus one always-present global custom-script block).

     Cross-checked against War3Net.Build's MapCustomTextTriggers.cs/CustomTextTrigger.cs.
     Much simpler than war3map.wtg: no TriggerData.txt dependency, since each script blob is
     just a length-prefixed byte string, not a structured function tree.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in WctFileType]:
                    raise ValueError(f"Invalid custom text trigger file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     # ---- low-level binary helpers ---------------------------------------------------

     @staticmethod
     def _read_i32(reader):
          return struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_i32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', value))

     @staticmethod
     def _read_str(reader):
          return reader.read(BinaryDataType.STRING)

     @staticmethod
     def _write_str(writer, value):
          writer.write(BinaryDataType.STRING, value or '')

     @staticmethod
     def _decode(raw):
          try:
               return raw.decode('utf-8-sig')
          except UnicodeDecodeError:
               return raw.decode('cp1252', errors='replace')

     def _read_code_block(self, reader):
          length = self._read_i32(reader)
          if length == 0:
               return ''
          return self._decode(reader.read(BinaryDataType.BYTES, num=length))

     def _write_code_block(self, writer, text):
          raw = (text or '').encode('utf-8')
          self._write_i32(writer, len(raw))
          writer.write(BinaryDataType.BYTES, raw)

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          version = self._read_i32(reader)

          if version in (0, 1):
               format_version = version
               subversion = None
          elif version in (-2147483647, -2147483644):
               subversion = version
               format_version = self._read_i32(reader)
               if format_version not in (0, 1):
                    raise ValueError(f"Unsupported war3map.wct format version: {format_version}")
          else:
               raise ValueError(f"Unsupported war3map.wct version: {version}")

          data = {'format_version': format_version, 'subversion': subversion}

          if format_version >= 1:
               data['global_comment'] = self._read_str(reader)
               data['global_code'] = self._read_code_block(reader)

          triggers = []
          if subversion is None or subversion == -2147483647:
               for _ in range(self._read_i32(reader)):
                    triggers.append(self._read_code_block(reader))
          else:
               while reader.tell() < reader.size():
                    triggers.append(self._read_code_block(reader))
          data['triggers'] = triggers

          return data

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          format_version = data['format_version']
          subversion = data.get('subversion')

          if subversion is not None:
               self._write_i32(writer, subversion)
          self._write_i32(writer, format_version)

          if format_version >= 1:
               self._write_str(writer, data.get('global_comment', ''))
               self._write_code_block(writer, data.get('global_code', ''))

          triggers = data.get('triggers', [])
          if subversion is None or subversion == -2147483647:
               self._write_i32(writer, len(triggers))
          for code in triggers:
               self._write_code_block(writer, code)
