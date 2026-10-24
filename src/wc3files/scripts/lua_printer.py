import re

from . import ir
from .lua_lexer import LUA_KEYWORDS

_INDENT = '    '
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# Node types that are already unambiguous as the left side of `.`/`[`/`(`/`:`
# (a "prefixexp" in Lua grammar terms) without extra parens - BinaryExpr is
# included because _print_expr always wraps it in its own parens already (see
# below), so `(a + b)(...)` is syntactically safe as printed. Everything else
# (a literal, a unary expression, a table/function literal, `...`) is not a
# valid prefixexp on its own and needs explicit wrapping parens.
_SAFE_AS_PREFIX = (ir.VarExpr, ir.IndexExpr, ir.LuaCallExpr, ir.LuaMethodCallExpr, ir.BinaryExpr)


def print_chunk(chunk):
     """Serialize an ir.Chunk back to Lua source text. Same correctness bar as
     jass_printer.print_program: structural round-trip (parse -> print ->
     re-parse), not byte-identical text or preserved comments/formatting."""
     lines = _print_block(chunk.body, depth=0)
     return '\n'.join(lines) + '\n'


def _print_block(statements, depth):
     """Every statement's last printed line gets a trailing `;` - not needed
     by Lua's grammar in general (a bare newline separates statements just
     fine), but real fixture round-trip testing found a real bug this avoids
     entirely: Lua's own grammar genuinely is ambiguous when a call-ending
     statement is immediately followed - just a newline, no separator - by a
     statement/expression starting with `(` (e.g. `(D ^ "Foo").bar = ...`,
     seen in real, metatable-heavy map code): it greedily reparses as one
     single call chain (`f(x)(...)`) instead of two statements, per the Lua
     reference manual's own documented resolution of this case. Every real
     fixture already defends against this by writing a `;` after every
     statement (confirmed directly in their raw source) - this printer didn't
     emit any statement-terminating punctuation at all, so its own output
     could recreate the hazard even from an unambiguous original. Always
     emitting `;` (harmless and explicitly legal after every kind of
     statement, including `end`) matches that same real-world defensive
     convention and sidesteps the whole ambiguity class - see TODO.md."""
     lines = []
     for stmt in statements:
          stmt_lines = _print_statement(stmt, depth)
          stmt_lines[-1] += ';'
          lines.extend(stmt_lines)
     return lines


def _print_statement(stmt, depth):
     indent = _INDENT * depth

     if isinstance(stmt, ir.LocalStmt):
          text = f'{indent}local {", ".join(stmt.names)}'
          if stmt.values:
               text += f' = {_print_expr_list(stmt.values, depth)}'
          return [text]

     if isinstance(stmt, ir.AssignStmt):
          targets = ', '.join(_print_expr(t, depth) for t in stmt.targets)
          return [f'{indent}{targets} = {_print_expr_list(stmt.values, depth)}']

     if isinstance(stmt, ir.LocalFunctionStmt):
          header = f'{indent}local function {stmt.name}({_print_params(stmt.params, stmt.is_vararg)})'
          return [header] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.FunctionStmt):
          name = '.'.join(stmt.name_path)
          if stmt.method_name is not None:
               name += f':{stmt.method_name}'
          header = f'{indent}function {name}({_print_params(stmt.params, stmt.is_vararg)})'
          return [header] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.ExprStmt):
          return [f'{indent}{_print_expr(stmt.call, depth)}']

     if isinstance(stmt, ir.DoStmt):
          return [f'{indent}do'] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.WhileStmt):
          header = f'{indent}while {_print_expr(stmt.condition, depth)} do'
          return [header] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.RepeatStmt):
          lines = [f'{indent}repeat'] + _print_block(stmt.body, depth + 1)
          lines.append(f'{indent}until {_print_expr(stmt.condition, depth)}')
          return lines

     if isinstance(stmt, ir.NumericForStmt):
          header = (f'{indent}for {stmt.name} = {_print_expr(stmt.start, depth)}, '
                    f'{_print_expr(stmt.stop, depth)}')
          if stmt.step is not None:
               header += f', {_print_expr(stmt.step, depth)}'
          header += ' do'
          return [header] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.GenericForStmt):
          header = (f'{indent}for {", ".join(stmt.names)} in '
                    f'{_print_expr_list(stmt.exprs, depth)} do')
          return [header] + _print_block(stmt.body, depth + 1) + [f'{indent}end']

     if isinstance(stmt, ir.BreakStmt):
          return [f'{indent}break']

     if isinstance(stmt, ir.GotoStmt):
          return [f'{indent}goto {stmt.label}']

     if isinstance(stmt, ir.LabelStmt):
          return [f'{indent}::{stmt.name}::']

     if isinstance(stmt, ir.LuaReturnStmt):
          if stmt.values:
               return [f'{indent}return {_print_expr_list(stmt.values, depth)}']
          return [f'{indent}return']

     if isinstance(stmt, ir.IfStmt):
          lines = []
          for i, (condition, body) in enumerate(stmt.branches):
               keyword = 'if' if i == 0 else 'elseif'
               lines.append(f'{indent}{keyword} {_print_expr(condition, depth)} then')
               lines.extend(_print_block(body, depth + 1))
          if stmt.else_body is not None:
               lines.append(f'{indent}else')
               lines.extend(_print_block(stmt.else_body, depth + 1))
          lines.append(f'{indent}end')
          return lines

     raise TypeError(f'unhandled Lua statement node: {type(stmt).__name__}')


def _print_params(params, is_vararg):
     parts = list(params)
     if is_vararg:
          parts.append('...')
     return ', '.join(parts)


def _print_expr_list(exprs, depth=0):
     return ', '.join(_print_expr(e, depth) for e in exprs)


def print_expr(expr):
     """Public single-expression printer, mirroring jass_printer.print_expr -
     handy for error/finding messages."""
     return _print_expr(expr, 0)


def _print_expr(expr, depth=0):
     """`depth` is the indentation depth of the statement this expression is
     embedded in - only meaningful to LuaFunctionExpr (the only expression
     that can itself span multiple lines); every other case ignores it.
     Without threading it through, a closure's body printed deep inside a
     statement (e.g. a `return function() ... end`) would always indent
     itself as if it were at depth 1, regardless of where it actually sits -
     a real, if purely cosmetic (never round-trip-breaking, since Lua isn't
     layout-sensitive), bug caught during manual verification of the phase-5
     obfuscator's output, not by any test - see TODO.md."""
     if isinstance(expr, ir.Literal):
          return _print_literal(expr)
     if isinstance(expr, ir.VarExpr):
          return expr.name
     if isinstance(expr, ir.VarargExpr):
          return '...'
     if isinstance(expr, ir.IndexExpr):
          obj = _print_prefix(expr.obj, depth)
          if isinstance(expr.index, ir.Literal) and expr.index.kind == 'string' \
                  and _is_plain_identifier(expr.index.value):
               return f'{obj}.{expr.index.value}'
          return f'{obj}[{_print_expr(expr.index, depth)}]'
     if isinstance(expr, ir.LuaCallExpr):
          args = _print_expr_list(expr.args, depth)
          return f'{_print_prefix(expr.func, depth)}({args})'
     if isinstance(expr, ir.LuaMethodCallExpr):
          args = _print_expr_list(expr.args, depth)
          return f'{_print_prefix(expr.obj, depth)}:{expr.method}({args})'
     if isinstance(expr, ir.LuaFunctionExpr):
          header = f'function({_print_params(expr.params, expr.is_vararg)})'
          lines = [header] + _print_block(expr.body, depth + 1) + [_INDENT * depth + 'end']
          return '\n'.join(lines)
     if isinstance(expr, ir.TableExpr):
          return _print_table(expr, depth)
     if isinstance(expr, ir.UnaryExpr):
          # Always a trailing space, even for '-'/'#' - avoids `--x` (a
          # comment starter) for a double-negation and keeps every unary form
          # unambiguous, at the cost of `- x` instead of the more idiomatic
          # `-x` (structural round-trip is the bar, not idiomatic spacing).
          return f'{expr.op} {_print_expr(expr.operand, depth)}'
     if isinstance(expr, ir.BinaryExpr):
          return f'({_print_expr(expr.left, depth)} {expr.op} {_print_expr(expr.right, depth)})'
     raise TypeError(f'unhandled Lua expression node: {type(expr).__name__}')


def _print_prefix(expr, depth=0):
     text = _print_expr(expr, depth)
     if isinstance(expr, _SAFE_AS_PREFIX):
          return text
     return f'({text})'


def _print_table(table, depth=0):
     parts = []
     for key, value in table.fields:
          if key is None:
               parts.append(_print_expr(value, depth))
          elif isinstance(key, ir.Literal) and key.kind == 'string' and _is_plain_identifier(key.value):
               parts.append(f'{key.value} = {_print_expr(value, depth)}')
          else:
               parts.append(f'[{_print_expr(key, depth)}] = {_print_expr(value, depth)}')
     return '{' + ', '.join(parts) + '}'


def _is_plain_identifier(text):
     return bool(_IDENT_RE.match(text)) and text not in LUA_KEYWORDS


def _format_infinite_real():
     """A real fixture had a `real` literal written with just enough digits to
     overflow to +inf when parsed as an IEEE-754 double (`1.79769313486232E+308`
     - one ULP over the true max double, apparently written as a manual
     `DBL_MAX`/`math.huge`-equivalent constant by whatever tool generated that
     script) - `repr(float('inf'))` is the bare word `'inf'`, not a valid Lua
     numeral, so printing it verbatim would reparse as a VarExpr instead of the
     same Literal. Any decimal literal comfortably past the max double already
     overflows to +inf the same way (a universal strtod-family behavior, not a
     Lua-specific quirk) and reparses correctly through this same package's own
     lexer, so a clearly-over-the-line exponent is used instead of trying to
     reproduce the original's exact (accidental) digit count."""
     return '1e309'


def _format_real(value):
     """Unlike JASS (see jass_printer.py's own _format_real), Lua number
     literals support exponent notation directly (confirmed by this frontend's
     own lexer/parser - see lua_lexer.py), so repr()'s shortest round-trip
     form can be used as-is with no plain-decimal rewriting - which matters
     for real fixtures: an earlier version here copied JASS's `.17f`-based
     plain-decimal fallback, which silently loses precision for very
     small/large magnitudes (`.17f` is 17 digits *after the point*, not 17
     *significant* digits) - caught by a real value from a real
     `war3map.lua` fixture failing round-trip (see TODO.md)."""
     return repr(float(value))


_STRING_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t',
                    '\a': '\\a', '\b': '\\b', '\f': '\\f', '\v': '\\v'}


def _escape_lua_string(value):
     chars = []
     for ch in value:
          if ch in _STRING_ESCAPES:
               chars.append(_STRING_ESCAPES[ch])
          elif ord(ch) < 0x20 or ord(ch) == 0x7f:
               chars.append(f'\\{ord(ch)}')
          else:
               chars.append(ch)
     return ''.join(chars)


def _print_literal(literal):
     if literal.kind == 'int':
          return str(literal.value)
     if literal.kind == 'real':
          if literal.value == float('inf'):
               return _format_infinite_real()
          return _format_real(literal.value)
     if literal.kind == 'string':
          return f'"{_escape_lua_string(literal.value)}"'
     if literal.kind == 'bool':
          return 'true' if literal.value else 'false'
     if literal.kind == 'nil':
          return 'nil'
     raise TypeError(f'unhandled Lua literal kind: {literal.kind}')
