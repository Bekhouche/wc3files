import struct
from ..data import BinaryDataType
from .data import WtgFileType, TriggerItemType
from wc3files.files import BinaryFile
from .trigger_data import TriggerData, EVENT, CONDITION, ACTION, CALL


class MapTriggersFile(object):
     """
     Reader/writer for war3map.wtg (GUI trigger definitions: categories, global variables,
     triggers, and each trigger's event/condition/action function tree).

     Cross-checked field-for-field against the War3Net.Build library
     (github.com/Drake53/War3Net - Serialization/Binary/Script/MapTriggers.cs and friends),
     and verified byte-for-byte (full read -> zero leftover bytes) against real maps covering
     every version family this format has shipped in the wild:
       - classic v3/v4 (Reign of Chaos)
       - classic v6/v7 (The Frozen Throne) - v7 adds per-category/per-trigger "is comment"
         flags, per-variable array size, and nested child functions (used for constructs like
         "If/Then/Else" and "And/Or Multiple Conditions")
       - Reforged: a flat TriggerItems list tagged by TriggerItemType, prefixed by a per-type
         item count and an explicit deleted-item list, signalled by a version field that reads
         as a large *negative* sentinel (-2147483647 or -2147483644) instead of 3/4/6/7.

     The one thing this binary format cannot self-describe: a function's parameter COUNT is
     not stored inline - it must be looked up by name in a TriggerData.txt (see trigger_data.py
     and the bundled default). Get this wrong and every field after that function silently
     misaligns, which is exactly what happened while building this: TriggerData.txt lists
     argument type "nothing" for functions that take zero real parameters (e.g. AndMultiple,
     IfThenElseMultiple, LockGameSpeedBJ) - it is not itself a one-slot placeholder argument,
     unlike every other listed type.
     """

     def __init__(self, trigger_data=None):
          self.trigger_data = trigger_data or TriggerData.default()
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in WtgFileType]:
                    raise ValueError(f"Invalid trigger file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self._data = self._read(reader)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               self._write(writer, self._data)

     def get_data(self):
          return self._data

     # Item kinds in the format-7 unified item list.
     ITEM_ROOT = 1
     ITEM_CATEGORY = 4
     ITEM_TRIGGER = 8
     ITEM_COMMENT = 16
     ITEM_VARIABLE = 64

     def get_triggers(self):
          """Every trigger, whichever layout this file uses.

          Older files keep a separate `triggers` list; format 7 puts
          categories, triggers, comments and variables in one `items` list
          tagged by `item_type`, so reading `get_data()['triggers']` on a
          current map silently yields nothing at all rather than failing.
          """
          data = self._data or {}
          if data.get('triggers'):
               return list(data['triggers'])
          return [item for item in (data.get('items') or [])
                  if item.get('item_type') == self.ITEM_TRIGGER]

     def get_categories(self):
          """The folders triggers are organized into (format 7 only)."""
          data = self._data or {}
          return [item for item in (data.get('items') or [])
                  if item.get('item_type') == self.ITEM_CATEGORY]

     # ---- low-level binary helpers ---------------------------------------------------

     @staticmethod
     def _read_i32(reader):
          return struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0]

     @staticmethod
     def _write_i32(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', value))

     @staticmethod
     def _read_bool(reader):
          return bool(struct.unpack('<i', reader.read(BinaryDataType.BYTES, num=4))[0])

     @staticmethod
     def _write_bool(writer, value):
          writer.write(BinaryDataType.BYTES, struct.pack('<i', 1 if value else 0))

     @staticmethod
     def _read_str(reader):
          return reader.read(BinaryDataType.STRING)

     @staticmethod
     def _write_str(writer, value):
          writer.write(BinaryDataType.STRING, value or '')

     # ---- read -------------------------------------------------------------------

     def _read(self, reader):
          magic = reader.read(BinaryDataType.BYTES, num=4)
          if magic != b'WTG!':
               raise ValueError(f"Invalid trigger file header. Got: {magic!r}")

          version = self._read_i32(reader)

          if version in (3, 4, 6, 7):
               return self._read_classic(reader, version)
          if version in (-2147483647, -2147483644):
               return self._read_reforged(reader, version)
          raise ValueError(f"Unsupported war3map.wtg version: {version}")

     def _read_classic(self, reader, format_version):
          data = {'format_version': format_version, 'subversion': None}

          data['categories'] = [self._read_category(reader, format_version, None) for _ in range(self._read_i32(reader))]
          data['game_version'] = self._read_i32(reader)
          data['variables'] = [self._read_variable(reader, format_version, None) for _ in range(self._read_i32(reader))]
          data['triggers'] = [self._read_trigger(reader, format_version, None) for _ in range(self._read_i32(reader))]

          return data

     def _read_reforged(self, reader, subversion):
          format_version = self._read_i32(reader)
          data = {'format_version': format_version, 'subversion': subversion}

          item_counts = {}
          deleted_items = []
          for item_type in TriggerItemType:
               item_counts[item_type.value] = self._read_i32(reader)
               for _ in range(self._read_i32(reader)):
                    deleted_items.append({'item_type': item_type.value, 'id': self._read_i32(reader)})
          data['item_counts'] = item_counts
          data['deleted_items'] = deleted_items

          data['game_version'] = self._read_i32(reader)
          data['variables'] = [self._read_variable(reader, format_version, subversion) for _ in range(self._read_i32(reader))]

          items = []
          for _ in range(self._read_i32(reader)):
               item_type = self._read_i32(reader)
               if item_type in (TriggerItemType.ROOT_CATEGORY.value, TriggerItemType.CATEGORY.value):
                    item = self._read_category(reader, format_version, subversion)
               elif item_type in (TriggerItemType.GUI.value, TriggerItemType.COMMENT.value, TriggerItemType.SCRIPT.value):
                    item = self._read_trigger(reader, format_version, subversion)
               elif item_type == TriggerItemType.VARIABLE.value:
                    item = {'id': self._read_i32(reader), 'name': self._read_str(reader), 'parent_id': self._read_i32(reader)}
               else:
                    raise ValueError(f"Unsupported trigger item type: {item_type}")
               item['item_type'] = item_type
               items.append(item)
          data['items'] = items

          return data

     def _read_category(self, reader, format_version, subversion):
          item = {'id': self._read_i32(reader), 'name': self._read_str(reader)}
          if format_version >= 7:
               item['is_comment'] = self._read_bool(reader)
          if subversion is not None:
               item['is_expanded'] = self._read_bool(reader)
               item['parent_id'] = self._read_i32(reader)
          return item

     def _read_variable(self, reader, format_version, subversion):
          item = {
               'name': self._read_str(reader),
               'type': self._read_str(reader),
               'unknown': self._read_i32(reader),
               'is_array': self._read_bool(reader),
          }
          if format_version >= 7:
               item['array_size'] = self._read_i32(reader)
          item['is_initialized'] = self._read_bool(reader)
          item['initial_value'] = self._read_str(reader)
          if subversion is not None:
               item['id'] = self._read_i32(reader)
               item['parent_id'] = self._read_i32(reader)
          return item

     def _read_trigger(self, reader, format_version, subversion):
          item = {'name': self._read_str(reader), 'description': self._read_str(reader)}
          if format_version >= 7:
               item['is_comment'] = self._read_bool(reader)
          if subversion is not None:
               item['id'] = self._read_i32(reader)
          item['is_enabled'] = self._read_bool(reader)
          item['is_custom_text'] = self._read_bool(reader)
          item['is_initially_on'] = not self._read_bool(reader)
          item['run_on_map_init'] = self._read_bool(reader)
          item['parent_id'] = self._read_i32(reader)

          func_count = self._read_i32(reader)
          if item['is_custom_text'] and func_count > 0:
               raise ValueError("Custom text trigger should not have any GUI functions.")
          item['functions'] = [self._read_function(reader, format_version, is_child=False) for _ in range(func_count)]
          return item

     def _read_function(self, reader, format_version, is_child):
          function_type = self._read_i32(reader)
          branch = self._read_i32(reader) if is_child else None
          name = self._read_str(reader)
          is_enabled = self._read_bool(reader)

          param_count = self.trigger_data.arg_count(function_type, name)
          parameters = [self._read_parameter(reader, format_version) for _ in range(param_count)]

          children = []
          if format_version >= 7:
               children = [self._read_function(reader, format_version, is_child=True) for _ in range(self._read_i32(reader))]

          return {
               'type': function_type,
               'branch': branch,
               'name': name,
               'enabled': is_enabled,
               'parameters': parameters,
               'children': children,
          }

     def _read_parameter(self, reader, format_version):
          param_type = self._read_i32(reader)
          value = self._read_str(reader)

          function = self._read_function(reader, format_version, is_child=False) if self._read_bool(reader) else None
          array_indexer = self._read_parameter(reader, format_version) if self._read_bool(reader) else None

          return {'type': param_type, 'value': value, 'function': function, 'array_indexer': array_indexer}

     # ---- write --------------------------------------------------------------------

     def _write(self, writer, data):
          writer.write(BinaryDataType.BYTES, b'WTG!')
          format_version = data['format_version']
          subversion = data.get('subversion')

          if subversion is None:
               self._write_i32(writer, format_version)
               self._write_classic(writer, data, format_version)
          else:
               self._write_i32(writer, subversion)
               self._write_i32(writer, format_version)
               self._write_reforged(writer, data, format_version, subversion)

     def _write_classic(self, writer, data, format_version):
          categories = data.get('categories', [])
          self._write_i32(writer, len(categories))
          for category in categories:
               self._write_category(writer, category, format_version, None)

          self._write_i32(writer, data.get('game_version', 0))

          variables = data.get('variables', [])
          self._write_i32(writer, len(variables))
          for variable in variables:
               self._write_variable(writer, variable, format_version, None)

          triggers = data.get('triggers', [])
          self._write_i32(writer, len(triggers))
          for trigger in triggers:
               self._write_trigger(writer, trigger, format_version, None)

     def _write_reforged(self, writer, data, format_version, subversion):
          item_counts = data.get('item_counts', {})
          deleted_items = data.get('deleted_items', [])
          for item_type in TriggerItemType:
               self._write_i32(writer, item_counts.get(item_type.value, 0))
               entries = [entry for entry in deleted_items if entry['item_type'] == item_type.value]
               self._write_i32(writer, len(entries))
               for entry in entries:
                    self._write_i32(writer, entry['id'])

          self._write_i32(writer, data.get('game_version', 0))

          variables = data.get('variables', [])
          self._write_i32(writer, len(variables))
          for variable in variables:
               self._write_variable(writer, variable, format_version, subversion)

          items = data.get('items', [])
          self._write_i32(writer, len(items))
          for item in items:
               item_type = item['item_type']
               self._write_i32(writer, item_type)
               if item_type in (TriggerItemType.ROOT_CATEGORY.value, TriggerItemType.CATEGORY.value):
                    self._write_category(writer, item, format_version, subversion)
               elif item_type in (TriggerItemType.GUI.value, TriggerItemType.COMMENT.value, TriggerItemType.SCRIPT.value):
                    self._write_trigger(writer, item, format_version, subversion)
               elif item_type == TriggerItemType.VARIABLE.value:
                    self._write_i32(writer, item['id'])
                    self._write_str(writer, item.get('name', ''))
                    self._write_i32(writer, item.get('parent_id', -1))
               else:
                    raise ValueError(f"Unsupported trigger item type: {item_type}")

     def _write_category(self, writer, item, format_version, subversion):
          self._write_i32(writer, item['id'])
          self._write_str(writer, item.get('name', ''))
          if format_version >= 7:
               self._write_bool(writer, item.get('is_comment', False))
          if subversion is not None:
               self._write_bool(writer, item.get('is_expanded', True))
               self._write_i32(writer, item.get('parent_id', -1))

     def _write_variable(self, writer, item, format_version, subversion):
          self._write_str(writer, item.get('name', ''))
          self._write_str(writer, item.get('type', ''))
          self._write_i32(writer, item.get('unknown', 1))
          self._write_bool(writer, item.get('is_array', False))
          if format_version >= 7:
               self._write_i32(writer, item.get('array_size', 1))
          self._write_bool(writer, item.get('is_initialized', False))
          self._write_str(writer, item.get('initial_value', ''))
          if subversion is not None:
               self._write_i32(writer, item['id'])
               self._write_i32(writer, item.get('parent_id', -1))

     def _write_trigger(self, writer, item, format_version, subversion):
          self._write_str(writer, item.get('name', ''))
          self._write_str(writer, item.get('description', ''))
          if format_version >= 7:
               self._write_bool(writer, item.get('is_comment', False))
          if subversion is not None:
               self._write_i32(writer, item['id'])
          self._write_bool(writer, item.get('is_enabled', True))
          self._write_bool(writer, item.get('is_custom_text', False))
          self._write_bool(writer, not item.get('is_initially_on', True))
          self._write_bool(writer, item.get('run_on_map_init', True))
          self._write_i32(writer, item.get('parent_id', -1))

          functions = item.get('functions', [])
          self._write_i32(writer, len(functions))
          for function in functions:
               self._write_function(writer, function, format_version)

     def _write_function(self, writer, function, format_version):
          self._write_i32(writer, function['type'])
          if function.get('branch') is not None:
               self._write_i32(writer, function['branch'])

          self._write_str(writer, function.get('name', ''))
          self._write_bool(writer, function.get('enabled', True))

          for parameter in function.get('parameters', []):
               self._write_parameter(writer, parameter, format_version)

          if format_version >= 7:
               children = function.get('children', [])
               self._write_i32(writer, len(children))
               for child in children:
                    self._write_function(writer, child, format_version)

     def _write_parameter(self, writer, parameter, format_version):
          self._write_i32(writer, parameter['type'])
          self._write_str(writer, parameter.get('value', ''))

          function = parameter.get('function')
          self._write_bool(writer, function is not None)
          if function is not None:
               self._write_function(writer, function, format_version)

          array_indexer = parameter.get('array_indexer')
          self._write_bool(writer, array_indexer is not None)
          if array_indexer is not None:
               self._write_parameter(writer, array_indexer, format_version)
