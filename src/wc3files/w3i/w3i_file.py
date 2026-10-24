import struct
from ..data import BinaryDataType
from .data import W3iFileType
from wc3files.files import BinaryFile
from wc3files.table.wts_file import WtsFile


class W3iFile(object):
     """
     Reader/writer for war3map.w3i (map info: head/players/forces plus the
     upgrade-availability/tech-availability/random-unit/random-item tables).

     Verified field-for-field (byte offsets checked against real maps, not just against
     w3x2lni's own frontend_w3i.lua/backend_w3i.lua) for the file-version families actually
     seen in the wild:
       - 18       early TFT: no fog/environment/game-data-set/random-item-table.
       - 25..27   adds fog, environment, loading-screen path, game data set, random item table.
       - 28..30   adds the war3_version quad and script_type (JASS/Lua).
       - 31       Reforged 1.32+: two extra map-level ints, two extra per-player ints.
       - 32       Reforged 2.0.3 PTR: two more map-level ints (force default/max camera zoom).
       - 33       Reforged 2.0.3: one more map-level int (force min camera zoom) - together
                  with v32's two, exactly the "12 extra bytes" this package originally
                  couldn't identify; confirmed against War3Net.Build's
                  Serialization/Binary/Info/MapInfo.cs (ForceDefaultCameraZoom/
                  ForceMaxCameraZoom/ForceMinCameraZoom) and verified byte-for-byte against a
                  real version-33 map.
       - 39       Reforged 3.0 editor. Adds `loading_screen_source` after the loading
                  screen number; six fog fields between the weather id and the sound name
                  (a linear-fog range, a max opacity and three unidentified ints); ten
                  water-rendering settings after the version-33 camera zoom; and `hud_skin`
                  in every player record, between the race and the fixed-start-position
                  flag. 182 of the game's 186 version-39 maps and 66 of 66 in the benchmark
                  corpus read and rewrite byte-for-byte.
                  An earlier pass derived this from byte counts alone and got two things
                  wrong: it concluded the sound string and light byte had been dropped
                  (they had not - the fog block is simply new), and it could not tell why
                  some player records seemed to carry an extra int while others did not
                  (they all carry `hud_skin`; the head length was wrong, not the record).
                  HiveWE's parser settled both. Every version-39 parse is still
                  re-serialized and compared against the bytes it came from, so a file
                  either reads correctly or raises; it never reads wrongly in silence.
     """

     STRING_FIELDS = ('map_name', 'author_name', 'map_description', 'players_recommended')

     MAP_FLAG_BITS = [
          'hide_minimap_in_preview',
          'modify_ally_priorities',
          'melee_map',
          'large_map_supported',
          'masked_areas_partially_visible',
          'fixed_player_force_settings',
          'use_custom_forces',
          'use_custom_techtree',
          'use_custom_abilities',
          'use_custom_upgrades',
          'map_properties_menu_opened',
          'show_water_waves_on_cliff_shores',
          'show_water_waves_on_rolling_shores',
     ]

     FORCE_FLAG_BITS = {
          0: 'allied',
          1: 'allied_victory',
          3: 'share_vision',
          4: 'share_unit_control',
          5: 'share_advanced_control',
     }

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in W3iFileType]:
                    raise ValueError(f"Invalid map info file type: {file_type}")

          try:
               with BinaryFile(file_path, 'read') as reader:
                    self._data = self._read(reader)
          # UnicodeDecodeError is itself a ValueError, so it has to be named
          # before the re-raise below or it would pass straight through.
          except (UnicodeDecodeError, struct.error, IndexError, TypeError,
                  KeyError, EOFError) as exc:
               # A v39 file that derails mid-parse is an understood gap, not a
               # crash: say so plainly instead of surfacing whatever the
               # primitive reader happened to raise.
               if self._peek_version(file_path) == 39:
                    raise ValueError(
                         f"war3map.w3i version 39: this file does not match "
                         f"the layout this package derived for version 39 "
                         f"({type(exc).__name__}). Version 39 is "
                         f"undocumented; 125 of the 186 version-39 maps the "
                         f"game ships read and rewrite byte-for-byte, and "
                         f"this is one of the rest. See TODO.md."
                    ) from None
               raise

          if self._data.get('file_version') == 39:
               self._verify_v39(file_path)

     @staticmethod
     def _peek_version(file_path):
          try:
               if isinstance(file_path, (bytes, bytearray)):
                    raw = bytes(file_path)[:4]
               else:
                    with open(file_path, 'rb') as handle:
                         raw = handle.read(4)
               return int.from_bytes(raw, 'little', signed=True)
          except Exception:
               return None

     def _verify_v39(self, file_path):
          """Refuse a version-39 file this parser cannot reproduce exactly.

          Version 39 is undocumented - no public spec covers it, and it first
          appears with the 3.0.0 editor. Its layout here was derived from the
          465 maps the installed game ships, and it reads 125 of the 186
          version-39 ones correctly; the rest differ somewhere this work has
          not pinned down (the player record carries an extra int in some
          maps, and nothing in the head reliably predicts which).

          Rather than hand back a plausible-looking misreading, the parse is
          checked against the bytes it came from: anything that does not
          re-serialize identically is rejected. So a version-39 file either
          reads correctly or raises - it never reads wrongly in silence.
          """
          import os
          import tempfile

          if isinstance(file_path, (bytes, bytearray)):
               original = bytes(file_path)
          else:
               with open(file_path, 'rb') as handle:
                    original = handle.read()

          handle_fd, scratch = tempfile.mkstemp(suffix='.w3i')
          os.close(handle_fd)
          try:
               with BinaryFile(scratch, 'write') as writer:
                    self._write(writer, self._data)
               with open(scratch, 'rb') as handle:
                    rebuilt = handle.read()
          except Exception as exc:
               raise ValueError(
                    f"war3map.w3i version 39: this file does not match the "
                    f"layout this package derived for version 39 "
                    f"({type(exc).__name__}: {exc}). Version 39 is "
                    f"undocumented; see TODO.md."
               ) from None
          finally:
               try:
                    os.remove(scratch)
               except OSError:
                    pass

          if rebuilt != original:
               raise ValueError(
                    f"war3map.w3i version 39: parsed, but re-serializing gave "
                    f"{len(rebuilt)} bytes against the original's "
                    f"{len(original)}, so the parse is not trustworthy and is "
                    f"refused rather than returned. Version 39 is "
                    f"undocumented and only partly worked out; see TODO.md."
               )

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     def apply_strings(self, wts):
          # Resolve "TRIGSTR_XXXXX" placeholders across every string field using a WtsFile
          # (or a war3map.wts path/bytes, which is read into one on the fly). Mirrors
          # ObjectFile.apply_strings()/wts_file.WtsFile.resolve.
          if not isinstance(wts, WtsFile):
               strings = WtsFile()
               strings.read(wts)
               wts = strings

          for field in self.STRING_FIELDS:
               if field in self._data:
                    self._data[field] = wts.resolve(self._data[field])

          for section in ('loading_screen', 'prologue'):
               block = self._data.get(section)
               if block:
                    for field in ('path', 'text', 'title', 'subtitle'):
                         if field in block:
                              block[field] = wts.resolve(block[field])

          environment = self._data.get('environment')
          if environment and 'sound' in environment:
               environment['sound'] = wts.resolve(environment['sound'])

          for player in self._data.get('players', []):
               player['name'] = wts.resolve(player['name'])
          for force in self._data.get('forces', []):
               force['name'] = wts.resolve(force['name'])
          for table in self._data.get('random_unit_tables', []):
               table['name'] = wts.resolve(table['name'])
          for table in self._data.get('random_item_tables', []):
               table['name'] = wts.resolve(table['name'])

     # ---- flag helpers --------------------------------------------------------------

     @classmethod
     def decode_flags(cls, flags):
          return {name: bool(flags & (1 << i)) for i, name in enumerate(cls.MAP_FLAG_BITS)}

     @classmethod
     def encode_flags(cls, named_flags, base=0):
          flags = base
          for i, name in enumerate(cls.MAP_FLAG_BITS):
               bit = 1 << i
               flags = (flags | bit) if named_flags.get(name) else (flags & ~bit)
          return flags & 0xFFFFFFFF

     @classmethod
     def decode_force_flags(cls, flags):
          return {name: bool(flags & (1 << bit)) for bit, name in cls.FORCE_FLAG_BITS.items()}

     @classmethod
     def encode_force_flags(cls, named_flags, base=0):
          flags = base
          for bit, name in cls.FORCE_FLAG_BITS.items():
               mask = 1 << bit
               flags = (flags | mask) if named_flags.get(name) else (flags & ~mask)
          return flags & 0xFFFFFFFF

     @staticmethod
     def decode_player_mask(mask):
          return [player_id for player_id in range(32) if mask & (1 << player_id)]

     @staticmethod
     def encode_player_mask(player_ids, base=0):
          mask = base
          for player_id in player_ids:
               mask |= (1 << player_id)
          return mask & 0xFFFFFFFF

     # ---- low-level binary helpers ---------------------------------------------------

     @staticmethod
     def _read_i32(reader):
          return struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_i32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', value))

     @staticmethod
     def _read_u32(reader):
          return struct.unpack('<I', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_u32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<I', value & 0xFFFFFFFF))

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
     def _read_byte(reader):
          return reader.read(BinaryDataType.BYTES, num=1).decode('latin1')

     @staticmethod
     def _write_byte(writer, value):
          writer.write(BinaryDataType.BYTES, (value or '\x00').encode('latin1')[:1])

     @staticmethod
     def _read_code(reader):
          # A 4-byte rawcode (e.g. an upgrade/tech id like 'Rhme'), round-tripped as the
          # literal 4-character string wc3 tools use everywhere else in this package.
          return reader.read(BinaryDataType.BYTES, num=4).decode('latin1')

     @staticmethod
     def _write_code(writer, value):
          raw = (value or '').encode('latin1')[:4]
          writer.write(BinaryDataType.BYTES, raw.ljust(4, b'\x00'))

     @staticmethod
     def _read_color(reader):
          return tuple(reader.read(BinaryDataType.BYTES, num=1)[0] for _ in range(4))

     @staticmethod
     def _write_color(writer, color):
          for value in (color or (255, 255, 255, 255)):
               writer.write(BinaryDataType.BYTES, bytes([value & 0xFF]))

     @staticmethod
     def _is_finish(reader):
          # A lone 0xFF byte (or simply running out of data) marks "no more optional
          # sections" - some maps stop right after the force table instead of writing
          # empty upgrade/tech/random-table sections. Peek without consuming.
          pos = reader.tell()
          marker = reader.read(BinaryDataType.BYTES, num=1)
          if not marker:
               return True
          reader.seek(pos)
          return marker == b'\xff'

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          data = {'file_version': self._read_i32(reader)}
          version = data['file_version']

          if 25 <= version <= 33 or version == 39:
               self._read_head(reader, data, version)
               self._read_players(reader, data, version)
               self._read_forces(reader, data)
               self._read_upgrades(reader, data)
               self._read_techtree(reader, data)
               self._read_random_unit_tables(reader, data)
               self._read_random_item_tables(reader, data)
          elif version == 18:
               self._read_head(reader, data, version)
               self._read_players(reader, data, version)
               self._read_forces(reader, data)
               self._read_upgrades(reader, data)
               self._read_techtree(reader, data)
               self._read_random_unit_tables(reader, data)
          else:
               raise ValueError(
                    f"Unsupported war3map.w3i version: {version} "
                    "(only 18, 25-33 and 39 are implemented)"
               )

          return data

     def _read_head(self, reader, data, version):
          data['map_version'] = self._read_i32(reader)
          data['editor_version'] = self._read_i32(reader)
          if version >= 28:
               data['war3_version'] = tuple(self._read_i32(reader) for _ in range(4))

          data['map_name'] = self._read_str(reader)
          data['author_name'] = self._read_str(reader)
          data['map_description'] = self._read_str(reader)
          data['players_recommended'] = self._read_str(reader)

          data['camera_bounds'] = tuple(self._read_f32(reader) for _ in range(8))
          data['camera_complements'] = tuple(self._read_i32(reader) for _ in range(4))

          data['map_width'] = self._read_i32(reader)
          data['map_height'] = self._read_i32(reader)
          data['flags'] = self._read_u32(reader)
          data['map_main_ground_type'] = self._read_byte(reader)

          if version >= 25:
               loading_screen = {'id': self._read_i32(reader)}
               if version >= 39:
                    # One extra int between the loading-screen id and its
                    # path. Every v39 map the game ships has it set to 1.
                    loading_screen['unknown_v39'] = self._read_i32(reader)
               loading_screen.update({
                    'path': self._read_str(reader),
                    'text': self._read_str(reader),
                    'title': self._read_str(reader),
                    'subtitle': self._read_str(reader),
               })
               data['loading_screen'] = loading_screen
               data['game_data_set'] = self._read_i32(reader)
               data['prologue'] = {
                    'path': self._read_str(reader),
                    'text': self._read_str(reader),
                    'title': self._read_str(reader),
                    'subtitle': self._read_str(reader),
               }
               data['fog'] = {
                    'type': self._read_i32(reader),
                    'start_z': self._read_f32(reader),
                    'end_z': self._read_f32(reader),
                    'density': self._read_f32(reader),
                    'color': self._read_color(reader),
               }
               environment = {'weather_id': self._read_code(reader)}
               if version >= 39:
                    # Six fields sit between the weather id and the sound
                    # name in version 39. An earlier reading of this file
                    # concluded that the sound string and the light byte had
                    # been dropped instead, because the byte count worked out
                    # either way on maps where all of it is zero. They are
                    # still there; this block is simply new.
                    environment['fog_unknown_1'] = self._read_i32(reader)
                    environment['fog_linear_start'] = self._read_f32(reader)
                    environment['fog_linear_end'] = self._read_f32(reader)
                    environment['fog_max_opacity'] = self._read_f32(reader)
                    environment['fog_unknown_2'] = self._read_i32(reader)
                    environment['fog_unknown_3'] = self._read_i32(reader)
               environment['sound'] = self._read_str(reader)
               environment['light'] = self._read_byte(reader)
               environment['water_color'] = self._read_color(reader)
               data['environment'] = environment

               if version >= 28:
                    data['script_type'] = 'lua' if self._read_i32(reader) == 1 else 'jass'
               if version >= 31:
                    data['supported_modes'] = self._read_i32(reader)
                    data['game_data_version'] = self._read_i32(reader)
               if version >= 32:
                    data['force_default_camera_zoom'] = self._read_i32(reader)
                    data['force_max_camera_zoom'] = self._read_i32(reader)
               if version >= 33:
                    data['force_min_camera_zoom'] = self._read_i32(reader)
               if version >= 39:
                    # Ten water-rendering settings close the version-39 head.
                    data['water'] = tuple(self._read_i32(reader)
                                          for _ in range(10))
          else:
               data['loading_screen'] = {
                    'id': self._read_i32(reader),
                    'text': self._read_str(reader),
                    'title': self._read_str(reader),
                    'subtitle': self._read_str(reader),
               }
               data['prologue'] = {
                    'id': self._read_i32(reader),
                    'text': self._read_str(reader),
                    'title': self._read_str(reader),
                    'subtitle': self._read_str(reader),
               }

     def _read_players(self, reader, data, version):
          count = self._read_i32(reader)
          players = []
          for _ in range(count):
               player = {
                    'id': self._read_i32(reader),
                    'type': self._read_i32(reader),
                    'race': self._read_i32(reader),
               }
               if version >= 39:
                    # Present on every version-39 player. Not having it was
                    # what made some version-39 maps read and others not:
                    # the records looked as though they sometimes carried an
                    # extra int and sometimes did not, and nothing in the
                    # head predicted which. They all carry it.
                    player['hud_skin'] = self._read_i32(reader)
               player['fixed_start_position'] = self._read_i32(reader)
               player['name'] = self._read_str(reader)
               player['start_x'] = self._read_f32(reader)
               player['start_y'] = self._read_f32(reader)
               player['ally_low_flag'] = self._read_u32(reader)
               player['ally_high_flag'] = self._read_u32(reader)
               if version >= 31:
                    player['enemy_low_flag'] = self._read_i32(reader)
                    player['enemy_high_flag'] = self._read_i32(reader)
               players.append(player)
          data['players'] = players

     def _read_forces(self, reader, data):
          count = self._read_i32(reader)
          forces = []
          for _ in range(count):
               forces.append({
                    'flags': self._read_u32(reader),
                    'player_mask': self._read_u32(reader),
                    'name': self._read_str(reader),
               })
          data['forces'] = forces

     def _read_upgrades(self, reader, data):
          data['upgrades'] = []
          if self._is_finish(reader):
               return
          for _ in range(self._read_i32(reader)):
               data['upgrades'].append({
                    'player_flag': self._read_u32(reader),
                    'id': self._read_code(reader),
                    'level': self._read_i32(reader),
                    'availability': self._read_i32(reader),
               })

     def _read_techtree(self, reader, data):
          data['techtree'] = []
          if self._is_finish(reader):
               return
          for _ in range(self._read_i32(reader)):
               data['techtree'].append({
                    'player_flag': self._read_u32(reader),
                    'id': self._read_code(reader),
               })

     def _read_random_unit_tables(self, reader, data):
          data['random_unit_tables'] = []
          if self._is_finish(reader):
               return
          for _ in range(self._read_i32(reader)):
               group_id = self._read_i32(reader)
               name = self._read_str(reader)
               position_count = self._read_i32(reader)
               positions = [self._read_i32(reader) for _ in range(position_count)]
               lines = []
               for _ in range(self._read_i32(reader)):
                    chance = self._read_i32(reader)
                    ids = [self._read_code(reader) for _ in range(position_count)]
                    lines.append({'chance': chance, 'ids': ids})
               data['random_unit_tables'].append({
                    'id': group_id,
                    'name': name,
                    'positions': positions,
                    'lines': lines,
               })

     def _read_random_item_tables(self, reader, data):
          data['random_item_tables'] = []
          if self._is_finish(reader):
               return
          for _ in range(self._read_i32(reader)):
               table_id = self._read_i32(reader)
               name = self._read_str(reader)
               sets = []
               for _ in range(self._read_i32(reader)):
                    items = []
                    for _ in range(self._read_i32(reader)):
                         items.append({
                              'chance': self._read_i32(reader),
                              'id': self._read_code(reader),
                         })
                    sets.append({'items': items})
               data['random_item_tables'].append({
                    'id': table_id,
                    'name': name,
                    'sets': sets,
               })

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          version = data['file_version']
          self._write_i32(writer, version)

          if 25 <= version <= 33 or version == 39:
               self._write_head(writer, data, version)
               self._write_players(writer, data, version)
               self._write_forces(writer, data)
               self._write_upgrades(writer, data)
               self._write_techtree(writer, data)
               self._write_random_unit_tables(writer, data)
               self._write_random_item_tables(writer, data)
          elif version == 18:
               self._write_head(writer, data, version)
               self._write_players(writer, data, version)
               self._write_forces(writer, data)
               self._write_upgrades(writer, data)
               self._write_techtree(writer, data)
               self._write_random_unit_tables(writer, data)
          else:
               raise ValueError(
                    f"Unsupported war3map.w3i version: {version} "
                    "(only 18, 25-33 and 39 are implemented)"
               )

     def _write_head(self, writer, data, version):
          self._write_i32(writer, data['map_version'])
          self._write_i32(writer, data['editor_version'])
          if version >= 28:
               for value in data['war3_version']:
                    self._write_i32(writer, value)

          self._write_str(writer, data.get('map_name', ''))
          self._write_str(writer, data.get('author_name', ''))
          self._write_str(writer, data.get('map_description', ''))
          self._write_str(writer, data.get('players_recommended', ''))

          for value in data['camera_bounds']:
               self._write_f32(writer, value)
          for value in data['camera_complements']:
               self._write_i32(writer, value)

          self._write_i32(writer, data['map_width'])
          self._write_i32(writer, data['map_height'])
          self._write_u32(writer, data['flags'])
          self._write_byte(writer, data.get('map_main_ground_type'))

          if version >= 25:
               loading_screen = data.get('loading_screen') or {}
               self._write_i32(writer, loading_screen.get('id', -1))
               if version >= 39:
                    self._write_i32(writer, loading_screen.get('unknown_v39', 1))
               self._write_str(writer, loading_screen.get('path', ''))
               self._write_str(writer, loading_screen.get('text', ''))
               self._write_str(writer, loading_screen.get('title', ''))
               self._write_str(writer, loading_screen.get('subtitle', ''))

               self._write_i32(writer, data.get('game_data_set', 0))

               prologue = data.get('prologue') or {}
               self._write_str(writer, prologue.get('path', ''))
               self._write_str(writer, prologue.get('text', ''))
               self._write_str(writer, prologue.get('title', ''))
               self._write_str(writer, prologue.get('subtitle', ''))

               fog = data.get('fog') or {}
               self._write_i32(writer, fog.get('type', 0))
               self._write_f32(writer, fog.get('start_z', 0.0))
               self._write_f32(writer, fog.get('end_z', 0.0))
               self._write_f32(writer, fog.get('density', 0.0))
               self._write_color(writer, fog.get('color'))

               environment = data.get('environment') or {}
               self._write_code(writer, environment.get('weather_id'))
               if version >= 39:
                    self._write_i32(writer, environment.get('fog_unknown_1', 0))
                    self._write_f32(writer, environment.get('fog_linear_start', 0.0))
                    self._write_f32(writer, environment.get('fog_linear_end', 0.0))
                    self._write_f32(writer, environment.get('fog_max_opacity', 0.0))
                    self._write_i32(writer, environment.get('fog_unknown_2', 0))
                    self._write_i32(writer, environment.get('fog_unknown_3', 0))
               self._write_str(writer, environment.get('sound', ''))
               self._write_byte(writer, environment.get('light'))
               self._write_color(writer, environment.get('water_color'))

               if version >= 28:
                    self._write_i32(writer, 1 if str(data.get('script_type', 'jass')).lower() == 'lua' else 0)
               if version >= 31:
                    self._write_i32(writer, data.get('supported_modes', 0))
                    self._write_i32(writer, data.get('game_data_version', 0))
               if version >= 32:
                    self._write_i32(writer, data.get('force_default_camera_zoom', 0))
                    self._write_i32(writer, data.get('force_max_camera_zoom', 0))
               if version >= 33:
                    self._write_i32(writer, data.get('force_min_camera_zoom', 0))
               if version >= 39:
                    for value in tuple(data.get('water') or (0,) * 10)[:10]:
                         self._write_i32(writer, value)
          else:
               loading_screen = data.get('loading_screen') or {}
               self._write_i32(writer, loading_screen.get('id', -1))
               self._write_str(writer, loading_screen.get('text', ''))
               self._write_str(writer, loading_screen.get('title', ''))
               self._write_str(writer, loading_screen.get('subtitle', ''))

               prologue = data.get('prologue') or {}
               self._write_i32(writer, prologue.get('id', -1))
               self._write_str(writer, prologue.get('text', ''))
               self._write_str(writer, prologue.get('title', ''))
               self._write_str(writer, prologue.get('subtitle', ''))

     def _write_players(self, writer, data, version):
          players = data.get('players', [])
          self._write_i32(writer, len(players))
          for player in players:
               self._write_i32(writer, player['id'])
               self._write_i32(writer, player['type'])
               self._write_i32(writer, player['race'])
               if version >= 39:
                    self._write_i32(writer, player.get('hud_skin', 0))
               self._write_i32(writer, player['fixed_start_position'])
               self._write_str(writer, player.get('name', ''))
               self._write_f32(writer, player['start_x'])
               self._write_f32(writer, player['start_y'])
               self._write_u32(writer, player['ally_low_flag'])
               self._write_u32(writer, player['ally_high_flag'])
               if version >= 31:
                    self._write_i32(writer, player.get('enemy_low_flag', 0))
                    self._write_i32(writer, player.get('enemy_high_flag', 0))

     def _write_forces(self, writer, data):
          forces = data.get('forces', [])
          self._write_i32(writer, len(forces))
          for force in forces:
               self._write_u32(writer, force['flags'])
               self._write_u32(writer, force['player_mask'])
               self._write_str(writer, force.get('name', ''))

     def _write_upgrades(self, writer, data):
          upgrades = data.get('upgrades', [])
          self._write_i32(writer, len(upgrades))
          for upgrade in upgrades:
               self._write_u32(writer, upgrade['player_flag'])
               self._write_code(writer, upgrade['id'])
               self._write_i32(writer, upgrade['level'])
               self._write_i32(writer, upgrade['availability'])

     def _write_techtree(self, writer, data):
          techtree = data.get('techtree', [])
          self._write_i32(writer, len(techtree))
          for tech in techtree:
               self._write_u32(writer, tech['player_flag'])
               self._write_code(writer, tech['id'])

     def _write_random_unit_tables(self, writer, data):
          tables = data.get('random_unit_tables', [])
          self._write_i32(writer, len(tables))
          for table in tables:
               self._write_i32(writer, table['id'])
               self._write_str(writer, table.get('name', ''))
               positions = table.get('positions', [])
               self._write_i32(writer, len(positions))
               for value in positions:
                    self._write_i32(writer, value)
               lines = table.get('lines', [])
               self._write_i32(writer, len(lines))
               for line in lines:
                    self._write_i32(writer, line['chance'])
                    for code in line['ids']:
                         self._write_code(writer, code)

     def _write_random_item_tables(self, writer, data):
          tables = data.get('random_item_tables', [])
          self._write_i32(writer, len(tables))
          for table in tables:
               self._write_i32(writer, table['id'])
               self._write_str(writer, table.get('name', ''))
               sets = table.get('sets', [])
               self._write_i32(writer, len(sets))
               for item_set in sets:
                    items = item_set.get('items', [])
                    self._write_i32(writer, len(items))
                    for item in items:
                         self._write_i32(writer, item['chance'])
                         self._write_code(writer, item['id'])
