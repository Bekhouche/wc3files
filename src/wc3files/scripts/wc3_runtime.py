"""
Run a Reforged `war3map.lua` without the game: the natives of common.j as
Python stubs, blizzard.j translated to Lua, and a clock for timers.

    runtime = MapRuntime()
    report = runtime.run(source, 'war3map.lua', seconds=10)
    for error in report.errors:
        print(error)

What it is for is finding the errors a map script makes while it sets
itself up: `config()` and `main()` run, the triggers they create can be
executed, and timers fire as game time passes, so code started from a
0-second timer or an "elapsed game time" event runs too. A call to a
function that does not exist, indexing a nil value, a bad argument to a
library function: each is reported with its Lua position and traceback.

What it is not: the game. There is no terrain, no unit behaviour, no
combat and no player input. Units, groups, forces, locations, rects,
hashtables, triggers and timers have just enough state for scripts to
work with them; every other native returns the default value of its return
type (0, 0.0, false, "", or a new handle of the declared type). Events other
than timers never fire. Natives check their argument types, and a mismatch
is reported as a warning, since how the game reacts to one is not something
this package can verify.
"""

import heapq
import math
import os
import random as _random

from . import ir
from .checker import NativeSignatures
from .jass_parser import parse as parse_jass
from .jass_to_lua import jass_to_lua
from .lua_vm import (LuaVM, LuaError, LuaTable, LuaFunction, PyFunction, LuaUserdata,
                     Coroutine, run_with_stack, type_name)
from .lua_stdlib import LuaHost

_GAMEDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_DEFAULT_RETURN = {'integer': 0, 'real': 0.0, 'boolean': False, 'string': '',
                   'nothing': None, 'code': None}
_SLEEP = object()


class GameHandle(LuaUserdata):
     """A game object as Lua sees it: a userdata of a common.j type."""

     def __init__(self, kind, handle_id, metatable, **data):
          self.kind = kind
          self.handle_id = handle_id
          self.metatable = metatable
          self.data = data

     def __repr__(self):
          return f'<{self.kind} {self.handle_id}>'


class RunReport:
     """What a run found. `errors` and `warnings` are lists of strings (with
     their Lua position); `messages` is the text the map displayed;
     `game_time` how far the clock got; `callbacks` how many timer and
     trigger callbacks ran."""

     def __init__(self):
          self.errors = []
          self.warnings = []
          self.messages = []
          self.game_time = 0.0
          self.callbacks = 0
          self.stopped = None

     @property
     def ok(self):
          return not self.errors

     def __repr__(self):
          return (f'<RunReport errors={len(self.errors)} warnings={len(self.warnings)} '
                  f'time={self.game_time:.2f}>')


_PROGRAMS = {}


def _game_program(name):
     program = _PROGRAMS.get(name)
     if program is None:
          with open(os.path.join(_GAMEDATA, name), encoding='utf-8', errors='replace') as h:
               program = _PROGRAMS[name] = parse_jass(h.read())
     return program


_BLIZZARD_LUA = {}


def _blizzard_lua(natives):
     """common.j's globals and blizzard.j, translated to Lua once."""
     text = _BLIZZARD_LUA.get('text')
     if text is None:
          common = _game_program('common.j')
          blizzard = _game_program('blizzard.j')
          constants = ir.Program(globals=common.globals)
          text = (jass_to_lua(constants, natives) +
                  jass_to_lua(blizzard, natives, prelude=False))
          _BLIZZARD_LUA['text'] = text
     return text


class MapRuntime:
     """A Lua 5.3 interpreter with the game's API installed.

     `players` is how many player slots are in use (their controller is a
     user and their slot state "playing"); the rest are empty. `seed` makes
     GetRandomInt/GetRandomReal repeatable. `max_steps` bounds the whole run.
     """

     def __init__(self, players=1, seed=0, max_steps=200_000_000, root=None):
          self.natives = NativeSignatures.default()
          self.players = players
          self.rng = _random.Random(seed)
          self.report = RunReport()
          host = LuaHost(root=root, output=self._print)
          self.vm = LuaVM(version='5.3', host=host, max_steps=max_steps)
          self.next_id = 0x100000
          self.metatables = {}
          self.queue = []
          self.sequence = 0
          self.now = 0.0
          self.converted = {}
          self.player_handles = {}
          self.units = []
          self.event = {}               # what the Get...() event responses return
          self.current_timer = None
          self._install()

     # -- handles ------------------------------------------------------------

     def new(self, kind, **data):
          self.next_id += 1
          mt = self.metatables.get(kind)
          if mt is None:
               mt = LuaTable()
               mt.set('__name', kind)
               handle_text = kind

               def tostring(args, handle_text=handle_text):
                    h = args[0]
                    return [f'{handle_text}: {h.handle_id:08X}']
               mt.set('__tostring', PyFunction(tostring, '__tostring'))
               self.metatables[kind] = mt
          return GameHandle(kind, self.next_id, mt, **data)

     def player(self, index):
          handle = self.player_handles.get(index)
          if handle is None:
               handle = self.new('player', id=index, name=f'Player {index + 1}',
                                 gold=0, lumber=0, team=index, color=index)
               self.player_handles[index] = handle
          return handle

     def converted_handle(self, kind, value):
          key = (kind, value)
          handle = self.converted.get(key)
          if handle is None:
               handle = self.converted[key] = self.new(kind, value=value)
          return handle

     # -- output and positions ---------------------------------------------

     def _print(self, text):
          self.report.messages.append(text.rstrip('\n'))

     def where(self):
          """The Lua position of the running code, for warnings."""
          for frame in reversed(self.vm.stack):
               proto = frame.fn.proto
               if frame.line is not None:
                    return f'{proto.chunk}:{frame.line}'
          return '?'

     def warn(self, message):
          text = f'{self.where()}: {message}'
          if text not in self.report.warnings and len(self.report.warnings) < 500:
               self.report.warnings.append(text)

     # -- calling Lua ---------------------------------------------------------

     def call(self, fn, args=()):
          if fn is None:
               return []
          return self.vm.call(fn, list(args))

     def schedule(self, delay, callback):
          self.sequence += 1
          heapq.heappush(self.queue, (self.now + max(0.0, delay), self.sequence, callback))

     def run_thread(self, fn, args=(), on_done=None):
          """Run Lua code as Reforged runs triggers and timers: in a coroutine,
          so TriggerSleepAction can wait."""
          co = Coroutine(self.vm, fn)
          co.wc3_thread = True
          self._resume(co, list(args), on_done)

     def _resume(self, co, args, on_done):
          self.report.callbacks += 1
          result = co.resume(args)
          if not result[0]:
               message = result[1] if len(result) > 1 else 'error'
               self._error(message, co)
               return
          if co.status == 'suspended' and len(result) > 1 and result[1] is _SLEEP:
               delay = result[2] if len(result) > 2 else 0.0
               self.schedule(delay, lambda: self._resume(co, [], on_done))
               return
          if on_done is not None:
               on_done(result[1:])

     def _error(self, message, co=None):
          text = message if isinstance(message, str) else self.vm.tostring(message)
          trace = getattr(co, 'error_traceback', None) if co is not None else None
          if trace:
               text += ''.join(f'\n    in {fn} at {pos}' for pos, fn in trace[:6])
          if len(self.report.errors) < 200:
               self.report.errors.append(text)

     # -- running a map -------------------------------------------------------

     def load(self, source, chunkname='war3map.lua'):
          """Compile and run the script's top level (its function definitions)."""
          fn = self.vm.compile(source, chunkname)
          run_with_stack(self.vm.call, fn, [])

     def run(self, source, chunkname='war3map.lua', seconds=10.0, entry=('config', 'main')):
          """Load the script, call config() and main(), then let `seconds` of
          game time pass. Returns the RunReport."""
          try:
               self.load(source, chunkname)
          except LuaError as error:
               self._error(error.value if isinstance(error.value, str) else str(error))
               return self.report
          for name in entry:
               fn = self.vm.globals.get(name)
               if fn is None:
                    continue
               try:
                    run_with_stack(self.run_thread, fn)
               except LuaError as error:
                    self._error(str(error))
          try:
               run_with_stack(self.advance, seconds)
          except LuaError as error:
               self.report.stopped = str(error)
               self._error(str(error))
          return self.report

     def advance(self, seconds):
          """Fire everything scheduled in the next `seconds` of game time."""
          end = self.now + seconds
          while self.queue and self.queue[0][0] <= end:
               when, _, callback = heapq.heappop(self.queue)
               self.now = max(self.now, when)
               self.report.game_time = self.now
               callback()
          self.now = end
          self.report.game_time = end

     # -- installing the natives -----------------------------------------------

     def _install(self):
          vm = self.vm
          g = vm.globals
          special = _special_natives(self)
          for name, decl in self.natives._natives.items():
               impl = special.get(name)
               g.set(name, PyFunction(self._native(decl, impl), name))
          for name in ('FourCC', '__jarray'):
               g.set(name, PyFunction(special[name], name))
          # The game's Lua has no debug library: maps' own code depends on it
          # (the CSharp.lua runtime defines System.DelegateCombine only when
          # debug.setmetatable is missing, and calls it unconditionally).
          g.set('debug', None)
          vm.globals.get('package').get('loaded').set('debug', None)
          blizzard = _blizzard_lua(self.natives)
          fn = vm.compile(blizzard, 'blizzard.j', skip_comment_line=False)
          run_with_stack(vm.call, fn, [])

     def declare_natives(self, declarations):
          """Give natives a script declares itself (a JASS map's
          `native UnitAlive takes unit id returns boolean`) a default stub,
          unless the runtime has one already."""
          special = _special_natives(self)
          for decl in declarations:
               if self.vm.globals.get(decl.name) is None:
                    self.vm.globals.set(decl.name, PyFunction(
                         self._native(decl, special.get(decl.name)), decl.name))

     def _native(self, decl, impl):
          params = decl.params
          returns = decl.returns
          runtime = self
          is_handle_return = returns not in _DEFAULT_RETURN
          name = decl.name

          def native(args):
               for position, param in enumerate(params):
                    value = args[position] if position < len(args) else None
                    runtime.check_arg(name, position, param, value)
               if impl is not None:
                    value = impl(*[args[k] if k < len(args) else None
                                   for k in range(len(params))])
               elif is_handle_return:
                    value = runtime.new(returns)
               else:
                    value = _DEFAULT_RETURN.get(returns)
               if returns == 'nothing':
                    return []
               return [value]
          return native

     def check_arg(self, name, position, param, value):
          expected = param.type
          if value is None:
               if expected in ('integer', 'real', 'boolean'):
                    self.warn(f"'{name}' argument {position + 1} ({param.name}): "
                              f"{expected} expected, got nil")
               return
          kind = type(value)
          if expected == 'integer':
               ok = kind is int or (kind is float and value.is_integer())
          elif expected == 'real':
               ok = kind in (int, float)
          elif expected == 'boolean':
               ok = kind is bool
          elif expected == 'string':
               ok = kind is str
          elif expected == 'code':
               ok = kind in (LuaFunction, PyFunction)
          elif isinstance(value, GameHandle):
               ok = self.natives.is_ancestor(expected, value.kind)
          else:
               ok = False
          if not ok:
               got = value.kind if isinstance(value, GameHandle) else type_name(value)
               self.warn(f"'{name}' argument {position + 1} ({param.name}): "
                         f"{expected} expected, got {got}")


def _int(v):
     if type(v) is float:
          return int(v)
     return v if type(v) is int else 0


def _num(v):
     return float(v) if type(v) in (int, float) else 0.0


def _special_natives(rt):
     """The natives that do something; every other one returns a default."""
     vm = rt.vm
     n = {}

     def sleep(seconds):
          co = vm.current_coroutine
          if co is not None and getattr(co, 'wc3_thread', False) and not getattr(co, 'c_calls', 0):
               co.yield_([_SLEEP, max(0.0, _num(seconds))])
          return None

     # player slot numbers (Reforged: 24 players, then the four neutrals)
     n['GetBJMaxPlayers'] = lambda: 24
     n['GetPlayerNeutralAggressive'] = lambda: 24
     n['GetBJPlayerNeutralVictim'] = lambda: 25
     n['GetBJPlayerNeutralExtra'] = lambda: 26
     n['GetPlayerNeutralPassive'] = lambda: 27
     n['GetBJMaxPlayerSlots'] = lambda: 28

     # what config() sets up, read back
     setup = {'players': rt.players, 'teams': 1, 'name': '', 'description': ''}
     n['SetPlayers'] = lambda count: setup.__setitem__('players', _int(count))
     n['GetPlayers'] = lambda: setup['players']
     n['SetTeams'] = lambda count: setup.__setitem__('teams', _int(count))
     n['GetTeams'] = lambda: setup['teams']
     n['SetMapName'] = lambda name: setup.__setitem__('name', name)
     n['SetMapDescription'] = lambda text: setup.__setitem__('description', text)
     n['GetPlayerTeam'] = lambda p: p.data.get('team', 0) if isinstance(p, GameHandle) else 0
     n['SetPlayerTeam'] = lambda p, team: p.data.__setitem__('team', _int(team)) \
          if isinstance(p, GameHandle) else None
     n['GetPlayerStartLocation'] = lambda p: p.data.get('start', 0) if isinstance(p, GameHandle) else 0
     n['SetPlayerStartLocation'] = lambda p, i: p.data.__setitem__('start', _int(i)) \
          if isinstance(p, GameHandle) else None

     # players, constants, ids
     n['Player'] = lambda i: rt.player(_int(i))
     n['GetLocalPlayer'] = lambda: rt.player(0)
     n['GetPlayerId'] = lambda p: p.data.get('id', 0) if isinstance(p, GameHandle) else 0
     n['GetPlayerName'] = lambda p: p.data.get('name', '') if isinstance(p, GameHandle) else ''
     n['SetPlayerName'] = lambda p, s: p.data.__setitem__('name', s) if isinstance(p, GameHandle) else None
     n['GetHandleId'] = lambda h: h.handle_id if isinstance(h, GameHandle) else 0

     def controller(p):
          index = p.data.get('id', 0) if isinstance(p, GameHandle) else 0
          return rt.converted_handle('mapcontrol', 0 if index < rt.players else
                                     (5 if index >= 24 else 0))
     n['GetPlayerController'] = controller

     def slot_state(p):
          index = p.data.get('id', 0) if isinstance(p, GameHandle) else 0
          return rt.converted_handle('playerslotstate', 1 if index < rt.players else 0)
     n['GetPlayerSlotState'] = slot_state

     def player_state(p, state):
          return 0
     n['GetPlayerState'] = player_state
     for decl in rt.natives._natives.values():
          if decl.name.startswith('Convert') and len(decl.params) == 1 and \
                    decl.params[0].type == 'integer' and decl.returns not in _DEFAULT_RETURN:
               n[decl.name] = (lambda kind: lambda i: rt.converted_handle(kind, _int(i)))(decl.returns)

     def fourcc(args):
          s = args[0] if args else ''
          value = 0
          for c in str(s)[:4]:
               value = value * 256 + (ord(c) & 0xFF)
          return [value]
     n['FourCC'] = fourcc

     def jarray(args):
          default = args[0] if args else None
          t = LuaTable()
          mt = LuaTable()
          mt.set('__index', PyFunction(lambda a: [default], '__index'))
          t.metatable = mt
          return [t]
     n['__jarray'] = jarray

     # strings and numbers
     n['I2S'] = lambda i: str(_int(i))
     n['R2S'] = lambda r: f'{_num(r):.3f}'
     n['R2SW'] = lambda r, w, p: f'{_num(r):{_int(w)}.{max(0, _int(p))}f}'
     n['I2R'] = lambda i: float(_int(i))
     n['R2I'] = lambda r: int(_num(r))

     def s2i(s):
          import re
          m = re.match(r'\s*([-+]?\d+)', s or '')
          return int(m.group(1)) if m else 0
     n['S2I'] = s2i

     def s2r(s):
          import re
          m = re.match(r'\s*([-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?)', s or '')
          return float(m.group(1)) if m else 0.0
     n['S2R'] = s2r
     n['StringLength'] = lambda s: len(s or '')
     n['StringCase'] = lambda s, upper: (s or '').upper() if upper else (s or '').lower()

     def substring(s, start, end):
          s = s or ''
          start = max(0, _int(start))
          end = _int(end)
          return s[start:end] if end > start else ''
     n['SubString'] = substring
     n['StringHash'] = lambda s: hash(s or '') & 0x7FFFFFFF
     n['GetLocalizedString'] = lambda s: s or ''
     n['Deg2Rad'] = lambda d: math.radians(_num(d))
     n['Rad2Deg'] = lambda r: math.degrees(_num(r))
     for name, fn in (('Sin', math.sin), ('Cos', math.cos), ('Tan', math.tan),
                      ('Asin', math.asin), ('Acos', math.acos), ('Atan', math.atan)):
          n[name] = (lambda fn: lambda x: _safe(fn, _num(x)))(fn)
     n['Atan2'] = lambda y, x: math.atan2(_num(y), _num(x))
     n['SquareRoot'] = lambda x: math.sqrt(_num(x)) if _num(x) >= 0 else 0.0
     n['Pow'] = lambda x, p: _safe(lambda a: a ** _num(p), _num(x))
     n['GetRandomInt'] = lambda lo, hi: rt.rng.randint(min(_int(lo), _int(hi)),
                                                      max(_int(lo), _int(hi)))
     n['GetRandomReal'] = lambda lo, hi: rt.rng.uniform(_num(lo), _num(hi))

     # output
     def display(p, x, y, msg, *rest):
          rt.report.messages.append(msg if isinstance(msg, str) else vm.tostring(msg))
     n['DisplayTextToPlayer'] = lambda p, x, y, msg: display(p, x, y, msg)
     n['DisplayTimedTextToPlayer'] = lambda p, x, y, d, msg: display(p, x, y, msg)
     n['DisplayTimedTextFromPlayer'] = lambda p, x, y, d, msg: display(p, x, y, msg)

     # hashtables
     def ht_key(table, parent, child):
          return table.data.setdefault('store', {}), (_int(parent), _int(child))

     def saver(table, parent, child, value):
          if isinstance(table, GameHandle):
               store, key = ht_key(table, parent, child)
               store[key] = value
          return True
     for kind, default in (('Integer', 0), ('Real', 0.0), ('Boolean', False), ('Str', ''),
                           ('PlayerHandle', None), ('UnitHandle', None)):
          pass
     for decl in rt.natives._natives.values():
          if decl.name.startswith('Save') and len(decl.params) == 4 and \
                    decl.params[0].type == 'hashtable':
               n[decl.name] = saver
          elif decl.name.startswith('Load') and len(decl.params) == 3 and \
                    decl.params[0].type == 'hashtable':
               default = _DEFAULT_RETURN.get(decl.returns)

               def loader(table, parent, child, default=default):
                    if not isinstance(table, GameHandle):
                         return default
                    store, key = ht_key(table, parent, child)
                    return store.get(key, default)
               n[decl.name] = loader
          elif decl.name.startswith('HaveSaved') and decl.params[0].type == 'hashtable':
               n[decl.name] = lambda table, parent, child: isinstance(table, GameHandle) and \
                    ht_key(table, parent, child)[1] in table.data.get('store', {})
          elif decl.name.startswith('RemoveSaved') and decl.params[0].type == 'hashtable':
               def remover(table, parent, child):
                    if isinstance(table, GameHandle):
                         store, key = ht_key(table, parent, child)
                         store.pop(key, None)
               n[decl.name] = remover

     def flush_child(table, parent):
          if isinstance(table, GameHandle):
               store = table.data.setdefault('store', {})
               for key in [k for k in store if k[0] == _int(parent)]:
                    del store[key]
     n['FlushChildHashtable'] = flush_child
     n['FlushParentHashtable'] = lambda table: table.data.__setitem__('store', {}) \
          if isinstance(table, GameHandle) else None

     # boolexprs, triggers
     n['Condition'] = lambda f: rt.new('conditionfunc', fn=f) if f is not None else None
     n['Filter'] = lambda f: rt.new('filterfunc', fn=f) if f is not None else None
     n['And'] = lambda a, b: rt.new('boolexpr', op='and', parts=(a, b))
     n['Or'] = lambda a, b: rt.new('boolexpr', op='or', parts=(a, b))
     n['Not'] = lambda a: rt.new('boolexpr', op='not', parts=(a,))

     def evaluate(boolexpr):
          if not isinstance(boolexpr, GameHandle):
               return True
          if 'fn' in boolexpr.data:
               r = rt.call(boolexpr.data['fn'])
               return bool(r and r[0])
          op = boolexpr.data.get('op')
          parts = boolexpr.data.get('parts', ())
          if op == 'and':
               return evaluate(parts[0]) and evaluate(parts[1])
          if op == 'or':
               return evaluate(parts[0]) or evaluate(parts[1])
          if op == 'not':
               return not evaluate(parts[0])
          return True
     rt.evaluate = evaluate

     def create_trigger():
          return rt.new('trigger', actions=[], conditions=[], enabled=True, runs=0)
     n['CreateTrigger'] = create_trigger

     def add_action(trig, fn):
          if isinstance(trig, GameHandle) and fn is not None:
               trig.data['actions'].append(fn)
          return rt.new('triggeraction', fn=fn)
     n['TriggerAddAction'] = add_action

     def add_condition(trig, boolexpr):
          if isinstance(trig, GameHandle) and boolexpr is not None:
               trig.data['conditions'].append(boolexpr)
          return rt.new('triggercondition', boolexpr=boolexpr)
     n['TriggerAddCondition'] = add_condition
     n['TriggerClearActions'] = lambda t: t.data['actions'].clear() if isinstance(t, GameHandle) else None
     n['TriggerClearConditions'] = lambda t: t.data['conditions'].clear() if isinstance(t, GameHandle) else None
     n['EnableTrigger'] = lambda t: t.data.__setitem__('enabled', True) if isinstance(t, GameHandle) else None
     n['DisableTrigger'] = lambda t: t.data.__setitem__('enabled', False) if isinstance(t, GameHandle) else None
     n['IsTriggerEnabled'] = lambda t: bool(isinstance(t, GameHandle) and t.data.get('enabled'))

     def trigger_evaluate(trig):
          if not isinstance(trig, GameHandle):
               return False
          return all(evaluate(c) for c in list(trig.data.get('conditions', [])))
     n['TriggerEvaluate'] = trigger_evaluate

     def trigger_execute(trig):
          if not isinstance(trig, GameHandle):
               return
          trig.data['runs'] = trig.data.get('runs', 0) + 1
          saved = rt.event.get('trigger')
          for fn in list(trig.data.get('actions', [])):
               rt.event['trigger'] = trig
               rt.call(fn)
          rt.event['trigger'] = saved
     n['TriggerExecute'] = trigger_execute
     n['TriggerExecuteWait'] = lambda t, wait: trigger_execute(t)
     n['GetTriggeringTrigger'] = lambda: rt.event.get('trigger')
     n['GetTriggerExecCount'] = lambda t: t.data.get('runs', 0) if isinstance(t, GameHandle) else 0
     n['TriggerSleepAction'] = sleep

     def fire_trigger(trig):
          if not isinstance(trig, GameHandle) or not trig.data.get('enabled', True):
               return

          def body(args):
               if trigger_evaluate(trig):
                    trigger_execute(trig)
               return []
          rt.run_thread(PyFunction(body, 'trigger'))

     def timer_event(trig, timeout, periodic):
          delay = _num(timeout)

          def fire():
               fire_trigger(trig)
               if periodic and delay > 0:
                    rt.schedule(delay, fire)
          rt.schedule(delay, fire)
          return rt.new('event')
     n['TriggerRegisterTimerEvent'] = timer_event

     # timers
     n['CreateTimer'] = lambda: rt.new('timer', generation=0, timeout=0.0, start=0.0,
                                       periodic=False, running=False, triggers=[])
     n['GetExpiredTimer'] = lambda: rt.current_timer

     def timer_start(timer, timeout, periodic, handler):
          if not isinstance(timer, GameHandle):
               return
          data = timer.data
          data['generation'] = data.get('generation', 0) + 1
          generation = data['generation']
          data.update(timeout=_num(timeout), periodic=bool(periodic), handler=handler,
                      start=rt.now, running=True)

          def expire():
               if data.get('generation') != generation or not data.get('running'):
                    return
               rt.current_timer = timer
               if data.get('handler') is not None:
                    rt.run_thread(data['handler'])
               for trig in list(data.get('triggers', [])):
                    fire_trigger(trig)
               rt.current_timer = None
               if data.get('periodic') and data['timeout'] > 0 and \
                         data.get('generation') == generation:
                    data['start'] = rt.now
                    rt.schedule(data['timeout'], expire)
               elif data.get('generation') == generation:
                    data['running'] = False
          rt.schedule(data['timeout'], expire)
     n['TimerStart'] = timer_start

     def pause_timer(timer):
          if isinstance(timer, GameHandle):
               timer.data['running'] = False
               timer.data['generation'] = timer.data.get('generation', 0) + 1
     n['PauseTimer'] = pause_timer
     n['DestroyTimer'] = pause_timer
     n['TimerGetTimeout'] = lambda t: t.data.get('timeout', 0.0) if isinstance(t, GameHandle) else 0.0
     n['TimerGetElapsed'] = lambda t: (rt.now - t.data.get('start', rt.now)) \
          if isinstance(t, GameHandle) and t.data.get('running') else 0.0
     n['TimerGetRemaining'] = lambda t: max(0.0, t.data.get('timeout', 0.0) -
                                            (rt.now - t.data.get('start', rt.now))) \
          if isinstance(t, GameHandle) and t.data.get('running') else 0.0

     def expire_event(trig, timer):
          if isinstance(timer, GameHandle):
               timer.data.setdefault('triggers', []).append(trig)
          return rt.new('event')
     n['TriggerRegisterTimerExpireEvent'] = expire_event

     def execute_func(name):
          fn = vm.globals.get(name)
          if fn is None:
               rt.warn(f"ExecuteFunc: no function '{name}'")
               return
          rt.run_thread(fn)
     n['ExecuteFunc'] = execute_func

     # groups, forces, units
     n['CreateGroup'] = lambda: rt.new('group', members=[])
     n['CreateForce'] = lambda: rt.new('force', members=[])

     def add_member(container, item):
          if isinstance(container, GameHandle) and item is not None:
               members = container.data.setdefault('members', [])
               if item not in members:
                    members.append(item)
          return True

     def remove_member(container, item):
          if isinstance(container, GameHandle):
               members = container.data.setdefault('members', [])
               if item in members:
                    members.remove(item)
          return True
     n['GroupAddUnit'] = add_member
     n['GroupRemoveUnit'] = remove_member
     n['ForceAddPlayer'] = add_member
     n['ForceRemovePlayer'] = remove_member
     n['GroupClear'] = lambda g: g.data.__setitem__('members', []) if isinstance(g, GameHandle) else None
     n['ForceClear'] = n['GroupClear']
     n['IsUnitInGroup'] = lambda u, g: isinstance(g, GameHandle) and u in g.data.get('members', [])
     n['IsPlayerInForce'] = lambda p, f: isinstance(f, GameHandle) and p in f.data.get('members', [])
     n['FirstOfGroup'] = lambda g: (g.data.get('members') or [None])[0] if isinstance(g, GameHandle) else None
     n['BlzGroupGetSize'] = lambda g: len(g.data.get('members', [])) if isinstance(g, GameHandle) else 0
     n['BlzGroupUnitAt'] = lambda g, i: (g.data.get('members', [])[_int(i)]
                                         if isinstance(g, GameHandle) and
                                         0 <= _int(i) < len(g.data.get('members', [])) else None)

     def for_each(container, fn, slot):
          if not isinstance(container, GameHandle) or fn is None:
               return
          saved = rt.event.get(slot)
          for item in list(container.data.get('members', [])):
               rt.event[slot] = item
               rt.call(fn)
          rt.event[slot] = saved
     n['ForGroup'] = lambda g, fn: for_each(g, fn, 'enum_unit')
     n['ForForce'] = lambda f, fn: for_each(f, fn, 'enum_player')
     n['GetEnumUnit'] = lambda: rt.event.get('enum_unit')
     n['GetEnumPlayer'] = lambda: rt.event.get('enum_player')
     n['GetFilterUnit'] = lambda: rt.event.get('filter_unit')
     n['GetFilterPlayer'] = lambda: rt.event.get('filter_player')

     def enum(container, candidates, boolexpr, slot):
          if not isinstance(container, GameHandle):
               return
          saved = rt.event.get(slot)
          for item in candidates:
               rt.event[slot] = item
               if boolexpr is None or evaluate(boolexpr):
                    add_member(container, item)
          rt.event[slot] = saved

     def all_players():
          return [rt.player(i) for i in range(28)]
     n['ForceEnumPlayers'] = lambda f, cond: enum(f, all_players(), cond, 'filter_player')
     n['ForceEnumAllies'] = lambda f, p, cond: enum(f, all_players(), cond, 'filter_player')
     n['ForceEnumEnemies'] = lambda f, p, cond: enum(f, [], cond, 'filter_player')
     n['GroupEnumUnitsOfPlayer'] = lambda g, p, cond: enum(
          g, [u for u in rt.units if u.data.get('owner') is p], cond, 'filter_unit')
     n['GroupEnumUnitsInRect'] = lambda g, r, cond: enum(g, list(rt.units), cond, 'filter_unit')
     n['GroupEnumUnitsInRange'] = lambda g, x, y, radius, cond: enum(
          g, [u for u in rt.units if math.hypot(u.data['x'] - _num(x), u.data['y'] - _num(y))
              <= _num(radius)], cond, 'filter_unit')
     n['GroupEnumUnitsOfType'] = lambda g, name, cond: enum(g, list(rt.units), cond, 'filter_unit')

     def create_unit(p, unit_id, x, y, face, *rest):
          u = rt.new('unit', owner=p, type=_int(unit_id), x=_num(x), y=_num(y),
                     face=_num(face), life=100.0, mana=0.0, alive=True)
          rt.units.append(u)
          return u
     n['CreateUnit'] = create_unit
     n['BlzCreateUnitWithSkin'] = lambda p, i, x, y, f, skin: create_unit(p, i, x, y, f)
     n['CreateUnitByName'] = lambda p, name, x, y, f: create_unit(p, 0, x, y, f)
     n['CreateUnitAtLoc'] = lambda p, i, loc, f: create_unit(
          p, i, loc.data.get('x', 0.0) if isinstance(loc, GameHandle) else 0.0,
          loc.data.get('y', 0.0) if isinstance(loc, GameHandle) else 0.0, f)

     def unit_field(field, default):
          return lambda u: u.data.get(field, default) if isinstance(u, GameHandle) else default

     def set_unit_field(field, convert):
          def setter(u, value):
               if isinstance(u, GameHandle):
                    u.data[field] = convert(value)
          return setter
     n['GetUnitX'] = unit_field('x', 0.0)
     n['GetUnitY'] = unit_field('y', 0.0)
     n['GetUnitFacing'] = unit_field('face', 0.0)
     n['GetUnitTypeId'] = unit_field('type', 0)
     n['GetOwningPlayer'] = unit_field('owner', None)
     n['SetUnitX'] = set_unit_field('x', _num)
     n['SetUnitY'] = set_unit_field('y', _num)
     n['SetUnitFacing'] = set_unit_field('face', _num)
     n['SetUnitOwner'] = lambda u, p, change: u.data.__setitem__('owner', p) \
          if isinstance(u, GameHandle) else None

     def set_position(u, x, y):
          if isinstance(u, GameHandle):
               u.data['x'] = _num(x)
               u.data['y'] = _num(y)
     n['SetUnitPosition'] = set_position

     def kill(u):
          if isinstance(u, GameHandle):
               u.data['alive'] = False
               u.data['life'] = 0.0
     n['KillUnit'] = kill

     def remove_unit(u):
          if u in rt.units:
               rt.units.remove(u)
     n['RemoveUnit'] = remove_unit
     n['UnitAlive'] = unit_field('alive', False)

     # locations, rects
     n['Location'] = lambda x, y: rt.new('location', x=_num(x), y=_num(y))
     n['GetLocationX'] = lambda loc: loc.data.get('x', 0.0) if isinstance(loc, GameHandle) else 0.0
     n['GetLocationY'] = lambda loc: loc.data.get('y', 0.0) if isinstance(loc, GameHandle) else 0.0
     n['MoveLocation'] = lambda loc, x, y: loc.data.update(x=_num(x), y=_num(y)) \
          if isinstance(loc, GameHandle) else None
     n['Rect'] = lambda a, b, c, d: rt.new('rect', minx=_num(a), miny=_num(b), maxx=_num(c),
                                           maxy=_num(d))
     # camera bounds as main() sets them; the world reaches a little further
     # (the real margin depends on the map's size, which a script does not say)
     bounds = {'minx': -8192.0, 'miny': -8192.0, 'maxx': 8192.0, 'maxy': 8192.0}

     def set_camera_bounds(x1, y1, x2, y2, x3, y3, x4, y4):
          xs = [_num(v) for v in (x1, x2, x3, x4)]
          ys = [_num(v) for v in (y1, y2, y3, y4)]
          bounds.update(minx=min(xs), maxx=max(xs), miny=min(ys), maxy=max(ys))
     n['SetCameraBounds'] = set_camera_bounds
     n['GetWorldBounds'] = lambda: rt.new('rect', minx=bounds['minx'] - 512.0,
                                          miny=bounds['miny'] - 512.0,
                                          maxx=bounds['maxx'] + 512.0,
                                          maxy=bounds['maxy'] + 512.0)
     for field, name in (('minx', 'GetRectMinX'), ('miny', 'GetRectMinY'),
                         ('maxx', 'GetRectMaxX'), ('maxy', 'GetRectMaxY')):
          n[name] = (lambda field: lambda r: r.data.get(field, 0.0)
                     if isinstance(r, GameHandle) else 0.0)(field)
     n['GetRectCenterX'] = lambda r: (r.data.get('minx', 0.0) + r.data.get('maxx', 0.0)) / 2 \
          if isinstance(r, GameHandle) else 0.0
     n['GetRectCenterY'] = lambda r: (r.data.get('miny', 0.0) + r.data.get('maxy', 0.0)) / 2 \
          if isinstance(r, GameHandle) else 0.0
     n['GetCameraBoundMinX'] = lambda: bounds['minx']
     n['GetCameraBoundMinY'] = lambda: bounds['miny']
     n['GetCameraBoundMaxX'] = lambda: bounds['maxx']
     n['GetCameraBoundMaxY'] = lambda: bounds['maxy']
     n['GetFloatGameState'] = lambda state: rt.now
     # loading a map's own UI files succeeds (the files are in the map)
     n['BlzLoadTOCFile'] = lambda path: True
     n['BlzChangeMinimapTerrainTex'] = lambda path: True
     return n


def _safe(fn, x):
     try:
          return fn(x)
     except (ValueError, OverflowError, ZeroDivisionError):
          return 0.0


def load_map_script(path):
     """(Lua source, chunk name, natives the script declares) for a
     war3map.lua, a war3map.j (translated to Lua) or a map archive."""
     lower = path.lower()
     if lower.endswith(('.w3x', '.w3m')):
          from wc3files.w3x import W3xFile
          archive = W3xFile()
          archive.read(path)
          files = {name.lower(): data for name, data in (archive.files or {}).items()}
          for name in ('war3map.lua', 'scripts\\war3map.lua'):
               if files.get(name) is not None:
                    return bytes(files[name]).decode('utf-8-sig', 'replace'), 'war3map.lua', []
          for name in ('war3map.j', 'scripts\\war3map.j'):
               if files.get(name) is not None:
                    return _jass_source(bytes(files[name]).decode('utf-8-sig', 'replace'))
          raise ValueError(f'{path} has no war3map.lua or war3map.j')
     with open(path, 'rb') as handle:
          text = handle.read().decode('utf-8-sig', 'replace')
     if lower.endswith('.j'):
          return _jass_source(text)
     return text, os.path.basename(path), []


def _jass_source(text):
     program = parse_jass(text)
     return jass_to_lua(program), 'war3map.j (as Lua)', program.natives


def run_map_script(source, chunkname='war3map.lua', seconds=10.0, **options):
     """Run a war3map.lua; returns the RunReport. See MapRuntime."""
     return MapRuntime(**options).run(source, chunkname, seconds)
