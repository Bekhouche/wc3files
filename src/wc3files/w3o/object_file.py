from ..data import BinaryDataType
from .data import ObjectFileType
from wc3files.files import BinaryFile
from ..table.wts_file import WtsFile
from .object_slk import object_table_to_slk
from .object_validator import validate_object_table
from .object_override import build_override_tables, rebuild_object_table


def _has_nonzero_trailing(reader):
     """
     Whether any byte remains between the reader's current position and EOF that isn't
     zero-padding. A real map's own war3map.w3a/.w3u/... can legitimately leave a handful of
     trailing zero bytes after both its 'original'/'custom' tables read cleanly (confirmed
     against a real fixture: exactly 4 trailing 0x00 bytes, harmless padding of some kind -
     w3x2lni's own frontend_obj.lua tolerates this identically, never asserting the whole
     buffer was consumed). Treating ANY leftover byte count as proof the plain (non-skin)
     entry layout must be wrong was the actual root cause of a real parse failure on that
     fixture: the retry-as-skin-layout fallback this triggered then desynced on an ordinary
     ability tooltip string (a WC3 color-code split, `|cffffcc00W` + `|r` + `ard`) and raised
     a confusing "not a valid BinaryDataType" error, when the plain layout had in fact already
     parsed every real object correctly. Restores the reader's position before returning so
     this can be used as a non-destructive check.
     """
     pos = reader.tell()
     trailing = reader.file.read()
     reader.seek(pos)
     return any(trailing)


class ObjectTable(object):
     def __init__(self, data_type=ObjectFileType.File, data_table='original', skin=False):

          self._data_type = data_type
          self._data_table = data_table
          # war3mapSkin.* entries carry an extra 8-byte reserved field (always
          # observed as int 1, int 0) between new_id and mod_count that base
          # war3map.* files do not have.
          self._skin = skin
          self.clear()
     
     def clear(self):
          self._data = {}
          self._data_total = 0
     
     def load(self, data):
          self._data = data
          self._data_total = len(data)

     @staticmethod
     def _rawcode_bytes(code):
          """
          A mod-id/object-id rawcode is a fixed 4-byte field in the binary format, no matter
          how many characters this package's own data happens to carry for it. Confirmed
          against a real Blizzard file, not a hypothetical: the bundled AbilityMetaData.slk
          has exactly one row (mod-id nominally "Curse"'s slot-A Data field) whose id is
          stored as the literal 3-character quoted string "Crs", not 4 - Blizzard's own
          authoring quirk, most likely because the real 4-byte rawcode is "Crs" NUL-padded
          and the SLK text just never carried the trailing control byte. Writing a 3-byte
          id via a plain .encode('latin-1') permanently shifts every single byte written
          after it by one - not a localized corruption but a whole-file desync that can
          travel through hundreds of unrelated objects before finally producing a
          garbage-looking count or type code far downstream (confirmed while debugging
          exactly that: a real map's rebuilt war3map.w3a failed to reread with a wildly
          implausible modification count dozens of objects after the true culprit). Padding
          to 4 bytes here, at the one place every rawcode is actually serialized, fixes this
          regardless of which field (mod-id, object id, template id) ever turns up short.
          """
          # Accepts bytes as well as text: the preserved trailing token and the
          # all-null default arrive as bytes, every other caller passes a str.
          raw = bytes(code) if isinstance(code, (bytes, bytearray)) else code.encode('latin-1')
          return raw.ljust(4, b'\x00')[:4]

     def _has_level_data(self):
          return ObjectFileType(self._data_type) in (
               ObjectFileType.Doodads,
               ObjectFileType.Abilities,
               ObjectFileType.Upgrades,
          )

     # Real WC3 object tables never come close to these counts. A misread
     # (e.g. guessing the wrong entry layout) tends to turn garbage bytes into
     # a huge count instead of raising - these caps turn that into a fast,
     # catchable ValueError instead of a billion-iteration hang.
     MAX_ENTRIES = 200_000
     MAX_MODS_PER_ENTRY = 20_000

     def read(self, reader: BinaryFile):
          # Each table entry is: original_id[4], new_id[4], mod_count, then mods.
          # There is no padding between new_id and mod_count (version 2).
          self._data_total = reader.read(BinaryDataType.INT)
          if not (0 <= self._data_total <= self.MAX_ENTRIES):
               raise ValueError(f"implausible object table entry count: {self._data_total}")
          for _ in range(self._data_total):
               ORG_ID = reader.read(BinaryDataType.BYTES).decode('latin-1')
               NEW_ID = reader.read(BinaryDataType.BYTES).decode('latin-1')

               IDX = ORG_ID
               if self._data_table == 'custom':
                    IDX = NEW_ID
                    self._data[IDX] = {'ID': ORG_ID}
               else:
                    self._data[IDX] = {}

               if self._skin:
                    reader.read(BinaryDataType.INT)
                    reader.read(BinaryDataType.INT)

               modifications_num = reader.read(BinaryDataType.INT)
               if not (0 <= modifications_num <= self.MAX_MODS_PER_ENTRY):
                    raise ValueError(f"implausible modification count: {modifications_num}")
               for mod_index in range(modifications_num):
                    MOD_ID = reader.read(BinaryDataType.BYTES).decode('latin-1')
                    MOD_TYPE = reader.read(BinaryDataType.INT)
                    if MOD_ID not in self._data[IDX]:
                         self._data[IDX][MOD_ID] = {'value': None, 'type': MOD_TYPE}

                    if self._has_level_data():
                         MOD_LEVEL = reader.read(BinaryDataType.INT)
                         MOD_DATA_PTR = reader.read(BinaryDataType.INT)
                         MOD_VALUE = reader.read(BinaryDataType(MOD_TYPE), signed=True)

                         if self._data[IDX][MOD_ID]['value'] is None:
                              self._data[IDX][MOD_ID]['value'] = {}
                              self._data[IDX][MOD_ID]['ptr'] = {}
                              self._data[IDX][MOD_ID]['end'] = {}
                              self._data[IDX][MOD_ID]['seq'] = {}
                         self._data[IDX][MOD_ID]['value'][MOD_LEVEL] = MOD_VALUE
                         self._data[IDX][MOD_ID]['ptr'][MOD_LEVEL] = MOD_DATA_PTR
                         # Levels of different mods are interleaved in real
                         # files - an ability writes one mod at level 1, the
                         # next mod at level 1, then back for level 2 - while
                         # grouping them per mod on write reorders the whole
                         # entry. Recording the position each (mod, level) was
                         # read at is what lets the original order be restored.
                         self._data[IDX][MOD_ID]['seq'][MOD_LEVEL] = mod_index
                    else:
                         MOD_VALUE = reader.read(BinaryDataType(MOD_TYPE), signed=True)
                         self._data[IDX][MOD_ID]['value'] = MOD_VALUE

                    # Trailing object-id token, kept rather than discarded.
                    # It is not reliably the entry's own id: real maps put the
                    # id of a *different* object here, and rebuilding it from
                    # the entry id (or nulls) is what made every object file
                    # in the benchmark corpus write back differently - 701
                    # files across six formats, none of them byte-exact.
                    raw_token = reader.read(BinaryDataType.BYTES)
                    if len(raw_token) < 4:
                         # Defensive: reading past the end of the file yields
                         # fewer than four bytes, which would otherwise become
                         # four zero bytes and be written back as though the
                         # data had been there. No file in the benchmark corpus
                         # triggers this - the short `.w3b` files that looked
                         # truncated turned out to be complete, their apparent
                         # extra bytes being the reserved skin field the writer
                         # was forcing on.
                         raise ValueError(
                              f"object table is truncated: entry {IDX!r} "
                              f"declares modification {MOD_ID!r} but the file "
                              f"ends before it is complete.")
                    END_TOKEN = raw_token.decode('latin-1')
                    if self._has_level_data():
                         self._data[IDX][MOD_ID]['end'][MOD_LEVEL] = END_TOKEN
                    else:
                         self._data[IDX][MOD_ID]['end'] = END_TOKEN

          # Entries are held in a dict keyed by object id, which cannot hold
          # two entries that share one. A real map does exactly that: its
          # table declares four entries and only three distinct ids, so one
          # was being dropped on read and the rebuilt file came out an entry
          # short with a smaller count in its header. Losing data quietly is
          # worse than refusing, so the two are required to agree.
          if len(self._data) != self._data_total:
               raise ValueError(
                    f"object table declares {self._data_total} entries but "
                    f"only {len(self._data)} distinct object ids were read - "
                    f"{self._data_total - len(self._data)} entry/entries share "
                    f"an id and would be lost, so the table is refused rather "
                    f"than silently returned incomplete.")

     def write(self, writer: BinaryFile):
          writer.write(BinaryDataType.INT, self._data_total)
          for idx in self._data.keys():
               if 'ID' in self._data[idx]:
                    writer.write(BinaryDataType.BYTES, self._rawcode_bytes(self._data[idx]['ID']))
                    writer.write(BinaryDataType.BYTES, self._rawcode_bytes(idx))
                    end_token = self._rawcode_bytes(idx)
               else:
                    writer.write(BinaryDataType.BYTES, self._rawcode_bytes(idx))
                    writer.write(BinaryDataType.BYTES, b'\x00\x00\x00\x00')
                    end_token = b'\x00\x00\x00\x00'

               if self._skin:
                    writer.write(BinaryDataType.INT, 1)
                    writer.write(BinaryDataType.INT, 0)

               if self._has_level_data():
                    mods, levels, ptrs, values, types, ends = [], [], [], [], [], []

                    # File order, not sorted order. Reading inserts mods in
                    # the order the file lists them and dicts keep that, so
                    # sorting here rewrote every entry whose mods were not
                    # already alphabetical.
                    for mod in self._data[idx].keys():
                         if mod != 'ID':
                              entry = self._data[idx][mod]
                              stored_end = entry.get('end')
                              if isinstance(entry['value'], dict):
                                   for level in entry['value'].keys():
                                        mods.append(mod)
                                        levels.append(level)
                                        ptrs.append(entry['ptr'][level])
                                        values.append(entry['value'][level])
                                        types.append(entry['type'])
                                        ends.append(
                                             stored_end.get(level, end_token)
                                             if isinstance(stored_end, dict)
                                             else (stored_end if stored_end is not None
                                                   else end_token))
                              else:
                                   mods.append(mod)
                                   levels.append(0)
                                   ptrs.append(0)
                                   values.append(entry['value'])
                                   types.append(entry['type'])
                                   ends.append(stored_end if stored_end is not None
                                               else end_token)

                    # Restore the order the file listed these in, when it was
                    # recorded on read; otherwise keep the order built above.
                    order = list(range(len(mods)))
                    sequences = []
                    for position in order:
                         entry = self._data[idx][mods[position]]
                         stored_seq = entry.get('seq')
                         if isinstance(stored_seq, dict):
                              sequences.append(stored_seq.get(levels[position]))
                         else:
                              sequences.append(stored_seq)
                    if all(value is not None for value in sequences):
                         order.sort(key=lambda position: sequences[position])
                         mods = [mods[position] for position in order]
                         levels = [levels[position] for position in order]
                         ptrs = [ptrs[position] for position in order]
                         values = [values[position] for position in order]
                         types = [types[position] for position in order]
                         ends = [ends[position] for position in order]

                    writer.write(BinaryDataType.INT, len(mods))
                    for mod in range(len(mods)):
                         writer.write(BinaryDataType.BYTES, self._rawcode_bytes(mods[mod]))
                         writer.write(BinaryDataType.INT, types[mod])
                         writer.write(BinaryDataType.INT, levels[mod])
                         writer.write(BinaryDataType.INT, ptrs[mod])
                         writer.write(BinaryDataType(types[mod]), values[mod])
                         writer.write(BinaryDataType.BYTES,
                                      self._rawcode_bytes(ends[mod]))
               else:
                    mod_keys = [mod for mod in self._data[idx].keys() if mod != 'ID']
                    writer.write(BinaryDataType.INT, len(mod_keys))

                    for mod in mod_keys:
                         entry = self._data[idx][mod]
                         stored_end = entry.get('end')
                         writer.write(BinaryDataType.BYTES, self._rawcode_bytes(mod))
                         writer.write(BinaryDataType.INT, entry['type'])
                         writer.write(BinaryDataType(entry['type']), entry['value'])
                         writer.write(BinaryDataType.BYTES, self._rawcode_bytes(
                              stored_end if stored_end is not None else end_token))

     def filter(self, remove=[], keep=[]):
          if len(keep):
               self._data = {key: self._data[key] for key in keep if key in self._data}
               self._data_total = len(self._data)

          if len(remove):
               self._data = {key: self._data[key] for key in remove if key in self._data}
               self._data_total = len(self._data)

     def get(self):
          return self._data

     def __len__(self):
          return self._data_total
     

class ObjectFile(object):
     def __init__(self):

          self._hooks = {'read': [], 'write': []}
          self.clear()

     def clear(self):

          self._data = {'w3u': None, 'w3t': None, 'w3b': None, 'w3d': None,
                        'w3a': None, 'w3h': None, 'w3q': None}
          self._data_total = {'w3u': {'original': 0, 'custom':0}, 'w3t': {'original': 0, 'custom':0}, 'w3b': {'original': 0, 'custom':0}, 'w3d': {'original': 0, 'custom':0},
                        'w3a': {'original': 0, 'custom':0}, 'w3h': {'original': 0, 'custom':0}, 'w3q': {'original': 0, 'custom':0}}
          self._data_type = ObjectFileType.File
          # What the file that was read actually declared. The writer used to
          # emit a fixed version 3 and always include the 8-byte reserved
          # field, so a real version-2 file came back with a different version
          # and one extra field per entry - the rebuilt war3map.w3u of one
          # benchmark map grew by 472 bytes that way.
          self._version = None
          self._skin_used = None

     def import_data(self, data):
          self._data = data

     def export_data(self):
          return self._data

     # ---- mutation hooks ---------------------------------------------------------
     # A wc3files-native equivalent of w3x2lni's plugin architecture: that mechanism
     # is a Lua extension hook tied specifically to w3x2lni's own object-id mark-
     # and-sweep protection pass (a plugin's real job is telling that protector
     # "don't rename this rawcode") - wc3files has no equivalent protection pass to
     # hook into, so rather than build a hook system with nothing real to attach to,
     # this is a generic "inspect or mutate the parsed object-data dict at a defined
     # pipeline point" capability instead: register a Python callback per instance
     # (not global/class-level state, so hooks on one ObjectFile never leak into
     # another), called as callback(self, data) once after read() finishes parsing
     # and once before write() serializes. A callback may mutate `data` in place, or
     # return a replacement dict which is used instead if not None.

     def register_hook(self, event, callback):
          if event not in self._hooks:
               raise ValueError(f"Unknown hook event: {event!r} (expected 'read' or 'write')")
          self._hooks[event].append(callback)

     def unregister_hook(self, event, callback):
          if event not in self._hooks:
               raise ValueError(f"Unknown hook event: {event!r} (expected 'read' or 'write')")
          self._hooks[event].remove(callback)

     def clear_hooks(self, event=None):
          if event is None:
               for hooks in self._hooks.values():
                    hooks.clear()
          elif event in self._hooks:
               self._hooks[event].clear()
          else:
               raise ValueError(f"Unknown hook event: {event!r} (expected 'read' or 'write')")

     def _call_hooks(self, event):
          for callback in self._hooks[event]:
               result = callback(self, self._data)
               if result is not None:
                    self._data = result

     def read(self, file_path, object_type=None, skin='auto'):
          if isinstance(file_path, str):
               object_type = file_path.split('.')[-1].lower()
               if object_type in [item.value for item in ObjectFileType]:
                    pass
               else:
                    raise ValueError("Invalid file type")
          elif isinstance(file_path, bytes):
               if object_type in [item.value for item in ObjectFileType]:
                    pass
               else:
                    raise ValueError("Invalid file type")
          else:
               raise ValueError("Invalid file type")

          with BinaryFile(file_path, 'read') as reader:
               if ObjectFileType(object_type) == ObjectFileType.File:
                    version_ = reader.read(BinaryDataType.INT)
                    for object_type_ in self._data.keys():
                         if reader.read(BinaryDataType.INT):
                              version_ = reader.read(BinaryDataType.INT)
                              start_pos = reader.tell()
                              use_skin = skin if skin != 'auto' else False
                              try:
                                   original = ObjectTable(ObjectFileType(object_type_), 'original', skin=use_skin)
                                   original.read(reader)
                                   custom = ObjectTable(ObjectFileType(object_type_), 'custom', skin=use_skin)
                                   custom.read(reader)
                                   # Peek: the next 4 bytes should be a plausible has-data
                                   # flag (0 or 1) for the next type, or we're at EOF.
                                   pos = reader.tell()
                                   ok = pos >= reader.size()
                                   if not ok:
                                        flag = reader.read(BinaryDataType.INT)
                                        reader.seek(pos)
                                        ok = flag in (0, 1)
                                   if not ok and skin == 'auto':
                                        raise ValueError("misaligned object table")
                              except ValueError:
                                   if skin != 'auto':
                                        raise
                                   reader.seek(start_pos)
                                   original = ObjectTable(ObjectFileType(object_type_), 'original', skin=True)
                                   original.read(reader)
                                   custom = ObjectTable(ObjectFileType(object_type_), 'custom', skin=True)
                                   custom.read(reader)
                              self._data[object_type_] = {'original': original.get(), 'custom': custom.get()}
                              self._data_total[object_type_]['original'] = len(self._data[object_type_]['original'])
                              self._data_total[object_type_]['custom'] = len(self._data[object_type_]['custom'])
               else:
                    version = reader.read(BinaryDataType.INT)
                    self._version = version
                    start_pos = reader.tell()
                    use_skin = skin if skin != 'auto' else False
                    self._skin_used = use_skin
                    try:
                         original = ObjectTable(ObjectFileType(object_type), 'original', skin=use_skin)
                         original.read(reader)
                         custom = ObjectTable(ObjectFileType(object_type), 'custom', skin=use_skin)
                         custom.read(reader)
                         if skin == 'auto' and reader.tell() != reader.size() and _has_nonzero_trailing(reader):
                              raise ValueError("misaligned object table")
                    except ValueError:
                         if skin != 'auto':
                              raise
                         reader.seek(start_pos)
                         self._skin_used = True
                         original = ObjectTable(ObjectFileType(object_type), 'original', skin=True)
                         original.read(reader)
                         custom = ObjectTable(ObjectFileType(object_type), 'custom', skin=True)
                         custom.read(reader)
                         if reader.tell() != reader.size() and _has_nonzero_trailing(reader):
                              raise ValueError(
                                   f"unable to parse {object_type} object table: "
                                   "does not fit either known entry format"
                              )
                    self._data[object_type] = {'original': original.get(), 'custom': custom.get()}
                    self._data_total[object_type]['original'] = len(self._data[object_type]['original'])
                    self._data_total[object_type]['custom'] = len(self._data[object_type]['custom'])
                    self._data_type = ObjectFileType(object_type)

          self._call_hooks('read')

     def write(self, file_path, skin='as-read', validate=False):
          # World Editor's own object-table writer always includes the extra
          # 8-byte reserved field (see ObjectTable._skin). A war3map.* base
          # file written without it round-trips fine through this library's
          # own reader but is rejected by World Editor's Import Objects -
          # confirmed against war3map.w3a/w3u. So data built from scratch
          # still gets the WE-compatible layout.
          #
          # 'as-read' means: whatever the file that was read used, falling
          # back to that WE-compatible default when there was no such file.
          # Forcing it on unconditionally rewrote real version-2 files that
          # legitimately do not carry the field. Pass True or False to choose
          # explicitly.
          self._call_hooks('write')
          object_type = file_path.split('.')[-1].lower()
          if object_type in [item.value for item in ObjectFileType]:
               if validate:
                    self._raise_if_invalid(object_type)
               with BinaryFile(file_path, 'write', ) as writer:
                    if ObjectFileType(object_type) == ObjectFileType.File:
                         writer.write(BinaryDataType.INT, 1)
                         for object_type_ in self._data.keys():
                              if self._data[object_type_] != None:
                                   writer.write(BinaryDataType.INT, 1)
                                   writer.write(BinaryDataType.INT, 3)

                                   original = ObjectTable(ObjectFileType(object_type_), 'original', skin=skin)
                                   original.load(self._data[object_type_]['original'])
                                   original.write(writer)

                                   custom = ObjectTable(ObjectFileType(object_type_), 'custom', skin=skin)
                                   custom.load(self._data[object_type_]['custom'])
                                   custom.write(writer)
                              else:
                                   writer.write(BinaryDataType.INT, 0)

                    else:
                         # Put back what the file declared. Emitting a fixed
                         # version 3 over a real version-2 file changed its
                         # first four bytes, and forcing the reserved field on
                         # added eight bytes to every entry on top of that.
                         effective_skin = (self._skin_used
                                           if skin == 'as-read'
                                           else skin)
                         if effective_skin is None:
                              effective_skin = True
                         writer.write(BinaryDataType.INT,
                                      self._version if self._version is not None else 3)

                         original = ObjectTable(ObjectFileType(object_type), 'original', skin=effective_skin)
                         original.load(self._data[object_type]['original'])
                         original.write(writer)

                         custom = ObjectTable(ObjectFileType(object_type), 'custom', skin=effective_skin)
                         custom.load(self._data[object_type]['custom'])
                         custom.write(writer)
          else:
               raise ValueError("Invalid file type")
          
     def filter(object_type=ObjectFileType.File, table=['original', 'custom'], keep=[], remove=[]):
          #print(object_type, table, keep, remove)
          x = 0
          
     def get_object_type(self):
          return self._data_type

     def set_object_type(self, object_type):
          self._data_type = object_type

     def read_skin(self, file_path, object_type=None):
          # A war3mapSkin.* file carries the same per-id/per-field shape as its war3map.* base
          # file, but only for the fields it wants to add or override (e.g. HD model/icon
          # overrides) - so merge it onto whatever is already loaded instead of replacing it.
          # Skin tables also carry an extra 8-byte reserved field per entry that base
          # war3map.* files do not (see ObjectTable._skin).
          skin = ObjectFile()
          skin.read(file_path, object_type, skin=True)
          target_type = skin.get_object_type().value
          skin_data = skin.export_data()[target_type] or {}

          if self._data[target_type] is None:
               self._data[target_type] = {'original': {}, 'custom': {}}

          for table in ('original', 'custom'):
               base_table = self._data[target_type].setdefault(table, {})
               for idx, mods in (skin_data.get(table) or {}).items():
                    base_table.setdefault(idx, {}).update(mods)
               self._data_total[target_type][table] = len(base_table)

          return self

     def validate(self, object_type=None, metadata=None):
          # Report mode - see object_validator.validate_object_table for exactly what this
          # does and does not check. object_type=None validates every type currently loaded
          # (a multi-type 'File'/war3map.w3o read); pass a specific type to check just that one.
          if object_type is not None:
               object_type = ObjectFileType(object_type)
               return validate_object_table(object_type.value, self._data[object_type.value], metadata=metadata)

          reports = []
          for object_type_, table_data in self._data.items():
               reports.extend(validate_object_table(object_type_, table_data, metadata=metadata))
          return reports

     def _raise_if_invalid(self, object_type, metadata=None):
          if ObjectFileType(object_type) == ObjectFileType.File:
               reports = self.validate(metadata=metadata)
          else:
               reports = self.validate(object_type, metadata=metadata)

          # 'note'-severity findings (e.g. a value below a declared minVal of 0) are a common,
          # usually-intentional way to build below-baseline effects by editing object data
          # directly - not something strict/write-time validation should block on. Only
          # 'issue'-severity findings (enum-value mismatches, out-of-range against a non-zero
          # bound, empty-when-not-allowed, ...) do.
          blocking = [
               (r, [i for i in r['issues'] if i['severity'] == 'issue'])
               for r in reports
          ]
          blocking = [(r, issues) for r, issues in blocking if issues]
          if blocking:
               preview = '; '.join(
                    f"{r['table']}/{r['id']}/{r['field']}: "
                    f"{', '.join(i['message'] for i in issues)}"
                    for r, issues in blocking[:5]
               )
               more = f' (+{len(blocking) - 5} more)' if len(blocking) > 5 else ''
               raise ValueError(f"{len(blocking)} field(s) failed metadata validation: {preview}{more}")

     def to_override_tables(self, object_type=None, metadata=None, base_data=None):
          # See object_override.build_override_tables for exactly what this does, what it
          # deliberately does NOT move to a literal-path table (icons/models/tooltips and
          # anything else ObjectMetaData tags 'Profile'), and why. object_type defaults to
          # whatever single type was just read() (get_object_type()).
          object_type = ObjectFileType(object_type) if object_type is not None else self.get_object_type()
          return build_override_tables(object_type, self._data[object_type.value],
                                        metadata=metadata, base_data=base_data)

     @classmethod
     def from_override_tables(cls, object_type, tables, remainder=None, metadata=None, base_data=None):
          # Reverse of to_override_tables() - see object_override.rebuild_object_table for
          # exactly what this does and does not recover with certainty (report explains any
          # ambiguous field resolutions, guessed custom-object templates, unconfirmed binary
          # storage types, or unrecognized columns it hit along the way).
          object_type = ObjectFileType(object_type)
          table_data, report = rebuild_object_table(object_type, tables, remainder=remainder,
                                                     metadata=metadata, base_data=base_data)
          instance = cls()
          instance.set_object_type(object_type)
          instance.import_data({object_type.value: table_data})
          return instance, report

     def to_slk(self, object_type=None, metadata=None, base_data=None):
          # See object_slk.object_table_to_slk for exactly what this does and does not
          # reconstruct. object_type defaults to whatever single type was just read()
          # (get_object_type()); pass it explicitly for a multi-type 'File'/war3map.w3o read.
          # base_data (a wc3files.w3o.BaseObjectData) merges each row onto its real base-game
          # object's full default stats first, instead of showing only the map's own diff.
          object_type = ObjectFileType(object_type) if object_type is not None else self.get_object_type()
          return object_table_to_slk(object_type.value, self._data[object_type.value],
                                      metadata=metadata, base_data=base_data)

     def apply_strings(self, wts):
          # Resolve "TRIGSTR_XXXXX" placeholders across every already-read table using a
          # WtsFile (or a war3map.wts path/bytes, which is read into one on the fly).
          if not isinstance(wts, WtsFile):
               strings = WtsFile()
               strings.read(wts)
               wts = strings

          for object_type_data in self._data.values():
               if not object_type_data:
                    continue
               for table in ('original', 'custom'):
                    for entry in (object_type_data.get(table) or {}).values():
                         for mod_id, mod in entry.items():
                              if mod_id == 'ID':
                                   continue
                              if isinstance(mod['value'], dict):
                                   for level, value in mod['value'].items():
                                        mod['value'][level] = wts.resolve(value)
                              else:
                                   mod['value'] = wts.resolve(mod['value'])

          return self

     
