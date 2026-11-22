"""
Object data as Lni text: one `[ID]` section per object, the way w3x2lni
writes it, so a map's objects can be read, diffed and edited as text.

     [h000]
     _parent = 'hfoo'
     -- unam: Name
     Name = 'Footman Captain'
     -- Hbz1: DataA
     DataA = {10.0, 20.0, 30.0}

1. A custom object has `_parent`, the object it was made from; a changed
   standard object has none.
2. Only what the map changes is written. A field is named by its SLK column
   (`Name`, `DataA`, `Buttonpos_1`) when that name means one field for the
   object's base; otherwise (ability data shared by many abilities, a field
   the metadata does not know) by its four-letter ID, so nothing is ever
   ambiguous.
3. A field with a value per level is a table: `{1, 2, 3}` when levels 1..n
   are all set, `{[2] = 5}` otherwise. Level 0 is written `[0]`.
4. Values are Lua literals: integers, reals with a decimal point, and
   strings in single quotes (`[=[ ]=]` for text with line breaks).
5. What the binary file carries beyond that is written only when it is not
   the usual value: a field's stored type (`-- type`), data pointer and
   end marker, as `Key__type`, `Key__ptr`, `Key__end`.

`objects_to_lni(data, ext)` and `lni_to_objects(text, ext)` convert one
object file's data (ObjectFile.export_data()[ext]).
"""

import re

_TYPES = {'int': 0, 'bool': 0, 'real': 1, 'unreal': 2}
_LEVELED = ('w3a', 'w3d', 'w3q')
_IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_BARE_KEY = re.compile(r'^[A-Za-z0-9_]+$')
_ESCAPE_OUT = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}


class LniError(ValueError):
     pass


def _fields(metadata, ext):
     from wc3files.w3o.data import ObjectFileType
     return metadata.fields(ObjectFileType(ext))


# --- field names --------------------------------------------------------------

def _key_of(row):
     key = row['field'] or row['ID']
     if row.get('data'):
          key += chr(ord('A') + int(row['data']) - 1)
     return key


def _applies(row, base):
     specific = row.get('useSpecific')
     if specific:
          return base in str(specific).split(',')
     not_specific = row.get('notSpecific')
     if not_specific and base in str(not_specific).split(','):
          return False
     return True


class _Names:
     """Field id <-> Lni key for one object type."""

     def __init__(self, fields):
          self.fields = fields
          self.by_key = {}
          index_count = {}
          for row in fields.values():
               index_count[_key_of(row)] = max(index_count.get(_key_of(row), 0),
                                               int(row.get('index') or 0))
          for field_id, row in fields.items():
               key = _key_of(row)
               if index_count.get(key, 0) > 0 or int(row.get('index') or 0) > 0:
                    key = f'{key}_{int(row.get("index") or 0) + 1}'
               row_key = key
               self.by_key.setdefault(row_key, []).append(field_id)
          self.key_of_id = {}
          for key, ids in self.by_key.items():
               for field_id in ids:
                    self.key_of_id[field_id] = key

     def key(self, field_id, base):
          key = self.key_of_id.get(field_id)
          if key is None or not _IDENT.match(key):
               return field_id
          candidates = [i for i in self.by_key[key] if _applies(self.fields[i], base)]
          if candidates == [field_id]:
               return key
          return field_id

     def field_id(self, key, base):
          if key in self.fields:
               return key
          ids = self.by_key.get(key)
          if not ids and len(key) == 4:
               return key                 # a field the metadata does not know
          if not ids:
               raise LniError(f"unknown field '{key}'")
          candidates = [i for i in ids if _applies(self.fields[i], base)]
          if len(candidates) != 1:
               raise LniError(f"field '{key}' is ambiguous for '{base}': "
                              f"write its id ({', '.join(candidates or ids)})")
          return candidates[0]

     def default_type(self, field_id):
          row = self.fields.get(field_id)
          if row is None:
               return None
          return _TYPES.get(row.get('type'), 3)


# --- writing --------------------------------------------------------------------

def _literal(value, kind):
     if kind == 3 or isinstance(value, str):
          text = str(value)
          controls = {c for c in text if ord(c) < 32 or ord(c) == 127}
          if '\n' in text and controls <= {'\n', '\t'} and not text.startswith('\n'):
               # readable multi-line text; a \r is never written raw, as git
               # and text-mode reads would turn \r\n into \n
               level = 0
               while f']{"=" * level}]' in text:
                    level += 1
               return f'[{"=" * level}[{text}]{"=" * level}]'
          out = []
          for c in text:
               if c == '\\':
                    out.append('\\\\')
               elif c == "'":
                    out.append("\\'")
               elif c in _ESCAPE_OUT:
                    out.append(_ESCAPE_OUT[c])
               elif ord(c) < 32 or ord(c) == 127:
                    out.append(f'\\{ord(c):03d}')
               else:
                    out.append(c)
          return "'" + ''.join(out) + "'"
     if kind in (1, 2) or isinstance(value, float):
          # (a float under an integer type is kept: real maps have them)
          return repr(float(value))
     return str(int(value))


def _section_name(object_id):
     if _IDENT.match(object_id) or re.match(r'^[A-Za-z0-9_]{4}$', object_id):
          return f'[{object_id}]'
     return '[' + _literal(object_id, 3).replace("'", '"', 1)[:-1] + '"]'


def objects_to_lni(data, ext, metadata=None):
     """Lni text for one object file's data: {'original': {...}, 'custom': {...}}."""
     from wc3files.w3o.object_metadata import ObjectMetaData
     metadata = metadata or ObjectMetaData.default()
     names = _Names(_fields(metadata, ext))
     leveled = ext in _LEVELED
     out = []
     for table in ('original', 'custom'):
          objects = (data or {}).get(table) or {}
          for object_id, mods in objects.items():
               base = mods.get('ID', object_id) if table == 'custom' else object_id
               out.append(_section_name(object_id))
               if table == 'custom':
                    out.append(f"_parent = {_literal(mods.get('ID', ''), 3)}")
               for field_id, mod in mods.items():
                    if field_id == 'ID':
                         continue
                    out.extend(_field_lines(names, field_id, mod, base, table, object_id,
                                            leveled))
               out.append('')
     return '\n'.join(out)


def _field_lines(names, field_id, mod, base, table, object_id, leveled):
     key = names.key(field_id, base)
     kind = mod.get('type')
     lines = [f'-- {field_id}: {names.key_of_id.get(field_id, "?")}']
     default_end = object_id if table == 'custom' else '\0\0\0\0'
     if isinstance(mod.get('value'), dict) and list(mod['value']) == [0]:
          # a plain field in a file with level data: stored as level 0
          lines.append(f'{key} = {_literal(mod["value"][0], kind)}')
          ptr = (mod.get('ptr') or {}).get(0, 0)
          default_ptr = names.fields.get(field_id, {}).get('data') or 0
          if int(ptr) != int(default_ptr):
               lines.append(f'{key}__ptr = {int(ptr)}')
          end = (mod.get('end') or {}).get(0, default_end)
          if end != default_end:
               lines.append(f'{key}__end = {_literal(end, 3)}')
     elif isinstance(mod.get('value'), dict):
          values = mod['value']
          levels = sorted(values, key=int)
          if [int(l) for l in levels] == list(range(1, len(levels) + 1)):
               body = ', '.join(_literal(values[l], kind) for l in levels)
          else:
               body = ', '.join(f'[{int(l)}] = {_literal(values[l], kind)}' for l in levels)
          lines.append(f'{key} = {{{body}}}')
          ptrs = mod.get('ptr') or {}
          default_ptr = names.fields.get(field_id, {}).get('data') or 0
          odd = {l: p for l, p in ptrs.items() if int(p) != int(default_ptr)}
          if odd:
               lines.append(f'{key}__ptr = {{{", ".join(f"[{int(l)}] = {int(p)}" for l, p in odd.items())}}}')
          ends = mod.get('end')
          if isinstance(ends, dict):
               odd_ends = {l: e for l, e in ends.items() if e != default_end}
               if odd_ends:
                    lines.append(f'{key}__end = {{{", ".join(f"[{int(l)}] = {_literal(e, 3)}" for l, e in odd_ends.items())}}}')
          elif ends is not None and ends != default_end:
               lines.append(f'{key}__end = {_literal(ends, 3)}')
     else:
          lines.append(f'{key} = {_literal(mod.get("value"), kind)}')
          end = mod.get('end')
          if end is not None and end != default_end:
               lines.append(f'{key}__end = {_literal(end, 3)}')
          if leveled and 'level' in mod:
               lines.append(f'{key}__level = {int(mod["level"])}')
          if 'ptr' in mod and not isinstance(mod['ptr'], dict):
               lines.append(f'{key}__ptr = {int(mod["ptr"])}')
     if kind != names.default_type(field_id):
          lines.append(f'{key}__type = {int(kind)}')
     if not _BARE_KEY.match(key):
          # a field id no bare key can hold (real maps have ids with NULs)
          quoted = []
          for line in lines[1:]:
               lhs, _, rhs = line.partition(' = ')
               quoted.append(f'[{_literal(lhs, 3)}] = {rhs}')
          lines = [_comment(lines[0])] + quoted
     return lines


def _comment(text):
     return ''.join(c if 32 <= ord(c) < 127 else f'\\x{ord(c):02x}' for c in text)


# --- reading --------------------------------------------------------------------

class _Reader:
     def __init__(self, text):
          self.text = text
          self.pos = 0
          self.line = 1

     def error(self, message):
          return LniError(f'line {self.line}: {message}')

     def skip_space(self, newlines=True):
          text = self.text
          while self.pos < len(text):
               c = text[self.pos]
               if c == '\n':
                    if not newlines:
                         return
                    self.line += 1
                    self.pos += 1
               elif c in ' \t\r':
                    self.pos += 1
               elif text.startswith('--', self.pos):
                    end = text.find('\n', self.pos)
                    self.pos = len(text) if end < 0 else end
               else:
                    return

     def at(self, s):
          return self.text.startswith(s, self.pos)

     def value(self):
          self.skip_space()
          text = self.text
          if self.at('{'):
               return self.table()
          m = re.compile(r'\[(=*)\[').match(text, self.pos)
          if m:
               closer = f']{m.group(1)}]'
               end = text.find(closer, m.end())
               if end < 0:
                    raise self.error('unfinished long string')
               value = text[m.end():end]
               self.line += value.count('\n')
               self.pos = end + len(closer)
               return value
          if self.at("'") or self.at('"'):
               return self.string()
          m = re.compile(r'[-+]?(\d+\.\d*([eE][-+]?\d+)?|\d+[eE][-+]?\d+|\.\d+|inf|nan)').match(text, self.pos)
          if m and ('.' in m.group(0) or 'e' in m.group(0).lower() or 'inf' in m.group(0) or 'nan' in m.group(0)):
               self.pos = m.end()
               return float(m.group(0))
          m = re.compile(r'[-+]?\d+').match(text, self.pos)
          if m:
               self.pos = m.end()
               return int(m.group(0))
          raise self.error('expected a value')

     def string(self):
          quote = self.text[self.pos]
          self.pos += 1
          out = []
          text = self.text
          while True:
               if self.pos >= len(text) or text[self.pos] == '\n':
                    raise self.error('unfinished string')
               c = text[self.pos]
               if c == quote:
                    self.pos += 1
                    return ''.join(out)
               if c == '\\':
                    self.pos += 1
                    e = text[self.pos]
                    m = re.compile(r'\d{1,3}').match(text, self.pos)
                    if m:
                         out.append(chr(int(m.group(0))))
                         self.pos = m.end()
                         continue
                    out.append({'n': '\n', 'r': '\r', 't': '\t'}.get(e, e))
                    self.pos += 1
                    continue
               out.append(c)
               self.pos += 1

     def table(self):
          self.pos += 1
          items = {}
          index = 1
          while True:
               self.skip_space()
               if self.at('}'):
                    self.pos += 1
                    return items
               m = re.compile(r'\[\s*(-?\d+)\s*\]\s*=').match(self.text, self.pos)
               if m:
                    self.pos = m.end()
                    items[int(m.group(1))] = self.value()
               else:
                    items[index] = self.value()
                    index += 1
               self.skip_space()
               if self.at(','):
                    self.pos += 1
               elif not self.at('}'):
                    raise self.error("expected ',' or '}'")


def _parse(text):
     """[(object_id, [(key, value), ...]), ...] in file order."""
     reader = _Reader(text)
     sections = []
     current = None
     while True:
          reader.skip_space()
          if reader.pos >= len(text):
               return sections
          if reader.at("['") or reader.at('["'):
               reader.pos += 1
               name = reader.string()
               reader.skip_space(False)
               if not reader.at(']'):
                    raise reader.error("expected ']'")
               reader.pos += 1
               reader.skip_space(False)
               if reader.at('=') and current is not None:
                    reader.pos += 1           # a quoted key: ["id"] = value
                    current[1].append((name, reader.value()))
                    continue
               current = (name, [])
               sections.append(current)
               continue
          if reader.at('['):
               end = text.find(']', reader.pos)
               if False:
                    pass
               else:
                    name = text[reader.pos + 1:end]
                    reader.pos = end + 1
               current = (name, [])
               sections.append(current)
               continue
          m = re.compile(r'([A-Za-z0-9_]+)\s*=').match(text, reader.pos)
          if not m or current is None:
               raise reader.error('expected [object] or key = value')
          reader.pos = m.end()
          current[1].append((m.group(1), reader.value()))


def lni_to_objects(text, ext, metadata=None):
     """The object data ({'original': ..., 'custom': ...}) an Lni text describes."""
     from wc3files.w3o.object_metadata import ObjectMetaData
     metadata = metadata or ObjectMetaData.default()
     names = _Names(_fields(metadata, ext))
     result = {'original': {}, 'custom': {}}
     for object_id, entries in _parse(text):
          values = dict(entries)
          parent = values.pop('_parent', None)
          table = 'custom' if parent is not None else 'original'
          base = parent if parent is not None else object_id
          mods = {'ID': parent} if parent is not None else {}
          extras = {}
          plain = []
          for key, value in entries:
               if key == '_parent':
                    continue
               if '__' in key:
                    head, _, attr = key.rpartition('__')
                    extras.setdefault(head, {})[attr] = value
               else:
                    plain.append((key, value))
          default_end = object_id if table == 'custom' else '\0\0\0\0'
          for key, value in plain:
               field_id = names.field_id(key, base)
               extra = extras.get(key, {})
               kind = extra.get('type', names.default_type(field_id))
               if kind is None:
                    kind = 3 if isinstance(value, str) else (1 if isinstance(value, float) else 0)
               if not isinstance(value, dict) and ext in _LEVELED:
                    # a plain field of a levelled file is its level 0
                    value = {0: value}
                    if 'ptr' in extra and not isinstance(extra['ptr'], dict):
                         extra = dict(extra, ptr={0: extra['ptr']})
                    if 'end' in extra and not isinstance(extra['end'], dict):
                         extra = dict(extra, end={0: extra['end']})
               if isinstance(value, dict):
                    levels = {k: _typed(v, kind) for k, v in value.items()}
                    default_ptr = int(names.fields.get(field_id, {}).get('data') or 0)
                    ptrs = {k: default_ptr for k in levels}
                    ptrs.update(extra.get('ptr', {}) if isinstance(extra.get('ptr'), dict) else {})
                    ends = {k: default_end for k in levels}
                    if isinstance(extra.get('end'), dict):
                         ends.update(extra['end'])
                    mod = {'value': levels, 'type': kind, 'ptr': ptrs, 'end': ends}
               else:
                    mod = {'value': _typed(value, kind), 'type': kind,
                           'end': extra.get('end', default_end)}
                    if 'level' in extra:
                         mod['level'] = extra['level']
                    if 'ptr' in extra:
                         mod['ptr'] = extra['ptr']
               mods[field_id] = mod
          result[table][object_id] = mods
     return result


def _typed(value, kind):
     if kind == 3:
          return value if isinstance(value, str) else str(value)
     if kind in (1, 2) or isinstance(value, float):
          return float(value)
     return int(value)
