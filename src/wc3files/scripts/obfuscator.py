"""
JASS obfuscator: shortest-available-name identifier renaming for functions and
global variables.

Ports the *design* of w3x2lni's `confuser.lua` (a counter over a charset,
skipping names that collide with a keyword/native/already-used name) - not its
code. Scoped to functions and globals only, not locals/params: those are
function-local in JASS (never referenced by name outside their own function),
so renaming them is lower-risk in principle, but this first pass keeps the
renaming surface to what w3x2lni's own confuser actually targets and what's
been verified end-to-end (parse -> optimize -> obfuscate -> reparse -> recheck).
See TODO.md.
"""

import itertools
from .jass_lexer import KEYWORDS
from . import ir

_CHARSET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'


def _generate_names():
     """'a'..'z','A'..'Z','aa','ab',... - matches confuser.lua's base-N counter
     scheme (shortest available name first)."""
     length = 1
     while True:
          for combo in itertools.product(_CHARSET, repeat=length):
               yield ''.join(combo)
          length += 1


def _collect_local_and_param_names(program):
     """Every locally-scoped name in the whole program (locals + parameters) -
     never renamed, but a newly-generated global/function name must still avoid
     colliding with one, or a local could start silently shadowing a renamed
     global inside whatever function declares it."""
     names = set()

     def visit_statements(statements):
          for stmt in statements:
               visit_statement(stmt)

     def visit_statement(stmt):
          if isinstance(stmt, ir.Local):
               names.add(stmt.name)
          elif isinstance(stmt, ir.IfStmt):
               for _, body in stmt.branches:
                    visit_statements(body)
               if stmt.else_body is not None:
                    visit_statements(stmt.else_body)
          elif isinstance(stmt, ir.LoopStmt):
               visit_statements(stmt.body)

     for function in program.functions:
          for param in function.params:
               names.add(param.name)
          visit_statements(function.body)

     return names


def build_rename_table(program, reachable, natives=None, extra_protected_names=()):
     """Assign a short new name to every reachable, non-protected function and
     global in `program`. `natives`, if given, is an iterable of native/BJ
     function names that must never be used as a generated name (they're valid
     call targets that aren't part of `program` itself)."""
     protected = (set(KEYWORDS) | set(natives or ())
                  | set(reachable.get('protected_functions', ()))
                  | set(reachable.get('protected_globals', ()))
                  | set(extra_protected_names))

     renameable = []
     for function in program.functions:
          if function.name in reachable['reachable_functions'] and function.name not in protected:
               renameable.append(function.name)
     for global_var in program.globals:
          if global_var.name in reachable['reachable_globals'] and global_var.name not in protected:
               renameable.append(global_var.name)

     used_names = set(protected) | _collect_local_and_param_names(program)
     for function in program.functions:
          if function.name not in renameable:
               used_names.add(function.name)
     for global_var in program.globals:
          if global_var.name not in renameable:
               used_names.add(global_var.name)

     rename_table = {}
     name_generator = _generate_names()
     for old_name in renameable:
          candidate = next(name_generator)
          while candidate in used_names:
               candidate = next(name_generator)
          rename_table[old_name] = candidate
          used_names.add(candidate)

     return rename_table


def apply_rename(program, rename_table):
     """Rewrite every reference to a renamed function/global throughout
     `program` (declarations, calls, variable reads/writes, `function Foo`
     code-value references). Mutates and returns `program`."""

     # Names shadowed by a local or parameter in the function currently being
     # walked. A local may share a name with a function - real maps do this -
     # and in JASS the local wins for *variable* references while a call still
     # reaches the function. Renaming such references anyway rewrote
     # `set gFF = 0` to `set cq = 0` while the `local integer gFF` declaration
     # correctly stayed put, producing a script that references an undeclared
     # variable. Found by validating output with the game's own pjass.
     shadowed = set()

     def rename(name):
          return rename_table.get(name, name)

     def rename_var(name):
          if name in shadowed:
               return name
          return rename_table.get(name, name)

     def walk_statements(statements):
          for stmt in statements:
               walk_statement(stmt)

     def walk_statement(stmt):
          if isinstance(stmt, ir.Local):
               if stmt.initial is not None:
                    walk_expr(stmt.initial)
          elif isinstance(stmt, ir.SetStmt):
               stmt.target = rename_var(stmt.target)
               if stmt.index is not None:
                    walk_expr(stmt.index)
               walk_expr(stmt.value)
          elif isinstance(stmt, ir.CallStmt):
               stmt.name = rename(stmt.name)
               for arg in stmt.args:
                    walk_expr(arg)
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
               expr.name = rename_var(expr.name)
          elif isinstance(expr, ir.ArrayRefExpr):
               expr.name = rename_var(expr.name)
               walk_expr(expr.index)
          elif isinstance(expr, ir.CallExpr):
               expr.name = rename(expr.name)
               for arg in expr.args:
                    walk_expr(arg)
          elif isinstance(expr, ir.FunctionRefExpr):
               expr.name = rename(expr.name)
          elif isinstance(expr, ir.UnaryExpr):
               walk_expr(expr.operand)
          elif isinstance(expr, ir.BinaryExpr):
               walk_expr(expr.left)
               walk_expr(expr.right)

     for function in program.functions:
          function.name = rename(function.name)
          # JASS requires locals at the top of a function, so collecting them
          # up front covers the whole body.
          shadowed = {getattr(parameter, 'name', parameter)
                      for parameter in (function.params or ())}
          shadowed |= {statement.name for statement in function.body
                       if isinstance(statement, ir.Local)}
          walk_statements(function.body)
     shadowed = set()
     for global_var in program.globals:
          global_var.name = rename(global_var.name)
          if global_var.initial is not None:
               walk_expr(global_var.initial)

     return program
