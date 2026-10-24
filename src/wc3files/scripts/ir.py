"""
Shared script AST/IR - the semantic model a JASS (or, eventually, Lua) frontend
parses source text into, and a printer walks back into source text. Mirrors how
wc3files.mdl keeps one shared model (empty_model()) that both its text (MDL) and
binary (MDX) frontends convert to/from.

Only the subset of JASS this package's parser actually recognizes is modeled here
(no macro/preprocessor constructs - vJass-only syntax like structs or textmacros is
out of scope, see TODO.md).
"""


class Node:
     """Base class: gives every node a field-based __repr__/__eq__ without extra
     boilerplate per subclass. Equality is structural (used by the parse -> print ->
     re-parse round-trip check), not identity."""

     _fields = ()

     def __eq__(self, other):
          if type(self) is not type(other):
               return False
          return all(getattr(self, f) == getattr(other, f) for f in self._fields)

     def __repr__(self):
          values = ', '.join(f'{f}={getattr(self, f)!r}' for f in self._fields)
          return f'{type(self).__name__}({values})'


class Program(Node):
     _fields = ('type_defs', 'globals', 'natives', 'functions')

     def __init__(self, type_defs=None, globals=None, natives=None, functions=None):
          self.type_defs = type_defs or []
          self.globals = globals or []
          self.natives = natives or []
          self.functions = functions or []


class TypeDef(Node):
     _fields = ('name', 'base')

     def __init__(self, name, base):
          self.name = name
          self.base = base


class Param(Node):
     _fields = ('type', 'name')

     def __init__(self, type, name):
          self.type = type
          self.name = name


class GlobalVar(Node):
     _fields = ('type', 'is_array', 'is_constant', 'name', 'initial')

     def __init__(self, type, name, is_array=False, is_constant=False, initial=None):
          self.type = type
          self.name = name
          self.is_array = is_array
          self.is_constant = is_constant
          self.initial = initial


class NativeDecl(Node):
     _fields = ('name', 'is_constant', 'params', 'returns')

     def __init__(self, name, params, returns, is_constant=False):
          self.name = name
          self.params = params
          self.returns = returns
          self.is_constant = is_constant


class FunctionDecl(Node):
     _fields = ('name', 'is_constant', 'params', 'returns', 'body')

     def __init__(self, name, params, returns, body=None, is_constant=False):
          self.name = name
          self.params = params
          self.returns = returns
          self.body = body or []
          self.is_constant = is_constant


# --- statements ---------------------------------------------------------------

class Local(Node):
     _fields = ('type', 'is_array', 'name', 'initial')

     def __init__(self, type, name, is_array=False, initial=None):
          self.type = type
          self.name = name
          self.is_array = is_array
          self.initial = initial


class SetStmt(Node):
     _fields = ('is_debug', 'target', 'index', 'value')

     def __init__(self, target, value, index=None, is_debug=False):
          self.target = target
          self.value = value
          self.index = index
          self.is_debug = is_debug


class CallStmt(Node):
     _fields = ('is_debug', 'name', 'args')

     def __init__(self, name, args=None, is_debug=False):
          self.name = name
          self.args = args or []
          self.is_debug = is_debug


class IfStmt(Node):
     """`branches` is a list of (condition, body) pairs: the first is the `if`
     itself, any further entries are `elseif`s. `else_body` is None if there's no
     trailing `else`."""
     _fields = ('is_debug', 'branches', 'else_body')

     def __init__(self, branches, else_body=None, is_debug=False):
          self.branches = branches
          self.else_body = else_body
          self.is_debug = is_debug


class LoopStmt(Node):
     _fields = ('is_debug', 'body')

     def __init__(self, body=None, is_debug=False):
          self.body = body or []
          self.is_debug = is_debug


class ExitWhenStmt(Node):
     _fields = ('is_debug', 'condition')

     def __init__(self, condition, is_debug=False):
          self.condition = condition
          self.is_debug = is_debug


class ReturnStmt(Node):
     _fields = ('is_debug', 'value')

     def __init__(self, value=None, is_debug=False):
          self.value = value
          self.is_debug = is_debug


# --- expressions ---------------------------------------------------------------

class Literal(Node):
     """`kind` is one of 'int', 'real', 'string', 'rawcode', 'bool', 'null'."""
     _fields = ('kind', 'value')

     def __init__(self, kind, value):
          self.kind = kind
          self.value = value


class VarExpr(Node):
     _fields = ('name',)

     def __init__(self, name):
          self.name = name


class ArrayRefExpr(Node):
     _fields = ('name', 'index')

     def __init__(self, name, index):
          self.name = name
          self.index = index


class CallExpr(Node):
     _fields = ('name', 'args')

     def __init__(self, name, args=None):
          self.name = name
          self.args = args or []


class FunctionRefExpr(Node):
     """A bare `function Foo` reference used as a `code`-typed value (e.g. passed
     to Condition()/Filter()/TriggerAddAction())."""
     _fields = ('name',)

     def __init__(self, name):
          self.name = name


class UnaryExpr(Node):
     """`op` is one of '-', 'not'."""
     _fields = ('op', 'operand')

     def __init__(self, op, operand):
          self.op = op
          self.operand = operand


class BinaryExpr(Node):
     """`op` is one of '+', '-', '*', '/', '==', '!=', '<', '>', '<=', '>=',
     'and', 'or' for JASS; also reused by the Lua frontend, which additionally
     uses '%', '^', '..', '~=' (Lua's not-equal, where JASS uses '!=')."""
     _fields = ('op', 'left', 'right')

     def __init__(self, op, left, right):
          self.op = op
          self.left = left
          self.right = right


# --- Lua-only nodes -------------------------------------------------------
#
# Lua's grammar is different enough from JASS's (dynamically typed, multiple
# assignment/return, closures, tables, method-call sugar) that most of its
# constructs need their own node types rather than reusing a JASS-shaped one -
# but where the shape genuinely matches (a literal value, a bare identifier
# reference, a unary/binary operation, an if/elseif/else chain) the Lua
# frontend reuses the node types above instead of duplicating them.
#
# Two deliberate normalizations keep round-trip (parse -> print -> re-parse)
# structurally stable regardless of which surface spelling the original source
# used: `t.name` is always represented the same as `t["name"]` (IndexExpr with
# a string Literal index - the printer picks the shorter `.name` spelling back
# when it can), and a table constructor's `Name = exp` field is represented
# the same as `["Name"] = exp` (TableExpr's `key` is a string Literal either
# way).

class Chunk(Node):
     """Lua's top-level unit (a plain statement list - Lua has no separate
     globals/native/function-declaration segregation the way JASS does)."""
     _fields = ('body',)

     def __init__(self, body=None):
          self.body = body or []


class LocalStmt(Node):
     """`local a, b = 1, 2` (or no initializers at all: `local a, b`).
     `values` may be shorter than `names` (or empty)."""
     _fields = ('names', 'values')

     def __init__(self, names, values=None):
          self.names = names
          self.values = values or []


class AssignStmt(Node):
     """`a, b.c, d[e] = 1, 2, 3` - each target is a VarExpr or IndexExpr."""
     _fields = ('targets', 'values')

     def __init__(self, targets, values):
          self.targets = targets
          self.values = values


class LocalFunctionStmt(Node):
     """`local function f(a, b) ... end`."""
     _fields = ('name', 'params', 'is_vararg', 'body')

     def __init__(self, name, params, body=None, is_vararg=False):
          self.name = name
          self.params = params
          self.body = body or []
          self.is_vararg = is_vararg


class FunctionStmt(Node):
     """`function a.b.c:d(...) ... end`. `name_path` is every dotted segment
     (`['a', 'b', 'c']`); `method_name`, if not None, is the last, `:`-declared
     segment (`'d'`) - kept separate from `name_path` rather than folded in, so
     the printer can tell `.`-declared from `:`-declared apart. `is_method` is
     just `method_name is not None`, kept as an explicit field for clarity at
     call sites."""
     _fields = ('name_path', 'method_name', 'params', 'is_vararg', 'body')

     def __init__(self, name_path, method_name=None, params=None, body=None, is_vararg=False):
          self.name_path = name_path
          self.method_name = method_name
          self.params = params or []
          self.body = body or []
          self.is_vararg = is_vararg


class ExprStmt(Node):
     """A bare function/method-call statement (`foo()`, `obj:method()`) - Lua
     is the only frontend where a call can be a statement on its own, not just
     part of an expression, so this wraps one for the statement list."""
     _fields = ('call',)

     def __init__(self, call):
          self.call = call


class DoStmt(Node):
     _fields = ('body',)

     def __init__(self, body=None):
          self.body = body or []


class WhileStmt(Node):
     _fields = ('condition', 'body')

     def __init__(self, condition, body=None):
          self.condition = condition
          self.body = body or []


class RepeatStmt(Node):
     """`repeat ... until cond` - `cond` can reference locals declared inside
     `body` (Lua's scoping quirk), which is why it isn't reused as a WhileStmt
     with the test at the end."""
     _fields = ('body', 'condition')

     def __init__(self, body, condition):
          self.body = body or []
          self.condition = condition


class NumericForStmt(Node):
     """`for name = start, stop[, step] do ... end`. `step` is None when
     omitted (Lua defaults it to 1 at runtime; kept as None here rather than
     synthesizing a Literal(1), so print->reparse doesn't invent a step clause
     that wasn't in the source)."""
     _fields = ('name', 'start', 'stop', 'step', 'body')

     def __init__(self, name, start, stop, step=None, body=None):
          self.name = name
          self.start = start
          self.stop = stop
          self.step = step
          self.body = body or []


class GenericForStmt(Node):
     """`for k, v in pairs(t) do ... end`."""
     _fields = ('names', 'exprs', 'body')

     def __init__(self, names, exprs, body=None):
          self.names = names
          self.exprs = exprs
          self.body = body or []


class BreakStmt(Node):
     _fields = ()


class GotoStmt(Node):
     """Lua 5.2+ `goto name`. Reforged's Lua is 5.3, and real map scripts use
     it - most often as `goto continue` to emulate the continue statement Lua
     does not have."""
     _fields = ('label',)

     def __init__(self, label):
          self.label = label


class LabelStmt(Node):
     """Lua 5.2+ `::name::`, the target of a goto."""
     _fields = ('name',)

     def __init__(self, name):
          self.name = name


class LuaReturnStmt(Node):
     """Lua `return` can yield zero, one, or several values - unlike JASS's
     single-value ReturnStmt, so this is a distinct node rather than a reuse
     with a list jammed into `value`."""
     _fields = ('values',)

     def __init__(self, values=None):
          self.values = values or []


class IndexExpr(Node):
     """`obj[index]` / `obj.name` (normalized to a string Literal index - see
     the module docstring)."""
     _fields = ('obj', 'index')

     def __init__(self, obj, index):
          self.obj = obj
          self.index = index


class LuaCallExpr(Node):
     """`func(args...)` where `func` is an arbitrary expression (unlike JASS's
     CallExpr, which only ever calls a bare name)."""
     _fields = ('func', 'args')

     def __init__(self, func, args=None):
          self.func = func
          self.args = args or []


class LuaMethodCallExpr(Node):
     """`obj:method(args...)` - kept distinct from LuaCallExpr(IndexExpr(obj,
     method), args) so the printer can reproduce the `:` sugar (and so a
     future semantic pass can tell "this call implicitly passes obj as the
     first argument" apart from a plain indexed call)."""
     _fields = ('obj', 'method', 'args')

     def __init__(self, obj, method, args=None):
          self.obj = obj
          self.method = method
          self.args = args or []


class LuaFunctionExpr(Node):
     """`function(...) ... end` used as a value (closure literal) - JASS's
     FunctionRefExpr only names an existing top-level function by identifier,
     so it doesn't fit an anonymous, body-carrying function value."""
     _fields = ('params', 'is_vararg', 'body')

     def __init__(self, params, body=None, is_vararg=False):
          self.params = params
          self.body = body or []
          self.is_vararg = is_vararg


class TableExpr(Node):
     """`{1, 2, [5] = "x", name = "y"}` - `fields` is an ordered list of
     `(key, value)` pairs; `key` is None for a plain positional item (order
     among positional items matters and is preserved by list order), or a
     string Literal for both `name = value` and `["name"] = value` forms
     (normalized the same way IndexExpr normalizes `.name` - see the module
     docstring), or any other expression for a general `[expr] = value` key."""
     _fields = ('fields',)

     def __init__(self, fields=None):
          self.fields = fields or []


class VarargExpr(Node):
     """Bare `...` used as an expression (e.g. `f(...)`, `local a = ...`)."""
     _fields = ()
