"""
JASS to Lua, the way Reforged runs JASS-era code under Lua.

`jass_to_lua(program)` turns a parsed ir.Program (JASS) into Lua 5.3 source.
It is what the game itself needs to run blizzard.j under a Lua map script,
and what a map maker needs to move a JASS map to Lua.

The translation keeps JASS's meaning where Lua differs:

1. **Integer division** truncates toward zero in JASS; Lua's `//` floors. An
   integer `/` becomes `__jdiv(a, b)`, a helper the prelude defines.
2. **String `+`** becomes `..`; a null string concatenates as empty, as in
   JASS, through `__jconcat`.
3. **Arrays** become `__jarray(default)` (the Reforged helper: a table that
   reads a missing index as the type's default), handle arrays plain tables.
4. **Loops** become `while true do ... end`, `exitwhen c` an `if c then break
   end`.
5. **Rawcodes** become integers, **null** becomes `nil`, a function
   reference `function F` becomes `F`.
6. **Integer locals** start at 0 and reals at 0.0 when not initialized, so an
   uninitialized local reads as the game reads it, not as nil.

Types are taken from the declarations (locals, parameters, globals,
function and native return types) to tell integer from real and string `+`
from numeric `+`. `natives` gives the natives' signatures (common.j's by
default).
"""

from . import ir
from .lua_printer import _escape_lua_string

PRELUDE = """\
-- helpers for code translated from JASS
function __jdiv(a, b)
     local q = a // b
     if q < 0 and q * b ~= a then q = q + 1 end
     return q
end
function __jconcat(a, b)
     return (a or "") .. (b or "")
end
if __jarray == nil then
     function __jarray(default)
          return setmetatable({}, {__index = function() return default end})
     end
end
"""

_DEFAULTS = {'integer': '0', 'real': '0.0', 'boolean': 'false', 'string': '""'}
_LUA_KEYWORDS = {
     'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function', 'goto',
     'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true',
     'until', 'while'}

# Lua's precedence, lowest first, for the operators JASS has
_PRECEDENCE = {'or': 1, 'and': 2, '<': 3, '>': 3, '<=': 3, '>=': 3, '~=': 3, '==': 3,
               '..': 4, '+': 5, '-': 5, '*': 6, '/': 6, '//': 6, 'not': 7, 'neg': 7}


def _unescape(text):
     out = []
     i = 0
     while i < len(text):
          c = text[i]
          if c == '\\' and i + 1 < len(text):
               i += 1
               c = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f',
                    '\\': '\\', '"': '"'}.get(text[i], text[i])
          out.append(c)
          i += 1
     return ''.join(out)


def _rawcode(text):
     value = 0
     for c in _unescape(text):
          value = value * 256 + (ord(c) & 0xFF)
     return value


def _name(name):
     """A JASS name that is a Lua keyword (`end`, `goto`, ...) gets a suffix."""
     return name + '_' if name in _LUA_KEYWORDS else name


class _Translator:

     def __init__(self, program, natives):
          self.program = program
          self.globals = {g.name: g.type for g in program.globals}
          self.functions = {f.name: f.returns for f in program.functions}
          for native in program.natives:
               self.functions[native.name] = native.returns
          self.natives = natives
          self.lines = []
          self.indent = 0

     def returns_of(self, name):
          if name in self.functions:
               return self.functions[name]
          if self.natives is not None:
               decl = self.natives.lookup(name)
               if decl is not None:
                    return decl.returns
          return None

     def global_type(self, name):
          if name in self.globals:
               return self.globals[name]
          if self.natives is not None:
               glob = self.natives.lookup_global(name)
               if glob is not None:
                    return glob.type
          return None

     def emit(self, text):
          self.lines.append('     ' * self.indent + text)

     # -- program -----------------------------------------------------------------

     def program_text(self, prelude=True):
          if prelude:
               self.lines.extend(PRELUDE.rstrip('\n').split('\n'))
          for glob in self.program.globals:
               self.global_var(glob)
          for function in self.program.functions:
               self.function(function)
          return '\n'.join(self.lines) + '\n'

     def global_var(self, glob):
          name = _name(glob.name)
          if glob.is_array:
               self.emit(f'{name} = {self.array_of(glob.type)}')
          elif glob.initial is not None:
               self.locals = {}
               self.emit(f'{name} = {self.expr(glob.initial, glob.type)}')
          elif glob.type in _DEFAULTS:
               self.emit(f'{name} = {_DEFAULTS[glob.type]}')
          else:
               self.emit(f'{name} = nil')

     @staticmethod
     def array_of(type_name):
          if type_name in _DEFAULTS:
               return f'__jarray({_DEFAULTS[type_name]})'
          return '{}'

     def function(self, function):
          self.locals = {p.name: p.type for p in function.params}
          params = ', '.join(_name(p.name) for p in function.params)
          self.emit(f'function {_name(function.name)}({params})')
          self.indent += 1
          self.block(function.body)
          self.indent -= 1
          self.emit('end')
          self.emit('')

     # -- statements ----------------------------------------------------------

     def block(self, statements):
          for stmt in statements:
               self.statement(stmt)

     def statement(self, stmt):
          if isinstance(stmt, ir.Local):
               self.locals[stmt.name] = stmt.type
               name = _name(stmt.name)
               if stmt.is_array:
                    self.emit(f'local {name} = {self.array_of(stmt.type)}')
               elif stmt.initial is not None:
                    self.emit(f'local {name} = {self.expr(stmt.initial, stmt.type)}')
               elif stmt.type in _DEFAULTS and stmt.type != 'string':
                    self.emit(f'local {name} = {_DEFAULTS[stmt.type]}')
               else:
                    self.emit(f'local {name}')
          elif isinstance(stmt, ir.SetStmt):
               kind = self.type_of_name(stmt.target)
               value = self.expr(stmt.value, kind)
               target = _name(stmt.target)
               if stmt.index is not None:
                    target += f'[{self.expr(stmt.index, "integer")}]'
               self.emit(f'{target} = {value}')
          elif isinstance(stmt, ir.CallStmt):
               self.emit(self.call(stmt.name, stmt.args))
          elif isinstance(stmt, ir.IfStmt):
               for position, (condition, body) in enumerate(stmt.branches):
                    word = 'if' if position == 0 else 'elseif'
                    self.emit(f'{word} {self.expr(condition)} then')
                    self.indent += 1
                    self.block(body)
                    self.indent -= 1
               if stmt.else_body is not None:
                    self.emit('else')
                    self.indent += 1
                    self.block(stmt.else_body)
                    self.indent -= 1
               self.emit('end')
          elif isinstance(stmt, ir.LoopStmt):
               self.emit('while true do')
               self.indent += 1
               self.block(stmt.body)
               self.indent -= 1
               self.emit('end')
          elif isinstance(stmt, ir.ExitWhenStmt):
               self.emit(f'if {self.expr(stmt.condition)} then break end')
          elif isinstance(stmt, ir.ReturnStmt):
               if stmt.value is None:
                    # a return in the middle of a block needs its own do-end in Lua
                    self.emit('do return end')
               else:
                    self.emit(f'do return {self.expr(stmt.value)} end')

     # -- expressions ---------------------------------------------------------

     def type_of_name(self, name):
          if name in self.locals:
               return self.locals[name]
          return self.global_type(name)

     def type_of(self, expr):
          if isinstance(expr, ir.Literal):
               return {'int': 'integer', 'rawcode': 'integer', 'real': 'real',
                       'string': 'string', 'bool': 'boolean'}.get(expr.kind)
          if isinstance(expr, (ir.VarExpr, ir.ArrayRefExpr)):
               return self.type_of_name(expr.name)
          if isinstance(expr, ir.CallExpr):
               return self.returns_of(expr.name)
          if isinstance(expr, ir.FunctionRefExpr):
               return 'code'
          if isinstance(expr, ir.UnaryExpr):
               return 'boolean' if expr.op == 'not' else self.type_of(expr.operand)
          if isinstance(expr, ir.BinaryExpr):
               if expr.op in ('and', 'or', '==', '!=', '<', '>', '<=', '>='):
                    return 'boolean'
               left = self.type_of(expr.left)
               right = self.type_of(expr.right)
               if expr.op == '+' and 'string' in (left, right):
                    return 'string'
               if left == 'integer' and right == 'integer':
                    return 'integer'
               return 'real'
          return None

     def call(self, name, args):
          return f'{_name(name)}({", ".join(self.expr(a) for a in args)})'

     def expr(self, expr, expected=None, parent=0):
          text, precedence = self.expr_p(expr)
          if precedence < parent:
               text = f'({text})'
          return text

     def expr_p(self, expr):
          """(text, precedence) - precedence 9 means atomic."""
          if isinstance(expr, ir.Literal):
               kind = expr.kind
               if kind == 'int':
                    return (str(expr.value), 9) if expr.value >= 0 else (f'({expr.value})', 9)
               if kind == 'rawcode':
                    return str(_rawcode(expr.value)), 9
               if kind == 'real':
                    return repr(float(expr.value)), 9
               if kind == 'string':
                    return f'"{_escape_lua_string(_unescape(expr.value))}"', 9
               if kind == 'bool':
                    return ('true' if expr.value else 'false'), 9
               return 'nil', 9
          if isinstance(expr, ir.VarExpr):
               return _name(expr.name), 9
          if isinstance(expr, ir.ArrayRefExpr):
               return f'{_name(expr.name)}[{self.expr(expr.index)}]', 9
          if isinstance(expr, ir.FunctionRefExpr):
               return _name(expr.name), 9
          if isinstance(expr, ir.CallExpr):
               return self.call(expr.name, expr.args), 9
          if isinstance(expr, ir.UnaryExpr):
               if expr.op == 'not':
                    return f'not {self.expr(expr.operand, parent=7)}', 7
               return f'-{self.expr(expr.operand, parent=7)}', 7
          if isinstance(expr, ir.BinaryExpr):
               op = expr.op
               left_type = self.type_of(expr.left)
               right_type = self.type_of(expr.right)
               if op == '+' and 'string' in (left_type, right_type):
                    return f'__jconcat({self.expr(expr.left)}, {self.expr(expr.right)})', 9
               if op == '/' and left_type == 'integer' and right_type == 'integer':
                    return f'__jdiv({self.expr(expr.left)}, {self.expr(expr.right)})', 9
               lua_op = '~=' if op == '!=' else op
               precedence = _PRECEDENCE[lua_op]
               # left-associative: the right operand needs parentheses at equal
               # precedence ("a - (b - c)")
               left = self.expr(expr.left, parent=precedence)
               right = self.expr(expr.right, parent=precedence + 1)
               return f'{left} {lua_op} {right}', precedence
          raise TypeError(f'cannot translate {type(expr).__name__}')


def jass_to_lua(program, natives=None, prelude=True):
     """Lua 5.3 source for a parsed JASS ir.Program. `natives` is a
     checker.NativeSignatures for the natives and common.j globals the program
     uses (the bundled common.j/blizzard.j by default)."""
     if natives is None:
          from .checker import NativeSignatures
          natives = NativeSignatures.default()
     return _Translator(program, natives).program_text(prelude)
