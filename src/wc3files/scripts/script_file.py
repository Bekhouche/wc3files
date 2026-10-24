from .data import ScriptFileType
from .jass_parser import parse as parse_jass
from .jass_printer import print_program as print_jass
from .lua_parser import parse as parse_lua
from .lua_printer import print_chunk as print_lua
from .checker import check_program
from . import ir


class ScriptFile:
     """Reader/writer for Warcraft III map scripts (.j / .lua). `get_data()`
     returns an `ir.Program` for JASS, an `ir.Chunk` for Lua - the two
     frontends share most expression/control-flow node types (see ir.py) but
     have different top-level shapes, so callers that need to tell them apart
     can check `get_file_type()` or the returned object's type directly.
     `validate()`/`write(path, validate=True)` are JASS-only (no native/BJ
     signature checker exists for Lua - Lua has no natives concept - so
     `validate=True` against a `.lua` path raises rather than silently
     skipping the check)."""

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = ir.Program()
          self._file_type = None

     def get_data(self):
          return self._data

     def import_data(self, program):
          if not isinstance(program, (ir.Program, ir.Chunk)):
               raise TypeError('program must be an ir.Program (JASS) or ir.Chunk (Lua)')
          self._data = program
          self._file_type = ScriptFileType.LUA if isinstance(program, ir.Chunk) else ScriptFileType.JASS
          return self

     def read(self, file_path):
          if isinstance(file_path, (bytes, bytearray)):
               # utf-8-sig drops a leading byte-order mark if there is one
               # and behaves as plain utf-8 otherwise. Real map scripts are
               # saved with a BOM often enough to matter: one in the
               # benchmark corpus failed at line 1, column 1 on U+FEFF.
               source = bytes(file_path).decode('utf-8-sig', errors='replace')
               self._data = parse_jass(source)
               self._file_type = ScriptFileType.JASS
               return self

          if not isinstance(file_path, str):
               raise TypeError('file_path must be a path string or bytes')

          ext = file_path.rsplit('.', 1)[-1].lower()
          if ext not in (item.value for item in ScriptFileType):
               raise ValueError(f'Invalid script file type: {ext}')

          # newline='' disables universal-newline translation. Real map scripts
          # turn up carrying runs of bare CR (one real map's script has
          # 2,304,396 of them against 14,867 real lines), and translating each
          # one into a line break inflates every reported line number past
          # uselessness. The lexer already treats CR as whitespace, so leaving
          # them alone costs nothing and keeps error locations true to the file.
          with open(file_path, 'r', encoding='utf-8-sig', errors='replace',
                    newline='') as fh:
               source = fh.read()

          if ext == ScriptFileType.LUA.value:
               self._data = parse_lua(source)
               self._file_type = ScriptFileType.LUA
               return self

          self._data = parse_jass(source)
          self._file_type = ScriptFileType.JASS
          return self

     def write(self, file_path, validate=False):
          ext = file_path.rsplit('.', 1)[-1].lower()
          if ext not in (item.value for item in ScriptFileType):
               raise ValueError(f'Invalid script file type: {ext}')

          if ext == ScriptFileType.LUA.value:
               if validate:
                    raise NotImplementedError(
                         'validate=True is not supported for Lua scripts - no native/BJ '
                         'signature checker exists for Lua (Lua has no natives concept)')
               text = print_lua(self._data)
               with open(file_path, 'w', encoding='utf-8', newline='\n') as fh:
                    fh.write(text)
               self._file_type = ScriptFileType.LUA
               return self

          if validate:
               issues = [f for f in self.validate() if f['severity'] == 'issue']
               if issues:
                    messages = '; '.join(f['message'] for f in issues)
                    raise ValueError(f'Script failed validation: {messages}')

          text = print_jass(self._data)
          with open(file_path, 'w', encoding='utf-8', newline='\n') as fh:
               fh.write(text)
          self._file_type = ScriptFileType.JASS
          return self

     def validate(self, natives=None):
          """Report mode - see checker.check_program for exactly what this does
          and does not check. Returns a list of {'severity', 'message'} findings;
          never raises on its own (see write(validate=True) for the raising form)."""
          return check_program(self._data, natives=natives)

     def get_file_type(self):
          return self._file_type
