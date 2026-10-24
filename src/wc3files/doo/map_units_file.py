import struct
from ..data import BinaryDataType
from .data import DooFileType, RandomUnitDataMode
from wc3files.files import BinaryFile


class MapUnitsFile(object):
     """
     Reader/writer for war3mapUnits.doo (unit/item placement).

     Field layout verified against real maps spanning format/subversion 7/9 and 8/11, and
     cross-checked against the War3Net.Build library (github.com/Drake53/War3Net -
     src/War3Net.Build.Core/Serialization/Binary/Widget/UnitData.cs), a mature, widely-used
     reference implementation - not against w3x2lni, which never actually parses this format
     (its own writer only ever emits an empty stub).

     Notable format quirks this implementation follows (see UnitData.cs for the original):
       - The Reforged "Skin" rawcode is NOT gated by the file's own version/subversion (that
         changed in patch 1.32 without a version bump - see the Blizzard bug report at
         https://us.forums.blizzard.com/en/warcraft3/t/war3mapunitsdoo-changed-format-version-not-changed/29081).
         Presence is instead detected per-unit by peeking whether the byte right after Scale
         looks printable (>= 0x20) - a real rawcode's first character always is, while the
         Flags byte that would otherwise be there never is.
       - MapItemTableId only exists when format==8 and subversion==11.
       - Hero stat overrides (Strength/Agility/Intelligence) only exist when
         (format==8 and subversion==11) or subversion==10.
       - The "random unit/item" data block is governed purely by its own mode value
         (-1=none, 0=any, 1=global table, 2=custom table) - not by whether the placed type id
         is 'uDNR'/'iDNR'; a ready-made table reference can accompany an ordinary unit too.
     """

     PEEK_PRINTABLE_THRESHOLD = 0x20

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {'version': 8, 'subversion': 11, 'units': []}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in DooFileType]:
                    raise ValueError(f"Invalid unit placement file type: {file_type}")

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
     def _read_f32(reader):
          return struct.unpack('<f', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_f32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<f', value))

     @staticmethod
     def _read_byte(reader):
          return reader.read(BinaryDataType.BYTES, num=1)[0]

     @staticmethod
     def _write_byte(writer, value):
          writer.write(BinaryDataType.BYTES, bytes([value & 0xFF]))

     @staticmethod
     def _read_int24(reader):
          data = reader.read(BinaryDataType.BYTES, num=3)
          value = data[0] | (data[1] << 8) | (data[2] << 16)
          return value - (1 << 24) if value >= (1 << 23) else value

     @staticmethod
     def _write_int24(writer, value):
          value &= 0xFFFFFF
          writer.write(BinaryDataType.BYTES, bytes([value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF]))

     @staticmethod
     def _read_code(reader):
          return reader.read(BinaryDataType.BYTES, num=4).decode('latin1')

     @staticmethod
     def _write_code(writer, value):
          raw = (value or '').encode('latin1')[:4]
          writer.write(BinaryDataType.BYTES, raw.ljust(4, b'\x00'))

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          magic = reader.read(BinaryDataType.BYTES, num=4)
          if magic != b'W3do':
               raise ValueError(f"Invalid unit placement file header. Got: {magic!r}")

          version = self._read_i32(reader)
          subversion = self._read_i32(reader)
          count = self._read_i32(reader)

          units = [self._read_unit(reader, version, subversion) for _ in range(count)]

          return {'version': version, 'subversion': subversion, 'units': units}

     def _read_unit(self, reader, version, subversion):
          unit = {
               'id': self._read_code(reader),
               'variation': self._read_i32(reader),
               'position': (self._read_f32(reader), self._read_f32(reader), self._read_f32(reader)),
               'rotation': self._read_f32(reader),
               'scale': (self._read_f32(reader), self._read_f32(reader), self._read_f32(reader)),
          }

          pos = reader.tell()
          next_byte = reader.read(BinaryDataType.BYTES, num=1)[0]
          reader.seek(pos)
          use_new_format = next_byte >= self.PEEK_PRINTABLE_THRESHOLD

          unit['skin'] = self._read_code(reader) if use_new_format else unit['id']
          # Persisted separately from comparing skin to id: real Reforged-saved maps write
          # this field unconditionally (see the Blizzard bug report cited above), including
          # for units with no actual override (skin == id) - so "was this field present in
          # the source file" cannot be recovered later just by checking skin != id on write.
          unit['has_skin'] = use_new_format

          if version >= 13:
               # Reforged 2.x/3.x writes format 13. One extra int sits between
               # the skin rawcode and the flags byte; every unit in the 14
               # version-13 files measured carries it, and without it the
               # record misaligns and the parse runs off the end of the file.
               unit['unknown_v13'] = self._read_i32(reader)

          unit['flags'] = self._read_byte(reader)
          unit['owner'] = self._read_i32(reader)
          unit['unknown_1'] = self._read_byte(reader)
          unit['unknown_2'] = self._read_byte(reader)
          unit['hp'] = self._read_i32(reader)
          unit['mp'] = self._read_i32(reader)

          if self._has_item_table_id(version, subversion):
               unit['item_table_id'] = self._read_i32(reader)

          item_sets = []
          for _ in range(self._read_i32(reader)):
               item_sets.append([
                    {'item_id': self._read_code(reader), 'chance': self._read_i32(reader)}
                    for _ in range(self._read_i32(reader))
               ])
          unit['item_sets'] = item_sets

          unit['gold'] = self._read_i32(reader)
          unit['target_acquisition'] = self._read_f32(reader)
          unit['hero_level'] = self._read_i32(reader)

          if self._has_hero_stats(version, subversion):
               unit['hero_stats'] = (self._read_i32(reader), self._read_i32(reader), self._read_i32(reader))

          unit['inventory'] = [
               {'slot': self._read_i32(reader), 'item_id': self._read_code(reader)}
               for _ in range(self._read_i32(reader))
          ]

          unit['abilities'] = [
               {'ability_id': self._read_code(reader), 'autocast': bool(self._read_i32(reader)), 'level': self._read_i32(reader)}
               for _ in range(self._read_i32(reader))
          ]

          mode = self._read_i32(reader)
          unit['random_mode'] = mode
          if mode == RandomUnitDataMode.ANY.value:
               unit['random_data'] = {'level': self._read_int24(reader), 'item_class': self._read_byte(reader)}
          elif mode == RandomUnitDataMode.GLOBAL_TABLE.value:
               unit['random_data'] = {'table_id': self._read_i32(reader), 'column': self._read_i32(reader)}
          elif mode == RandomUnitDataMode.CUSTOM_TABLE.value:
               unit['random_data'] = [
                    {'id': self._read_code(reader), 'chance': self._read_i32(reader)}
                    for _ in range(self._read_i32(reader))
               ]
          elif mode != RandomUnitDataMode.NONE.value:
               raise ValueError(f"Unexpected random unit/item data mode: {mode}")

          if version > 6 and subversion > 7:
               unit['color'] = self._read_i32(reader)
               unit['waygate'] = self._read_i32(reader)
               unit['creation_number'] = self._read_i32(reader)

          if version >= 13:
               # Three further ints close a format-13 record. What they hold is
               # not known - they are zero in every unit measured - so they are
               # carried through rather than given invented names.
               unit['unknown_v13_tail'] = tuple(self._read_i32(reader)
                                                for _ in range(3))

          return unit

     @staticmethod
     def _has_item_table_id(version, subversion):
          return (version == 8 and subversion == 11) or version >= 13

     @staticmethod
     def _has_hero_stats(version, subversion):
          return ((version == 8 and subversion == 11) or subversion == 10
                  or version >= 13)

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          writer.write(BinaryDataType.BYTES, b'W3do')
          version = data.get('version', 8)
          subversion = data.get('subversion', 11)
          units = data.get('units', [])
          self._write_i32(writer, version)
          self._write_i32(writer, subversion)
          self._write_i32(writer, len(units))
          for unit in units:
               self._write_unit(writer, unit, version, subversion)

     def _write_unit(self, writer, unit, version, subversion):
          self._write_code(writer, unit['id'])
          self._write_i32(writer, unit.get('variation', 0))
          for value in unit.get('position', (0.0, 0.0, 0.0)):
               self._write_f32(writer, value)
          self._write_f32(writer, unit.get('rotation', 0.0))
          for value in unit.get('scale', (1.0, 1.0, 1.0)):
               self._write_f32(writer, value)

          skin = unit.get('skin', unit['id'])
          flags = unit.get('flags', 2)
          # Skin presence is detected on read by peeking the byte right after Scale: if we
          # skip writing it, that peeked byte becomes Flags, so Flags itself must not look
          # printable or a reader would wrongly assume a skin field is present.
          # Prefer the presence flag captured on read (has_skin) over comparing skin to id:
          # a real Reforged-saved unit can have skin == id and still have this field present
          # in the source file - falls back to the old value-comparison heuristic only for
          # data that was never read from a real file (e.g. built from scratch).
          if 'has_skin' in unit:
               write_skin = unit['has_skin']
          else:
               write_skin = skin != unit['id'] or flags >= self.PEEK_PRINTABLE_THRESHOLD
          if write_skin:
               self._write_code(writer, skin)

          if version >= 13:
               self._write_i32(writer, unit.get('unknown_v13', -1))

          self._write_byte(writer, flags)
          self._write_i32(writer, unit.get('owner', 0))
          self._write_byte(writer, unit.get('unknown_1', 0))
          self._write_byte(writer, unit.get('unknown_2', 0))
          self._write_i32(writer, unit.get('hp', -1))
          self._write_i32(writer, unit.get('mp', -1))

          if self._has_item_table_id(version, subversion):
               self._write_i32(writer, unit.get('item_table_id', -1))

          item_sets = unit.get('item_sets', [])
          self._write_i32(writer, len(item_sets))
          for item_set in item_sets:
               self._write_i32(writer, len(item_set))
               for item in item_set:
                    self._write_code(writer, item['item_id'])
                    self._write_i32(writer, item['chance'])

          self._write_i32(writer, unit.get('gold', 0))
          self._write_f32(writer, unit.get('target_acquisition', -1.0))
          self._write_i32(writer, unit.get('hero_level', 1))

          if self._has_hero_stats(version, subversion):
               hero_stats = unit.get('hero_stats', (0, 0, 0))
               for value in hero_stats:
                    self._write_i32(writer, value)

          inventory = unit.get('inventory', [])
          self._write_i32(writer, len(inventory))
          for entry in inventory:
               self._write_i32(writer, entry['slot'])
               self._write_code(writer, entry['item_id'])

          abilities = unit.get('abilities', [])
          self._write_i32(writer, len(abilities))
          for ability in abilities:
               self._write_code(writer, ability['ability_id'])
               self._write_i32(writer, 1 if ability.get('autocast') else 0)
               self._write_i32(writer, ability.get('level', 1))

          mode = unit.get('random_mode', RandomUnitDataMode.NONE.value)
          self._write_i32(writer, mode)
          random_data = unit.get('random_data')
          if mode == RandomUnitDataMode.ANY.value:
               self._write_int24(writer, random_data.get('level', -1))
               self._write_byte(writer, random_data.get('item_class', 0))
          elif mode == RandomUnitDataMode.GLOBAL_TABLE.value:
               self._write_i32(writer, random_data.get('table_id', 0))
               self._write_i32(writer, random_data.get('column', 0))
          elif mode == RandomUnitDataMode.CUSTOM_TABLE.value:
               entries = random_data or []
               self._write_i32(writer, len(entries))
               for entry in entries:
                    self._write_code(writer, entry['id'])
                    self._write_i32(writer, entry['chance'])

          if version > 6 and subversion > 7:
               self._write_i32(writer, unit.get('color', -1))
               self._write_i32(writer, unit.get('waygate', -1))
               self._write_i32(writer, unit.get('creation_number', 0))

          if version >= 13:
               for value in unit.get('unknown_v13_tail', (0, 0, 0)):
                    self._write_i32(writer, value)
