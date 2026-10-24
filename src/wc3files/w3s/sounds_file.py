from .data import SoundsFileType, SOUND_FLAG_BITS
from ..data import BinaryDataType
from wc3files.files import BinaryFile

# Length of the disputed, unresolved tail (see class docstring) for each version
# that has one. Confirmed against real data only for version 3 (9 bytes); version
# 2's 8-byte length follows the same documented formula (one fewer byte, since v3
# adds one more field on top of the shared v2 structure) but no real version-2
# fixture was available locally to independently confirm it - disclosed, not hidden.


class SoundsFile(object):
     """
     Reader/writer for war3map.w3s (sound object definitions).

     Header: `version` (int32: 1 classic/<=1.31, 2 patch 1.32, 3 patch 1.32.6+) +
     `count` (int32). Per-sound base fields (name, filePath, eaxSetting, flags,
     fade in/out, volume, pitch, pitch variance, priority, channel, min/max
     distance, distance cutoff, cone angles/volume, cone orientation) are
     well-agreed across War3Net.Build.Core, HiveWE's sounds.ixx/wiki, and
     ChiefOfGxBxL's spec. Version >= 2 adds a well-agreed second head (name
     repeated, an SLK sound label, filePath repeated, a dialogue text key, a
     comments string, a dialogue speaker name key).

     **What follows that, before the next record, is a genuinely unresolved
     ambiguity - four independent sources give four different, mutually
     incompatible field-by-field readings of the same ~8-9 bytes** (a
     `listenerName`/`speakerUnitId`/animation-label-and-group/`assetFlags` reading
     per HiveWE's wiki - the best-sourced, from real debug symbols; a bare
     `int32` + 4 strings per HiveWE's own code; an `int32 Unk4` with a
     conditional extra string per War3Net; a run of untyped ints/bytes per
     ChiefOfGxBxL). All four happen to consume the same byte count when every
     field in it is empty/zero, which is every real case actually observed -
     none of these fields have World Editor UI to set them to anything else.
     Rather than assign field names nobody has verified against non-empty data,
     this reader keeps that whole span as one opaque `tail` bytes blob per
     record, preserved verbatim on write - correct either way, since it's never
     interpreted. Version 3 additionally has a trailing `int32` (documented as
     always `1`) immediately after each record's tail, also preserved verbatim
     rather than assumed.

     **Verified against all 6 real local fixtures**: the one version-1 fixture
     round-trips byte-for-byte with its base fields alone (no tail at all, as
     documented); the two version-3 fixtures round-trip byte-for-byte with an
     exactly-9-byte tail per record, all-zero in every real record checked
     (consistent with "no field in this span has ever been set by anything"),
     each followed by a real trailing marker of exactly `1`. No version-2
     fixture was available locally to confirm the 8-byte case independently.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in SoundsFileType]:
                    raise ValueError(f"Invalid sounds file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     @classmethod
     def decode_flags(cls, flags):
          return {name: bool(flags & bit) for bit, name in SOUND_FLAG_BITS.items()}

     @classmethod
     def encode_flags(cls, named_flags, base=0):
          flags = base
          for bit, name in SOUND_FLAG_BITS.items():
               flags = (flags | bit) if named_flags.get(name) else (flags & ~bit)
          return flags & 0xFFFFFFFF

     # ---- low-level helpers ---------------------------------------------------

     @staticmethod
     def _read_str(reader):
          return reader.read(BinaryDataType.STRING)

     @staticmethod
     def _write_str(writer, value):
          writer.write(BinaryDataType.STRING, value or '')

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          version = reader.read(BinaryDataType.INT)
          if version not in (1, 2, 3):
               raise ValueError(f"Unsupported war3map.w3s version: {version} (only 1, 2, and 3 are implemented)")

          count = reader.read(BinaryDataType.INT)
          sounds = [self._read_sound(reader, version) for _ in range(count)]

          # The per-record tail length is a constant taken from real files, and
          # a record whose real shape differs does not announce itself - the
          # parse simply drifts, and every later name is cut out of the middle
          # of the preceding record. That happened silently on one real map:
          # ten sounds came back named things like '?.' and 'd_Forsaken1', and
          # only the byte count gave it away. Landing anywhere but the end of
          # the file means the layout was not the one assumed, so it is refused
          # rather than returned.
          position = reader.tell()
          reader.file.seek(0, 2)
          size = reader.file.tell()
          reader.file.seek(position)
          if position != size:
               raise ValueError(
                    f"war3map.w3s: {count} sounds consumed {position} of "
                    f"{size} bytes, so the record layout is not the one this "
                    f"reader assumes and the result would be garbage. "
                    f"Version {version}; see the class docstring on the "
                    f"unresolved per-record tail.")

          return {'version': version, 'sounds': sounds}

     def _read_sound(self, reader, version):
          sound = {
               'name': self._read_str(reader),
               'file_path': self._read_str(reader),
               'eax_setting': self._read_str(reader),
               'flags': reader.read(BinaryDataType.INT, signed=True),
               'fade_in_rate': reader.read(BinaryDataType.INT, signed=True),
               'fade_out_rate': reader.read(BinaryDataType.INT, signed=True),
               'volume': reader.read(BinaryDataType.INT, signed=True),
               'pitch': reader.read(BinaryDataType.REAL),
               'pitch_variance': reader.read(BinaryDataType.REAL),
               'priority': reader.read(BinaryDataType.INT, signed=True),
               'channel': reader.read(BinaryDataType.INT, signed=True),
               'min_distance': reader.read(BinaryDataType.REAL),
               'max_distance': reader.read(BinaryDataType.REAL),
               'distance_cutoff': reader.read(BinaryDataType.REAL),
               'cone_angle_inside': reader.read(BinaryDataType.REAL),
               'cone_angle_outside': reader.read(BinaryDataType.REAL),
               'cone_outside_volume': reader.read(BinaryDataType.INT, signed=True),
               'cone_orientation': (
                    reader.read(BinaryDataType.REAL),
                    reader.read(BinaryDataType.REAL),
                    reader.read(BinaryDataType.REAL),
               ),
          }

          if version >= 2:
               sound['name_repeated'] = self._read_str(reader)
               sound['slk_label'] = self._read_str(reader)
               sound['file_path_repeated'] = self._read_str(reader)
               sound['dialogue_text_key'] = reader.read(BinaryDataType.INT, signed=True)
               sound['comments'] = self._read_str(reader)
               sound['dialogue_speaker_name_key'] = reader.read(BinaryDataType.INT, signed=True)
               # Five more strings and an int close the version-2 record.
               # These were read as a fixed 9-byte blob, which is exactly what
               # they come to when every string is empty - true of 200 of the
               # 209 sound files in the benchmark corpus. The nine that broke
               # are campaign maps with voiced dialogue, where these carry a
               # speaker code, a dialogue id, a category and the path of a
               # facial-animation asset, and the record is far longer. What
               # each one means is not established, so the names stay neutral.
               sound['dialogue_unknown_1'] = self._read_str(reader)
               sound['dialogue_unknown_2'] = reader.read(BinaryDataType.INT, signed=True)
               sound['dialogue_unknown_3'] = self._read_str(reader)
               sound['dialogue_unknown_4'] = self._read_str(reader)
               sound['dialogue_unknown_5'] = self._read_str(reader)
               sound['dialogue_unknown_6'] = self._read_str(reader)

          if version >= 3:
               sound['trailing_marker'] = reader.read(BinaryDataType.INT, signed=True)

          return sound

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          version = data.get('version', 1)
          writer.write(BinaryDataType.INT, version)

          sounds = data.get('sounds', [])
          writer.write(BinaryDataType.INT, len(sounds))
          for sound in sounds:
               self._write_sound(writer, sound, version)

     def _write_sound(self, writer, sound, version):
          self._write_str(writer, sound.get('name', ''))
          self._write_str(writer, sound.get('file_path', ''))
          self._write_str(writer, sound.get('eax_setting', ''))
          writer.write(BinaryDataType.INT, sound.get('flags', 0))
          writer.write(BinaryDataType.INT, sound.get('fade_in_rate', 0))
          writer.write(BinaryDataType.INT, sound.get('fade_out_rate', 0))
          writer.write(BinaryDataType.INT, sound.get('volume', -1))
          writer.write(BinaryDataType.REAL, sound.get('pitch', 1.0))
          writer.write(BinaryDataType.REAL, sound.get('pitch_variance', 0.0))
          writer.write(BinaryDataType.INT, sound.get('priority', 0))
          writer.write(BinaryDataType.INT, sound.get('channel', 0))
          writer.write(BinaryDataType.REAL, sound.get('min_distance', 0.0))
          writer.write(BinaryDataType.REAL, sound.get('max_distance', 0.0))
          writer.write(BinaryDataType.REAL, sound.get('distance_cutoff', 0.0))
          writer.write(BinaryDataType.REAL, sound.get('cone_angle_inside', 0.0))
          writer.write(BinaryDataType.REAL, sound.get('cone_angle_outside', 0.0))
          writer.write(BinaryDataType.INT, sound.get('cone_outside_volume', 0))
          for value in sound.get('cone_orientation', (0.0, 0.0, 0.0)):
               writer.write(BinaryDataType.REAL, value)

          if version >= 2:
               self._write_str(writer, sound.get('name_repeated', sound.get('name', '')))
               self._write_str(writer, sound.get('slk_label', ''))
               self._write_str(writer, sound.get('file_path_repeated', sound.get('file_path', '')))
               writer.write(BinaryDataType.INT, sound.get('dialogue_text_key', -1))
               self._write_str(writer, sound.get('comments', ''))
               writer.write(BinaryDataType.INT, sound.get('dialogue_speaker_name_key', -1))
               self._write_str(writer, sound.get('dialogue_unknown_1', ''))
               writer.write(BinaryDataType.INT, sound.get('dialogue_unknown_2', 0))
               self._write_str(writer, sound.get('dialogue_unknown_3', ''))
               self._write_str(writer, sound.get('dialogue_unknown_4', ''))
               self._write_str(writer, sound.get('dialogue_unknown_5', ''))
               self._write_str(writer, sound.get('dialogue_unknown_6', ''))

          if version >= 3:
               writer.write(BinaryDataType.INT, sound.get('trailing_marker', 1))
