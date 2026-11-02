"""
A Lua interpreter in pure Python.

It runs the Lua that Warcraft III tooling embeds in map sources: the
`//! externalblock extension=lua ObjectMerger $FILENAME$` blocks and the
`//! i` lines of vJass, which Grim Extension Pack's tools (ObjectMerger,
FileExporter, ...) execute with an embedded Lua 5.1 when the map is saved.
`wc3files.grimex` builds those tools on top of this.

The source is parsed with `lua_parser` (Lua 5.3 syntax, a superset of 5.1's)
and the tree is compiled once into nested Python closures, which is several
times faster than walking the tree on every step. The language is complete:
closures and upvalues with per-iteration loop variables, multiple results and
varargs, metatables and every metamethod, `goto`, integer and float numbers,
coroutines (each on its own thread, handed control one at a time), `pcall` and
`error` with positions, and the standard library (`lua_stdlib`).

`version='5.1'` (the default, because that is what Grim Extension Pack embeds)
formats numbers the way Lua 5.1 does, where every number is a double:
`tostring(10/2)` is `5`, not `5.0`, and also enables the 5.1-only library
functions (`unpack`, `loadstring`, `table.getn`, `math.mod`, ...).
`version='5.3'` gives 5.3's integer/float distinction.

Strings are byte strings held in Python `str`, one character per byte
(latin-1), so `#s` and `string.byte` count bytes as Lua does. Source text is
converted to that form before it is parsed; `to_text` converts back.

The interpreter is sandboxed by construction: Lua code can only reach what the
standard library exposes, and `lua_stdlib` routes every file and process
access through a `LuaHost`, which by default allows nothing. A step budget and
a call-depth limit stop runaway scripts.
"""

import math
import re
import sys
import threading

from . import ir
from .lua_parser import parse as _parse, LuaParseError
from .lua_lexer import LuaLexError


# --- values -----------------------------------------------------------------

class LuaError(Exception):
     """A Lua error: `value` is the error object (usually a message string).

     `traceback` lists the source positions the error went through, innermost
     first, one per Lua function."""

     def __init__(self, value, level=1):
          super().__init__(value)
          self.value = value
          # How many function frames still have to be left before the
          # position is prepended (`error(msg, level)`); 0 once it is done or
          # when no position should be added at all.
          self.level = level if isinstance(value, str) else 0
          self.traceback = []
          self._trace_frame = None
          # The call stack where the error was raised, for xpcall handlers
          # (debug.traceback) that run after it has been unwound.
          self.stack = None

     def __str__(self):
          value = self.value
          if isinstance(value, str):
               return to_text(value)
          if value is None:
               return 'nil'
          if isinstance(value, LuaTable):
               return '(error object is a table value)'
          return to_text(str(value))


class LuaTable:
     """A Lua table. `hash` maps normalized keys (see `_key`) to non-nil
     values; nil is never stored."""

     __slots__ = ('hash', 'metatable', 'border', '_keys', '_positions', '__weakref__')

     def __init__(self, items=None):
          self.hash = {} if items is None else items
          self.metatable = None
          self.border = 0
          self._keys = None
          self._positions = None

     def get(self, key):
          return self.hash.get(_key(key))

     def set(self, key, value):
          """Raw assignment (`rawset`)."""
          if key is None:
               raise LuaError('table index is nil')
          k = _key(key)
          if k != k:
               raise LuaError('table index is NaN')
          if value is None:
               self.hash.pop(k, None)
          else:
               if k not in self.hash:
                    self._keys = None
               self.hash[k] = value

     def length(self):
          """The border `#t` returns (without __len)."""
          h = self.hash
          n = self.border
          if n and n not in h:
               while n > 0 and n not in h:
                    n -= 1
          else:
               while (n + 1) in h:
                    n += 1
          self.border = n
          return n

     def next(self, key):
          """`next(t, key)`: the (key, value) after `key`, or None at the end."""
          if self._keys is None:
               self._keys = list(self.hash)
               self._positions = None
          keys = self._keys
          if key is None:
               start = 0
          else:
               if self._positions is None:
                    self._positions = {k: i for i, k in enumerate(keys)}
               position = self._positions.get(_key(key))
               if position is None:
                    raise LuaError("invalid key to 'next'")
               start = position + 1
          h = self.hash
          for i in range(start, len(keys)):
               k = keys[i]
               v = h.get(k)
               if v is not None:
                    return _unkey(k), v
          return None

     def items(self):
          """A snapshot of (key, value) pairs, Lua keys restored."""
          return [(_unkey(k), v) for k, v in list(self.hash.items())]

     def array(self):
          """t[1] .. t[#t] as a Python list."""
          h = self.hash
          return [h.get(i) for i in range(1, self.length() + 1)]

     @classmethod
     def from_list(cls, values, start=1):
          t = cls({i: v for i, v in enumerate(values, start) if v is not None})
          t.border = len(values) if start == 1 and None not in values else 0
          return t

     @classmethod
     def from_dict(cls, mapping):
          t = cls()
          for k, v in mapping.items():
               t.set(k, v)
          return t

     def __repr__(self):
          return f'<LuaTable {len(self.hash)} entries>'


_COLLECTABLE = None


def _collectable(v):
     global _COLLECTABLE
     if _COLLECTABLE is None:
          _COLLECTABLE = (LuaTable, LuaFunction, PyFunction, LuaUserdata, Coroutine)
     return isinstance(v, _COLLECTABLE)


class _WeakHash:
     """The `hash` of a table with weak keys and/or values (`__mode`).

     It offers the dict operations the interpreter uses on `LuaTable.hash`.
     Entries whose weak key or value is no longer referenced anywhere else
     disappear, as Lua's collector removes them; under CPython's reference
     counting that happens as soon as the last other reference goes, rather
     than at the next collection."""

     def __init__(self, weak_keys, weak_values, items=()):
          import weakref
          self._ref = weakref.ref
          self.weak_keys = weak_keys
          self.weak_values = weak_values
          self.d = {}
          for k, v in items:
               self[k] = v

     def _wrap_key(self, k):
          if self.weak_keys and _collectable(k):
               return self._ref(k)
          return k

     def __setitem__(self, k, v):
          d = self.d
          key = k
          if self.weak_keys and _collectable(k):
               key = self._ref(k, lambda ref, d=d: d.pop(ref, None))
          if self.weak_values and _collectable(v):
               v = self._ref(v, lambda ref, d=d, key=key: d.pop(key, None)
                             if d.get(key) is ref else None)
          d[key] = v

     def _value(self, v):
          if self.weak_values and type(v) is self._ref_type:
               return v()
          return v

     @property
     def _ref_type(self):
          import weakref
          return weakref.ReferenceType

     def get(self, k, default=None):
          v = self.d.get(self._wrap_key(k))
          if v is None:
               return default
          v = self._value(v)
          return default if v is None else v

     def __getitem__(self, k):
          v = self.get(k)
          if v is None:
               raise KeyError(k)
          return v

     def __contains__(self, k):
          return self.get(k) is not None

     def pop(self, k, default=None):
          v = self.d.pop(self._wrap_key(k), None)
          if v is None:
               return default
          v = self._value(v)
          return default if v is None else v

     def _pairs(self):
          import weakref
          for key, v in list(self.d.items()):
               k = key() if type(key) is weakref.ReferenceType else key
               v = self._value(v)
               if k is not None and v is not None:
                    yield k, v

     def __iter__(self):
          return iter([k for k, _ in self._pairs()])

     def keys(self):
          return [k for k, _ in self._pairs()]

     def items(self):
          return list(self._pairs())

     def values(self):
          return [v for _, v in self._pairs()]

     def __len__(self):
          return len(list(self._pairs()))


def set_weak_mode(table, mode):
     """Make `table` weak as its metatable's `__mode` asks ('k', 'v', 'kv'),
     or strong again."""
     weak_keys = isinstance(mode, str) and 'k' in mode
     weak_values = isinstance(mode, str) and 'v' in mode
     current = table.hash
     if isinstance(current, _WeakHash):
          if (current.weak_keys, current.weak_values) == (weak_keys, weak_values):
               return
          items = current.items()
     else:
          if not (weak_keys or weak_values):
               return
          items = list(current.items())
     if weak_keys or weak_values:
          table.hash = _WeakHash(weak_keys, weak_values, items)
     else:
          table.hash = dict(items)
     table._keys = None


class _BoolKey:
     """Table key standing for true/false: Python's True == 1 would otherwise
     make t[true] and t[1] the same slot."""
     __slots__ = ('value',)

     def __init__(self, value):
          self.value = value

     def __repr__(self):
          return f'_BoolKey({self.value})'


_TRUE_KEY = _BoolKey(True)
_FALSE_KEY = _BoolKey(False)


def _key(k):
     t = type(k)
     if t is str or t is int:
          return k
     if t is float:
          if k.is_integer():
               return int(k)
          return k
     if t is bool:
          return _TRUE_KEY if k else _FALSE_KEY
     return k


def _unkey(k):
     if type(k) is _BoolKey:
          return k.value
     return k


class LuaFunction:
     """A Lua closure. `env` is its environment, the table its global names
     are looked up in (Lua 5.1's `setfenv`); a closure starts with the
     environment of the function that created it."""
     __slots__ = ('proto', 'upvals', 'vm', 'env', '__weakref__')

     def __init__(self, proto, upvals, vm, env=None):
          self.proto = proto
          self.upvals = upvals
          self.vm = vm
          self.env = vm.globals if env is None else env

     def __call__(self, *args):
          """Call from Python; returns the list of results."""
          return self.vm.call(self, list(args))

     def __repr__(self):
          return f'<LuaFunction {self.proto.name}>'


class PyFunction:
     """A function implemented in Python: `fn(args)` takes the argument list
     and returns the list of results (or None for no result)."""
     __slots__ = ('fn', 'name', '__weakref__')

     def __init__(self, fn, name='?'):
          self.fn = fn
          self.name = name

     def __call__(self, *args):
          return self.fn(list(args)) or []

     def __repr__(self):
          return f'<PyFunction {self.name}>'


class LuaUserdata:
     """Base for host objects exposed to Lua (file handles, ...)."""
     metatable = None


class Coroutine:
     """A Lua coroutine. Each one runs on its own thread, and control is
     handed over explicitly, so exactly one thread runs Lua at any time and
     `yield` works from anywhere, inside pcall and metamethods included."""

     def __init__(self, vm, fn):
          self.vm = vm
          self.fn = fn
          self.status = 'suspended'
          self.thread = None
          self._resume = threading.Semaphore(0)
          self._yielded = threading.Semaphore(0)
          self._transfer = None
          self._depth = 0
          self._stack = []
          self.env = vm.globals

     def resume(self, args):
          vm = self.vm
          if self.status == 'dead':
               return [False, 'cannot resume dead coroutine']
          if self.status != 'suspended':
               return [False, 'cannot resume non-suspended coroutine']
          previous = vm.current_coroutine
          if previous is not None:
               previous.status = 'normal'
          saved_depth = vm.depth
          saved_stack = vm.stack
          saved_globals = vm.globals
          vm.depth = self._depth
          vm.stack = self._stack
          vm.globals = self.env
          self.status = 'running'
          vm.current_coroutine = self
          self._transfer = list(args)
          if self.thread is None:
               self.thread = _start_thread(self._run)
          else:
               self._resume.release()
          self._yielded.acquire()
          self._depth = vm.depth
          vm.depth = saved_depth
          vm.stack = saved_stack
          self.env = vm.globals
          vm.globals = saved_globals
          vm.current_coroutine = previous
          if previous is not None:
               previous.status = 'running'
          kind, values = self._transfer
          if kind == 'yield':
               self.status = 'suspended'
               return [True] + values
          self.status = 'dead'
          if kind == 'return':
               return [True] + values
          return [False, values]

     def _run(self):
          try:
               self._transfer = ('return', self.vm.call(self.fn, self._transfer))
          except LuaError as error:
               self._transfer = ('error', error.value)
          except RecursionError:
               self._transfer = ('error', 'stack overflow')
          except BaseException as error:  # pragma: no cover - interpreter bug
               self._transfer = ('error', f'internal error: {error!r}')
          self._yielded.release()

     def yield_(self, values):
          self._transfer = ('yield', list(values))
          self._yielded.release()
          self._resume.acquire()
          return self._transfer


STACK_SIZE = 256 * 1024 * 1024
RECURSION_LIMIT = 200000


def _start_thread(target):
     old = threading.stack_size()
     try:
          threading.stack_size(STACK_SIZE)
     except (ValueError, RuntimeError):  # pragma: no cover - platform limit
          pass
     try:
          thread = threading.Thread(target=target, daemon=True)
          thread.start()
     finally:
          threading.stack_size(old)
     return thread


def run_with_stack(fn, *args):
     """Run `fn(*args)` on a thread with a large stack, so deeply recursive
     Lua code is limited by the interpreter's call-depth limit rather than by
     the Python stack. Returns its result, re-raises its exception."""
     result = {}

     def target():
          old_limit = sys.getrecursionlimit()
          try:
               sys.setrecursionlimit(max(old_limit, RECURSION_LIMIT))
               result['value'] = fn(*args)
          except BaseException as error:
               result['error'] = error
          finally:
               sys.setrecursionlimit(old_limit)

     _start_thread(target).join()
     if 'error' in result:
          raise result['error']
     return result.get('value')


# --- helpers ------------------------------------------------------------------

def to_bytes_text(text):
     """Text (any Unicode) to the byte-string form the interpreter uses: its
     UTF-8 bytes, one character each."""
     if text.isascii():
          return text
     return text.encode('utf-8').decode('latin-1')


def to_text(value):
     """A byte string back to text: UTF-8 when it is valid UTF-8, latin-1
     otherwise."""
     if not isinstance(value, str):
          return value
     try:
          raw = value.encode('latin-1')
     except UnicodeEncodeError:
          return value
     try:
          return raw.decode('utf-8')
     except UnicodeDecodeError:
          return value


def type_name(v):
     if v is None:
          return 'nil'
     t = type(v)
     if t is bool:
          return 'boolean'
     if t is int or t is float:
          return 'number'
     if t is str:
          return 'string'
     if t is LuaTable:
          return 'table'
     if t is LuaFunction or t is PyFunction:
          return 'function'
     if t is Coroutine:
          return 'thread'
     return 'userdata'


def is_number(v):
     t = type(v)
     return t is int or t is float


def truthy(v):
     return v is not None and v is not False


_INT_MIN = -(1 << 63)
_INT_MAX = (1 << 63) - 1


def _wrap(v):
     if _INT_MIN <= v <= _INT_MAX:
          return v
     return ((v + (1 << 63)) % (1 << 64)) - (1 << 63)


_SPACE = ' \t\n\r\f\v'


def str_to_number(s):
     """Lua's string-to-number conversion; None when `s` is not a numeral."""
     text = s.strip(_SPACE)
     if not text:
          return None
     body = text
     sign = 1
     if body[0] in '+-':
          if body[0] == '-':
               sign = -1
          body = body[1:]
     if body[:2] in ('0x', '0X'):
          digits = body[2:]
          if not digits:
               return None
          if all(c in '0123456789abcdefABCDEF' for c in digits):
               return _wrap(sign * int(digits, 16))
          try:
               return sign * float.fromhex('0x' + digits)
          except ValueError:
               return None
     if not body or not (body[0].isdigit() or body[0] == '.'):
          return None
     integral = True
     i = 0
     n = len(body)
     seen_digit = False
     while i < n and body[i].isdigit():
          i += 1
          seen_digit = True
     if i < n and body[i] == '.':
          integral = False
          i += 1
          while i < n and body[i].isdigit():
               i += 1
               seen_digit = True
     if not seen_digit:
          return None
     if i < n and body[i] in 'eE':
          integral = False
          i += 1
          if i < n and body[i] in '+-':
               i += 1
          if i >= n or not body[i].isdigit():
               return None
          while i < n and body[i].isdigit():
               i += 1
     if i != n:
          return None
     if integral:
          value = int(body)
          if value <= _INT_MAX + (sign < 0):
               return sign * value
          return sign * float(body)
     return sign * float(body)


def _float_text(v):
     if v != v:
          return 'nan' if math.copysign(1, v) > 0 else '-nan'
     if v == math.inf:
          return 'inf'
     if v == -math.inf:
          return '-inf'
     return '%.14g' % v


def number_to_str(v, version='5.1'):
     if type(v) is int:
          if version == '5.1':
               return str(v) if -10 ** 14 < v < 10 ** 14 else _float_text(float(v))
          return str(v)
     text = _float_text(v)
     if version != '5.1' and v == v and v not in (math.inf, -math.inf) \
               and all(c not in text for c in '.eEn'):
          text += '.0'
     return text


# --- control-flow signals -------------------------------------------------------

class _Return:
     __slots__ = ('values',)

     def __init__(self, values):
          self.values = values


class _Goto:
     __slots__ = ('label',)

     def __init__(self, label):
          self.label = label


class _TailCall:
     __slots__ = ('fn', 'args')

     def __init__(self, fn, args):
          self.fn = fn
          self.args = args


_BREAK = object()


class _Cell:
     __slots__ = ('v',)

     def __init__(self, v=None):
          self.v = v


class _Frame:
     __slots__ = ('regs', 'up', 'varargs', 'fn', 'line')

     def __init__(self, regs, up, varargs, fn):
          self.regs = regs
          self.up = up
          self.varargs = varargs
          self.fn = fn
          self.line = None


class _Proto:
     __slots__ = ('name', 'nparams', 'is_vararg', 'nslots', 'body', 'upvals',
                  'chunk', 'line', 'position', 'upnames', 'slotnames', 'positions',
                  'needs_arg', 'source')

     def __init__(self, name, nparams, is_vararg, chunk, line, position):
          self.name = name
          self.nparams = nparams
          self.is_vararg = is_vararg
          self.nslots = 0
          self.body = None
          self.upvals = []
          self.chunk = chunk
          self.line = line
          self.position = position
          self.upnames = []
          self.slotnames = []
          self.positions = None
          self.needs_arg = False
          self.source = None


# --- compiler -----------------------------------------------------------------

# Lua 5.1's per-function limits (LUAI_MAXVARS, LUAI_MAXUPVALUES).
_MAX_LOCALS = 200
_MAX_UPVALUES = 60


class _FuncState:
     def __init__(self, parent, is_vararg, line=0, compiler=None):
          self.line = line
          self.compiler = compiler
          self.parent = parent
          self.is_vararg = is_vararg
          self.blocks = [{}]
          self.labels = [set()]
          self.nslots = 0
          self.upvals = []
          self.upnames = {}
          self.loops = 0
          self.slotnames = []

     def declare(self, name):
          slot = self.nslots
          self.nslots += 1
          self.blocks[-1][name] = slot
          self.slotnames.append(name)
          if self.compiler is not None and self.compiler.vm.version == '5.1' and \
                    sum(len(b) for b in self.blocks) > _MAX_LOCALS:
               self.limit(_MAX_LOCALS, 'local variables')
          return slot

     def limit(self, limit, what):
          where = 'main function' if not self.line else f'function at line {self.line}'
          raise LuaCompileError(f'{self.compiler.position(self.compiler.line)}: '
                                f'{where} has more than {limit} {what}')

     def find_local(self, name):
          for block in reversed(self.blocks):
               if name in block:
                    return block[name]
          return None

     def resolve(self, name):
          slot = self.find_local(name)
          if slot is not None:
               return 'local', slot
          if name in self.upnames:
               return 'up', self.upnames[name]
          if self.parent is None:
               return 'global', name
          kind, index = self.parent.resolve(name)
          if kind == 'global':
               return 'global', name
          position = len(self.upvals)
          self.upvals.append((kind == 'local', index))
          self.upnames[name] = position
          if self.compiler is not None and self.compiler.vm.version == '5.1' and \
                    len(self.upvals) > _MAX_UPVALUES:
               self.limit(_MAX_UPVALUES, 'upvalues')
          return 'up', position


class LuaCompileError(Exception):
     pass


class _Compiler:
     def __init__(self, vm, chunk, positions, source=None):
          self.vm = vm
          self.chunk = chunk
          self.positions = positions
          self.source = source if source is not None else '@' + chunk
          self.fs = None
          self.line = None

     def position(self, line):
          if line is None:
               return self.chunk
          if self.positions is not None and 0 < line <= len(self.positions):
               return self.positions[line - 1]
          return f'{self.chunk}:{line}'

     def describe(self, node):
          if isinstance(node, ir.VarExpr):
               kind, _ = self.fs.resolve(node.name)
               kind = {'local': 'local', 'up': 'upvalue', 'global': 'global'}[kind]
               return f"{kind} '{node.name}'"
          if isinstance(node, ir.IndexExpr) and isinstance(node.index, ir.Literal) \
                    and node.index.kind == 'string':
               return f"field '{to_text(node.index.value)}'"
          if isinstance(node, ir.LuaMethodCallExpr):
               return f"method '{node.method}'"
          return None

     # -- functions ---------------------------------------------------------

     def compile_chunk(self, chunk):
          proto = _Proto('main chunk', 0, True, self.chunk, 0, self.chunk)
          proto.positions = self.positions
          proto.source = self.source
          self.fs = _FuncState(None, True, 0, self)
          if self.vm.version != '5.1':
               # Lua 5.2+: a main chunk has exactly one upvalue, _ENV, and
               # every free name is a field of whatever _ENV is in scope
               self.fs.upnames['_ENV'] = 0
               proto.upnames = ['_ENV']
          proto.body = self.block(chunk.body)
          proto.nslots = self.fs.nslots
          proto.slotnames = self.fs.slotnames
          return proto

     def env_access(self):
          """5.2+: (kind, index) of the _ENV in scope for a free name."""
          return self.fs.resolve('_ENV')

     def function(self, params, is_vararg, body, name, line, method=False):
          outer = self.fs
          fs = _FuncState(outer, is_vararg, line or 0, self)
          self.fs = fs
          names = (['self'] if method else []) + list(params)
          for p in names:
               fs.declare(p)
          proto = _Proto(name, len(names), is_vararg, self.chunk, line,
                         self.position(line))
          if is_vararg and self.vm.version == '5.1' and not _uses_vararg(body):
               # Lua 5.1 gives a vararg function that never says `...` a
               # local `arg` holding its extra arguments, with `arg.n`.
               fs.declare('arg')
               proto.needs_arg = True
          try:
               proto.body = self.block(body, new_scope=False)
          finally:
               self.fs = outer
          proto.nslots = fs.nslots
          proto.upvals = fs.upvals
          proto.slotnames = fs.slotnames
          proto.positions = self.positions
          proto.source = self.source
          proto.upnames = sorted(fs.upnames, key=fs.upnames.get)
          upvals = tuple(fs.upvals)
          vm = self.vm

          def make(f):
               regs = f.regs
               up = f.up
               return LuaFunction(
                    proto, [regs[i] if is_local else up[i] for is_local, i in upvals], vm,
                    f.fn.env)
          return make

     # -- blocks ------------------------------------------------------------

     def block(self, statements, new_scope=True):
          fs = self.fs
          if new_scope:
               fs.blocks.append({})
          labels = {}
          for index, stmt in enumerate(statements):
               if isinstance(stmt, ir.LabelStmt):
                    if stmt.name in labels:
                         first = getattr(statements[labels[stmt.name]], 'line', None)
                         raise LuaCompileError(
                              f"{self.position(getattr(stmt, 'line', None))}: "
                              f"label '{stmt.name}' already defined on line {first}")
                    labels[stmt.name] = index
          if labels:
               _check_goto_scopes(self, statements, labels)
          fs.labels.append(set(labels))
          try:
               compiled = [self.statement(s) for s in statements]
          finally:
               fs.labels.pop()
               if new_scope:
                    fs.blocks.pop()
          if labels:
               count = len(compiled)

               def run(f):
                    i = 0
                    while i < count:
                         sig = compiled[i](f)
                         if sig is not None:
                              if type(sig) is _Goto and sig.label in labels:
                                   i = labels[sig.label]
                                   continue
                              return sig
                         i += 1
                    return None
               return run
          if not compiled:
               return lambda f: None
          if len(compiled) == 1:
               return compiled[0]
          compiled = tuple(compiled)

          def run(f):
               for s in compiled:
                    sig = s(f)
                    if sig is not None:
                         return sig
               return None
          return run

     def statement(self, stmt):
          self.line = getattr(stmt, 'line', None)
          inner = self._statement(stmt)
          line = getattr(stmt, 'line', None)
          position = self.position(line)
          chunk = self.chunk

          def run(f):
               f.line = line
               try:
                    return inner(f)
               except LuaError as error:
                    _annotate(error, f, position)
                    raise
               except RecursionError:
                    raise
               except Exception as error:
                    # A Python exception escaping a library function is a bug
                    # in it; report it as a Lua error at this line rather than
                    # as a bare Python traceback.
                    lua_error = LuaError(f'internal error: {error!r}')
                    _annotate(lua_error, f, position)
                    raise lua_error from error
          run.__name__ = f'stmt_{chunk}_{line}'
          return run

     def _statement(self, stmt):
          method = getattr(self, 'stmt_' + type(stmt).__name__, None)
          if method is None:
               raise LuaCompileError(f'unsupported statement {type(stmt).__name__}')
          return method(stmt)

     # -- statements ----------------------------------------------------------

     def stmt_LocalStmt(self, stmt):
          # The values are compiled before the names are declared: in
          # `local x = x` the right side is the outer x.
          values = self.explist(stmt.values, len(stmt.names))
          single = None
          if len(stmt.names) == 1 and len(stmt.values) == 1 \
                    and not _is_multi(stmt.values[0]):
               single = self.expr(stmt.values[0])
          slots = [self.fs.declare(name) for name in stmt.names]
          if len(slots) == 1:
               slot = slots[0]
               if stmt.values:
                    if single is not None:
                         def run(f):
                              f.regs[slot] = _Cell(single(f))
                         return run

               def run(f):
                    vals = values(f)
                    f.regs[slot] = _Cell(vals[0] if vals else None)
               return run
          count = len(slots)

          def run(f):
               vals = values(f)
               regs = f.regs
               for i in range(count):
                    regs[slots[i]] = _Cell(vals[i] if i < len(vals) else None)
          return run

     def stmt_LocalFunctionStmt(self, stmt):
          slot = self.fs.declare(stmt.name)
          make = self.function(stmt.params, stmt.is_vararg, stmt.body, stmt.name,
                               getattr(stmt, 'line', None))

          def run(f):
               cell = _Cell()
               f.regs[slot] = cell
               cell.v = make(f)
          return run

     def stmt_FunctionStmt(self, stmt):
          path = stmt.name_path
          name = '.'.join(path) + (':' + stmt.method_name if stmt.method_name else '')
          make = self.function(stmt.params, stmt.is_vararg, stmt.body, name,
                               getattr(stmt, 'line', None),
                               method=stmt.method_name is not None)
          if len(path) == 1 and stmt.method_name is None:
               store = self.store(ir.VarExpr(path[0]))

               def run(f):
                    store(f, make(f))
               return run
          target = ir.VarExpr(path[0])
          for part in path[1:]:
               target = ir.IndexExpr(target, ir.Literal('string', part))
          last = stmt.method_name if stmt.method_name else None
          if last is None:
               obj = self.expr(target.obj)
               key = target.index.value
          else:
               obj = self.expr(target)
               key = last
          vm = self.vm

          def run(f):
               vm.setindex(obj(f), key, make(f))
          return run

     def stmt_AssignStmt(self, stmt):
          targets = stmt.targets
          if len(targets) == 1 and len(stmt.values) == 1:
               value = self.expr(stmt.values[0])
               target = targets[0]
               if isinstance(target, ir.IndexExpr):
                    obj = self.expr(target.obj)
                    key = self.expr(target.index)
                    vm = self.vm
                    desc = self.describe(target.obj)

                    def run(f):
                         o = obj(f)
                         k = key(f)
                         vm.setindex(o, k, value(f), desc)
                    return run
               store = self.store(target)

               def run(f):
                    store(f, value(f))
               return run
          # General case: evaluate the targets' tables and keys, then every
          # value, then assign.
          prepared = []
          for target in targets:
               if isinstance(target, ir.IndexExpr):
                    prepared.append(('index', self.expr(target.obj), self.expr(target.index),
                                     self.describe(target.obj)))
               else:
                    prepared.append(('name', self.store(target), None, None))
          values = self.explist(stmt.values, len(targets))
          vm = self.vm

          def run(f):
               places = []
               for kind, a, b, desc in prepared:
                    if kind == 'index':
                         places.append((a(f), b(f), desc))
                    else:
                         places.append((a, None, None))
               vals = values(f)
               for i, (a, b, desc) in enumerate(places):
                    v = vals[i] if i < len(vals) else None
                    if prepared[i][0] == 'index':
                         vm.setindex(a, b, v, desc)
                    else:
                         a(f, v)
          return run

     def stmt_ExprStmt(self, stmt):
          call = self.call(stmt.call, multi=True)

          def run(f):
               call(f)
          return run

     def stmt_DoStmt(self, stmt):
          return self.block(stmt.body)

     def stmt_WhileStmt(self, stmt):
          cond = self.expr(stmt.condition)
          self.fs.loops += 1
          try:
               body = self.block(stmt.body)
          finally:
               self.fs.loops -= 1
          vm = self.vm

          def run(f):
               while truthy(cond(f)):
                    vm.tick()
                    sig = body(f)
                    if sig is not None:
                         if sig is _BREAK:
                              break
                         return sig
          return run

     def stmt_RepeatStmt(self, stmt):
          fs = self.fs
          fs.blocks.append({})
          fs.loops += 1
          try:
               body = self.block(stmt.body, new_scope=False)
               cond = self.expr(stmt.condition)
          finally:
               fs.loops -= 1
               fs.blocks.pop()
          vm = self.vm

          def run(f):
               while True:
                    vm.tick()
                    sig = body(f)
                    if sig is not None:
                         if sig is _BREAK:
                              break
                         return sig
                    if truthy(cond(f)):
                         break
          return run

     def stmt_IfStmt(self, stmt):
          branches = [(self.expr(c), self.block(b)) for c, b in stmt.branches]
          else_body = self.block(stmt.else_body) if stmt.else_body is not None else None
          if len(branches) == 1:
               cond, body = branches[0]
               if else_body is None:
                    def run(f):
                         v = cond(f)
                         if v is not None and v is not False:
                              return body(f)
                    return run

               def run(f):
                    v = cond(f)
                    if v is not None and v is not False:
                         return body(f)
                    return else_body(f)
               return run

          def run(f):
               for cond, body in branches:
                    v = cond(f)
                    if v is not None and v is not False:
                         return body(f)
               if else_body is not None:
                    return else_body(f)
          return run

     def stmt_NumericForStmt(self, stmt):
          start = self.expr(stmt.start)
          stop = self.expr(stmt.stop)
          step = self.expr(stmt.step) if stmt.step is not None else None
          fs = self.fs
          fs.blocks.append({})
          slot = fs.declare(stmt.name)
          fs.loops += 1
          try:
               body = self.block(stmt.body)
          finally:
               fs.loops -= 1
               fs.blocks.pop()
          vm = self.vm

          def number(v, what):
               if is_number(v):
                    return v
               if type(v) is str:
                    n = str_to_number(v)
                    if n is not None:
                         return n
               raise LuaError(f"'for' {what} must be a number")

          def run(f):
               a = number(start(f), 'initial value')
               b = number(stop(f), 'limit')
               c = number(step(f), 'step') if step is not None else 1
               if c == 0:
                    raise LuaError("'for' step is zero")
               if type(a) is int and type(c) is int:
                    if type(b) is float:
                         if b != b:
                              return None
                         b = math.floor(b) if c > 0 else math.ceil(b)
                         if b in (math.inf, -math.inf):
                              b = _INT_MAX if b > 0 else _INT_MIN
                         b = int(b)
               else:
                    a = float(a)
                    b = float(b)
                    c = float(c)
               i = a
               regs = f.regs
               if c > 0:
                    while i <= b:
                         vm.tick()
                         regs[slot] = _Cell(i)
                         sig = body(f)
                         if sig is not None:
                              if sig is _BREAK:
                                   break
                              return sig
                         i += c
               else:
                    while i >= b:
                         vm.tick()
                         regs[slot] = _Cell(i)
                         sig = body(f)
                         if sig is not None:
                              if sig is _BREAK:
                                   break
                              return sig
                         i += c
          return run

     def stmt_GenericForStmt(self, stmt):
          exprs = self.explist(stmt.exprs, 3)
          # Lua reports a failing iterator call at the line the expression
          # list ends on, not where `for` is.
          iter_position = self.position(getattr(stmt.exprs[-1], 'line', None)
                                        or getattr(stmt, 'line', None))
          fs = self.fs
          fs.blocks.append({})
          slots = [fs.declare(n) for n in stmt.names]
          fs.loops += 1
          try:
               body = self.block(stmt.body)
          finally:
               fs.loops -= 1
               fs.blocks.pop()
          vm = self.vm
          count = len(slots)
          next_fn = vm.next_function

          def run(f):
               vals = exprs(f)
               fn, state, control = (vals + [None, None, None])[:3]
               regs = f.regs
               if fn is next_fn and type(state) is LuaTable and control is None \
                         and state.metatable is None:
                    # pairs() over a plain table: iterate a snapshot of its
                    # keys directly instead of calling next() for each step.
                    h = state.hash
                    for k in list(h):
                         v = h.get(k)
                         if v is None:
                              continue
                         vm.tick()
                         regs[slots[0]] = _Cell(_unkey(k))
                         if count > 1:
                              regs[slots[1]] = _Cell(v)
                              for i in range(2, count):
                                   regs[slots[i]] = _Cell(None)
                         sig = body(f)
                         if sig is not None:
                              if sig is _BREAK:
                                   break
                              return sig
                    return None
               while True:
                    vm.tick()
                    try:
                         results = vm.call(fn, [state, control])
                    except LuaError as error:
                         _annotate(error, f, iter_position)
                         raise
                    first = results[0] if results else None
                    if first is None:
                         break
                    control = first
                    for i in range(count):
                         regs[slots[i]] = _Cell(results[i] if i < len(results) else None)
                    sig = body(f)
                    if sig is not None:
                         if sig is _BREAK:
                              break
                         return sig
          return run

     def stmt_BreakStmt(self, stmt):
          if self.fs.loops == 0:
               raise LuaCompileError(
                    f"{self.position(getattr(stmt, 'line', None))}: "
                    f"break outside a loop")
          return lambda f: _BREAK

     def stmt_GotoStmt(self, stmt):
          if not any(stmt.label in labels for labels in self.fs.labels):
               line = getattr(stmt, 'line', None)
               raise LuaCompileError(
                    f"{self.position(line)}: "
                    f"no visible label '{stmt.label}' for <goto> at line {line}")
          signal = _Goto(stmt.label)
          return lambda f: signal

     def stmt_LabelStmt(self, stmt):
          return lambda f: None

     def stmt_LuaReturnStmt(self, stmt):
          values = stmt.values
          if not values:
               return lambda f: _Return([])
          if len(values) == 1 and _is_multi(values[0]) and \
                    not isinstance(values[0], ir.VarargExpr):
               # A tail call: the callee runs in this call's place, so a
               # tail-recursive loop does not grow the stack.
               prepare = self.call_parts(values[0])
               vm = self.vm

               def tail(f):
                    fn, args, desc = prepare(f)
                    if type(fn) is LuaFunction:
                         return _TailCall(fn, args)
                    return _Return(vm.call(fn, args, desc))
               return tail
          if len(values) == 1 and not _is_multi(values[0]):
               single = self.expr(values[0])
               return lambda f: _Return([single(f)])
          explist = self.explist(values)
          return lambda f: _Return(explist(f))

     # -- stores ----------------------------------------------------------------

     def store(self, target):
          if isinstance(target, ir.VarExpr):
               kind, index = self.fs.resolve(target.name)
               if kind == 'local':
                    def store_local(f, v):
                         f.regs[index].v = v
                    return store_local
               if kind == 'up':
                    def store_up(f, v):
                         f.up[index].v = v
                    return store_up
               vm = self.vm
               name = target.name
               if vm.version != '5.1':
                    env_kind, env_index = self.env_access()

                    def store_env_field(f, v):
                         env = (f.regs if env_kind == 'local' else f.up)[env_index].v
                         if type(env) is LuaTable and env.metatable is None:
                              if v is None:
                                   env.hash.pop(name, None)
                              else:
                                   if name not in env.hash:
                                        env._keys = None
                                   env.hash[name] = v
                         else:
                              vm.setindex(env, name, v, "upvalue '_ENV'")
                    return store_env_field

               def store_global(f, v):
                    env = f.fn.env
                    if env.metatable is None:
                         if v is None:
                              env.hash.pop(name, None)
                         else:
                              if name not in env.hash:
                                   env._keys = None
                              env.hash[name] = v
                    else:
                         vm.setindex(env, name, v)
               return store_global
          if isinstance(target, ir.IndexExpr):
               obj = self.expr(target.obj)
               key = self.expr(target.index)
               vm = self.vm
               desc = self.describe(target.obj)

               def store_index(f, v):
                    vm.setindex(obj(f), key(f), v, desc)
               return store_index
          raise LuaCompileError('cannot assign to this expression')

     # -- expressions -------------------------------------------------------------

     def explist(self, exprs, wanted=None):
          """Evaluate an expression list to a Python list, the last expression
          expanded to all its values."""
          if not exprs:
               return lambda f: []
          head = [self.expr(e) for e in exprs[:-1]]
          last = exprs[-1]
          if _is_multi(last):
               tail = self.multi(last)
               if not head:
                    return tail

               def run(f):
                    return [h(f) for h in head] + tail(f)
               return run
          compiled = head + [self.expr(last)]
          if len(compiled) == 1:
               only = compiled[0]
               return lambda f: [only(f)]
          return lambda f: [c(f) for c in compiled]

     def multi(self, expr):
          if isinstance(expr, ir.VarargExpr):
               return lambda f: list(f.varargs)
          return self.call(expr, multi=True)

     def expr(self, expr):
          method = getattr(self, 'expr_' + type(expr).__name__, None)
          if method is None:
               raise LuaCompileError(f'unsupported expression {type(expr).__name__}')
          return method(expr)

     def expr_Literal(self, expr):
          kind = expr.kind
          if kind == 'nil':
               return lambda f: None
          if kind == 'bool':
               value = bool(expr.value)
          elif kind == 'string':
               value = expr.value
          elif kind == 'int':
               value = _wrap(expr.value) if self.vm.version != '5.1' else expr.value
               if self.vm.version == '5.1' and not (-(1 << 53) <= value <= (1 << 53)):
                    value = float(value)
          else:
               value = float(expr.value)
          return lambda f: value

     def expr_VarExpr(self, expr):
          kind, index = self.fs.resolve(expr.name)
          if kind == 'local':
               return lambda f: f.regs[index].v
          if kind == 'up':
               return lambda f: f.up[index].v
          vm = self.vm
          name = expr.name
          if vm.version != '5.1':
               env_kind, env_index = self.env_access()

               def get_env_field(f):
                    env = (f.regs if env_kind == 'local' else f.up)[env_index].v
                    if type(env) is LuaTable:
                         v = env.hash.get(name)
                         if v is not None or env.metatable is None:
                              return v
                    return vm.index(env, name, "upvalue '_ENV'")
               return get_env_field

          def get_global(f):
               env = f.fn.env
               v = env.hash.get(name)
               if v is None and env.metatable is not None:
                    return vm.index(env, name)
               return v
          return get_global

     def expr_VarargExpr(self, expr):
          if not self.fs.is_vararg:
               raise LuaCompileError(
                    f"{self.position(getattr(expr, 'line', None))}: "
                    f"cannot use '...' outside a vararg function")
          return lambda f: f.varargs[0] if f.varargs else None

     def expr_IndexExpr(self, expr):
          obj = self.expr(expr.obj)
          desc = self.describe(expr.obj)
          vm = self.vm
          if isinstance(expr.index, ir.Literal) and expr.index.kind == 'string':
               key = expr.index.value

               def index_const(f):
                    o = obj(f)
                    if type(o) is LuaTable:
                         v = o.hash.get(key)
                         if v is not None or o.metatable is None:
                              return v
                    return vm.index(o, key, desc)
               return index_const
          key = self.expr(expr.index)

          def index(f):
               o = obj(f)
               k = key(f)
               if type(o) is LuaTable:
                    v = o.hash.get(_key(k))
                    if v is not None or o.metatable is None:
                         return v
               return vm.index(o, k, desc)
          return index

     def expr_LuaCallExpr(self, expr):
          return self.call(expr, multi=False)

     def expr_LuaMethodCallExpr(self, expr):
          return self.call(expr, multi=False)

     def call_parts(self, expr):
          """A closure evaluating a call's function and arguments, without
          calling: (fn, args, description for errors)."""
          vm = self.vm
          args = self.explist(expr.args)
          if isinstance(expr, ir.LuaMethodCallExpr):
               obj = self.expr(expr.obj)
               method = expr.method
               desc = f"method '{method}'"
               obj_desc = self.describe(expr.obj)

               def prepare(f):
                    o = obj(f)
                    fn = vm.index(o, method, obj_desc)
                    a = args(f)
                    a.insert(0, o)
                    return fn, a, desc
               return prepare
          func = self.expr(expr.func)
          desc = self.describe(expr.func)

          def prepare(f):
               return func(f), args(f), desc
          return prepare

     def call(self, expr, multi):
          vm = self.vm
          args = self.explist(expr.args)
          # Lua gives a call the line its prefix expression starts on, which
          # can differ from the statement's (a call after a multi-line string)
          line = _start_line(expr)
          if line is None:
               line = self.line
          if isinstance(expr, ir.LuaMethodCallExpr):
               obj = self.expr(expr.obj)
               method = expr.method
               desc = f"method '{method}'"
               obj_desc = self.describe(expr.obj)

               def run(f):
                    o = obj(f)
                    fn = vm.index(o, method, obj_desc)
                    a = args(f)
                    a.insert(0, o)
                    f.line = line
                    return vm.call(fn, a, desc)
          else:
               func = self.expr(expr.func)
               desc = self.describe(expr.func)

               def run(f):
                    fn = func(f)
                    a = args(f)
                    f.line = line
                    return vm.call(fn, a, desc)
          if multi and not getattr(expr, 'paren', False):
               return run

          def first(f):
               r = run(f)
               return r[0] if r else None
          return first

     def expr_LuaFunctionExpr(self, expr):
          return self.function(expr.params, expr.is_vararg, expr.body,
                               'anonymous', getattr(expr, 'line', None))

     def expr_TableExpr(self, expr):
          fields = expr.fields
          if not fields:
               return lambda f: LuaTable()
          positional = []
          keyed = []
          last_multi = None
          for i, (key, value) in enumerate(fields):
               if key is None:
                    if i == len(fields) - 1 and _is_multi(value):
                         last_multi = (len(positional), self.multi(value))
                    else:
                         positional.append((len(positional) + 1, self.expr(value)))
               else:
                    keyed.append((self.expr(key), self.expr(value), len(positional)))

          def run(f):
               t = LuaTable()
               h = t.hash
               ki = 0
               for n, value in positional:
                    # Keyed fields are evaluated in source order with the
                    # positional ones; a positional item always wins its slot.
                    while ki < len(keyed) and keyed[ki][2] < n:
                         kexpr, vexpr, _ = keyed[ki]
                         k = kexpr(f)
                         if k is None:
                              raise LuaError('table index is nil')
                         v = vexpr(f)
                         if v is not None:
                              h[_key(k)] = v
                         ki += 1
                    v = value(f)
                    if v is not None:
                         h[n] = v
                    else:
                         h.pop(n, None)
               while ki < len(keyed):
                    kexpr, vexpr, _ = keyed[ki]
                    k = kexpr(f)
                    if k is None:
                         raise LuaError('table index is nil')
                    v = vexpr(f)
                    if v is not None:
                         h[_key(k)] = v
                    ki += 1
               if last_multi is not None:
                    base, values = last_multi
                    for i, v in enumerate(values(f), base + 1):
                         if v is not None:
                              h[i] = v
               return t
          return run

     def expr_UnaryExpr(self, expr):
          operand = self.expr(expr.operand)
          vm = self.vm
          op = expr.op
          desc = self.describe(expr.operand)
          if op == 'not':
               return lambda f: not truthy(operand(f))
          if op == '-':
               def neg(f):
                    v = operand(f)
                    t = type(v)
                    if t is int and v != _INT_MIN:
                         return -v
                    if t is float:
                         return -v
                    return vm.arith('unm', v, v, desc, desc)
               return neg
          if op == '#':
               def length(f):
                    v = operand(f)
                    if type(v) is str:
                         return len(v)
                    return vm.length(v, desc)
               return length
          if op == '~':
               return lambda f: vm.arith('bnot', operand(f), 0, desc, desc)
          raise LuaCompileError(f'unknown unary operator {op}')

     def expr_BinaryExpr(self, expr):
          op = expr.op
          left = self.expr(expr.left)
          right = self.expr(expr.right)
          vm = self.vm
          if op == 'and':
               def and_(f):
                    v = left(f)
                    if v is None or v is False:
                         return v
                    return right(f)
               return and_
          if op == 'or':
               def or_(f):
                    v = left(f)
                    if v is not None and v is not False:
                         return v
                    return right(f)
               return or_
          ldesc = self.describe(expr.left)
          rdesc = self.describe(expr.right)
          overflow = vm.overflow
          if op == '==':
               return lambda f: vm.eq(left(f), right(f))
          if op in ('~=', '!='):
               return lambda f: not vm.eq(left(f), right(f))
          if op == '<':
               return lambda f: vm.lt(left(f), right(f))
          if op == '<=':
               return lambda f: vm.le(left(f), right(f))
          if op == '>':
               def gt(f):
                    a = left(f)
                    return vm.lt(right(f), a)
               return gt
          if op == '>=':
               def ge(f):
                    a = left(f)
                    return vm.le(right(f), a)
               return ge
          if op == '..':
               def concat(f):
                    a = left(f)
                    b = right(f)
                    if type(a) is str and type(b) is str:
                         return a + b
                    return vm.concat(a, b, ldesc, rdesc)
               return concat
          if op == '+':
               def add(f):
                    a = left(f)
                    b = right(f)
                    ta = type(a)
                    tb = type(b)
                    if ta is int and tb is int:
                         r = a + b
                         return r if _INT_MIN <= r <= _INT_MAX else overflow(r)
                    if (ta is float or ta is int) and (tb is float or tb is int):
                         return a + b
                    return vm.arith('add', a, b, ldesc, rdesc)
               return add
          if op == '-':
               def sub(f):
                    a = left(f)
                    b = right(f)
                    ta = type(a)
                    tb = type(b)
                    if ta is int and tb is int:
                         r = a - b
                         return r if _INT_MIN <= r <= _INT_MAX else overflow(r)
                    if (ta is float or ta is int) and (tb is float or tb is int):
                         return a - b
                    return vm.arith('sub', a, b, ldesc, rdesc)
               return sub
          if op == '*':
               def mul(f):
                    a = left(f)
                    b = right(f)
                    ta = type(a)
                    tb = type(b)
                    if ta is int and tb is int:
                         r = a * b
                         return r if _INT_MIN <= r <= _INT_MAX else overflow(r)
                    if (ta is float or ta is int) and (tb is float or tb is int):
                         return a * b
                    return vm.arith('mul', a, b, ldesc, rdesc)
               return mul
          name = _ARITH_NAMES.get(op)
          if name is None:
               raise LuaCompileError(f'unknown binary operator {op}')
          return lambda f: vm.arith(name, left(f), right(f), ldesc, rdesc)


_ARITH_NAMES = {
     '/': 'div', '%': 'mod', '^': 'pow', '//': 'idiv',
     '&': 'band', '|': 'bor', '~': 'bxor', '<<': 'shl', '>>': 'shr',
}


def _uses_vararg(body):
     """Whether `...` appears in a function body (outside nested functions)."""
     stack = list(body)
     while stack:
          node = stack.pop()
          if isinstance(node, ir.VarargExpr):
               return True
          if isinstance(node, (ir.LuaFunctionExpr, ir.FunctionStmt, ir.LocalFunctionStmt)):
               continue
          if isinstance(node, (list, tuple)):
               stack.extend(node)
               continue
          if isinstance(node, ir.Node):
               for name in node._fields:
                    value = getattr(node, name)
                    if isinstance(value, (ir.Node, list, tuple)):
                         stack.append(value)
     return False


def _is_multi(expr):
     return isinstance(expr, (ir.LuaCallExpr, ir.LuaMethodCallExpr, ir.VarargExpr)) \
          and not getattr(expr, 'paren', False)


def _annotate(error, frame, position):
     if error.stack is None and frame.fn is not None:
          error.stack = list(frame.fn.vm.stack)
     if error.level == 1:
          error.value = f'{position}: {error.value}'
          error.level = 0
     if error._trace_frame is not frame:
          error._trace_frame = frame
          name = frame.fn.proto.name if frame.fn is not None else 'main chunk'
          error.traceback.append((position, name))


# --- the interpreter ------------------------------------------------------------

class LuaVM:
     """A Lua state.

     version
          '5.1' (default) or '5.3'; see the module docstring.
     host
          A `lua_stdlib.LuaHost` giving the script its files, `print` output
          and processes. The default allows no file or process access.
     max_steps
          Loop iterations and calls before the script is stopped with an
          error (None for no limit).
     max_depth
          Nested Lua calls before "stack overflow".
     """

     def __init__(self, version='5.1', host=None, max_steps=50_000_000, max_depth=2000,
                  stdlib=True):
          if version not in ('5.1', '5.3'):
               raise ValueError(f'unsupported Lua version {version!r}')
          self.version = version
          # What an integer result that leaves the 64-bit range becomes:
          # wrapped in 5.3, a double in 5.1.
          self.overflow = float if version == '5.1' else _wrap
          self.globals = LuaTable()
          self.string_meta = None
          self.max_steps = max_steps
          self.max_depth = max_depth
          self.steps = 0
          self.depth = 0
          self.stack = []
          self.type_metas = {}
          self.dumps = []
          self.current_coroutine = None
          self.registry = {}
          self.next_function = PyFunction(self._next, 'next')
          if stdlib:
               from . import lua_stdlib
               lua_stdlib.install(self, host)

     # -- running -------------------------------------------------------------

     def compile(self, source, chunkname='?', positions=None, binary=False, env=None,
                 source_name=None):
          """Compile source text to a function. `positions`, if given, names
          the source position of each line (for error messages), replacing
          the default `chunkname:line`. `binary` says `source` is already a
          byte string (a Lua string value, or a file read as latin-1)."""
          text = source if binary else to_bytes_text(source)
          if '\r' in text:
               # Lua takes \r\n, \n\r and a lone \r each as one line break,
               # inside long strings too.
               text = re.sub(r'\r\n|\n\r|\r', '\n', text)
          if text.startswith('#'):
               newline = text.find('\n')
               text = '--' + text[newline:] if newline >= 0 else ''
          try:
               tree = _parse(text, self.version)
          except (LuaParseError, LuaLexError) as error:
               line = getattr(error, 'line', None)
               where = _position_of(chunkname, positions, line)
               message = _syntax_message(error)
               if self.version != '5.1':
                    # 5.1 quotes <eof> like any token; 5.2+ do not
                    message = message.replace("near '<eof>'", 'near <eof>')
               raise LuaError(f'{where}: {message}', level=0) from None
          compiler = _Compiler(self, chunkname, positions, source_name)
          try:
               proto = compiler.compile_chunk(tree)
          except LuaCompileError as error:
               raise LuaError(str(error), level=0) from None
          if self.version != '5.1':
               return LuaFunction(proto, [_Cell(self.globals if env is None else env)],
                                  self, env)
          return LuaFunction(proto, [], self, env)

     def execute(self, source, chunkname='?', positions=None, args=(), binary=False):
          """Compile and run source text; returns the chunk's results."""
          fn = self.compile(source, chunkname, positions, binary=binary)
          return self.call(fn, list(args))

     def tick(self):
          self.steps += 1
          if self.max_steps is not None and self.steps > self.max_steps:
               raise LuaError(f'script stopped after {self.max_steps} steps '
                              f'(an endless loop?)', level=0)

     # -- globals -------------------------------------------------------------------

     def get_global(self, name):
          return self.index(self.globals, name)

     def set_global(self, name, value):
          g = self.globals
          if g.metatable is None:
               g.set(name, value)
          else:
               self.setindex(g, name, value)

     def register(self, name, fn, table=None):
          """Expose a Python function `fn(args) -> list` as a Lua global (or
          as a field of `table`)."""
          wrapped = PyFunction(fn, name)
          (table or self.globals).set(name, wrapped)
          return wrapped

     # -- calls -----------------------------------------------------------------------

     def call(self, fn, args, desc=None):
          t = type(fn)
          if t is LuaFunction:
               return self._call_lua(fn, args)
          if t is PyFunction:
               result = fn.fn(args)
               return [] if result is None else result
          handler = self._metamethod(fn, '__call')
          if handler is not None:
               return self.call(handler, [fn] + list(args))
          raise LuaError(_op_error('call', fn, desc, self.version))

     def _call_lua(self, fn, args):
          proto = fn.proto
          self.steps += 1
          if self.max_steps is not None and self.steps > self.max_steps:
               raise LuaError(f'script stopped after {self.max_steps} steps '
                              f'(an endless loop?)', level=0)
          self.depth += 1
          if self.depth > self.max_depth:
               self.depth -= 1
               raise LuaError('stack overflow')
          try:
               stack = self.stack
               while True:
                    regs = [None] * proto.nslots
                    n = proto.nparams
                    nargs = len(args)
                    for i in range(n):
                         regs[i] = _Cell(args[i] if i < nargs else None)
                    varargs = args[n:] if proto.is_vararg else None
                    if proto.needs_arg:
                         extra = LuaTable.from_list(varargs)
                         extra.set('n', len(varargs))
                         regs[n] = _Cell(extra)
                    frame = _Frame(regs, fn.upvals, varargs, fn)
                    stack.append(frame)
                    try:
                         sig = proto.body(frame)
                    finally:
                         stack.pop()
                    if type(sig) is not _TailCall:
                         break
                    fn = sig.fn
                    args = sig.args
                    proto = fn.proto
                    self.steps += 1
                    if self.max_steps is not None and self.steps > self.max_steps:
                         raise LuaError(f'script stopped after {self.max_steps} steps '
                                        f'(an endless loop?)', level=0)
          except LuaError as error:
               if error.level > 1:
                    error.level -= 1
               raise
          except RecursionError:
               raise LuaError('stack overflow') from None
          finally:
               self.depth -= 1
          if sig is None:
               return []
          if type(sig) is _Return:
               return sig.values
          if type(sig) is _Goto:  # pragma: no cover - rejected at compile time
               raise LuaError(f"no visible label '{sig.label}' for goto")
          return []

     def pcall(self, fn, args):
          """Call `fn` in protected mode: (True, results) or (False, error)."""
          depth = self.depth
          height = len(self.stack)
          try:
               return True, self.call(fn, args)
          except LuaError as error:
               self.depth = depth
               del self.stack[height:]
               return False, error
          except RecursionError:
               self.depth = depth
               del self.stack[height:]
               return False, LuaError('stack overflow', level=0)

     # -- metatables --------------------------------------------------------------------

     def getmetatable(self, v):
          t = type(v)
          if t is LuaTable:
               return v.metatable
          if t is str:
               return self.string_meta
          if isinstance(v, LuaUserdata):
               return v.metatable
          if self.type_metas:
               return self.type_metas.get(type_name(v))
          return None

     def _metamethod(self, v, event):
          mt = self.getmetatable(v)
          if mt is None:
               return None
          return mt.hash.get(event)

     # -- indexing -------------------------------------------------------------------------

     def index(self, o, k, desc=None):
          for _ in range(100):
               if type(o) is LuaTable:
                    v = o.hash.get(_key(k))
                    if v is not None:
                         return v
                    mt = o.metatable
                    if mt is None:
                         return None
                    handler = mt.hash.get('__index')
                    if handler is None:
                         return None
               else:
                    handler = self._metamethod(o, '__index')
                    if handler is None:
                         raise LuaError(_op_error('index', o, desc, self.version))
               if type(handler) is LuaFunction or type(handler) is PyFunction:
                    r = self.call(handler, [o, k])
                    return r[0] if r else None
               o = handler
               desc = None
          raise LuaError("'__index' chain too long; possible loop")

     def setindex(self, o, k, v, desc=None):
          for _ in range(100):
               if type(o) is LuaTable:
                    mt = o.metatable
                    if mt is None:
                         o.set(k, v)
                         return
                    handler = mt.hash.get('__newindex')
                    if handler is None or o.hash.get(_key(k)) is not None:
                         o.set(k, v)
                         return
               else:
                    handler = self._metamethod(o, '__newindex')
                    if handler is None:
                         raise LuaError(_op_error('index', o, desc, self.version))
               if type(handler) is LuaFunction or type(handler) is PyFunction:
                    self.call(handler, [o, k, v])
                    return
               o = handler
               desc = None
          raise LuaError("'__newindex' chain too long; possible loop")

     def _next(self, args):
          t = args[0] if args else None
          if type(t) is not LuaTable:
               raise LuaError(f"bad argument #1 to 'next' (table expected, got "
                              f"{_arg_type(args, 0)})")
          r = t.next(args[1] if len(args) > 1 else None)
          return [None] if r is None else [r[0], r[1]]

     # -- operators -------------------------------------------------------------------------

     def tonumber(self, v):
          t = type(v)
          if t is int or t is float:
               return v
          if t is str:
               return str_to_number(v)
          return None

     def arith(self, op, a, b, adesc=None, bdesc=None):
          x = self.tonumber(a)
          y = self.tonumber(b)
          if x is not None and y is not None and op in _ARITH:
               if self.version == '5.1':
                    # Every Lua 5.1 number is a double; integers only stand in
                    # for the doubles that hold them exactly.
                    both_int = type(x) is int and type(y) is int
                    if both_int and op in _ARITH_RAW:
                         r = _ARITH_RAW[op](x, y)
                         return r if _INT_MIN <= r <= _INT_MAX else float(r)
                    if op in ('mod', 'idiv'):
                         r = _ARITH[op](float(x), float(y))
                         if both_int and r == r and r not in (math.inf, -math.inf):
                              return int(r)
                         return r
               return _ARITH[op](x, y)
          if x is not None and y is not None and op in _BITWISE:
               return _BITWISE[op](self._toint(x), self._toint(y))
          event = '__' + op
          handler = self._metamethod(a, event)
          if handler is None:
               handler = self._metamethod(b, event)
          if handler is not None:
               r = self.call(handler, [a, b])
               return r[0] if r else None
          if op in _BITWISE:
               bad, desc = (b, bdesc) if x is not None and \
                    (type(a) is not float or a.is_integer()) else (a, adesc)
               if is_number(bad):
                    raise LuaError('number has no integer representation')
               raise LuaError(_op_error('perform bitwise operation on', bad, desc, self.version))
          bad, desc = (b, bdesc) if x is not None else (a, adesc)
          raise LuaError(_op_error('perform arithmetic on', bad, desc, self.version))

     def _toint(self, v):
          if type(v) is int:
               return v
          if v.is_integer() and -9223372036854775808.0 <= v < 9223372036854775808.0:
               return int(v)
          raise LuaError('number has no integer representation')

     def concat(self, a, b, adesc=None, bdesc=None):
          ta = type(a)
          tb = type(b)
          if (ta is str or ta is int or ta is float) and (tb is str or tb is int or tb is float):
               return self.tostr(a) + self.tostr(b)
          handler = self._metamethod(a, '__concat')
          if handler is None:
               handler = self._metamethod(b, '__concat')
          if handler is not None:
               r = self.call(handler, [a, b])
               return r[0] if r else None
          bad, desc = (b, bdesc) if (ta is str or ta is int or ta is float) else (a, adesc)
          raise LuaError(_op_error('concatenate', bad, desc, self.version))

     def length(self, v, desc=None):
          if type(v) is str:
               return len(v)
          handler = self._metamethod(v, '__len')
          if handler is not None:
               r = self.call(handler, [v])
               return r[0] if r else None
          if type(v) is LuaTable:
               return v.length()
          raise LuaError(_op_error('get length of', v, desc, self.version))

     def eq(self, a, b):
          if a is b:
               return type(a) is not float or a == a
          ta = type(a)
          tb = type(b)
          if ta is bool or tb is bool or a is None or b is None:
               return False
          if (ta is int or ta is float) and (tb is int or tb is float):
               return a == b
          if ta is str and tb is str:
               return a == b
          if ta is not tb:
               return False
          if ta is LuaTable or isinstance(a, LuaUserdata):
               handler = self._compare_handler(a, b, '__eq')
               if handler is not None:
                    r = self.call(handler, [a, b])
                    return truthy(r[0] if r else None)
          return False

     def _compare_handler(self, a, b, event):
          """The metamethod for a comparison. Lua 5.1 only uses one when both
          operands have the same one; 5.3 takes the first operand's, then the
          second's."""
          first = self._metamethod(a, event)
          if self.version != '5.1':
               return first if first is not None else self._metamethod(b, event)
          if first is None:
               return None
          if self.getmetatable(a) is self.getmetatable(b):
               return first
          second = self._metamethod(b, event)
          return first if second is first else None

     def lt(self, a, b):
          ta = type(a)
          tb = type(b)
          if (ta is int or ta is float) and (tb is int or tb is float):
               return a < b
          if ta is str and tb is str:
               return a < b
          if self.version == '5.1' and ta is not tb:
               raise LuaError(_compare_error(a, b))
          handler = self._compare_handler(a, b, '__lt')
          if handler is not None:
               r = self.call(handler, [a, b])
               return truthy(r[0] if r else None)
          raise LuaError(_compare_error(a, b))

     def le(self, a, b):
          ta = type(a)
          tb = type(b)
          if (ta is int or ta is float) and (tb is int or tb is float):
               return a <= b
          if ta is str and tb is str:
               return a <= b
          if self.version == '5.1' and ta is not tb:
               raise LuaError(_compare_error(a, b))
          handler = self._compare_handler(a, b, '__le')
          if handler is not None:
               r = self.call(handler, [a, b])
               return truthy(r[0] if r else None)
          handler = self._compare_handler(b, a, '__lt')
          if handler is not None:
               r = self.call(handler, [b, a])
               return not truthy(r[0] if r else None)
          raise LuaError(_compare_error(a, b))

     # -- conversions ------------------------------------------------------------------------

     def tostr(self, v):
          """tostring() without __tostring (numbers and strings)."""
          t = type(v)
          if t is str:
               return v
          if t is int or t is float:
               return number_to_str(v, self.version)
          return self.tostring(v)

     def tostring(self, v):
          handler = self._metamethod(v, '__tostring')
          if handler is not None:
               r = self.call(handler, [v])
               s = r[0] if r else None
               if type(s) is not str:
                    if self.version == '5.1':
                         return s
                    if is_number(s):
                         return self.tostr(s)
                    raise LuaError("'__tostring' must return a string")
               return s
          t = type(v)
          if v is None:
               return 'nil'
          if t is bool:
               return 'true' if v else 'false'
          if t is int or t is float:
               return number_to_str(v, self.version)
          if t is str:
               return v
          name = type_name(v)
          mt = self.getmetatable(v)
          if mt is not None and type(mt.hash.get('__name')) is str:
               name = mt.hash.get('__name')
          return f'{name}: 0x{id(v) & 0xffffffff:08x}'

     # -- Python <-> Lua ---------------------------------------------------------------------

     def to_lua(self, value):
          """Convert a Python value to a Lua one (dicts and lists to tables,
          callables to functions, text to byte strings)."""
          if value is None or isinstance(value, (bool, int, float, LuaTable, LuaFunction,
                                                 PyFunction, LuaUserdata, Coroutine)):
               return value
          if isinstance(value, str):
               return to_bytes_text(value)
          if isinstance(value, dict):
               t = LuaTable()
               for k, v in value.items():
                    t.set(self.to_lua(k), self.to_lua(v))
               return t
          if isinstance(value, (list, tuple)):
               return LuaTable.from_list([self.to_lua(v) for v in value])
          if callable(value):
               def wrapper(args):
                    result = value(*args)
                    if isinstance(result, tuple):
                         return [self.to_lua(r) for r in result]
                    return [self.to_lua(result)]
               return PyFunction(wrapper, getattr(value, '__name__', '?'))
          raise TypeError(f'cannot convert {type(value).__name__} to a Lua value')

     def to_python(self, value, _seen=None):
          """Convert a Lua value to Python: a table whose keys are 1..n becomes
          a list, any other table a dict; strings become text."""
          if isinstance(value, str):
               return to_text(value)
          if type(value) is LuaTable:
               if _seen is None:
                    _seen = {}
               if id(value) in _seen:
                    return _seen[id(value)]
               n = value.length()
               if n and n == len(value.hash):
                    result = []
                    _seen[id(value)] = result
                    result.extend(self.to_python(v, _seen) for v in value.array())
                    return result
               result = {}
               _seen[id(value)] = result
               for k, v in value.items():
                    result[self.to_python(k, _seen)] = self.to_python(v, _seen)
               return result
          return value


_LEX_MESSAGES = {
     'unterminated string': 'unfinished string',
     'unterminated long bracket': 'unfinished long string',
}


def _gotos_leaving(stmt, label):
     """The `goto label` statements in `stmt` that jump out of it: not
     inside a nested function, and not caught by a nested label of that name."""
     if isinstance(stmt, ir.GotoStmt):
          if stmt.label == label:
               yield stmt
          return
     bodies = []
     if isinstance(stmt, (ir.DoStmt, ir.WhileStmt, ir.RepeatStmt, ir.NumericForStmt,
                          ir.GenericForStmt)):
          bodies.append(stmt.body)
     elif isinstance(stmt, ir.IfStmt):
          bodies.extend(body for _, body in stmt.branches)
          if stmt.else_body is not None:
               bodies.append(stmt.else_body)
     for body in bodies:
          if any(isinstance(s, ir.LabelStmt) and s.name == label for s in body):
               continue
          for inner in body:
               yield from _gotos_leaving(inner, label)


def _check_goto_scopes(compiler, statements, labels):
     """Lua rejects a forward goto into the scope of a local declared
     between it and its label, unless the label ends the block (only labels
     and empty statements follow it)."""
     for name, target in labels.items():
          if all(isinstance(s, ir.LabelStmt) for s in statements[target + 1:]):
               continue
          for index in range(target):
               for goto in _gotos_leaving(statements[index], name):
                    for between in statements[index + 1:target]:
                         if isinstance(between, ir.LocalStmt):
                              local = between.names[0]
                         elif isinstance(between, ir.LocalFunctionStmt):
                              local = between.name
                         else:
                              continue
                         line = getattr(goto, 'line', None)
                         raise LuaCompileError(
                              f"{compiler.position(line)}: <goto {name}> at line {line} "
                              f"jumps into the scope of local '{local}'")


def _start_line(expr):
     """The line a suffixed expression (`a.b:c(d)(e)`) starts on."""
     while True:
          if getattr(expr, 'paren', False):
               return getattr(expr, 'line', None)
          if isinstance(expr, ir.LuaCallExpr):
               expr = expr.func
          elif isinstance(expr, ir.LuaMethodCallExpr):
               expr = expr.obj
          elif isinstance(expr, ir.IndexExpr):
               expr = expr.obj
          else:
               return getattr(expr, 'line', None)


def _syntax_message(error):
     """A parser or lexer error worded the way Lua words it: `'=' expected
     near 'x'`."""
     if getattr(error, 'lua_message', None):
          return error.lua_message
     message = getattr(error, 'message', str(error))
     token = getattr(error, 'token', None)
     if token is None:
          m = re.match(r"^unexpected character (.+)$", message)
          if m:
               import ast
               try:
                    char = ast.literal_eval(m.group(1))
               except (ValueError, SyntaxError):
                    char = m.group(1)
               return f"unexpected symbol near '{char}'"
          return _LEX_MESSAGES.get(message, message)
     if message.startswith("expected '") and message.endswith("'"):
          message = message[len('expected '):] + ' expected'
     elif message == 'expected a name':
          message = '<name> expected'
     elif message == 'expected end of file':
          message = "'<eof>' expected"
     elif message.startswith('expected'):
          message = 'unexpected symbol'
     if token.kind == 'EOF':
          near = '<eof>'
     elif getattr(token, 'raw', None):
          near = token.raw
     else:
          near = str(token.value)
     return f"{message} near '{near}'"


def _position_of(chunk, positions, line):
     if line is not None and positions is not None and 0 < line <= len(positions):
          return positions[line - 1]
     return f'{chunk}:{line}' if line is not None else chunk


def _arg_type(args, i):
     return type_name(args[i]) if i < len(args) else 'no value'


def _op_error(what, value, desc, version='5.1'):
     # 5.1: "attempt to index global 'x' (a nil value)";
     # 5.2+: "attempt to index a nil value (global 'x')"
     if desc and version == '5.1':
          return f'attempt to {what} {desc} (a {type_name(value)} value)'
     if desc:
          return f'attempt to {what} a {type_name(value)} value ({desc})'
     return f'attempt to {what} a {type_name(value)} value'


def _compare_error(a, b):
     ta = type_name(a)
     tb = type_name(b)
     if ta == tb:
          return f'attempt to compare two {ta} values'
     return f'attempt to compare {ta} with {tb}'


def _div(a, b):
     a = float(a)
     b = float(b)
     try:
          return a / b
     except ZeroDivisionError:
          if a == 0 or a != a:
               return math.nan
          return math.copysign(math.inf, a) * math.copysign(1, b)


def _mod(a, b):
     if type(a) is int and type(b) is int:
          if b == 0:
               raise LuaError("attempt to perform 'n%0'")
          return a % b
     a = float(a)
     b = float(b)
     if b == 0:
          return math.nan
     if b in (math.inf, -math.inf):
          if a != a or a in (math.inf, -math.inf):
               return math.nan
          if (a >= 0) == (b > 0):
               return a
          return b
     m = math.fmod(a, b)
     if m != 0 and (m < 0) != (b < 0):
          m += b
     return m


def _idiv(a, b):
     if type(a) is int and type(b) is int:
          if b == 0:
               raise LuaError('attempt to divide by zero')
          return _wrap(a // b)            # minint // -1 wraps to minint
     q = _div(a, b)
     if q != q or q in (math.inf, -math.inf):
          return q
     return float(math.floor(q))


def _pow(a, b):
     """C's pow: Python raises where C returns an infinity or NaN."""
     a = float(a)
     b = float(b)
     try:
          return math.pow(a, b)
     except (OverflowError, ValueError):
          odd = b.is_integer() and b % 2 == 1
          if a == 0:                      # pole: 0^-n
               return -math.inf if odd and math.copysign(1, a) < 0 else math.inf
          if a < 0 and not b.is_integer():
               return math.nan
          if abs(a) < 1:                  # underflow from a large negative power
               return math.inf if b < 0 else 0.0
          return -math.inf if a < 0 and odd else math.inf


def _unm(a, b):
     if type(a) is int:
          return _wrap(-a)
     return -a


def _shl(a, b):
     if b <= -64 or b >= 64:
          return 0
     if b < 0:
          return _shr(a, -b)
     return _wrap((a << b) & 0xFFFFFFFFFFFFFFFF)


def _shr(a, b):
     if b <= -64 or b >= 64:
          return 0
     if b < 0:
          return _shl(a, -b)
     return _wrap((a & 0xFFFFFFFFFFFFFFFF) >> b)


_ARITH = {
     'add': lambda a, b: _wrap(a + b) if type(a) is int and type(b) is int else a + b,
     'sub': lambda a, b: _wrap(a - b) if type(a) is int and type(b) is int else a - b,
     'mul': lambda a, b: _wrap(a * b) if type(a) is int and type(b) is int else a * b,
     'div': _div,
     'mod': _mod,
     'idiv': _idiv,
     'pow': _pow,
     'unm': _unm,
}

_ARITH_RAW = {
     'add': lambda a, b: a + b,
     'sub': lambda a, b: a - b,
     'mul': lambda a, b: a * b,
     'unm': lambda a, b: -a,
}

_BITWISE = {
     'band': lambda a, b: _wrap(a & b),
     'bor': lambda a, b: _wrap(a | b),
     'bxor': lambda a, b: _wrap(a ^ b),
     'shl': _shl,
     'shr': _shr,
     'bnot': lambda a, b: _wrap(~a),
}
