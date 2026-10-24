"""
JASS optimizer: reachability-based dead-code elimination.

Ports the *design* of w3x2lni's `simplify.lua` (mark-and-sweep from real entry
points), not its code - that operates on a different AST via a PEG parser. Per
TODO.md's own research into w3x2lni's optimizer: no constant folding, no
inlining, no control-flow restructuring were found there either - it's
reachability analysis plus a rename table (see obfuscator.py), and this module
mirrors exactly that scope, not a general-purpose optimizer.
"""

from . import ir

# Real WC3 map scripts always define these two; w3x2lni's own reachability walk
# starts from the same two names (see simplify.lua's mark_function calls).
DEFAULT_ENTRY_POINTS = ('main', 'config')

# Natives that take a function name as a plain string literal and call it
# dynamically at runtime - w3x2lni's mark_execute recognizes exactly this
# pattern. A function only ever reached this way must stay reachable *and*
# keep its exact name (the string that names it can't be renamed in lockstep).
DYNAMIC_DISPATCH_NATIVES = ('ExecuteFunc',)

# TriggerRegisterVariableEvent(trigger, "VarName", opcode, value) - the second
# argument names a global by string for the engine to watch; renaming that
# global without updating this string would silently break the event. Matches
# w3x2lni's own check_confuse/fix_globals reasoning.
VARIABLE_EVENT_NATIVE = 'TriggerRegisterVariableEvent'


def find_reachable(program, entry_points=DEFAULT_ENTRY_POINTS,
                    dynamic_dispatch_natives=DYNAMIC_DISPATCH_NATIVES):
     """Walk `program` from `entry_points`, returning a dict of four sets:
     `reachable_functions`, `reachable_globals` (by name), plus
     `protected_functions`/`protected_globals` - names referenced only via a
     string literal (ExecuteFunc / TriggerRegisterVariableEvent), which must
     stay reachable *and* must never be renamed, since the string referencing
     them isn't a trackable AST identifier."""
     functions_by_name = {f.name: f for f in program.functions}
     reachable_functions = set()
     reachable_globals = set()
     protected_functions = set()
     protected_globals = set()

     def visit_function(name):
          if name in reachable_functions or name not in functions_by_name:
               return
          reachable_functions.add(name)
          walk_statements(functions_by_name[name].body)

     def visit_call(name, args):
          visit_function(name)
          if name in dynamic_dispatch_natives:
               for arg in args:
                    if isinstance(arg, ir.Literal) and arg.kind == 'string':
                         protected_functions.add(arg.value)
                         visit_function(arg.value)
          if name == VARIABLE_EVENT_NATIVE and len(args) >= 2:
               name_arg = args[1]
               if isinstance(name_arg, ir.Literal) and name_arg.kind == 'string':
                    protected_globals.add(name_arg.value)
                    reachable_globals.add(name_arg.value)
          for arg in args:
               walk_expr(arg)

     def walk_statements(statements):
          for stmt in statements:
               walk_statement(stmt)

     def walk_statement(stmt):
          if isinstance(stmt, ir.Local):
               if stmt.initial is not None:
                    walk_expr(stmt.initial)
          elif isinstance(stmt, ir.SetStmt):
               reachable_globals.add(stmt.target)
               if stmt.index is not None:
                    walk_expr(stmt.index)
               walk_expr(stmt.value)
          elif isinstance(stmt, ir.CallStmt):
               visit_call(stmt.name, stmt.args)
          elif isinstance(stmt, ir.IfStmt):
               for condition, body in stmt.branches:
                    walk_expr(condition)
                    walk_statements(body)
               if stmt.else_body is not None:
                    walk_statements(stmt.else_body)
          elif isinstance(stmt, ir.LoopStmt):
               walk_statements(stmt.body)
          elif isinstance(stmt, ir.ExitWhenStmt):
               walk_expr(stmt.condition)
          elif isinstance(stmt, ir.ReturnStmt):
               if stmt.value is not None:
                    walk_expr(stmt.value)

     def walk_expr(expr):
          if isinstance(expr, ir.VarExpr):
               reachable_globals.add(expr.name)
          elif isinstance(expr, ir.ArrayRefExpr):
               reachable_globals.add(expr.name)
               walk_expr(expr.index)
          elif isinstance(expr, ir.CallExpr):
               visit_call(expr.name, expr.args)
          elif isinstance(expr, ir.FunctionRefExpr):
               # A bare `function Foo` code-value reference is a normal,
               # trackable AST reference (the printer emits it by name, the
               # parser reads it back the same way) - safe to rename in
               # lockstep, unlike a string literal. Just needs marking reachable.
               visit_function(expr.name)
          elif isinstance(expr, ir.UnaryExpr):
               walk_expr(expr.operand)
          elif isinstance(expr, ir.BinaryExpr):
               walk_expr(expr.left)
               walk_expr(expr.right)

     for entry_name in entry_points:
          visit_function(entry_name)

     return {
          'reachable_functions': reachable_functions,
          'reachable_globals': reachable_globals,
          'protected_functions': protected_functions,
          'protected_globals': protected_globals,
     }


def remove_dead_code(program, reachable):
     """Drop every function/global not in `reachable` (from `find_reachable`).
     Mutates and returns `program`."""
     program.functions = [f for f in program.functions if f.name in reachable['reachable_functions']]
     program.globals = [g for g in program.globals if g.name in reachable['reachable_globals']]
     return program
