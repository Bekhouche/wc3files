"""Recovering GUI triggers from the JASS a map's World Editor generated.

The World Editor compiles every GUI trigger to JASS in a fixed shape, which
makes the reverse direction tractable:

    function Trig_Example_Actions takes nothing returns nothing
        call AddHeroXPSwapped( 1000, GetTriggerUnit(), false )
    endfunction

    function InitTrig_Example takes nothing returns nothing
        set gg_trg_Example = CreateTrigger(  )
        call TriggerRegisterTimerEventPeriodic( gg_trg_Example, 2.00 )
        call TriggerAddAction( gg_trg_Example, function Trig_Example_Actions )
    endfunction

An `InitTrig_<Name>` function is one trigger; the `TriggerRegister*` calls in
it are its events; `TriggerAddCondition`/`TriggerAddAction` point at the
functions holding its conditions and actions. Each call inside those is a GUI
function if `TriggerData.txt` knows the name.

**This is deliberately partial, and says so in its output.** Only the shapes
the editor itself produces are recovered. Anything else - an `if`, a loop, a
call to a function the editor does not know, a hand-written helper - becomes
a *custom script* entry holding the original source line, which is exactly
how the editor represents such a trigger anyway. Every trigger reports its
own `coverage`, and `reconstruct()` reports the total, so a caller can see
how much was genuinely recovered rather than assuming it was all of it.

GUI *variables* are recovered too: the editor declares each one in the
script's `globals` block with a `udg_` prefix, so the block can be read back.
Verified against a real map that ships both files - 1,717 variables in its
`.wtg` and **1,717 recovered from its script, names matching exactly**, with
`is_array` right for every one. Its `.wtg` stores `casting_hero` where the
script declares `udg_casting_hero`, so the prefix comes off on the way back.
(The bundled `tests/samples/wtg/sample.wtg` keeps the prefix in its stored
name, which makes it a hand-made fixture rather than editor output - worth
knowing before treating it as a reference.)

The one thing a script cannot give back is a GUI *pseudo-type*. The editor
distinguishes `unitcode`, `abilcode`, `itemcode` and the like, but JASS has
no such types - they are all plain `integer` in the compiled script, so a
recovered variable says `integer`. On that same map this affected 42 of
1,717 variables (2.4%): 25 `unitcode`, 11 `abilcode`, 4 `itemcode`, one
`integervar` and one `lightningtype` (a `string` in script). Guessing the
refinement back from how a variable is used would be exactly that - a
guess - so the base type is reported instead.

What this does **not** attempt: trigger categories and folder structure
(neither survives into the script), and recovering a trigger that was
converted to custom text in the editor (its GUI form is gone from the map
entirely, not merely compiled away).

The output matches the shape `MapTriggersFile.get_data()` produces, so it
renders through `trigger_text` and can be written back out.
"""
import re

import copy

from .trigger_data import ACTION, CALL, CONDITION, EVENT, TriggerData
from .trigger_text import FUNCTION, PRESET, STRING, VARIABLE

_INIT_PREFIX = 'InitTrig_'
# The operators a GUI comparison can compile to. `==`/`!=` cover the
# EqualNotEqualOperator conditions, the ordering ones ComparisonOperator.
_COMPARISON_OPERATORS = frozenset({'==', '!=', '<', '<=', '>', '>='})
_TRIGGER_VAR = re.compile(r'^gg_trg_')
_UDG_PREFIX = 'udg_'

# Calls in an InitTrig body that are structural rather than events.
_TRIGGER_PLUMBING = {
    'CreateTrigger', 'TriggerAddAction', 'TriggerAddCondition',
    'DisableTrigger', 'EnableTrigger', 'Condition', 'Filter',
}


class ScriptTriggers(object):
    """Reconstructs GUI triggers from a parsed JASS program."""

    def __init__(self, trigger_data=None, trigger_text=None):
        self.trigger_data = trigger_data or TriggerData.default()
        self._text = trigger_text
        self._by_name = {}
        self._preset_by_code = None
        self._preset_by_code_and_type = None

        self._known = {}
        for function_type, section in ((EVENT, 'TriggerEvents'),
                                       (CONDITION, 'TriggerConditions'),
                                       (ACTION, 'TriggerActions'),
                                       (CALL, 'TriggerCalls')):
            for name in self.trigger_data._arg_counts[section]:
                # A name can appear in several sections; first wins, and the
                # order above puts events before calls deliberately.
                self._known.setdefault(name, function_type)

    # -- entry point ------------------------------------------------------

    def reconstruct(self, program):
        """Return `{'triggers': [...], 'coverage': ...}` for a parsed program."""
        self._by_name = {f.name: f for f in program.functions}
        # A GUI Comment action compiles to a plain `//` line and leaves
        # nothing else behind, so the parser keeps comments on the Program
        # and they are matched here to the statement they precede.
        self._comments = sorted(getattr(program, 'comments', None) or ())
        self._map_init = self._map_init_triggers()

        variables = self._variables_from_globals(program)
        regions, cameras, sounds = self._placed_objects(program)

        triggers = []
        for function in program.functions:
            if function.name.startswith(_INIT_PREFIX):
                triggers.append(self._trigger_from_init(function))

        recovered = sum(t['coverage']['recovered'] for t in triggers)
        total = sum(t['coverage']['total'] for t in triggers)
        return {
            'triggers': triggers,
            'variables': variables,
            'regions': regions,
            'cameras': cameras,
            'sounds': sounds,
            'coverage': {
                'variables': len(variables),
                'regions': len(regions),
                'cameras': len(cameras),
                'sounds': len(sounds),
                'triggers': len(triggers),
                'recovered': recovered,
                'total': total,
                'ratio': (recovered / total) if total else 0.0,
            },
        }

    def _variables_from_globals(self, program):
        """Recover the GUI variables from the script's `globals` block.

        The editor names every GUI variable `udg_<name>` in the script and
        stores it without that prefix in the `.wtg`, so the prefix comes off
        here. A global without it was not a GUI variable - it is the map's own
        or the editor's (`gg_trg_*`, `gg_rct_*`, ...) - and is left alone.
        """
        out = []
        for variable in getattr(program, 'globals', None) or ():
            name = getattr(variable, 'name', '') or ''
            if not name.startswith(_UDG_PREFIX):
                continue
            initial = getattr(variable, 'initial', None)
            initial_text = '' if initial is None else self._source_of(initial)
            out.append({
                'name': name[len(_UDG_PREFIX):],
                'type': getattr(variable, 'type', '') or '',
                'unknown': 1,
                'is_array': bool(getattr(variable, 'is_array', False)),
                'array_size': 1,
                'is_initialized': initial is not None,
                'initial_value': initial_text,
            })
        return out

    # -- map objects the editor also declares -----------------------------

    def _placed_objects(self, program):
        """Recover regions, cameras and sounds from the editor's setup code.

        Besides GUI variables the editor emits a `gg_`-prefixed global for
        everything placed on the map, and builds each one in a generated
        setup function:

            set gg_rct_CENTER = Rect( -608.0, -4096.0, 672.0, -2176.0 )
            set gg_snd_Hint   = CreateSound( "Sound\\Hint.wav", ... )
            set gg_cam_Intro  = CreateCameraSetup(  )

        These each have their own file too (`war3map.w3r`/`.w3c`/`.w3s`), so
        this is mainly worth having when those files are gone - a protector
        commonly strips them while leaving the script intact.

        A caveat on names: the editor turns a region called "ALL HERO" into
        the identifier `gg_rct_ALL_HERO`, and that mangling is not reversible
        - an underscore in the identifier may have been a space or an
        underscore. The identifier is reported as `name` and left alone
        rather than guessed back.
        """
        regions, cameras, sounds = [], [], []
        for function in program.functions:
            for statement in function.body:
                if type(statement).__name__ != 'SetStmt':
                    continue
                target = getattr(statement, 'target', '') or ''
                value = getattr(statement, 'value', None)
                if not isinstance(target, str) or type(value).__name__ != 'CallExpr':
                    continue

                if target.startswith('gg_rct_') and value.name == 'Rect':
                    bounds = [self._number(argument) for argument in value.args]
                    if len(bounds) == 4 and None not in bounds:
                        regions.append({
                            'name': target[len('gg_rct_'):],
                            'left': bounds[0], 'bottom': bounds[1],
                            'right': bounds[2], 'top': bounds[3],
                        })
                elif target.startswith('gg_cam_') and value.name == 'CreateCameraSetup':
                    cameras.append({'name': target[len('gg_cam_'):]})
                elif target.startswith('gg_snd_') and value.name in (
                        'CreateSound', 'CreateSoundFilenameWithLabel',
                        'CreateMIDISound'):
                    path = None
                    if value.args and type(value.args[0]).__name__ == 'Literal':
                        path = value.args[0].value
                    sounds.append({'name': target[len('gg_snd_'):], 'path': path})
        return regions, cameras, sounds

    @staticmethod
    def _number(expression):
        if type(expression).__name__ == 'Literal' and expression.kind in ('real', 'int'):
            try:
                return float(expression.value)
            except (TypeError, ValueError):
                return None
        if type(expression).__name__ == 'UnaryExpr' and expression.op == '-':
            inner = ScriptTriggers._number(expression.operand)
            return None if inner is None else -inner
        return None

    # -- one trigger ------------------------------------------------------

    _TRIGGER_GLOBAL_PREFIX = 'gg_trg_'

    def _map_init_triggers(self):
        """The triggers whose event is "Map initialization".

        That event compiles to no `TriggerRegister` call at all - the editor
        runs those triggers directly instead:

            function RunInitializationTriggers takes nothing returns nothing
                call ConditionalTriggerExecute( gg_trg_Melee_Initialization )
            endfunction

        so this is the only thing left in the script that says the event was
        there. Confirmed against a real map.
        """
        runner = self._by_name.get('RunInitializationTriggers')
        if runner is None:
            return frozenset()

        names = set()
        for statement in getattr(runner, 'body', None) or ():
            call = self._as_call(statement)
            if call is None or call.name != 'ConditionalTriggerExecute':
                continue
            for argument in call.args:
                name = self._name_of(argument)
                if name.startswith(self._TRIGGER_GLOBAL_PREFIX):
                    names.add(name[len(self._TRIGGER_GLOBAL_PREFIX):])
        return frozenset(names)

    def _trigger_from_init(self, init_function):
        name = init_function.name[len(_INIT_PREFIX):]
        functions = []
        if name in self._map_init:
            functions.append(self._gui_function(
                EVENT, 'MapInitializationEvent', []))
        enabled = True
        initially_on = True

        for statement in init_function.body:
            call = self._as_call(statement)
            if call is None:
                continue
            if call.name == 'DisableTrigger':
                initially_on = False
                continue
            if call.name == 'TriggerAddAction':
                functions.extend(self._functions_from_handler(call, ACTION))
                continue
            if call.name == 'TriggerAddCondition':
                functions.extend(self._functions_from_handler(call, CONDITION))
                continue
            if call.name in _TRIGGER_PLUMBING:
                continue
            if call.name.startswith('TriggerRegister'):
                # The first argument is the trigger itself; the rest are the
                # event's own parameters.
                args = self._strip_trigger_arg(call.args)
                functions.append(self._gui_function(EVENT, call.name, args))

        recovered = sum(1 for f in functions if not f.get('custom'))
        return {
            'name': name,
            'description': '',
            'is_enabled': enabled,
            'is_initially_on': initially_on,
            'functions': functions,
            'coverage': {'recovered': recovered, 'total': len(functions)},
        }

    def _functions_from_handler(self, call, function_type):
        """Follow TriggerAddAction/TriggerAddCondition to the function it names."""
        target = None
        for argument in call.args:
            name = self._function_ref_name(argument)
            if name:
                target = name
        if not target or target not in self._by_name:
            return []
        return self._functions_from_body(self._by_name[target], function_type)

    def _condition_group(self, function):
        """An `Or - Any conditions` block, if that is what this function is.

        The editor compiles "any of these" to its own function spelled

            if ( <a> ) then
                return true
            endif
            if ( <b> ) then
                return true
            endif
            return false

        which is the mirror of the ordinary conditions function, where each
        test is negated and returns false. Confirmed against a real map.
        The children are the individual conditions, so they are recovered
        too instead of being buried in raw text.
        """
        body = list(getattr(function, 'body', None) or [])
        if len(body) < 2:
            return None
        if type(body[-1]).__name__ != 'ReturnStmt':
            return None
        returned = getattr(body[-1], 'value', None)
        if type(returned).__name__ != 'Literal' or str(
                returned.value).lower() not in ('false', '0'):
            return None

        children = []
        for statement in body[:-1]:
            test = self._positive_test(statement)
            if test is None:
                return None
            children.extend(self._conditions_of(test))
        if not children:
            return None
        for child in children:
            child['branch'] = 0
        return {
            'type': CONDITION,
            'name': 'OrMultiple',
            'is_enabled': True,
            'parameters': [],
            'children': children,
        }

    @staticmethod
    def _positive_test(statement):
        """The `<test>` of an `if ( <test> ) then return true endif`."""
        if type(statement).__name__ != 'IfStmt':
            return None
        branches = getattr(statement, 'branches', None) or []
        if len(branches) != 1 or getattr(statement, 'else_body', None):
            return None
        condition, body = branches[0]
        if len(body or []) != 1 or type(body[0]).__name__ != 'ReturnStmt':
            return None
        returned = getattr(body[0], 'value', None)
        if type(returned).__name__ != 'Literal' or str(
                returned.value).lower() not in ('true', '1'):
            return None
        return condition

    def _functions_from_body(self, function, function_type):
        if function_type == CONDITION:
            group = self._condition_group(function)
            if group is not None:
                return [group]
        return self._functions_from_statements(
            function.body, function_type,
            span=(getattr(function, 'line', None),
                  getattr(function, 'end_line', None)))

    def _comments_between(self, start, end):
        """The comment text on lines in `(start, end)`, exclusive."""
        if start is None or end is None or not self._comments:
            return []
        return [text for line, text in self._comments if start < line < end]

    def _comment_actions(self, start, end):
        return [{
            'type': ACTION,
            'name': 'CommentString',
            'is_enabled': True,
            'parameters': [{'type': STRING, 'value': text,
                            'function': None, 'array_indexer': None}],
        } for text in self._comments_between(start, end)]

    def _functions_from_statements(self, statements, function_type, span=None):
        out = []
        # Only an actions body can hold a GUI comment; a conditions body is
        # generated whole and has nowhere to put one.
        comments_wanted = function_type == ACTION and span is not None
        previous_line = span[0] if comments_wanted else None

        index = 0
        while index < len(statements):
            statement = statements[index]
            index += 1
            if comments_wanted:
                line = getattr(statement, 'line', None)
                if line is not None:
                    out.extend(self._comment_actions(previous_line, line))
                    previous_line = line
            kind = type(statement).__name__

            # A counted loop is spelled across three statements, so it is
            # matched here where the following ones are still in reach.
            if function_type == ACTION:
                loop = self._for_loop(statements, index - 1)
                if loop is not None:
                    entry, index = loop
                    out.append(entry)
                    continue

            # A conditions function ends with `return true`, and each GUI
            # condition before it is an `if (not (...)) then return false
            # endif`. Those returns are the compiler's scaffolding, not
            # anything the author wrote - counting them inflated conditions
            # by exactly one per function (298 of them on the benchmark map).
            if function_type == CONDITION and kind == 'ReturnStmt':
                continue

            if function_type == CONDITION:
                unwrapped = self._unwrap_condition(statement)
                if unwrapped is not None:
                    out.append(unwrapped)
                    continue

            if function_type == ACTION and kind == 'IfStmt':
                out.append(self._if_then_else(statement))
                continue

            call = self._as_call(statement)
            if call is not None:
                enumeration = self._enumeration(call, function_type)
                if enumeration is not None:
                    out.append(enumeration)
                    continue

            if call is not None and call.name in self._known:
                out.append(self._gui_function(function_type, call.name, call.args))
                continue

            # The editor emits a different name for some GUI functions than
            # the one it stores; TriggerData.txt's `_X_ScriptName=Y` lines
            # are the only record of which. Without this a compiled
            # `SetPlayerTechMaxAllowedSwap` is unrecognisable as the GUI's
            # `SetPlayerUnitMaxAllowed` and comes back as raw script.
            if call is not None:
                candidates = [name for name in self.trigger_data.gui_names(call.name)
                              if self._known.get(name) == function_type]
                if len(candidates) == 1:
                    # The GUI name is the one TriggerData declares the
                    # argument types under, and the `Swap`/`BJ` wrapper takes
                    # them in that same order - it is named for swapping
                    # relative to the underlying native, not to the GUI.
                    out.append(self._gui_function(
                        function_type, candidates[0], call.args))
                    continue
            if call is not None and call.name == 'TriggerSleepAction':
                out.append(self._gui_function(function_type, call.name, call.args))
                continue

            set_variable = self._as_set_variable(statement, function_type)
            if set_variable is not None:
                out.append(set_variable)
                continue

            # Not something the editor would have generated from GUI: keep the
            # source so nothing is lost.
            out.append(self._custom(function_type, statement))

        if comments_wanted:
            # Anything after the last statement, up to `endfunction`.
            out.extend(self._comment_actions(previous_line, span[1]))
        return out

    def _unwrap_condition(self, statement):
        """Recover the test inside the editor's condition wrapper.

        Every GUI condition compiles to

            if ( not ( <test> ) ) then
                return false
            endif

        so reporting the whole block as raw script buries the only part that
        means anything. This returns a condition carrying just `<test>`.

        The *name* of the GUI function is deliberately not guessed. The editor
        distinguishes `OperatorCompareInteger` from `OperatorCompareUnitCode`
        and `OperatorCompareAbilityId`, but all three compile to the same
        integer comparison - the same pseudo-type problem that stops a
        variable's `unitcode` coming back. Naming one of them would be a coin
        toss dressed up as a fact, so the comparison is reported as custom
        text and only `AndMultiple`/`OrMultiple`, which the operator itself
        settles, are named.
        """
        if type(statement).__name__ != 'IfStmt':
            return None
        branches = getattr(statement, 'branches', None) or []
        if len(branches) != 1 or getattr(statement, 'else_body', None):
            return None

        condition, body = branches[0]
        # The body must be exactly `return false`.
        if len(body or []) != 1 or type(body[0]).__name__ != 'ReturnStmt':
            return None
        returned = getattr(body[0], 'value', None)
        if type(returned).__name__ != 'Literal' or str(returned.value).lower() not in (
                'false', '0'):
            return None

        # Strip the `not (...)` the wrapper adds.
        test = condition
        if type(test).__name__ == 'UnaryExpr' and test.op == 'not':
            test = test.operand

        # An inline `and`/`or`. The editor stores these as children, like
        # the lifted-out form, not as two flat parameters - and keeping the
        # operands as raw text lost the conditions inside them.
        if type(test).__name__ == 'BinaryExpr' and test.op in ('and', 'or'):
            children = (self._conditions_of(test.left)
                        + self._conditions_of(test.right))
            for child in children:
                child['branch'] = 0
            return {
                'type': CONDITION,
                'name': 'AndMultiple' if test.op == 'and' else 'OrMultiple',
                'is_enabled': True,
                'parameters': [],
                'children': children,
            }

        nested = self._nested_condition_group(test)
        if nested is not None:
            return nested

        named = self._named_comparison(test)
        if named is not None:
            return named

        return {
            'type': CONDITION,
            'name': 'CustomScriptCode',
            'is_enabled': True,
            'custom': True,
            'parameters': [{'type': STRING, 'value': self._source_of(test),
                            'function': None, 'array_indexer': None}],
        }

    def _if_then_else(self, statement):
        """Rebuild an `if/then/else` in an actions body as the GUI construct.

        The editor stores this as `IfThenElseMultiple` with no parameters and
        a flat list of children, each tagged with the branch it belongs to:
        0 for the conditions, 1 for the then-actions, 2 for the else-actions.
        That shape is taken from the editor's own `.wtg`, not invented here.

        JASS has `elseif` and the GUI does not, so a chained `elseif` is
        rebuilt the way the editor represents it: as a further
        `IfThenElseMultiple` nested in the else branch.
        """
        branches = getattr(statement, 'branches', None) or []
        if not branches:
            return self._custom(ACTION, statement)

        condition, body = branches[0]
        children = []
        for entry in self._conditions_of(condition):
            entry['branch'] = 0
            children.append(entry)
        for entry in self._functions_from_statements(body or [], ACTION):
            entry['branch'] = 1
            children.append(entry)

        if len(branches) > 1:
            # `elseif` - the remaining branches form a nested if in the else.
            nested = copy.copy(statement)
            nested.branches = branches[1:]
            else_entries = [self._if_then_else(nested)]
        else:
            else_entries = self._functions_from_statements(
                getattr(statement, 'else_body', None) or [], ACTION)
        for entry in else_entries:
            entry['branch'] = 2
            children.append(entry)

        return {
            'type': ACTION,
            'name': 'IfThenElseMultiple',
            'is_enabled': True,
            'parameters': [],
            'children': children,
        }

    _LOOP_COUNTERS = {
        'bj_forLoopAIndex': ('ForLoopAMultiple', 'bj_forLoopAIndexEnd'),
        'bj_forLoopBIndex': ('ForLoopBMultiple', 'bj_forLoopBIndexEnd'),
    }

    def _for_loop(self, statements, start):
        """Rebuild the GUI's counted loop from the idiom it compiles to.

        "For each integer A from x to y, do multiple actions" becomes

            set bj_forLoopAIndex = x
            set bj_forLoopAIndexEnd = y
            loop
                exitwhen bj_forLoopAIndex > bj_forLoopAIndexEnd
                <body>
                set bj_forLoopAIndex = bj_forLoopAIndex + 1
            endloop

        checked against a real map rather than assumed. A loop over a user
        variable is the same shape without the separate end variable, and is
        the GUI's `ForLoopVarMultiple`.

        Returns `(entry, next_index)`, or None if the statements are not that
        idiom - an ordinary `loop` the author wrote by hand still comes back
        as raw script rather than being forced into a GUI shape.
        """
        statement = statements[start]
        if type(statement).__name__ != 'SetStmt':
            return None
        counter = getattr(statement, 'target', '')
        if not isinstance(counter, str) or getattr(statement, 'index', None):
            return None

        known = self._LOOP_COUNTERS.get(counter)
        if known is None and not counter.startswith(_UDG_PREFIX):
            return None

        index = start + 1
        initial = getattr(statement, 'value', None)
        limit = None
        if known is not None:
            # The separate end variable must be set next.
            if index >= len(statements):
                return None
            end_statement = statements[index]
            if (type(end_statement).__name__ != 'SetStmt'
                    or getattr(end_statement, 'target', '') != known[1]):
                return None
            limit = getattr(end_statement, 'value', None)
            index += 1

        if index >= len(statements) or type(statements[index]).__name__ != 'LoopStmt':
            return None
        body = list(getattr(statements[index], 'body', None) or [])
        index += 1

        if not body or type(body[0]).__name__ != 'ExitWhenStmt':
            return None
        guard = getattr(body[0], 'condition', None)
        if type(guard).__name__ != 'BinaryExpr' or guard.op != '>':
            return None
        if self._name_of(guard.left) != counter:
            return None
        if known is not None:
            if self._name_of(guard.right) != known[1]:
                return None
        else:
            limit = guard.right

        # ...and the last statement must step the counter, or this is some
        # other loop that merely happens to start by testing it.
        if len(body) < 2 or not self._is_increment(body[-1], counter):
            return None

        name = known[0] if known is not None else 'ForLoopVarMultiple'
        # Both bounds are declared `integer`; passing that stops a literal
        # `1` being read as a preset that happens to compile to 1.
        types = self.trigger_data.arg_types(ACTION, name)
        if known is not None:
            bound_types = types[:2] if len(types) >= 2 else [None, None]
            parameters = [self._parameter(initial, bound_types[0]),
                          self._parameter(limit, bound_types[1])]
        else:
            bound_types = types[1:3] if len(types) >= 3 else [None, None]
            parameters = [{'type': VARIABLE,
                           'value': counter[len(_UDG_PREFIX):],
                           'function': None, 'array_indexer': None},
                          self._parameter(initial, bound_types[0]),
                          self._parameter(limit, bound_types[1])]

        children = self._functions_from_statements(body[1:-1], ACTION)
        for child in children:
            child['branch'] = 0

        return {
            'type': ACTION,
            'name': name,
            'is_enabled': True,
            'parameters': parameters,
            'children': children,
        }, index

    @staticmethod
    def _name_of(expression):
        """The variable an expression names, or '' if it names none."""
        if type(expression).__name__ != 'VarExpr':
            return ''
        return getattr(expression, 'name', '') or ''

    def _is_increment(self, statement, counter):
        """`set <counter> = <counter> + 1`."""
        if type(statement).__name__ != 'SetStmt':
            return False
        if getattr(statement, 'target', '') != counter:
            return False
        value = getattr(statement, 'value', None)
        if type(value).__name__ != 'BinaryExpr' or value.op != '+':
            return False
        return self._name_of(value.left) == counter

    def _enumeration(self, call, function_type):
        """Rebuild `ForGroupBJ`/`ForForce` and friends as the GUI construct.

        These take a function to run over each member, and the editor stores
        the GUI form with that function's body inlined as children - all on
        branch 0, unlike if/then/else, which is again taken from a real
        `.wtg` rather than assumed.

        One script name can stand for two GUI functions: `ForGroupBJ` is both
        `ForGroup` ("do one action") and `ForGroupMultiple` ("do multiple
        actions"). The table cannot settle that, but the call can - the
        `Multiple` form is the one whose body holds more than a single
        action.
        """
        if function_type != ACTION:
            return None
        candidates = [name for name in self.trigger_data.gui_names(call.name)
                      if self._known.get(name) == ACTION]
        multiple = [n for n in candidates if n.endswith('Multiple')]
        if not multiple:
            return None

        # The callback is passed directly - `ForGroupBJ( <group>, function
        # X )` - so only a bare function reference counts. Looking inside
        # nested calls as well swallowed the group itself whenever it was
        # built by something taking a filter, such as
        # GetUnitsInRectMatching( r, Condition(function X) ), which left the
        # enumeration with no arguments and the wrong body.
        targets = [argument.name for argument in call.args
                   if type(argument).__name__ == 'FunctionRefExpr']
        if len(targets) != 1 or targets[0] not in self._by_name:
            return None
        target = targets[0]

        children = self._functions_from_body(self._by_name[target], ACTION)
        for child in children:
            child['branch'] = 0

        # Both GUI forms - "do one action" and "do multiple actions" -
        # compile to the identical call, and the body says nothing about
        # which the author picked: the editor is equally willing to store a
        # one-action ForGroupMultiple. Choosing by the number of actions was
        # a heuristic, and measuring it against the editor's own .wtg showed
        # it losing more than it gained, because the one-action form keeps
        # its action in a parameter where the children would otherwise be.
        # The Multiple form is used throughout, which keeps the actions
        # themselves recoverable whichever form was really meant.
        name = multiple[0]
        single_action = None

        # Without the declared types a bare `1` comes back as whichever
        # preset happens to compile to 1, which is worse than raw script.
        types = self.trigger_data.arg_types(ACTION, name)
        rest = [argument for argument in call.args
                if type(argument).__name__ != 'FunctionRefExpr']
        parameters = [self._parameter(argument,
                                      types[index] if index < len(types) else None)
                      for index, argument in enumerate(rest)]
        if single_action is not None:
            parameters.append({'type': FUNCTION, 'value': '',
                               'array_indexer': None,
                               'function': single_action})
        return {
            'type': ACTION,
            'name': name,
            'is_enabled': True,
            'parameters': parameters,
            'children': children,
        }

    def _nested_condition_group(self, test):
        """An `And`/`Or` block the editor lifted into its own function.

        A grouped condition is a call to a generated conditions function.
        Which block it was is settled by that function's shape, not guessed:
        the `return true`-per-test form is `Or`, and the ordinary negated
        form is `And`.
        """
        if type(test).__name__ != 'CallExpr' or test.args:
            return None
        target = self._by_name.get(getattr(test, 'name', ''))
        if target is None:
            return None

        group = self._condition_group(target)
        if group is not None:
            return group

        children = self._functions_from_statements(target.body, CONDITION)
        if not children:
            return None
        for child in children:
            child['branch'] = 0
        return {
            'type': CONDITION,
            'name': 'AndMultiple',
            'is_enabled': True,
            'parameters': [],
            'children': children,
        }

    def _conditions_of(self, condition):
        """The GUI conditions behind an `if`'s test.

        The editor usually lifts the test into its own generated function and
        calls it, so following that call recovers the individual conditions
        rather than reporting the call itself as raw script.
        """
        if type(condition).__name__ == 'CallExpr':
            target = self._by_name.get(getattr(condition, 'name', ''))
            if target is not None:
                return self._functions_from_body(target, CONDITION)

        named = self._named_comparison(condition)
        if named is not None:
            return [named]
        return [{
            'type': CONDITION,
            'name': 'CustomScriptCode',
            'is_enabled': True,
            'custom': True,
            'parameters': [{'type': STRING, 'value': self._source_of(condition),
                            'function': None, 'array_indexer': None}],
        }]

    def _named_comparison(self, test):
        """Name a compiled comparison from the type of what it compares.

        An earlier version of this reader reported every comparison as raw
        script, reasoning that `OperatorCompareInteger`, `OperatorCompareUnitCode`
        and `OperatorCompareAbilityId` all compile to the same integer
        comparison and that picking one would be a guess. That is true of the
        *operator* and false of the *operands*: TriggerData.txt declares what
        every call returns, and `OperatorCompareX` compares exactly type `X`,
        so `GetSpellAbilityId() == 'A000'` can only have been
        `OperatorCompareAbilityId`. Both halves of that come out of the game's
        own data file, so nothing here is guessed.

        Measured against the editor's own `.wtg` across the benchmark corpus,
        the operand type agreed with the name the editor stored in every case
        checked for abilcode, heroskillcode, button, itemcode and unitcode.
        Where the operand has no declared type - a bare variable, a literal,
        arithmetic - there is genuinely nothing to go on, and the comparison
        is still reported as raw script rather than guessed.
        """
        if type(test).__name__ != 'BinaryExpr':
            return None
        if test.op not in _COMPARISON_OPERATORS:
            return None

        gui_type = (self._operand_type(test.left)
                    or self._operand_type(test.right))
        if not gui_type:
            return None
        name = self.trigger_data.comparison_for_type(gui_type)
        if not name:
            return None

        # The editor stores a comparison as three parameters - left, the
        # operator, right - not two; checked against a real .wtg. The
        # operator is a preset, and which one depends on the declared type
        # of that slot, since `==` is `OperatorEqual` under
        # ComparisonOperator and `OperatorEqualENE` under
        # EqualNotEqualOperator.
        types = self.trigger_data.arg_types(CONDITION, name)
        operator = self._preset_for(test.op, types[1] if len(types) > 1 else None)
        return {
            'type': CONDITION,
            'name': name,
            'is_enabled': True,
            'parameters': [
                self._parameter(test.left, types[0] if types else None),
                self._param(PRESET, operator) if operator
                else self._param(STRING, test.op),
                self._parameter(test.right, types[2] if len(types) > 2 else None),
            ],
        }

    def _operand_type(self, expression):
        """The GUI type of an operand, from the call at its head.

        Only a call says anything: a literal `'A000'` is an integer as far as
        the script is concerned, and a variable's GUI type is not recoverable
        here. Those return '' and leave the comparison unnamed.
        """
        if type(expression).__name__ != 'CallExpr':
            return ''
        return self.trigger_data.return_type(getattr(expression, 'name', '')) or ''

    def _as_set_variable(self, statement, function_type):
        """`set udg_x = value` is the GUI's "Set Variable" action.

        Reported as that rather than as raw script, which is what a reader
        wants - though note the count will exceed the editor's: it also
        inlines temporaries this way when expanding other actions, and those
        are indistinguishable here from a Set Variable the author placed.
        """
        if function_type != ACTION or type(statement).__name__ != 'SetStmt':
            return None
        target = getattr(statement, 'target', '')
        if not isinstance(target, str) or not target.startswith(_UDG_PREFIX):
            return None

        index = getattr(statement, 'index', None)
        variable = {
            'type': VARIABLE,
            'value': target[len(_UDG_PREFIX):],
            'function': None,
            'array_indexer': (self._parameter(index, 'integer')
                              if index is not None else None),
        }
        return {
            'type': ACTION,
            'name': 'SetVariable',
            'is_enabled': True,
            'parameters': [variable, self._parameter(getattr(statement, 'value', None))],
        }

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _as_call(statement):
        if type(statement).__name__ == 'CallStmt':
            return statement
        return None

    @staticmethod
    def _function_ref_name(expression):
        kind = type(expression).__name__
        if kind == 'FunctionRefExpr':
            return expression.name
        if kind == 'CallExpr':
            # Condition(function Trig_X_Conditions) / Filter(...)
            for argument in expression.args:
                name = ScriptTriggers._function_ref_name(argument)
                if name:
                    return name
        return None

    @staticmethod
    def _strip_trigger_arg(args):
        if args and type(args[0]).__name__ == 'VarExpr' and _TRIGGER_VAR.match(args[0].name):
            return args[1:]
        return args

    def _gui_function(self, function_type, name, args):
        # The declared argument types disambiguate presets: 48 different
        # presets compile to the plain JASS value `false`, and only the
        # parameter's type says which one a given call meant.
        types = self.trigger_data.arg_types(function_type, name)
        parameters = []
        for index, argument in enumerate(args):
            expected = types[index] if index < len(types) else None
            parameters.append(self._parameter(argument, expected))
        return {
            'type': function_type,
            'name': name,
            'is_enabled': True,
            'parameters': parameters,
        }

    def _custom(self, function_type, statement):
        source = self._source_of(statement)
        return {
            'type': function_type,
            'name': 'CustomScriptCode',
            'is_enabled': True,
            'custom': True,
            'parameters': [{'type': STRING, 'value': source,
                            'function': None, 'array_indexer': None}],
        }

    def _parameter(self, expression, expected_type=None):
        kind = type(expression).__name__

        if kind == 'Literal':
            value = expression.value
            if expression.kind == 'string':
                text = value
            elif expression.kind == 'bool':
                text = 'true' if value in (True, 'true') else 'false'
            elif expression.kind == 'real':
                # The editor writes every real with exactly two decimals -
                # 10,717 of 10,717 real values across the benchmark corpus,
                # with no exception - so `1.0` from the script has to be
                # rendered `1.00` to say the same thing it does.
                text = self._real_text(value)
            else:
                text = str(value)
            preset = self._preset_for(text, expected_type)
            if preset:
                return self._param(PRESET, preset)
            return self._param(STRING, text)

        if kind == 'CallExpr':
            # Some presets are a call, not a literal: the editor's `Player01`
            # is `Player(1)` in the script, and TriggerData.txt records that
            # as the preset's script text. Matching on the rendered call is
            # what connects the two.
            preset = self._preset_for(self._source_of(expression), expected_type)
            if preset:
                return self._param(PRESET, preset)

            if expression.name in self._known:
                return {
                    'type': FUNCTION,
                    'value': '',
                    'array_indexer': None,
                    'function': self._gui_function(
                        self._known[expression.name], expression.name,
                        expression.args),
                }
            return self._param(STRING, self._source_of(expression))

        if kind == 'VarExpr':
            preset = self._preset_for(expression.name, expected_type)
            if preset:
                return self._param(PRESET, preset)
            return self._param(VARIABLE, self._variable_name(expression.name))

        if kind == 'ArrayRefExpr':
            return {
                'type': VARIABLE,
                'value': self._variable_name(expression.name),
                'function': None,
                'array_indexer': self._parameter(expression.index, 'integer'),
            }

        return self._param(STRING, self._source_of(expression))

    @staticmethod
    def _variable_name(name):
        """A GUI variable as the editor stores it, without its `udg_` prefix.

        The prefix is the script's, not the author's - the editor shows and
        stores `HeroArray` for the script's `udg_HeroArray`. A global without
        it was not a GUI variable and is left alone.
        """
        if isinstance(name, str) and name.startswith(_UDG_PREFIX):
            return name[len(_UDG_PREFIX):]
        return name

    @staticmethod
    def _real_text(value):
        """A real as the editor writes it: two decimals."""
        try:
            return f'{float(value):.2f}'
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _param(kind, value):
        return {'type': kind, 'value': value, 'function': None,
                'array_indexer': None}

    def _preset_for(self, code_text, expected_type=None):
        """Map a JASS value back to the preset the editor would show.

        `TriggerParams` lists each preset's script code at index 2 and its
        type at index 1, so this is that table read backwards, keyed by both.
        The type matters: `false` is the script value of 48 different presets,
        and picking by code alone names the wrong one - which is worse than
        naming none, because the output looks right.
        """
        if self._preset_by_code is None:
            text = self._text
            if text is None:
                from .trigger_text import TriggerText

                text = TriggerText.default()
                self._text = text
            self._preset_by_code = {}
            self._preset_by_code_and_type = {}
            for key, code in getattr(text, 'preset_code', {}).items():
                self._preset_by_code.setdefault(code, key)
                kind = getattr(text, 'preset_type', {}).get(key)
                if kind:
                    self._preset_by_code_and_type.setdefault((code, kind), key)

        if expected_type:
            typed = self._preset_by_code_and_type.get((code_text, expected_type))
            if typed:
                return typed
            # A value with a declared type that matches no preset of that type
            # is a plain value, not a preset - returning the untyped match here
            # is how the wrong name would creep back in.
            return None

        # With no declared type there is nothing to pick by but the code, and
        # this method's own warning applies: `false` is the script value of 48
        # presets and `0` of many more, so a match here is a coin toss that
        # reads as fact. Measured against the editor's own .wtg, this was
        # wrong far more often than right - a plain `0` came back as
        # `UnitTypeNull` and `true` as `ElevatorWallOpOpen`. The value is left
        # as what the script says instead.
        return None

    @staticmethod
    def _source_of(node):
        """The original source for a node, for a custom-script entry.

        Expressions and statements need different printers; falling back to
        the node's class name produced output reading "CallExpr", which tells
        a reader nothing about what the trigger actually did.
        """
        from ..scripts import ir
        from ..scripts.jass_printer import print_expr, print_program

        # An expression prints on its own.
        if isinstance(node, (ir.CallExpr, ir.VarExpr, ir.Literal, ir.BinaryExpr,
                             ir.UnaryExpr, ir.ArrayRefExpr, ir.FunctionRefExpr)):
            try:
                return print_expr(node)
            except Exception:
                pass

        try:
            wrapper = ir.FunctionDecl(name='_', params=[], returns=None,
                                      body=[node])
            text = print_program(ir.Program(functions=[wrapper]))
            inner = [line.strip() for line in text.splitlines()
                     if line.strip()
                     and not line.strip().startswith(('function', 'endfunction'))]
            if inner:
                return ' '.join(inner)
        except Exception:
            pass
        return type(node).__name__


def reconstruct_from_jass(source, trigger_data=None):
    """Reconstruct GUI triggers from JASS source text or a parsed program."""
    from ..scripts import parse_jass

    program = parse_jass(source) if isinstance(source, str) else source
    return ScriptTriggers(trigger_data=trigger_data).reconstruct(program)
