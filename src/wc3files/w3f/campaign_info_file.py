import struct
from ..data import BinaryDataType
from .data import W3fFileType
from wc3files.files import BinaryFile


class CampaignInfoFile(object):
     """
     Reader/writer for war3campaign.w3f (campaign info: name/author/description, background
     image/sound, fog, race, and the two map lists - the launcher "chapter" buttons and the
     actual map-file play order).

     Cross-checked against the War3Net.Build library (github.com/Drake53/War3Net -
     Serialization/Binary/Info/CampaignInfo.cs and friends) and verified against a real
     campaign file (a Reign of Chaos "Tides of Darkness" retail-style .w3n from War3Net's own
     test data, github.com/Drake53/War3Net/tree/master/tests/.../TestData/Campaigns) - the
     parser consumes it to the exact last byte with zero leftover.
     """

     FLAG_BITS = ['variable_difficulty_levels', 'requires_expansion']

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in W3fFileType]:
                    raise ValueError(f"Invalid campaign info file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     # ---- flag helpers --------------------------------------------------------------

     @classmethod
     def decode_flags(cls, flags):
          return {name: bool(flags & (1 << i)) for i, name in enumerate(cls.FLAG_BITS)}

     @classmethod
     def encode_flags(cls, named_flags, base=0):
          flags = base
          for i, name in enumerate(cls.FLAG_BITS):
               bit = 1 << i
               flags = (flags | bit) if named_flags.get(name) else (flags & ~bit)
          return flags & 0xFFFFFFFF

     # ---- low-level binary helpers ---------------------------------------------------

     @staticmethod
     def _read_i32(reader):
          return struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_i32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', value))

     @staticmethod
     def _read_f32(reader):
          return struct.unpack('<f', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_f32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<f', value))

     @staticmethod
     def _read_str(reader):
          return reader.read(BinaryDataType.STRING)

     @staticmethod
     def _write_str(writer, value):
          writer.write(BinaryDataType.STRING, value or '')

     @staticmethod
     def _read_bgra(reader):
          blue, green, red, alpha = reader.read(BinaryDataType.BYTES, num=4)
          return (red, green, blue, alpha)

     @staticmethod
     def _write_bgra(writer, color):
          red, green, blue, alpha = color or (0, 0, 0, 0)
          writer.write(BinaryDataType.BYTES, bytes([blue & 0xFF, green & 0xFF, red & 0xFF, alpha & 0xFF]))

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          data = {'format_version': self._read_i32(reader)}
          version = data['format_version']

          data['campaign_version'] = self._read_i32(reader)
          data['editor_version'] = self._read_i32(reader)
          data['campaign_name'] = self._read_str(reader)
          data['campaign_difficulty'] = self._read_str(reader)
          data['campaign_author'] = self._read_str(reader)
          data['campaign_description'] = self._read_str(reader)
          data['flags'] = self._read_i32(reader)
          data['background_number'] = self._read_i32(reader)
          data['background_screen_path'] = self._read_str(reader)
          data['minimap_path'] = self._read_str(reader)
          data['ambient_sound_number'] = self._read_i32(reader)
          data['ambient_sound_path'] = self._read_str(reader)
          data['fog_style'] = self._read_i32(reader)
          data['fog_start_z'] = self._read_f32(reader)
          data['fog_end_z'] = self._read_f32(reader)
          data['fog_density'] = self._read_f32(reader)
          data['fog_color'] = self._read_bgra(reader)
          data['race'] = self._read_i32(reader)

          if version >= 2:
               data['background_version'] = self._read_i32(reader)

          data['map_buttons'] = [
               {
                    'is_visible_initially': self._read_i32(reader),
                    'chapter': self._read_str(reader),
                    'title': self._read_str(reader),
                    'map_file_path': self._read_str(reader),
               }
               for _ in range(self._read_i32(reader))
          ]

          data['maps'] = [
               {'unknown': self._read_str(reader), 'map_file_path': self._read_str(reader)}
               for _ in range(self._read_i32(reader))
          ]

          return data

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          version = data.get('format_version', 1)
          self._write_i32(writer, version)

          self._write_i32(writer, data.get('campaign_version', 0))
          self._write_i32(writer, data.get('editor_version', 0))
          self._write_str(writer, data.get('campaign_name', ''))
          self._write_str(writer, data.get('campaign_difficulty', ''))
          self._write_str(writer, data.get('campaign_author', ''))
          self._write_str(writer, data.get('campaign_description', ''))
          self._write_i32(writer, data.get('flags', 0))
          self._write_i32(writer, data.get('background_number', 0))
          self._write_str(writer, data.get('background_screen_path', ''))
          self._write_str(writer, data.get('minimap_path', ''))
          self._write_i32(writer, data.get('ambient_sound_number', 0))
          self._write_str(writer, data.get('ambient_sound_path', ''))
          self._write_i32(writer, data.get('fog_style', -1))
          self._write_f32(writer, data.get('fog_start_z', 0.0))
          self._write_f32(writer, data.get('fog_end_z', 0.0))
          self._write_f32(writer, data.get('fog_density', 0.0))
          self._write_bgra(writer, data.get('fog_color'))
          self._write_i32(writer, data.get('race', 0))

          if version >= 2:
               self._write_i32(writer, data.get('background_version', 0))

          map_buttons = data.get('map_buttons', [])
          self._write_i32(writer, len(map_buttons))
          for button in map_buttons:
               self._write_i32(writer, button.get('is_visible_initially', 0))
               self._write_str(writer, button.get('chapter', ''))
               self._write_str(writer, button.get('title', ''))
               self._write_str(writer, button.get('map_file_path', ''))

          maps = data.get('maps', [])
          self._write_i32(writer, len(maps))
          for map_entry in maps:
               self._write_str(writer, map_entry.get('unknown', ''))
               self._write_str(writer, map_entry.get('map_file_path', ''))
