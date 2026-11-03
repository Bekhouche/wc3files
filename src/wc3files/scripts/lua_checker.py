"""
Static checks for a Reforged `war3map.lua` (an ir.Chunk), against the game
API in the bundled common.j/blizzard.j and the Lua 5.3 standard library.

Lua has no declarations, so the checker works from what the script itself
shows:

1. **Names.** Every global the script reads or calls must be a native, a
   blizzard.j function, a common.j/blizzard.j global, a Lua 5.3 library name,
   `FourCC`, or something the script assigns somewhere. A call to anything
   else fails at run time with "attempt to call a nil value".
2. **Library members.** `math.flor(x)` and similar are reported when the
   library table is the standard one and the script never adds that member.
3. **Game calls.** A call to a native or blizzard.j function is checked for
   its argument count and for the argument types that can be told without
   running the code: literals, results of other game calls, functions, and
   locals that are assigned once. The checks that matter most in practice are
   a string where an integer id is expected (`'hfoo'` instead of
   `FourCC('hfoo')`) and a function where a boolexpr is expected (`f` instead
   of `Condition(f)`).

As with the JASS checker (checker.py) this is a triage signal: a finding is
only reported when the checker is sure, and anything it cannot resolve is
skipped. A script that writes globals through `_G[expr]`, `rawset(_G, ...)`,
`setmetatable(_G, ...)` or `load` gets no undefined-name findings at all,
since any name could be defined that way.
"""

from . import ir
from .checker import NativeSignatures

# Lua 5.3 reference manual, section 6.
LUA53_BASE = frozenset((
     '_G', '_ENV', '_VERSION', 'assert', 'collectgarbage', 'dofile', 'error', 'getmetatable',
     'ipairs', 'load', 'loadfile', 'next', 'pairs', 'pcall', 'print', 'rawequal',
     'rawget', 'rawlen', 'rawset', 'require', 'select', 'setmetatable', 'tonumber',
     'tostring', 'type', 'xpcall'))

LUA53_LIBRARIES = {
     'coroutine': frozenset(('create', 'isyieldable', 'resume', 'running', 'status',
                             'wrap', 'yield')),
     'package': frozenset(('config', 'cpath', 'loaded', 'loadlib', 'path', 'preload',
                           'searchers', 'searchpath')),
     'string': frozenset(('byte', 'char', 'dump', 'find', 'format', 'gmatch', 'gsub',
                          'len', 'lower', 'match', 'pack', 'packsize', 'rep', 'reverse',
                          'sub', 'unpack', 'upper')),
     'utf8': frozenset(('char', 'charpattern', 'codes', 'codepoint', 'len', 'offset')),
     'table': frozenset(('concat', 'insert', 'move', 'pack', 'remove', 'sort', 'unpack')),
     'math': frozenset(('abs', 'acos', 'asin', 'atan', 'ceil', 'cos', 'deg', 'exp',
                        'floor', 'fmod', 'huge', 'log', 'max', 'maxinteger', 'min',
                        'mininteger', 'modf', 'pi', 'rad', 'random', 'randomseed',
                        'sin', 'sqrt', 'tan', 'tointeger', 'type', 'ult')),
     'io': frozenset(('close', 'flush', 'input', 'lines', 'open', 'output', 'popen',
                      'read', 'stderr', 'stdin', 'stdout', 'tmpfile', 'type', 'write')),
     'os': frozenset(('clock', 'date', 'difftime', 'execute', 'exit', 'getenv',
                      'remove', 'rename', 'setlocale', 'time', 'tmpname')),
     'debug': frozenset(('debug', 'gethook', 'getinfo', 'getlocal', 'getmetatable',
                         'getregistry', 'getupvalue', 'getuservalue', 'sethook',
                         'setlocal', 'setmetatable', 'setupvalue', 'setuservalue',
                         'traceback', 'upvalueid', 'upvaluejoin')),
}

# Names Lua 5.1 had and 5.3 does not (without the compatibility build): the
# 5.1 functions 5.2 removed, and the math functions 5.3 deprecated.
LUA51_ONLY = frozenset(('unpack', 'loadstring', 'setfenv', 'getfenv', 'module', 'gcinfo',
                        'newproxy'))
LUA51_ONLY_MEMBERS = {
     'table': frozenset(('getn', 'setn', 'maxn', 'foreach', 'foreachi')),
     'math': frozenset(('pow', 'ldexp', 'frexp', 'cosh', 'sinh', 'tanh', 'log10', 'mod',
                        'atan2')),
     'string': frozenset(('gfind',)),
}

# Globals the game itself adds to Reforged's Lua, beyond common.j/blizzard.j.
REFORGED_GLOBALS = frozenset(('FourCC', '__jarray'))

# Value kinds the checker can infer. JASS type names are used as they are
# ('integer', 'real', 'string', 'boolean', 'unit', ...); 'function' is a
# Lua function value and 'nothing' the result of a function returning none.
_NUMBER = ('integer', 'real')
_PRIMITIVE = ('integer', 'real', 'string', 'boolean', 'function')
_BOOLEXPR_TYPES = ('boolexpr', 'conditionfunc', 'filterfunc')
_NIL_UNSAFE = ('integer', 'real', 'boolean')
# Base functions that only read a table passed to them.
_READS_ONLY = frozenset(('pairs', 'ipairs', 'next', 'type', 'tostring', 'print',
                         'rawget', 'rawlen', 'rawequal', 'getmetatable', 'select',
                         'assert'))


class _Binding:
     """One local variable (or parameter) declared in the script."""

     __slots__ = ('name', 'initial', 'assigned', 'is_function', 'library')

     def __init__(self, name, initial=None, is_function=False):
          self.name = name
          self.initial = initial
          self.assigned = False
          self.is_function = is_function
          self.library = None       # 'math' for `local Math = math`


class _Globals:
     """What the script does with global names, collected in a first pass."""

     def __init__(self):
          self.assigned = {}          # name -> list of value nodes (None = unknown)
          self.members = {}           # library name -> set of member names added
          self.replaced_libraries = set()
          self.probed = set()         # names or (library, member) tested before use
          self.dynamic = None         # a reason string when globals are written dynamically


def check_chunk(chunk, natives=None):
     """Check a parsed `war3map.lua` (ir.Chunk).

     Returns a list of finding dicts {'severity': 'note'|'issue', 'message': str,
     'line': int or None}, in source order. Never raises on its own.
     """
     natives = natives or NativeSignatures.default()
     return _Checker(natives).run(chunk)


class _Checker:

     def __init__(self, natives):
          self.natives = natives
          self.findings = []
          self.globals = _Globals()
          self.deferred = []

     # --- driver ------------------------------------------------------------

     def run(self, chunk):
          # Pass 1 resolves scopes and records every global assignment; game
          # calls are queued, because a local's or global's value may be
          # assigned after the call that uses it. Pass 2 checks them.
          self.scopes = []
          self.block(chunk.body, ())
          for check in self.deferred:
               check()
          if self.globals.dynamic:
               self.note(None, f'globals are written dynamically ({self.globals.dynamic}); '
                               f'undefined names are not checked')
          self.findings.sort(key=lambda f: (f['line'] is None, f['line'] or 0))
          return self.findings

     def report(self, severity, node, message):
          self.findings.append({'severity': severity, 'message': message,
                                'line': getattr(node, 'line', None) if node is not None
                                else None})

     def issue(self, node, message):
          self.report('issue', node, message)

     def note(self, node, message):
          self.report('note', node, message)

     # --- scopes ------------------------------------------------------------

     def lookup(self, name):
          for scope in reversed(self.scopes):
               if name in scope:
                    return scope[name]
          return None

     def declare(self, name, initial=None, is_function=False):
          binding = _Binding(name, initial, is_function)
          self.scopes[-1][name] = binding
          return binding

     def block(self, statements, declared):
          self.scopes.append({})
          for name in declared:
               self.declare(name)
          for stmt in statements:
               self.statement(stmt)
          self.scopes.pop()

     def function_body(self, params, body, is_method=False):
          names = (['self'] if is_method else []) + list(params)
          self.block(body, names)

     # --- statements --------------------------------------------------------

     def statement(self, stmt):
          kind = type(stmt)
          if kind is ir.LocalStmt:
               for value in stmt.values:
                    self.expr(value)
               single = len(stmt.values) == len(stmt.names)
               # resolved before the names exist: `local math = math`
               libraries = [self.library_of(v) for v in stmt.values] if single else ()
               for position, name in enumerate(stmt.names):
                    initial = stmt.values[position] if single else None
                    binding = self.declare(name, initial)
                    if single:
                         binding.library = libraries[position]
          elif kind is ir.AssignStmt:
               for value in stmt.values:
                    self.expr(value)
               single = len(stmt.values) == len(stmt.targets)
               for position, target in enumerate(stmt.targets):
                    self.assign(target, stmt.values[position] if single else None)
          elif kind is ir.LocalFunctionStmt:
               self.declare(stmt.name, is_function=True)
               self.function_body(stmt.params, stmt.body)
          elif kind is ir.FunctionStmt:
               self.function_statement(stmt)
          elif kind is ir.ExprStmt:
               self.expr(stmt.call)
          elif kind is ir.DoStmt:
               self.block(stmt.body, ())
          elif kind is ir.WhileStmt:
               self.expr(stmt.condition, probe=True)
               self.block(stmt.body, ())
          elif kind is ir.RepeatStmt:
               # the condition sees the body's locals
               self.scopes.append({})
               for inner in stmt.body:
                    self.statement(inner)
               self.expr(stmt.condition, probe=True)
               self.scopes.pop()
          elif kind is ir.IfStmt:
               for condition, body in stmt.branches:
                    self.expr(condition, probe=True)
                    self.block(body, ())
               if stmt.else_body is not None:
                    self.block(stmt.else_body, ())
          elif kind is ir.NumericForStmt:
               for part in (stmt.start, stmt.stop, stmt.step):
                    if part is not None:
                         self.expr(part)
               self.block(stmt.body, (stmt.name,))
          elif kind is ir.GenericForStmt:
               for value in stmt.exprs:
                    self.expr(value)
               self.block(stmt.body, stmt.names)
          elif kind is ir.LuaReturnStmt:
               for value in stmt.values:
                    self.expr(value)

     def function_statement(self, stmt):
          path = stmt.name_path
          root = path[0]
          binding = self.lookup(root)
          is_method = stmt.method_name is not None
          if len(path) == 1 and not is_method:
               if binding is not None:
                    binding.assigned = True
               else:
                    self.define_global(root, stmt)
          else:
               if binding is None:
                    self.read_global(root, stmt)
               if len(path) == 1:
                    member = stmt.method_name           # function a:b()
               elif len(path) == 2 and not is_method:
                    member = path[1]                    # function a.b()
               else:
                    member = None
               library = self.library_of(ir.VarExpr(root))
               if member is not None and library is not None:
                    self.globals.members.setdefault(library, set()).add(member)
          self.function_body(stmt.params, stmt.body, is_method=is_method)

     def library_of(self, node):
          """The standard library `node` denotes: `math`, or a local alias of
          it (`local Math = math`), through which the script may add members."""
          if not isinstance(node, ir.VarExpr):
               return None
          binding = self.lookup(node.name)
          if binding is None:
               return node.name if node.name in LUA53_LIBRARIES else None
          return binding.library

     def assign(self, target, value):
          if isinstance(target, ir.VarExpr):
               binding = self.lookup(target.name)
               if binding is not None:
                    binding.assigned = True
               else:
                    self.define_global(target.name, value)
               return
          if isinstance(target, ir.IndexExpr):
               obj = target.obj
               self.expr(target.index)
               library = self.library_of(obj)
               if library is not None:
                    key = _string_key(target.index)
                    if key is not None:
                         self.globals.members.setdefault(library, set()).add(key)
                    else:
                         self.globals.replaced_libraries.add(library)
               if isinstance(obj, ir.VarExpr) and self.lookup(obj.name) is None:
                    key = _string_key(target.index)
                    if obj.name in ('_G', '_ENV'):
                         if key is not None:
                              self.define_global(key, value)
                         else:
                              self.globals.dynamic = self.globals.dynamic or \
                                   f'{obj.name}[...] = at line {getattr(target, "line", "?")}'
                         return
                    self.read_global(obj.name, target)
                    return
               self.expr(obj)
               return
          self.expr(target)

     def define_global(self, name, value):
          self.globals.assigned.setdefault(name, []).append(value)
          if name in LUA53_LIBRARIES:
               self.globals.replaced_libraries.add(name)

     # --- expressions -------------------------------------------------------

     def expr(self, node, probe=False):
          """Walk an expression. `probe` is set where the value is only
          tested (a condition, an operand of and/or/not, a comparison): reading
          a missing name there is a feature test such as `table.unpack or
          unpack`, not a bug, and the name is then optional everywhere."""
          kind = type(node)
          if kind is ir.VarExpr:
               if self.lookup(node.name) is None:
                    if probe:
                         self.globals.probed.add(node.name)
                    self.read_global(node.name, node)
          elif kind is ir.IndexExpr:
               self.index(node, probe)
          elif kind is ir.LuaCallExpr:
               self.call(node)
          elif kind is ir.LuaMethodCallExpr:
               self.expr(node.obj)
               for arg in node.args:
                    self.expr(arg)
          elif kind is ir.BinaryExpr:
               test = node.op in ('and', 'or', '==', '~=')
               self.expr(node.left, test)
               self.expr(node.right, test)
          elif kind is ir.UnaryExpr:
               self.expr(node.operand, node.op == 'not')
          elif kind is ir.LuaFunctionExpr:
               self.function_body(node.params, node.body)
          elif kind is ir.TableExpr:
               for key, value in node.fields:
                    if key is not None:
                         self.expr(key)
                    self.expr(value)

     def index(self, node, probe=False):
          obj = node.obj
          if isinstance(obj, ir.VarExpr) and self.lookup(obj.name) is None:
               self.read_global(obj.name, obj)
               key = _string_key(node.index)
               if key is not None:
                    if probe:
                         self.globals.probed.add((obj.name, key))
                    self.deferred.append(lambda: self.check_member(obj.name, key, node))
               else:
                    self.expr(node.index)
               return
          self.expr(obj)
          self.expr(node.index)

     def call(self, node):
          func = node.func
          is_type_test = isinstance(func, ir.VarExpr) and func.name == 'type' and \
               self.lookup('type') is None
          reads_only = isinstance(func, ir.VarExpr) and func.name in _READS_ONLY and \
               self.lookup(func.name) is None
          for arg in node.args:
               self.expr(arg, probe=is_type_test)
               # A library handed to any other function may get members
               # added there (setmetatable(math, ...), a mixin helper).
               library = self.library_of(arg)
               if library is not None and not reads_only:
                    self.globals.replaced_libraries.add(library)
          if isinstance(func, ir.VarExpr) and self.lookup(func.name) is None:
               name = func.name
               self.read_global(name, func, called=True)
               if name in ('rawset', 'setmetatable') and node.args and \
                         _is_global_table(node.args[0], self):
                    self.globals.dynamic = self.globals.dynamic or \
                         f'{name}(_G, ...) at line {getattr(node, "line", "?")}'
               elif name in ('load', 'loadstring', 'dofile', 'require'):
                    self.globals.dynamic = self.globals.dynamic or \
                         f'{name}() at line {getattr(node, "line", "?")}'
               scopes = [dict(scope) for scope in self.scopes]
               self.deferred.append(lambda: self.check_game_call(name, node, scopes))
               return
          self.expr(func)

     def read_global(self, name, node, called=False):
          self.deferred.append(lambda: self.check_name(name, node, called))

     # --- pass 2: names -----------------------------------------------------

     def known_global(self, name):
          return (name in self.globals.assigned or name in LUA53_BASE
                  or name in LUA53_LIBRARIES or name in REFORGED_GLOBALS
                  or self.natives.lookup(name) is not None
                  or self.natives.lookup_global(name) is not None)

     def check_name(self, name, node, called):
          if self.known_global(name) or self.globals.dynamic or \
                    name in self.globals.probed:
               return
          key = ('name', name)
          if key in self._reported():
               return
          self._reported().add(key)
          if name in LUA51_ONLY:
               self.note(node, f"'{name}' is not in Lua 5.3 (Reforged's Lua) unless it is "
                               f"built with 5.1/5.2 compatibility")
          elif called and name in _common_ai_natives():
               self.note(node, f"'{name}' is a common.ai native, not part of common.j")
          elif called:
               self.issue(node, f"call to undefined global '{name}'")
          else:
               self.note(node, f"global '{name}' is read but never assigned "
                               f"(it is always nil)")

     def check_member(self, library, member, node):
          if library not in LUA53_LIBRARIES or library in self.globals.replaced_libraries:
               return
          if member in LUA53_LIBRARIES[library] or \
                    member in self.globals.members.get(library, ()) or \
                    (library, member) in self.globals.probed:
               return
          key = ('member', library, member)
          if key in self._reported():
               return
          self._reported().add(key)
          if member in LUA51_ONLY_MEMBERS.get(library, ()):
               self.note(node, f"'{library}.{member}' is not in Lua 5.3 (Reforged's Lua) "
                               f"unless it is built with 5.1/5.2 compatibility")
          else:
               self.issue(node, f"'{library}.{member}' is not part of Lua's {library} "
                                f"library")

     def _reported(self):
          if not hasattr(self, '_seen'):
               self._seen = set()
          return self._seen

     # --- pass 2: game calls ------------------------------------------------

     def game_decl(self, name):
          """The common.j/blizzard.j declaration `name` refers to, unless the
          script defines its own global of that name (a hook or replacement)."""
          if name in self.globals.assigned:
               return None
          return self.natives.lookup(name)

     def check_game_call(self, name, node, scopes):
          args = node.args
          if name == 'FourCC' and name not in self.globals.assigned:
               self.check_fourcc(node)
               return
          decl = self.game_decl(name)
          if decl is None:
               return
          params = decl.params
          count = self.argument_count(args, scopes)
          if count is not None and count != len(params):
               missing = [p.type for p in params[count:]]
               if missing and all(t not in _NIL_UNSAFE for t in missing):
                    # Lua passes nil for the missing ones, which is null for a handle
                    self.note(node, f"'{name}' takes {len(params)} argument(s), got {count}; "
                                    f"the missing ones are passed as nil")
               else:
                    self.issue(node, f"'{name}' takes {len(params)} argument(s), got {count}")
               return
          for position, (param, arg) in enumerate(zip(params, args), start=1):
               actual = self.infer(arg, scopes)
               if actual is None:
                    continue
               message = self.mismatch(param.type, actual, arg)
               if message is None:
                    continue
               severity, text = message
               self.report(severity, node, f"'{name}' argument {position} "
                                           f"({param.name}): {text}")

     def check_fourcc(self, node):
          if len(node.args) != 1:
               self.issue(node, f"'FourCC' takes 1 argument, got {len(node.args)}")
               return
          arg = node.args[0]
          if isinstance(arg, ir.Literal) and arg.kind == 'string':
               if len(arg.value.encode('utf-8')) != 4:
                    self.issue(node, f"FourCC({arg.value!r}): an object id is exactly 4 "
                                     f"characters")
          elif isinstance(arg, ir.Literal) and arg.kind in ('int', 'real', 'bool'):
               self.issue(node, "FourCC expects a 4-character string")

     def argument_count(self, args, scopes):
          """How many values `args` pass, or None when the last one is a
          call of unknown result count or `...`."""
          if not args:
               return 0
          last = args[-1]
          if getattr(last, 'paren', False):
               return len(args)
          if isinstance(last, ir.VarargExpr):
               return None
          if isinstance(last, ir.LuaMethodCallExpr):
               return None
          if isinstance(last, ir.LuaCallExpr):
               func = last.func
               if isinstance(func, ir.VarExpr) and _lookup(scopes, func.name) is None:
                    if func.name == 'FourCC' and func.name not in self.globals.assigned:
                         return len(args)
                    decl = self.game_decl(func.name)
                    if decl is not None:
                         return len(args) - (1 if decl.returns == 'nothing' else 0)
               return None
          return len(args)

     def infer(self, node, scopes, depth=0):
          """The value kind of `node`, or None when it cannot be told."""
          if depth > 20:
               return None
          kind = type(node)
          if kind is ir.Literal:
               return {'int': 'integer', 'real': 'real', 'string': 'string',
                       'bool': 'boolean'}.get(node.kind)
          if kind is ir.LuaFunctionExpr:
               return 'function'
          if kind is ir.VarExpr:
               binding = _lookup(scopes, node.name)
               if binding is not None:
                    if binding.assigned:
                         return None
                    if binding.is_function:
                         return 'function'
                    if binding.initial is None:
                         return None
                    return self.infer(binding.initial, scopes, depth + 1)
               values = self.globals.assigned.get(node.name)
               if values is not None:
                    if all(isinstance(v, ir.FunctionStmt) for v in values):
                         return 'function'
                    return None
               if self.natives.lookup(node.name) is not None or \
                         node.name in LUA53_BASE or node.name in REFORGED_GLOBALS:
                    return 'function'
               glob = self.natives.lookup_global(node.name)
               if glob is not None and glob.is_constant and not glob.is_array:
                    return glob.type
               return None
          if kind is ir.LuaCallExpr:
               func = node.func
               if isinstance(func, ir.VarExpr) and _lookup(scopes, func.name) is None:
                    if func.name == 'FourCC' and func.name not in self.globals.assigned:
                         return 'integer'
                    decl = self.game_decl(func.name)
                    if decl is not None:
                         return decl.returns
               return None
          if kind is ir.BinaryExpr:
               # Comparisons always give a boolean. Every other operator can
               # be overloaded by a metatable (obfuscated maps do this a lot),
               # so its result is only known when both operands are plain
               # numbers or strings.
               op = node.op
               if op in ('==', '~=', '<', '>', '<=', '>='):
                    return 'boolean'
               if op in ('and', 'or'):
                    return None
               left = self.infer(node.left, scopes, depth + 1)
               right = self.infer(node.right, scopes, depth + 1)
               plain = ('integer', 'real', 'string')
               if left not in plain or right not in plain:
                    return None
               if op == '..':
                    return 'string'
               if left not in _NUMBER or right not in _NUMBER:
                    return None
               if op in ('&', '|', '~', '<<', '>>'):
                    return 'integer'
               if op in ('/', '^'):
                    return 'real'
               if left == 'integer' and right == 'integer':
                    return 'integer'
               return 'real'
          if kind is ir.UnaryExpr:
               if node.op == 'not':
                    return 'boolean'
               operand = self.infer(node.operand, scopes, depth + 1)
               if node.op == '#':
                    return 'integer' if operand == 'string' else None
               if operand in _NUMBER:
                    return 'integer' if node.op == '~' else operand
          return None

     def mismatch(self, expected, actual, arg):
          """(severity, text) when a value of kind `actual` cannot be passed
          where JASS type `expected` is declared, else None."""
          if actual == expected:
               return None
          if actual == 'nothing':
               return ('issue', 'the call passed here returns nothing (nil)')
          if expected == 'real' and actual == 'integer':
               return None
          if expected == 'string' and actual in _NUMBER:
               return None          # Lua converts a number to a string
          if expected == 'integer' and actual == 'real':
               # Lua 5.3 converts a float only when it has an exact integer
               # value; that is only certain for a literal.
               if isinstance(arg, ir.Literal) and not float(arg.value).is_integer():
                    return ('issue', f'expects an integer, got {arg.value!r}, which '
                                     f'has no integer value')
               return None
          if expected in _NUMBER and actual == 'string':
               if not isinstance(arg, ir.Literal):
                    return ('note', f'expects {_describe(expected)}, got a string')
               if _numeric_string(arg.value):
                    return None      # Lua converts a numeric string
               hint = ''
               if expected == 'integer' and len(arg.value.encode('utf-8')) == 4:
                    hint = f"; use FourCC({arg.value!r}) for an object id"
               return ('issue', f'expects {_describe(expected)}, got the string '
                                f'{arg.value!r}{hint}')
          if expected == 'code':
               if actual == 'function':
                    return None
               return ('issue', f'expects a function, got {_describe(actual)}')
          if actual == 'function':
               if self.is_boolexpr(expected):
                    return ('issue', f'expects a {expected}, got a function; wrap it in '
                                     f'Condition() or Filter()')
               return ('issue', f'expects {_describe(expected)}, got a function')
          if expected in _PRIMITIVE or actual in _PRIMITIVE:
               return ('issue', f'expects {_describe(expected)}, got {_describe(actual)}')
          if self.natives.is_ancestor(expected, actual):
               return None
          return ('issue', f'expects {_describe(expected)}, got {_describe(actual)}')

     def is_boolexpr(self, type_name):
          return any(self.natives.is_ancestor(t, type_name) or
                     self.natives.is_ancestor(type_name, t) for t in _BOOLEXPR_TYPES)


_COMMON_AI = None


def _common_ai_natives():
     global _COMMON_AI
     if _COMMON_AI is None:
          from .jass_parser import parse
          from wc3files import gamedata
          path = gamedata.path('scripts/gamedata/common.ai')
          with open(path, 'r', encoding='utf-8', errors='replace') as handle:
               _COMMON_AI = frozenset(n.name for n in parse(handle.read()).natives)
     return _COMMON_AI


def _lookup(scopes, name):
     for scope in reversed(scopes):
          if name in scope:
               return scope[name]
     return None


def _string_key(node):
     if isinstance(node, ir.Literal) and node.kind == 'string':
          return node.value
     return None


def _is_global_table(node, checker):
     return isinstance(node, ir.VarExpr) and node.name in ('_G', '_ENV') and \
          checker.lookup(node.name) is None


def _numeric_string(text):
     from .lua_vm import str_to_number
     try:
          return str_to_number(text) is not None
     except Exception:
          return False


def _describe(kind):
     if kind == 'real':
          return 'a number'
     if kind in ('integer', 'string', 'boolean', 'function'):
          return ('an ' if kind == 'integer' else 'a ') + kind
     # "a unit", "an item", "an effect"
     article = 'an' if kind[:1] in 'aeio' else 'a'
     return f'{article} {kind}'
