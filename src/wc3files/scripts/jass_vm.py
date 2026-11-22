"""A small JASS interpreter, for testing compiled scripts.

It runs an `ir.Program` together with Blizzard.j, with a handful of natives
implemented in Python: enough for triggers as function pointers
(`TriggerEvaluate`, `TriggerExecute`, `ExecuteFunc`), hashtables, string and
math helpers, and text output. Messages shown with `BJDebugMsg` or
`DisplayTimedTextToPlayer` are collected in `vm.output`, which is what a test
compares.

It is not the game: there are no units, no timers, no waits, and threads are
not separate (an executed trigger runs to completion before the caller goes
on). Integer arithmetic does wrap at 32 bits and divide toward zero, as in the
game. A native that is not implemented raises `JassRuntimeError` rather than
pretending.

    vm = JassVM(program)
    vm.call('main')
    print(vm.output)
"""
import math
import os

from . import ir
from .jass_parser import parse as parse_jass

_GAMEDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')


class JassRuntimeError(Exception):
    pass


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class Handle(object):
    _next = 1

    def __init__(self, kind, **fields):
        self.kind = kind
        self.id = Handle._next
        Handle._next += 1
        self.__dict__.update(fields)

    def __repr__(self):
        return f'<{self.kind} #{self.id}>'


def _wrap32(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value >= 0x80000000 else value


def _default(type_name):
    if type_name == 'integer':
        return 0
    if type_name == 'real':
        return 0.0
    if type_name == 'boolean':
        return False
    return None


def _unescape(text):
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and i + 1 < len(text):
            i += 1
            c = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"'}.get(
                text[i], text[i])
        out.append(c)
        i += 1
    return ''.join(out)


def _rawcode(text):
    value = 0
    for c in _unescape(text):
        value = value * 256 + (ord(c) & 0xFF)
    return _wrap32(value)


_blizzard_cache = {}


def _blizzard():
    program = _blizzard_cache.get('program')
    if program is None:
        with open(os.path.join(_GAMEDATA, 'blizzard.j'), encoding='utf-8',
                  errors='replace') as fh:
            program = parse_jass(fh.read())
        with open(os.path.join(_GAMEDATA, 'common.j'), encoding='utf-8',
                  errors='replace') as fh:
            common = parse_jass(fh.read())
        _blizzard_cache['program'] = program
        _blizzard_cache['common'] = common
    return _blizzard_cache['common'], _blizzard_cache['program']


class JassVM(object):

    def __init__(self, program, with_blizzard=True, max_steps=5_000_000):
        self.functions = {}
        self.globals = {}
        self.arrays = {}
        self.types = {}
        self.output = []
        self.expired = []
        self.steps = 0
        self.max_steps = max_steps
        self.natives = dict(self._natives())
        programs = []
        if with_blizzard:
            common, blizzard = _blizzard()
            programs.extend([common, blizzard])
        programs.append(program)
        for prog in programs:
            for function in prog.functions:
                self.functions[function.name] = function
        for prog in programs:
            for var in prog.globals:
                self._declare_global(var)

    # ------------------------------------------------------------------ setup

    def _declare_global(self, var):
        self.types[var.name] = var.type
        if var.is_array:
            self.arrays[var.name] = {}
            self.globals[var.name] = None
            return
        if var.initial is not None:
            try:
                self.globals[var.name] = self.eval(var.initial, {})
            except JassRuntimeError:
                self.globals[var.name] = _default(var.type)
        else:
            self.globals[var.name] = _default(var.type)

    # ------------------------------------------------------------------ calls

    def call(self, name, *args):
        native = self.natives.get(name)
        if native is not None:
            return native(*args)
        function = self.functions.get(name)
        if function is None:
            raise JassRuntimeError(f'unknown function {name}')
        if len(args) != len(function.params):
            raise JassRuntimeError(f'{name} takes {len(function.params)} '
                                   f'arguments, got {len(args)}')
        frame = {}
        arrays = {}
        for param, value in zip(function.params, args):
            if param.type == 'real' and isinstance(value, int) and \
                    not isinstance(value, bool):
                value = float(value)
            frame[param.name] = value
        frame['#arrays'] = arrays
        frame['#types'] = {p.name: p.type for p in function.params}
        try:
            self.run_block(function.body, frame)
        except _Return as ret:
            value = ret.value
            if function.returns == 'real' and isinstance(value, int) and \
                    not isinstance(value, bool):
                value = float(value)
            return value
        return None

    # ------------------------------------------------------------------ statements

    def tick(self):
        self.steps += 1
        if self.steps > self.max_steps:
            raise JassRuntimeError('step limit reached (an endless loop?)')

    def run_block(self, statements, frame):
        for stmt in statements:
            self.run(stmt, frame)

    def run(self, stmt, frame):
        self.tick()
        if isinstance(stmt, ir.Local):
            frame['#types'][stmt.name] = stmt.type
            if stmt.is_array:
                frame['#arrays'][stmt.name] = {}
                frame[stmt.name] = None
            elif stmt.initial is not None:
                frame[stmt.name] = self.convert(self.eval(stmt.initial, frame),
                                                stmt.type)
            else:
                frame[stmt.name] = _default(stmt.type)
            return
        if isinstance(stmt, ir.SetStmt):
            value = self.eval(stmt.value, frame)
            if stmt.index is not None:
                index = self.eval(stmt.index, frame)
                self.array_of(stmt.target, frame)[index] = self.convert(
                    value, self.type_of(stmt.target, frame))
            elif stmt.target in frame:
                frame[stmt.target] = self.convert(value, frame['#types'].get(
                    stmt.target))
            elif stmt.target in self.globals:
                self.globals[stmt.target] = self.convert(value,
                                                         self.types.get(stmt.target))
            else:
                raise JassRuntimeError(f'unknown variable {stmt.target}')
            return
        if isinstance(stmt, ir.CallStmt):
            self.call(stmt.name, *[self.eval(a, frame) for a in stmt.args])
            return
        if isinstance(stmt, ir.IfStmt):
            for cond, body in stmt.branches:
                if self.eval(cond, frame):
                    self.run_block(body, frame)
                    return
            if stmt.else_body is not None:
                self.run_block(stmt.else_body, frame)
            return
        if isinstance(stmt, ir.LoopStmt):
            try:
                while True:
                    self.tick()
                    self.run_block(stmt.body, frame)
            except _ExitLoop:
                return
        if isinstance(stmt, ir.ExitWhenStmt):
            if self.eval(stmt.condition, frame):
                raise _ExitLoop()
            return
        if isinstance(stmt, ir.ReturnStmt):
            raise _Return(None if stmt.value is None else
                          self.eval(stmt.value, frame))
        raise JassRuntimeError(f'cannot run {type(stmt).__name__}')

    def convert(self, value, type_name):
        if type_name == 'real' and isinstance(value, int) and \
                not isinstance(value, bool):
            return float(value)
        return value

    def type_of(self, name, frame):
        if name in frame['#types']:
            return frame['#types'][name]
        return self.types.get(name)

    def array_of(self, name, frame):
        if name in frame['#arrays']:
            return _ArrayView(frame['#arrays'][name], self.type_of(name, frame))
        if name in self.arrays:
            return _ArrayView(self.arrays[name], self.types.get(name))
        raise JassRuntimeError(f'unknown array {name}')

    # ------------------------------------------------------------------ expressions

    def eval(self, expr, frame):
        if isinstance(expr, ir.Literal):
            kind = expr.kind
            value = expr.value
            if kind == 'int':
                if isinstance(value, str):
                    text = value
                    if text.startswith('$'):
                        return _wrap32(int(text[1:], 16))
                    if text[:2].lower() == '0x':
                        return _wrap32(int(text[2:], 16))
                    if len(text) > 1 and text.startswith('0'):
                        return _wrap32(int(text, 8))
                    return _wrap32(int(text))
                return _wrap32(int(value))
            if kind == 'real':
                return float(value)
            if kind == 'string':
                return _unescape(value)
            if kind == 'rawcode':
                text = value
                if text.startswith("'"):
                    text = text[1:-1]
                return _rawcode(text)
            if kind == 'bool':
                return value is True or value == 'true'
            if kind == 'null':
                return None
        if isinstance(expr, ir.VarExpr):
            name = expr.name
            if name in frame:
                return frame[name]
            if name in self.globals:
                return self.globals[name]
            raise JassRuntimeError(f'unknown variable {name}')
        if isinstance(expr, ir.ArrayRefExpr):
            index = self.eval(expr.index, frame)
            return self.array_of(expr.name, frame)[index]
        if isinstance(expr, ir.CallExpr):
            return self.call(expr.name, *[self.eval(a, frame) for a in expr.args])
        if isinstance(expr, ir.FunctionRefExpr):
            if expr.name not in self.functions:
                raise JassRuntimeError(f'unknown function {expr.name}')
            return Handle('code', name=expr.name)
        if isinstance(expr, ir.UnaryExpr):
            value = self.eval(expr.operand, frame)
            if expr.op == 'not':
                return not value
            if isinstance(value, float):
                return -value
            return _wrap32(-value)
        if isinstance(expr, ir.BinaryExpr):
            op = expr.op
            if op == 'and':
                return bool(self.eval(expr.left, frame)) and \
                    bool(self.eval(expr.right, frame))
            if op == 'or':
                return bool(self.eval(expr.left, frame)) or \
                    bool(self.eval(expr.right, frame))
            left = self.eval(expr.left, frame)
            right = self.eval(expr.right, frame)
            return self.binary(op, left, right)
        name = type(expr).__name__
        if name == 'ParenExpr':
            return self.eval(expr.expr, frame)
        raise JassRuntimeError(f'cannot evaluate {name}')

    def binary(self, op, left, right):
        if op == '==':
            return self._eq(left, right)
        if op == '!=':
            return not self._eq(left, right)
        if op == '+' and (isinstance(left, str) or isinstance(right, str) or
                          (left is None and isinstance(right, str)) or
                          (right is None and isinstance(left, str))):
            return (left or '') + (right or '')
        if left is None or right is None:
            if op == '+' and (left is None and right is None):
                return None
            raise JassRuntimeError(f'null operand for {op}')
        real = isinstance(left, float) or isinstance(right, float)
        if op == '+':
            return left + right if real else _wrap32(left + right)
        if op == '-':
            return left - right if real else _wrap32(left - right)
        if op == '*':
            return left * right if real else _wrap32(left * right)
        if op == '/':
            if real:
                if right == 0:
                    raise JassRuntimeError('division by zero')
                return left / right
            if right == 0:
                raise JassRuntimeError('division by zero')
            q = abs(left) // abs(right)
            return _wrap32(q if (left >= 0) == (right >= 0) else -q)
        if op == '<':
            return left < right
        if op == '>':
            return left > right
        if op == '<=':
            return left <= right
        if op == '>=':
            return left >= right
        raise JassRuntimeError(f'unknown operator {op}')

    @staticmethod
    def _eq(left, right):
        if left is None or right is None:
            # integer 0 compares equal to null, as the game does for
            # `this == null` on struct integers.
            other = right if left is None else left
            return other is None or other == 0 or other is False
        if isinstance(left, float) or isinstance(right, float):
            return abs(float(left) - float(right)) < 0.001
        return left == right

    # ------------------------------------------------------------------ natives

    def _natives(self):
        vm = self

        def show(*args):
            vm.output.append(args[-1] if args else '')

        def trigger_evaluate(trigger):
            if trigger is None:
                return False
            for condition in list(trigger.conditions):
                if not vm.call(condition.func.name):
                    return False
            return True

        def trigger_execute(trigger):
            if trigger is None:
                return None
            for action in list(trigger.actions):
                vm.call(action.name)
            return None

        def add_condition(trigger, boolexpr):
            trigger.conditions.append(boolexpr)
            return Handle('triggercondition')

        def add_action(trigger, code):
            trigger.actions.append(code)
            return Handle('triggeraction')

        def execute_func(name):
            if name in vm.functions:
                vm.call(name)
            return None

        def i2s(i):
            return str(i)

        def r2s(r):
            return f'{r:.3f}'

        def r2sw(r, width, precision):
            return f'{r:{width}.{precision}f}'

        def s2i(s):
            try:
                return _wrap32(int(float((s or '').strip() or '0')))
            except ValueError:
                return 0

        def s2r(s):
            try:
                return float((s or '').strip() or '0')
            except ValueError:
                return 0.0

        def hashtable_save(table, parent, child, value):
            table.data[(parent, child)] = value

        def hashtable_load(default):
            def load(table, parent, child):
                return table.data.get((parent, child), default)
            return load

        def hashtable_has(table, parent, child):
            return (parent, child) in table.data

        def hashtable_remove(table, parent, child):
            table.data.pop((parent, child), None)

        def hashtable_flush_child(table, parent):
            for key in [k for k in table.data if k[0] == parent]:
                del table.data[key]

        def substring(s, start, end):
            s = s or ''
            return s[max(start, 0):max(end, 0)]

        def timer_start(timer, timeout, periodic, code):
            # No game clock here: the callback runs once, right away. Tests
            # stay deterministic, and code that only needs "later" works.
            timer.callback = code
            if code is not None:
                vm.expired.append(timer)
                vm.call(code.name)
                vm.expired.pop()
            return None

        players = {}

        def player(i):
            if i not in players:
                players[i] = Handle('player', number=i)
            return players[i]

        natives = {
            'BJDebugMsg': show,
            'DisplayTextToPlayer': show,
            'DisplayTimedTextToPlayer': show,
            'DisplayTimedTextFromPlayer': show,
            'CreateTrigger': lambda: Handle('trigger', conditions=[], actions=[]),
            'DestroyTrigger': lambda t: None,
            'TriggerAddCondition': add_condition,
            'TriggerAddAction': add_action,
            'TriggerClearConditions': lambda t: t.conditions.clear(),
            'TriggerEvaluate': trigger_evaluate,
            'TriggerExecute': trigger_execute,
            'Condition': lambda code: Handle('conditionfunc', func=code),
            'Filter': lambda code: Handle('filterfunc', func=code),
            'And': lambda a, b: Handle('boolexpr', func=None),
            'ExecuteFunc': execute_func,
            'GetLocalPlayer': lambda: player(0),
            'Player': player,
            'GetPlayerId': lambda p: p.number,
            'I2S': i2s,
            'R2S': r2s,
            'R2SW': r2sw,
            'S2I': s2i,
            'S2R': s2r,
            'I2R': lambda i: float(i),
            'R2I': lambda r: _wrap32(int(r)),
            'StringLength': lambda s: len(s or ''),
            'SubString': substring,
            'StringCase': lambda s, upper: (s or '').upper() if upper
            else (s or '').lower(),
            'StringHash': lambda s: _wrap32(hash(s) & 0xFFFFFFFF),
            'InitHashtable': lambda: Handle('hashtable', data={}),
            'SaveInteger': hashtable_save,
            'SaveReal': hashtable_save,
            'SaveBoolean': hashtable_save,
            'SaveStr': hashtable_save,
            'LoadInteger': hashtable_load(0),
            'LoadReal': hashtable_load(0.0),
            'LoadBoolean': hashtable_load(False),
            'LoadStr': hashtable_load(None),
            'HaveSavedInteger': hashtable_has,
            'HaveSavedReal': hashtable_has,
            'HaveSavedBoolean': hashtable_has,
            'HaveSavedString': hashtable_has,
            'RemoveSavedInteger': hashtable_remove,
            'RemoveSavedReal': hashtable_remove,
            'RemoveSavedBoolean': hashtable_remove,
            'RemoveSavedString': hashtable_remove,
            'FlushChildHashtable': hashtable_flush_child,
            'SquareRoot': lambda x: math.sqrt(x) if x > 0 else 0.0,
            'Pow': lambda x, y: float(x) ** float(y),
            'Sin': math.sin,
            'Cos': math.cos,
            'GetRandomInt': lambda a, b: a,
            'GetRandomReal': lambda a, b: float(a),
            'InitBlizzard': lambda: None,
            'TriggerSleepAction': lambda t: None,
            'GetTriggeringTrigger': lambda: None,
            'CreateTimer': lambda: Handle('timer', callback=None),
            'TimerStart': timer_start,
            'PauseTimer': lambda t: None,
            'DestroyTimer': lambda t: None,
            'GetExpiredTimer': lambda: vm.expired[-1] if vm.expired else None,
            'GetHandleId': lambda h: h.id if h is not None else 0,
        }
        return natives


class _ExitLoop(Exception):
    pass


class _ArrayView(object):
    """JASS arrays read 0 / 0.0 / false / null where nothing was stored."""

    def __init__(self, data, type_name):
        self.data = data
        self.type_name = type_name

    def __getitem__(self, index):
        if not isinstance(index, int) or index < 0 or index > 32767:
            raise JassRuntimeError(f'array index out of range: {index}')
        return self.data.get(index, _default(self.type_name))

    def __setitem__(self, index, value):
        if not isinstance(index, int) or index < 0 or index > 32767:
            raise JassRuntimeError(f'array index out of range: {index}')
        self.data[index] = value


def run_script(text, entry='main', with_blizzard=True):
    """Parse JASS text, call `entry`, return the VM (see `vm.output`)."""
    vm = JassVM(parse_jass(text), with_blizzard=with_blizzard)
    vm.call(entry)
    return vm
