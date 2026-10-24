"""
Lua identifier obfuscator: renames every lexically-scoped local binding
(locals, parameters, `local function` names, for-loop variables) to a short
generated name, leaving every global reference (a name never bound `local`
anywhere in scope at its use site) completely untouched.

This is deliberately shaped differently from jass_obfuscator.py/obfuscator.py's
JASS-side rename table (a single flat dict, since JASS's globals/functions
share one program-wide namespace and its locals/params are left alone
entirely) - Lua's `local` bindings are properly nested/shadowing lexical
scopes, so the same generated short name can (and should, to stay short) be
reused across unrelated sibling scopes; there is no single flat rename table
that would even make sense here. A two-pass scope-tracking walk is used
instead: pass 1 walks the whole tree read-only, recording every identifier
that never resolves to an enclosing local/param/loop-var binding (a genuine
global reference); pass 2 walks it again, this time assigning a fresh
generated name to every local binding (skipping every Lua keyword, every
global name pass 1 found, and any caller-supplied protected name) and
rewriting every reference that *does* resolve to one.

Two conservative, deliberate limits on scope (not attempted here, unlike
JASS's phase 3):
- **Globals are never renamed at all**, not just protected by name collision
  avoidance the way JASS's ExecuteFunc/TriggerRegisterVariableEvent targets
  are. JASS has exactly two narrow, always-textually-detectable dynamic-
  dispatch mechanisms; Lua's dynamic global access (`_G[name]`, or any
  string-keyed field access built from a runtime string) has no such closed
  set to detect, so there's no way to prove a given global is safe to rename
  without risking a real map silently breaking. A `local` binding has no
  string-based escape hatch at all - every reference is a normal, trackable
  AST identifier - so renaming locals carries none of that risk.
- **No dead-code elimination is attempted for Lua at all** (unlike
  optimizer.py's JASS-side reachability pass). Removing an unreferenced
  top-level JASS function/global is safe because it's never observably
  executed until called; removing a Lua `local x = f()` even when `x` is
  never read would also discard whatever `f()`'s call does, and proving an
  arbitrary Lua call has no side effect is out of scope here.

A method function's implicit `self` parameter is not modeled as an explicit
Param in ir.FunctionStmt (see ir.py's own docstring), so it is never declared
into scope here either - a bare `self` reference inside a `function T:M()`
body is therefore (harmlessly) treated as an unresolved/global name and left
unrenamed, a known, disclosed gap rather than a silent miss - see TODO.md.
"""

from . import ir
from .lua_lexer import LUA_KEYWORDS
from .obfuscator import _generate_names


class _ScopeWalker:
     def __init__(self, collecting, forbidden_names=None, name_generator=None):
          self.collecting = collecting
          self.scopes = [{}]
          self.global_names = set()
          self.forbidden_names = forbidden_names or set()
          self.name_generator = name_generator

     def push_scope(self):
          self.scopes.append({})

     def pop_scope(self):
          self.scopes.pop()

     def declare(self, name):
          """Introduces a new local binding for `name` in the innermost
          scope. Returns the name to store in the IR node - unchanged while
          collecting, a freshly generated short name while renaming."""
          if self.collecting:
               self.scopes[-1][name] = name
               return name
          new_name = self._next_name()
          self.scopes[-1][name] = new_name
          return new_name

     def resolve(self, name):
          """Looks `name` up from the innermost scope outward. Returns the
          bound (possibly renamed) name if found; otherwise it's a genuine
          global reference - recorded (while collecting) and returned as-is
          (never renamed, in either pass)."""
          for scope in reversed(self.scopes):
               if name in scope:
                    return scope[name]
          if self.collecting:
               self.global_names.add(name)
          return name

     def _next_name(self):
          while True:
               candidate = next(self.name_generator)
               if candidate not in self.forbidden_names:
                    return candidate


def obfuscate_lua(chunk, extra_protected_names=()):
     """Renames every local/param/loop-var binding in `chunk` to a short
     generated name; every global reference is left untouched. Mutates and
     returns `chunk` - see this module's docstring for the two-pass design
     and the deliberate scope limits."""
     collector = _ScopeWalker(collecting=True)
     _walk_statements(collector, chunk.body)

     forbidden = set(LUA_KEYWORDS) | collector.global_names | set(extra_protected_names)
     renamer = _ScopeWalker(collecting=False, forbidden_names=forbidden,
                             name_generator=_generate_names())
     _walk_statements(renamer, chunk.body)
     return chunk


def _walk_statements(walker, statements):
     for stmt in statements:
          _walk_statement(walker, stmt)


def _walk_statement(walker, stmt):
     if isinstance(stmt, ir.LocalStmt):
          for value in stmt.values:
               _walk_expr(walker, value)
          stmt.names = [walker.declare(name) for name in stmt.names]
          return

     if isinstance(stmt, ir.AssignStmt):
          for value in stmt.values:
               _walk_expr(walker, value)
          for target in stmt.targets:
               _walk_expr(walker, target)
          return

     if isinstance(stmt, ir.LocalFunctionStmt):
          # Bound in the *enclosing* scope before the body is walked (Lua's
          # `local function f` is sugar for `local f; f = function ... end`),
          # so a recursive self-call inside the body resolves correctly.
          stmt.name = walker.declare(stmt.name)
          walker.push_scope()
          stmt.params = [walker.declare(p) for p in stmt.params]
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.FunctionStmt):
          if stmt.name_path:
               stmt.name_path[0] = walker.resolve(stmt.name_path[0])
          walker.push_scope()
          stmt.params = [walker.declare(p) for p in stmt.params]
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.ExprStmt):
          _walk_expr(walker, stmt.call)
          return

     if isinstance(stmt, ir.DoStmt):
          walker.push_scope()
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.WhileStmt):
          _walk_expr(walker, stmt.condition)
          walker.push_scope()
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.RepeatStmt):
          walker.push_scope()
          _walk_statements(walker, stmt.body)
          _walk_expr(walker, stmt.condition)  # sees the body's own locals - same scope, not popped yet
          walker.pop_scope()
          return

     if isinstance(stmt, ir.NumericForStmt):
          _walk_expr(walker, stmt.start)
          _walk_expr(walker, stmt.stop)
          if stmt.step is not None:
               _walk_expr(walker, stmt.step)
          walker.push_scope()
          stmt.name = walker.declare(stmt.name)
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.GenericForStmt):
          for expr in stmt.exprs:
               _walk_expr(walker, expr)
          walker.push_scope()
          stmt.names = [walker.declare(name) for name in stmt.names]
          _walk_statements(walker, stmt.body)
          walker.pop_scope()
          return

     if isinstance(stmt, ir.BreakStmt):
          return

     if isinstance(stmt, ir.LuaReturnStmt):
          for value in stmt.values:
               _walk_expr(walker, value)
          return

     if isinstance(stmt, ir.IfStmt):
          for condition, body in stmt.branches:
               _walk_expr(walker, condition)
               walker.push_scope()
               _walk_statements(walker, body)
               walker.pop_scope()
          if stmt.else_body is not None:
               walker.push_scope()
               _walk_statements(walker, stmt.else_body)
               walker.pop_scope()
          return

     raise TypeError(f'unhandled Lua statement node: {type(stmt).__name__}')


def _walk_expr(walker, expr):
     if isinstance(expr, (ir.Literal, ir.VarargExpr)):
          return

     if isinstance(expr, ir.VarExpr):
          expr.name = walker.resolve(expr.name)
          return

     if isinstance(expr, ir.IndexExpr):
          _walk_expr(walker, expr.obj)
          if not _is_field_name_literal(expr.index):
               _walk_expr(walker, expr.index)
          return

     if isinstance(expr, ir.LuaCallExpr):
          _walk_expr(walker, expr.func)
          for arg in expr.args:
               _walk_expr(walker, arg)
          return

     if isinstance(expr, ir.LuaMethodCallExpr):
          _walk_expr(walker, expr.obj)
          for arg in expr.args:
               _walk_expr(walker, arg)
          return

     if isinstance(expr, ir.LuaFunctionExpr):
          walker.push_scope()
          expr.params = [walker.declare(p) for p in expr.params]
          _walk_statements(walker, expr.body)
          walker.pop_scope()
          return

     if isinstance(expr, ir.TableExpr):
          for key, value in expr.fields:
               if key is not None and not _is_field_name_literal(key):
                    _walk_expr(walker, key)
               _walk_expr(walker, value)
          return

     if isinstance(expr, ir.UnaryExpr):
          _walk_expr(walker, expr.operand)
          return

     if isinstance(expr, ir.BinaryExpr):
          _walk_expr(walker, expr.left)
          _walk_expr(walker, expr.right)
          return

     raise TypeError(f'unhandled Lua expression node: {type(expr).__name__}')


def _is_field_name_literal(node):
     """True for the normalized `["name"]`/`.name` string-Literal index/key
     form (see ir.py's module docstring) - a field name, never a variable
     reference, so it must never be touched by scope resolution/renaming."""
     return isinstance(node, ir.Literal) and node.kind == 'string'
