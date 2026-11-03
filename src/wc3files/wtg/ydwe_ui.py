"""
GUI trigger definitions in YDWE's "ui" folder format, and back to the World
Editor's own TriggerData.txt / TriggerStrings.txt: w3x2lni's ui-builder.

An extended editor (YDWE, and the plugins built on it) keeps its GUI
functions as a folder:

     ui/
          define.txt       the TriggerData.txt sections that define types and
                           categories (TriggerCategories, TriggerTypes, ...)
          event.txt        one section per GUI function, in Lni syntax:
          condition.txt
          action.txt            [UnitDamageTarget]
          call.txt              title = "Damage Target"
                                description = "${Unit} damages ${Target}"
                                category = TC_UNIT
                                [[.args]]
                                type = unit
          zhCN/            optional: the same files with one language's text,
                           over the base ones

`read_trigger_data` / `write_trigger_data` convert the World Editor's two
files, `read_ui` / `write_ui` the folder, and `merge` combines several
definitions (a base and its plugins), a later one replacing a function or
type of the same name.

Nothing is dropped, unlike w3x2lni's reader: each function's version
number and its AI flags (`_UseWithAI`, `_AIDefaults`) are kept, as
`version`, `use_with_ai` and `ai_defaults`. In a description, an argument
`~Unit` of TriggerStrings.txt is `${Unit}` in the folder.
"""

import os
import re

KINDS = ('event', 'condition', 'action', 'call')
_DATA_SECTIONS = {'TriggerEvents': 'event', 'TriggerConditions': 'condition',
                  'TriggerActions': 'action', 'TriggerCalls': 'call'}
_STRING_SECTIONS = {'TriggerEventStrings': 'event', 'TriggerConditionStrings': 'condition',
                    'TriggerActionStrings': 'action', 'TriggerCallStrings': 'call'}
DEFINE_SECTIONS = ('TriggerCategories', 'TriggerTypes', 'TriggerTypeDefaults',
                   'TriggerParams', 'DefaultTriggerCategories', 'DefaultTriggers',
                   'AIFunctionStrings')


class UiDefinitions:
     """`define`: {section: [(key, value), ...]}; `functions`: {kind: {name:
     entry}} in file order, an entry being a dict with name, title,
     description, comment, script_name, category, returns, use_in_event,
     version, use_with_ai, ai_defaults and args ([{type, default, min, max}])."""

     def __init__(self):
          self.define = {}
          self.functions = {kind: {} for kind in KINDS}

     def __eq__(self, other):
          return isinstance(other, UiDefinitions) and self.define == other.define and \
               self.functions == other.functions

     def entry(self, kind, name):
          return self.functions[kind].setdefault(name, {'name': name})


# -- TriggerData.txt / TriggerStrings.txt ------------------------------------

def _split(text, sep=','):
     """Split on `sep` outside double quotes."""
     out, start, quoted = [], 0, False
     for i, c in enumerate(text):
          if c == '"':
               quoted = not quoted
          elif c == sep and not quoted:
               out.append(text[start:i])
               start = i + 1
     out.append(text[start:])
     return out


def _unquote(text):
     text = text.strip()
     return text[1:-1] if len(text) >= 2 and text[0] == '"' and text[-1] == '"' else text


def _lines(text):
     section = None
     for raw in text.splitlines():
          line = raw.strip()
          if not line or line.startswith('//'):
               continue
          if line.startswith('[') and line.endswith(']'):
               section = line[1:-1]
               continue
          if '=' in line and section is not None:
               key, _, value = line.partition('=')
               yield section, key, value


def read_trigger_data(data_text, strings_text=''):
     """UiDefinitions from TriggerData.txt (and TriggerStrings.txt)."""
     ui = UiDefinitions()
     suffixes = (('_Defaults', 'defaults'), ('_Limits', 'limits'), ('_Category', 'category'),
                 ('_ScriptName', 'script_name'), ('_UseWithAI', 'use_with_ai'),
                 ('_AIDefaults', 'ai_defaults'))
     for section, key, value in _lines(data_text):
          kind = _DATA_SECTIONS.get(section)
          if kind is None:
               if section in DEFINE_SECTIONS:
                    ui.define.setdefault(section, []).append((key, value))
               continue
          if not key.startswith('_'):
               parts = _split(value)
               entry = ui.entry(kind, key)
               entry['version'] = parts[0]
               if kind == 'call':
                    entry['use_in_event'] = parts[1] if len(parts) > 1 else '1'
                    entry['returns'] = parts[2] if len(parts) > 2 else None
                    types = parts[3:]
               else:
                    types = parts[1:]
               entry['args'] = [{'type': t} for t in types if t != 'nothing'] \
                    if types != ['nothing'] else []
               if types == ['nothing']:
                    entry['no_args'] = True
               continue
          for suffix, field in suffixes:
               if key.endswith(suffix):
                    name = key[1:-len(suffix)]
                    entry = ui.entry(kind, name)
                    if field in ('defaults', 'limits') and 'args' not in entry:
                         # a stray line with no declaration to attach to: kept as it is
                         entry[f'raw_{field}'] = value
                    elif field == 'defaults':
                         for arg, d in zip(entry.get('args', []), _split(value)):
                              if d != '_':
                                   arg['default'] = d
                    elif field == 'limits':
                         limits = _split(value)
                         for i, arg in enumerate(entry.get('args', [])):
                              low = limits[2 * i] if 2 * i < len(limits) else '_'
                              high = limits[2 * i + 1] if 2 * i + 1 < len(limits) else '_'
                              if low != '_':
                                   arg['min'] = low
                              if high != '_':
                                   arg['max'] = high
                    else:
                         entry[field] = value
                    break
     for section, key, value in _lines(strings_text or ''):
          kind = _STRING_SECTIONS.get(section)
          if kind is None:
               continue
          if key.endswith('Hint'):
               ui.entry(kind, key[:-4])['comment'] = _unquote(value)
               continue
          entry = ui.entry(kind, key)
          if 'title' not in entry:
               entry['title'] = _unquote(value)
          elif 'description' not in entry:
               pieces = []
               for part in _split(value):
                    part = part.strip()
                    if part.startswith('~'):
                         pieces.append('${' + part[1:] + '}')
                    else:
                         pieces.append(_unquote(part))
               entry['description'] = ''.join(pieces)
     return ui


def write_trigger_data(ui):
     """(TriggerData.txt, TriggerStrings.txt) text for UiDefinitions."""
     data = []
     for section in DEFINE_SECTIONS:
          if section in ui.define:
               data.append(f'[{section}]')
               data.extend(f'{k}={v}' for k, v in ui.define[section])
               data.append('')
     strings = []
     for section, kind in _DATA_SECTIONS.items():
          data.append(f'[{section}]')
          for name, entry in ui.functions[kind].items():
               args = entry.get('args', [])
               types = [a['type'] for a in args] or (['nothing'] if entry.get('no_args') else [])
               if 'version' in entry:       # a stray key's entry has no declaration
                    head = [entry['version']]
                    if kind == 'call':
                         head += [entry.get('use_in_event', '1'), entry.get('returns') or 'nothing']
                    data.append(f'{name}=' + ','.join(head + types))
               for field, suffix in (('raw_defaults', '_Defaults'), ('raw_limits', '_Limits')):
                    if field in entry:
                         data.append(f'_{name}{suffix}={entry[field]}')
               if any('default' in a for a in args):
                    data.append(f'_{name}_Defaults=' + ','.join(a.get('default', '_') for a in args))
               if any('min' in a or 'max' in a for a in args):
                    data.append(f'_{name}_Limits=' + ','.join(
                         f"{a.get('min', '_')},{a.get('max', '_')}" for a in args))
               for field, suffix in (('category', '_Category'), ('script_name', '_ScriptName'),
                                     ('use_with_ai', '_UseWithAI'), ('ai_defaults', '_AIDefaults')):
                    if entry.get(field) is not None:
                         data.append(f'_{name}{suffix}={entry[field]}')
          data.append('')
     for section, kind in _STRING_SECTIONS.items():
          strings.append(f'[{section}]')
          for name, entry in ui.functions[kind].items():
               if 'title' in entry:
                    strings.append(f'{name}="{entry["title"]}"')
               if 'description' in entry:
                    strings.append(f'{name}=' + _description_out(entry['description']))
               if 'comment' in entry:
                    strings.append(f'{name}Hint="{entry["comment"]}"')
          strings.append('')
     return '\n'.join(data) + '\n', '\n'.join(strings) + '\n'


def _description_out(text):
     parts = []
     for piece in re.split(r'(\$\{[^}]*\})', text):
          if piece.startswith('${') and piece.endswith('}'):
               parts.append('~' + piece[2:-1])
          elif piece:
               parts.append(f'"{piece}"')
     return ','.join(parts) if parts else '""'


# -- the ui folder ----------------------------------------------------------------

def _quote(text):
     return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def _section(name):
     return f'[{_quote(name)}]' if re.search(r'[\s.:\[\]"]', name) else f'[{name}]'


def write_ui(ui, folder):
     """Write the ui folder (define.txt, event.txt, ...)."""
     os.makedirs(folder, exist_ok=True)
     define = []
     for section in DEFINE_SECTIONS:
          if section in ui.define:
               define.append(f'[{section}]')
               define.extend(f'{k}={v}' for k, v in ui.define[section])
               define.append('')
     with open(os.path.join(folder, 'define.txt'), 'w', encoding='utf-8', newline='\n') as h:
          h.write('\n'.join(define))
     for kind in KINDS:
          out = []
          for name, entry in ui.functions[kind].items():
               out.append(_section(name))
               for field in ('title', 'description', 'comment'):
                    if field in entry:
                         out.append(f'{field} = {_quote(entry[field])}')
               for field in ('script_name', 'category', 'version', 'use_with_ai',
                             'ai_defaults', 'returns', 'raw_defaults', 'raw_limits'):
                    if entry.get(field) is not None:
                         out.append(f'{field} = {_quote(str(entry[field]))}')
               if kind == 'call' and entry.get('use_in_event', '1') != '1':
                    out.append(f'use_in_event = {entry["use_in_event"]}')
               if entry.get('no_args'):
                    out.append('no_args = true')
               for arg in entry.get('args', []):
                    out.append('[[.args]]')
                    for field in ('type', 'default', 'min', 'max'):
                         if field in arg:
                              out.append(f'{field} = {_quote(str(arg[field]))}')
               out.append('')
          with open(os.path.join(folder, f'{kind}.txt'), 'w', encoding='utf-8',
                    newline='\n') as h:
               h.write('\n'.join(out))


def _parse_value(text):
     text = text.strip()
     if text.startswith('"'):
          out, i = [], 1
          while i < len(text) and text[i] != '"':
               if text[i] == '\\' and i + 1 < len(text):
                    i += 1
                    out.append({'n': '\n', 't': '\t'}.get(text[i], text[i]))
               else:
                    out.append(text[i])
               i += 1
          return ''.join(out)
     return text


def _read_entries(text):
     entries = []
     current = arg = None
     for raw in text.splitlines():
          line = raw.strip()
          if not line or line.startswith('--') or line.startswith('//'):
               continue
          if line == '[[.args]]':
               if current is None:
                    continue
               arg = {}
               current.setdefault('args', []).append(arg)
               continue
          if line.startswith('[') and line.endswith(']'):
               name = _parse_value(line[1:-1])
               current = {'name': name}
               arg = None
               entries.append(current)
               continue
          if '=' in line and current is not None:
               key, _, value = line.partition('=')
               key = key.strip()
               value = _parse_value(value)
               if arg is not None and key in ('type', 'default', 'min', 'max'):
                    arg[key] = value
               elif key == 'no_args':
                    current['no_args'] = value == 'true'
               else:
                    current[key] = value
     return entries


def read_ui(folder, lang=None):
     """UiDefinitions from a ui folder; `lang` names a language subfolder
     whose text replaces the base text."""
     ui = UiDefinitions()

     def read(name):
          path = os.path.join(folder, name)
          if not os.path.isfile(path):
               return ''
          with open(path, encoding='utf-8-sig', errors='replace') as h:
               return h.read()
     for define_text in (read('define.txt'), read(os.path.join(lang, 'define.txt')) if lang else ''):
          for section, key, value in _lines(define_text):
               if section in DEFINE_SECTIONS:
                    ui.define.setdefault(section, []).append((key, value))
     for kind in KINDS:
          for entry in _read_entries(read(f'{kind}.txt')):
               if 'version' in entry:       # declared (not a stray key's entry)
                    if kind == 'call':
                         entry.setdefault('use_in_event', '1')
                    entry.setdefault('args', [])
               ui.functions[kind][entry['name']] = entry
          if lang:
               for local in _read_entries(read(os.path.join(lang, f'{kind}.txt'))):
                    target = ui.functions[kind].get(local['name'])
                    if target is not None:
                         target.update({k: v for k, v in local.items()
                                        if k in ('title', 'description', 'comment')})
     return ui


def merge(*definitions):
     """One UiDefinitions from several (a base, then plugins): a later
     function or define key of the same name replaces the earlier."""
     out = UiDefinitions()
     for ui in definitions:
          for section, items in ui.define.items():
               current = out.define.setdefault(section, [])
               keys = {k: i for i, (k, _) in enumerate(current)}
               for key, value in items:
                    if key in keys:
                         current[keys[key]] = (key, value)
                    else:
                         keys[key] = len(current)
                         current.append((key, value))
          for kind in KINDS:
               for name, entry in ui.functions[kind].items():
                    out.functions[kind][name] = dict(entry)
     return out
