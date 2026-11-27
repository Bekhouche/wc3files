"""
Handle leaks in a parsed JASS ir.Program: handles that are created and never
destroyed (#255).

The types that leak and the functions that create, destroy and keep them are
data, in gamedata/leaks.json. Three cases are reported, as warnings:

- a local (or parameter) set to a new handle, never destroyed, returned,
  assigned to another variable or passed to a function that keeps it, in the
  same function;
- a new handle dropped at once: a creating call whose result is passed to a
  function that does not keep it (`CreateUnitAtLoc(p, id, GetRectCenter(r),
  0)`), compared, or called as a statement (`call AddSpecialEffect(...)`);
- a global set to a new handle that no function of the script destroys,
  returns or passes on.

What counts as safe:

- the GUI idioms: `set bj_wantDestroyGroup = true` before the `ForGroupBJ`
  (or other BJ function reading it) it applies to; a handle a BJ function
  also stores in `bj_lastCreated...`, until the next one replaces it,
  destroyed or stored through `GetLastCreated...()` or the global; and a text
  tag made temporary with `SetTextTagPermanent(t, false)`;
- a handle passed to a function of the script itself, which may destroy it,
  or to a function that keeps it (hashtable saves, `TimerStart`, region
  events). Calling a function of the script or running a trigger also
  releases the `bj_lastCreated...` handles, which that code may destroy;
- the `gg_` globals of the World Editor;
- code that runs once, at map start: main and config, the functions only
  they call (also through `ExecuteFunc("name")`, as JassHelper runs library
  initializers), and the actions of a trigger whose one run is a map
  initialization or elapsed-time event. A handle lost there is lost once,
  and InitGlobals creates a group or force for every such variable.

Functions of the script that return a new handle on every path count as
creating functions too, so a call to one that drops the result is reported.

Known limits. Locals and globals are checked for the whole function, not in
the order of statements: a local destroyed once after a loop that sets it
on every turn is not reported, and neither is a global overwritten before it
is destroyed. A handle passed to a function of the script is taken as handled
even when that function does not destroy it. A global kept for the whole game
and set in a function that runs more than once, or in a trigger the check
cannot tell runs once, is reported although nothing leaks. Lua scripts are
not checked.
"""

import json
import os

from . import ir

_TABLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata', 'leaks.json')
_ONCE_ROOTS = ('main', 'config')


class LeakTable:
    """gamedata/leaks.json, indexed by function name."""

    _default = None

    def __init__(self, data):
        self.creates = {}         # function -> type
        self.destroys = {}        # function -> type
        self.release_false = {}   # SetTextTagPermanent... -> type
        self.want_destroy = set()
        self.keeps = set(data.get('keep', ()))
        self.last = {}            # type -> (global, getter, set_by)
        for type_name, entry in data['types'].items():
            for name in entry.get('create', ()):
                self.creates[name] = type_name
            for name in entry.get('destroy', ()):
                self.destroys[name] = type_name
            for name in entry.get('release_false', ()):
                self.release_false[name] = type_name
            self.want_destroy.update(entry.get('want_destroy', ()))
            self.keeps.update(entry.get('keep', ()))
            if 'last' in entry:
                last = entry['last']
                self.last[type_name] = (last['global'], last['getter'], set(last['set_by']))

    @classmethod
    def default(cls):
        if cls._default is None:
            with open(_TABLE_PATH, encoding='utf-8') as handle:
                cls._default = cls(json.load(handle))
        return cls._default


def _expressions(statements):
    """Every expression under `statements`, outermost first."""
    def expr(node):
        yield node
        if isinstance(node, ir.CallExpr):
            for arg in node.args:
                yield from expr(arg)
        elif isinstance(node, ir.ArrayRefExpr):
            yield from expr(node.index)
        elif isinstance(node, ir.UnaryExpr):
            yield from expr(node.operand)
        elif isinstance(node, ir.BinaryExpr):
            yield from expr(node.left)
            yield from expr(node.right)

    for stmt in statements:
        if isinstance(stmt, ir.CallStmt):
            yield stmt
            for arg in stmt.args:
                yield from expr(arg)
        elif isinstance(stmt, ir.Local) and stmt.initial is not None:
            yield from expr(stmt.initial)
        elif isinstance(stmt, ir.SetStmt):
            yield from expr(stmt.value)
            if stmt.index is not None:
                yield from expr(stmt.index)
        elif isinstance(stmt, ir.IfStmt):
            for condition, branch in stmt.branches:
                yield from expr(condition)
                yield from _expressions(branch)
            if stmt.else_body is not None:
                yield from _expressions(stmt.else_body)
        elif isinstance(stmt, ir.LoopStmt):
            yield from _expressions(stmt.body)
        elif isinstance(stmt, ir.ExitWhenStmt):
            yield from expr(stmt.condition)
        elif isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
            yield from expr(stmt.value)


_EXECUTES = ('TriggerExecute', 'ConditionalTriggerExecute', 'TriggerExecuteWait',
             'ConditionalTriggerExecuteWait')


def _is_single_timer(name, args):
    if name == 'TriggerRegisterTimerEventSingle':
        return True
    return (name == 'TriggerRegisterTimerEvent' and len(args) > 2
            and isinstance(args[2], ir.Literal) and args[2].kind == 'bool'
            and args[2].value is False)


def _once_functions(functions):
    """The functions that run once, at map start: main and config, a function
    only they call (by name, or through ExecuteFunc with a literal name, as
    JassHelper runs library initializers), and the actions of a trigger whose
    only run is one map initialization or elapsed-time event, or one execute
    from such a function. A function also used as code (a callback, a
    trigger's action otherwise) is not counted."""
    by_name = {f.name: f for f in functions}
    callers = {}          # function -> functions calling it
    refs = {}             # function -> times used as code
    actions = {}          # trigger variable -> action functions
    runs = {}             # trigger variable -> [(how, function)]
    for function in functions:
        for node in _expressions(function.body):
            if isinstance(node, ir.FunctionRefExpr):
                refs[node.name] = refs.get(node.name, 0) + 1
                continue
            if not isinstance(node, (ir.CallExpr, ir.CallStmt)):
                continue
            name, args = node.name, node.args
            callers.setdefault(name, set()).add(function.name)
            if (name == 'ExecuteFunc' and args and isinstance(args[0], ir.Literal)
                    and args[0].kind == 'string'):
                callers.setdefault(args[0].value, set()).add(function.name)
            target = args[0].name if args and isinstance(args[0], ir.VarExpr) else None
            if target is None:
                continue
            if (name == 'TriggerAddAction' and len(args) > 1
                    and isinstance(args[1], ir.FunctionRefExpr)):
                actions.setdefault(target, []).append(args[1].name)
            elif name.startswith('TriggerRegister'):
                runs.setdefault(target, []).append(
                    ('once' if _is_single_timer(name, args) else 'event', function.name))
            elif name in _EXECUTES:
                runs.setdefault(target, []).append(('execute', function.name))
    action_of = {}
    for trigger, names in actions.items():
        for name in names:
            action_of.setdefault(name, []).append(trigger)

    once = {name for name in _ONCE_ROOTS if name in by_name}
    changed = True
    while changed:
        changed = False
        for name in by_name:
            if name in once:
                continue
            uses = refs.get(name, 0)
            triggers = action_of.get(name, [])
            if uses != len(triggers):
                continue                      # used as code elsewhere
            if triggers:
                ok = (len(triggers) == 1 and len(runs.get(triggers[0], ())) == 1
                      and all(how == 'once' or how == 'execute' and where in once
                              for how, where in runs[triggers[0]])
                      and not callers.get(name))
            else:
                ok = bool(callers.get(name)) and callers[name] <= once
            if ok:
                once.add(name)
                changed = True
    return once


def _returns(statements):
    for stmt in statements:
        if isinstance(stmt, ir.ReturnStmt):
            yield stmt
        elif isinstance(stmt, ir.IfStmt):
            for _, branch in stmt.branches:
                yield from _returns(branch)
            if stmt.else_body is not None:
                yield from _returns(stmt.else_body)
        elif isinstance(stmt, ir.LoopStmt):
            yield from _returns(stmt.body)


def _bound_locals(statements, creates):
    """Local names set from a creating call anywhere in `statements`."""
    names = set()
    for stmt in statements:
        if isinstance(stmt, ir.Local) and isinstance(stmt.initial, ir.CallExpr) \
                and stmt.initial.name in creates:
            names.add(stmt.name)
        elif isinstance(stmt, ir.SetStmt) and stmt.index is None \
                and isinstance(stmt.value, ir.CallExpr) and stmt.value.name in creates:
            names.add(stmt.target)
        elif isinstance(stmt, ir.IfStmt):
            for _, branch in stmt.branches:
                names |= _bound_locals(branch, creates)
            if stmt.else_body is not None:
                names |= _bound_locals(stmt.else_body, creates)
        elif isinstance(stmt, ir.LoopStmt):
            names |= _bound_locals(stmt.body, creates)
    return names


def _script_creators(functions, table):
    """Functions of the script that return a new handle on every return."""
    creates = dict(table.creates)
    leaking = set(table.creates.values())
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.name in creates or function.returns not in leaking:
                continue
            returns = list(_returns(function.body))
            if not returns:
                continue
            bound = _bound_locals(function.body, creates)
            if all(isinstance(r.value, ir.CallExpr) and r.value.name in creates
                   or isinstance(r.value, ir.VarExpr) and r.value.name in bound
                   for r in returns):
                creates[function.name] = function.returns
                changed = True
    return creates


def find_leaks(program, table=None):
    """Report the handle leaks of `program`. Returns finding dicts like
    checker.check_program's: {'severity': 'warning', 'message', 'line',
    'function'}."""
    table = table or LeakTable.default()
    functions = program.functions
    script_functions = {f.name for f in functions}
    creates = _script_creators(functions, table)
    once = _once_functions(functions)
    globals_by_name = {g.name for g in program.globals}
    findings = []
    global_bindings = []      # (global, type, creator, line, function)
    global_released = set()

    for function in functions:
        if function.name in once:
            continue
        locals_ = {p.name for p in function.params}
        bindings = {}         # local -> (type, creator, line)
        released = set()
        dropped = []          # (type, creator, line)
        # Per type, the BJ-made handles bj_lastCreated... may still hold: one
        # in straight code, one per branch after an if.
        state = {'line': None, 'want': 0, 'last': {}}

        def release(node):
            if isinstance(node, (ir.VarExpr, ir.ArrayRefExpr)):
                if node.name in locals_:
                    released.add(node.name)
                else:
                    global_released.add(node.name)
                for type_name, (global_name, _, _) in table.last.items():
                    if node.name == global_name:
                        state['last'][type_name] = []
            elif isinstance(node, ir.CallExpr):
                for type_name, (_, getter, _) in table.last.items():
                    if node.name == getter:
                        state['last'][type_name] = []

        def call(name, args, safe):
            """A call whose own result is safe (bound, returned, destroyed or
            kept) or dropped."""
            if name in creates:
                type_name = creates[name]
                last = table.last.get(type_name)
                if last is not None and name in last[2]:
                    # The handle replaces the one bj_lastCreated... held.
                    dropped.extend(state['last'].get(type_name, ()))
                    state['last'][type_name] = ([] if safe
                                                else [(type_name, name, state['line'])])
                elif not safe:
                    dropped.append((type_name, name, state['line']))
            if name in _EXECUTES or name in script_functions:
                state['last'] = {}         # the code run may destroy GetLastCreated...()
            if not args:
                return
            if name in table.destroys:
                release(args[0])
                visit(args[0], True)
                rest = args[1:]
            elif name in table.release_false and len(args) > 1 \
                    and isinstance(args[1], ir.Literal) and args[1].kind == 'bool' \
                    and args[1].value in (False, 'false'):
                release(args[0])
                visit(args[0], True)
                rest = args[1:]
            elif name in table.keeps or name in script_functions:
                for arg in args:
                    release(arg)
                    visit(arg, True)
                return
            elif name in table.want_destroy:
                wanted = state['want'] > 0
                if wanted:
                    state['want'] -= 1
                visit(args[0], wanted)
                rest = args[1:]
            else:
                rest = args
            for arg in rest:
                visit(arg, False)

        def visit(node, safe):
            if isinstance(node, ir.CallExpr):
                call(node.name, node.args, safe)
            elif isinstance(node, ir.ArrayRefExpr):
                visit(node.index, False)
            elif isinstance(node, ir.UnaryExpr):
                visit(node.operand, False)
            elif isinstance(node, ir.BinaryExpr):
                visit(node.left, False)
                visit(node.right, False)

        def bind(target, value, index):
            """`target` set from `value`."""
            if isinstance(value, ir.CallExpr) and value.name in creates:
                type_name = creates[value.name]
                if target in locals_:
                    if index is None:
                        bindings.setdefault(target, (type_name, value.name, state['line']))
                    else:
                        released.add(target)
                elif not target.startswith('gg_'):
                    global_bindings.append((target, type_name, value.name, state['line'],
                                            function.name))
                call(value.name, value.args, True)
            else:
                release(value)
                visit(value, False)

        def walk(statements):
            for stmt in statements:
                state['line'] = getattr(stmt, 'line', state['line'])
                if isinstance(stmt, ir.Local):
                    locals_.add(stmt.name)
                    if stmt.initial is not None:
                        bind(stmt.name, stmt.initial, None)
                elif isinstance(stmt, ir.SetStmt):
                    if stmt.index is not None:
                        visit(stmt.index, False)
                    if stmt.target == 'bj_wantDestroyGroup':
                        value = stmt.value
                        if (isinstance(value, ir.Literal) and value.kind == 'bool'
                                and value.value is True):
                            state['want'] += 1
                        else:
                            state['want'] = 0
                    bind(stmt.target, stmt.value, stmt.index)
                elif isinstance(stmt, ir.CallStmt):
                    call(stmt.name, stmt.args, False)
                elif isinstance(stmt, ir.IfStmt):
                    before = {k: list(v) for k, v in state['last'].items()}
                    after = {}
                    bodies = [branch for _, branch in stmt.branches]
                    bodies.append(stmt.else_body or [])
                    for position, body in enumerate(bodies):
                        if position < len(stmt.branches):
                            visit(stmt.branches[position][0], False)
                        state['last'] = {k: list(v) for k, v in before.items()}
                        walk(body)
                        for type_name, held in state['last'].items():
                            merged = after.setdefault(type_name, [])
                            merged.extend(h for h in held if h not in merged)
                    state['last'] = after
                elif isinstance(stmt, ir.LoopStmt):
                    walk(stmt.body)
                elif isinstance(stmt, ir.ExitWhenStmt):
                    visit(stmt.condition, False)
                elif isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
                    release(stmt.value)
                    visit(stmt.value, True)

        walk(function.body)
        for held in state['last'].values():
            dropped.extend(held)

        for name, (type_name, creator, line) in bindings.items():
            if name not in released:
                findings.append(_finding(line, function.name,
                                         f'{type_name} {name} from {creator} is never '
                                         f'destroyed'))
        for type_name, creator, line in dict.fromkeys(dropped):
            findings.append(_finding(line, function.name,
                                     f'{type_name} from {creator} is dropped without '
                                     f'being destroyed'))

    for name, type_name, creator, line, function_name in global_bindings:
        if name not in global_released and name in globals_by_name:
            findings.append(_finding(line, function_name,
                                     f'{type_name} {name} from {creator} is never '
                                     f'destroyed by any function'))
    findings.sort(key=lambda f: (f['line'] is None, f['line'] or 0))
    return findings


def _finding(line, function, message):
    return {'severity': 'warning', 'message': f'in {function}: {message}', 'line': line,
            'function': function}
