"""
Semantic validation for a parsed JASS ir.Program: unknown call targets, wrong
argument counts, and (best-effort) argument type mismatches, checked against
the real native/BJ-wrapper-function declarations bundled in gamedata/
(common.j/blizzard.j) plus the program's own globals/functions/natives.

Mirrors wc3files.w3o.object_validator's philosophy: this is a triage signal
("worth a second look"), not a certainty oracle - only report a finding when
genuinely confident, and never flag anything this checker can't resolve with
enough information (an unknown identifier/type is silently skipped rather than
assumed wrong), since a real map's own natives/BJ functions might come from a
custom common.j/blizzard.j this package doesn't have loaded.
"""

import os

from . import ir
from .jass_parser import parse
from .jass_printer import print_expr

_GAMEDATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_DEFAULT_FILES = ('common.j', 'blizzard.j')


class NativeSignatures:
     """Native + BJ-wrapper-function signatures loaded from real Blizzard
     common.j/blizzard.j, plus the type hierarchy (`type X extends Y`)
     common.j itself declares."""

     _default_instance = None

     def __init__(self):
          self.clear()

     def clear(self):
          self._natives = {}
          self._functions = {}
          self._type_parent = {}

     def read(self, *paths):
          self.clear()
          for path in paths:
               with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                    program = parse(fh.read())
               for type_def in program.type_defs:
                    self._type_parent[type_def.name] = type_def.base
               for native in program.natives:
                    self._natives[native.name] = native
               for function in program.functions:
                    self._functions[function.name] = function
          return self

     @classmethod
     def default(cls):
          if cls._default_instance is None:
               instance = cls()
               instance.read(*(os.path.join(_GAMEDATA_DIR, name) for name in _DEFAULT_FILES))
               cls._default_instance = instance
          return cls._default_instance

     def lookup(self, name):
          """Return the declared ir.NativeDecl/ir.FunctionDecl for a native or
          BJ-wrapper function name, or None if unrecognized."""
          return self._natives.get(name) or self._functions.get(name)

     def is_ancestor(self, ancestor, type_name):
          """Whether `type_name` is `ancestor` or (transitively) extends it,
          per common.j's `type X extends Y` declarations."""
          if ancestor == type_name:
               return True
          seen = set()
          current = type_name
          while current in self._type_parent and current not in seen:
               seen.add(current)
               current = self._type_parent[current]
               if current == ancestor:
                    return True
          return False


class _Scope:
     """Name -> declared type, for the locals/params in scope while walking
     one function body, falling back to the program's own globals."""

     def __init__(self, globals_by_name):
          self.globals_by_name = globals_by_name
          self.locals_by_name = {}

     def type_of_name(self, name):
          if name in self.locals_by_name:
               return self.locals_by_name[name]
          if name in self.globals_by_name:
               return self.globals_by_name[name]
          return None


def check_program(program, natives=None):
     """Validate a parsed ir.Program. Returns a list of finding dicts:
     {'severity': 'note'|'issue', 'message': str}. Never raises on its own -
     see module docstring for why this stays a triage signal."""
     natives = natives or NativeSignatures.default()
     findings = []

     globals_by_name = {g.name: g.type for g in program.globals}
     locally_declared = {}
     for native in program.natives:
          locally_declared[native.name] = native
     for function in program.functions:
          locally_declared[function.name] = function

     def resolve_callable(name):
          return locally_declared.get(name) or natives.lookup(name)

     def infer_type(expr, scope):
          if isinstance(expr, ir.Literal):
               if expr.kind in ('int', 'rawcode'):
                    return 'integer'
               if expr.kind == 'real':
                    return 'real'
               if expr.kind == 'string':
                    return 'string'
               if expr.kind == 'bool':
                    return 'boolean'
               return None  # 'null' - compatible with any handle type, skip
          if isinstance(expr, ir.VarExpr):
               return scope.type_of_name(expr.name)
          if isinstance(expr, ir.ArrayRefExpr):
               return scope.type_of_name(expr.name)
          if isinstance(expr, ir.FunctionRefExpr):
               return 'code'
          if isinstance(expr, ir.CallExpr):
               decl = resolve_callable(expr.name)
               if decl is None or decl.returns == 'nothing':
                    return None
               return decl.returns
          if isinstance(expr, ir.UnaryExpr):
               if expr.op == 'not':
                    return 'boolean'
               return infer_type(expr.operand, scope)
          if isinstance(expr, ir.BinaryExpr):
               if expr.op in ('and', 'or', '==', '!=', '<', '>', '<=', '>='):
                    return 'boolean'
               left = infer_type(expr.left, scope)
               right = infer_type(expr.right, scope)
               if expr.op == '+' and (left == 'string' or right == 'string'):
                    return 'string'
               if left == 'real' or right == 'real':
                    return 'real'
               if left == 'integer' and right == 'integer':
                    return 'integer'
               return None
          return None

     def check_call(name, args, scope):
          decl = resolve_callable(name)
          if decl is None:
               findings.append({'severity': 'issue',
                                 'message': f"call to unrecognized function '{name}'"})
               return
          if len(decl.params) != len(args):
               findings.append({'severity': 'issue',
                                 'message': f"'{name}' takes {len(decl.params)} argument(s), "
                                            f"got {len(args)}"})
               return
          for position, (param, arg) in enumerate(zip(decl.params, args), start=1):
               actual = infer_type(arg, scope)
               if actual is None or actual == param.type:
                    continue
               if param.type == 'real' and actual == 'integer':
                    continue  # JASS implicitly widens integer -> real (confirmed against
                              # real map code - not a documented rule, an observed one)
               arg_text = print_expr(arg)
               if param.type == 'integer' and actual == 'real':
                    findings.append({'severity': 'note',
                                      'message': f"'{name}' argument {position} ({arg_text}) "
                                                 f"expects integer, got real (JASS does not "
                                                 f"narrow real -> integer - needs R2I())"})
               elif not (natives.is_ancestor(param.type, actual)):
                    findings.append({'severity': 'issue',
                                      'message': f"'{name}' argument {position} ({arg_text}) "
                                                 f"expects {param.type}, got incompatible "
                                                 f"type {actual}"})

     def walk_expr(expr, scope):
          if isinstance(expr, ir.CallExpr):
               check_call(expr.name, expr.args, scope)
               for arg in expr.args:
                    walk_expr(arg, scope)
          elif isinstance(expr, ir.ArrayRefExpr):
               walk_expr(expr.index, scope)
          elif isinstance(expr, ir.UnaryExpr):
               walk_expr(expr.operand, scope)
          elif isinstance(expr, ir.BinaryExpr):
               walk_expr(expr.left, scope)
               walk_expr(expr.right, scope)

     def walk_statements(statements, scope):
          for stmt in statements:
               walk_statement(stmt, scope)

     def walk_statement(stmt, scope):
          if isinstance(stmt, ir.Local):
               scope.locals_by_name[stmt.name] = stmt.type
               if stmt.initial is not None:
                    walk_expr(stmt.initial, scope)
          elif isinstance(stmt, ir.SetStmt):
               if stmt.index is not None:
                    walk_expr(stmt.index, scope)
               walk_expr(stmt.value, scope)
          elif isinstance(stmt, ir.CallStmt):
               check_call(stmt.name, stmt.args, scope)
               for arg in stmt.args:
                    walk_expr(arg, scope)
          elif isinstance(stmt, ir.IfStmt):
               for condition, body in stmt.branches:
                    walk_expr(condition, scope)
                    walk_statements(body, scope)
               if stmt.else_body is not None:
                    walk_statements(stmt.else_body, scope)
          elif isinstance(stmt, ir.LoopStmt):
               walk_statements(stmt.body, scope)
          elif isinstance(stmt, ir.ExitWhenStmt):
               walk_expr(stmt.condition, scope)
          elif isinstance(stmt, ir.ReturnStmt):
               if stmt.value is not None:
                    walk_expr(stmt.value, scope)

     for function in program.functions:
          scope = _Scope(globals_by_name)
          for param in function.params:
               scope.locals_by_name[param.name] = param.type
          walk_statements(function.body, scope)

     return findings
