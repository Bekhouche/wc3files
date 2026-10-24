import decimal

from . import ir

_INDENT = '    '


def print_program(program):
     """Serialize an ir.Program back to JASS source text. Not required to
     reproduce the original formatting/comments (the same lossy-but-correct
     tradeoff already accepted for WtsFile's discarded comments) - the
     correctness bar is structural: parse -> print -> re-parse must produce
     an equivalent AST, not byte-identical text."""
     lines = []

     for type_def in program.type_defs:
          lines.append(f'type {type_def.name} extends {type_def.base}')

     if program.globals:
          lines.append('globals')
          for var in program.globals:
               lines.append(_INDENT + _print_global_var(var))
          lines.append('endglobals')

     for native in program.natives:
          lines.append(_print_native(native))

     for function in program.functions:
          lines.extend(_print_function(function))

     return '\n'.join(lines) + '\n'


def _print_global_var(var):
     prefix = 'constant ' if var.is_constant else ''
     array = 'array ' if var.is_array else ''
     text = f'{prefix}{var.type} {array}{var.name}'
     if var.initial is not None:
          text += f' = {_print_expr(var.initial)}'
     return text


def _print_native(native):
     prefix = 'constant ' if native.is_constant else ''
     return f'{prefix}native {native.name} takes {_print_params(native.params)} returns {native.returns}'


def _print_function(function):
     prefix = 'constant ' if function.is_constant else ''
     lines = [f'{prefix}function {function.name} takes {_print_params(function.params)} '
              f'returns {function.returns}']
     lines.extend(_print_statements(function.body, depth=1))
     lines.append('endfunction')
     return lines


def _print_params(params):
     if not params:
          return 'nothing'
     return ', '.join(f'{p.type} {p.name}' for p in params)


def _print_statements(statements, depth):
     lines = []
     for stmt in statements:
          lines.extend(_print_statement(stmt, depth))
     return lines


def _print_statement(stmt, depth):
     indent = _INDENT * depth
     debug = 'debug ' if getattr(stmt, 'is_debug', False) else ''

     if isinstance(stmt, ir.Local):
          array = 'array ' if stmt.is_array else ''
          text = f'{indent}local {stmt.type} {array}{stmt.name}'
          if stmt.initial is not None:
               text += f' = {_print_expr(stmt.initial)}'
          return [text]

     if isinstance(stmt, ir.SetStmt):
          target = stmt.target
          if stmt.index is not None:
               target += f'[{_print_expr(stmt.index)}]'
          return [f'{indent}{debug}set {target} = {_print_expr(stmt.value)}']

     if isinstance(stmt, ir.CallStmt):
          args = ', '.join(_print_expr(a) for a in stmt.args)
          return [f'{indent}{debug}call {stmt.name}({args})']

     if isinstance(stmt, ir.IfStmt):
          lines = []
          for i, (condition, body) in enumerate(stmt.branches):
               keyword = 'if' if i == 0 else 'elseif'
               lines.append(f'{indent}{debug}{keyword} {_print_expr(condition)} then')
               lines.extend(_print_statements(body, depth + 1))
          if stmt.else_body is not None:
               lines.append(f'{indent}{debug}else')
               lines.extend(_print_statements(stmt.else_body, depth + 1))
          lines.append(f'{indent}{debug}endif')
          return lines

     if isinstance(stmt, ir.LoopStmt):
          lines = [f'{indent}{debug}loop']
          lines.extend(_print_statements(stmt.body, depth + 1))
          lines.append(f'{indent}{debug}endloop')
          return lines

     if isinstance(stmt, ir.ExitWhenStmt):
          return [f'{indent}{debug}exitwhen {_print_expr(stmt.condition)}']

     if isinstance(stmt, ir.ReturnStmt):
          if stmt.value is not None:
               return [f'{indent}{debug}return {_print_expr(stmt.value)}']
          return [f'{indent}{debug}return']

     raise TypeError(f'unhandled statement node: {type(stmt).__name__}')


_BINARY_OP_TOKEN = {
     '+': '+', '-': '-', '*': '*', '/': '/',
     '==': '==', '!=': '!=', '<': '<', '>': '>', '<=': '<=', '>=': '>=',
     'and': 'and', 'or': 'or',
}


def print_expr(expr):
     """Public single-expression printer - handy for error/finding messages
     that want to show the offending expression, not just its node type."""
     return _print_expr(expr)


def _print_expr(expr):
     if isinstance(expr, ir.Literal):
          return _print_literal(expr)
     if isinstance(expr, ir.VarExpr):
          return expr.name
     if isinstance(expr, ir.ArrayRefExpr):
          return f'{expr.name}[{_print_expr(expr.index)}]'
     if isinstance(expr, ir.CallExpr):
          args = ', '.join(_print_expr(a) for a in expr.args)
          return f'{expr.name}({args})'
     if isinstance(expr, ir.FunctionRefExpr):
          return f'function {expr.name}'
     if isinstance(expr, ir.UnaryExpr):
          op = 'not ' if expr.op == 'not' else '-'
          return f'{op}{_print_expr(expr.operand)}'
     if isinstance(expr, ir.BinaryExpr):
          op = _BINARY_OP_TOKEN[expr.op]
          return f'({_print_expr(expr.left)} {op} {_print_expr(expr.right)})'
     raise TypeError(f'unhandled expression node: {type(expr).__name__}')


def _format_real(value):
     """JASS real literals are plain decimal (digits '.' digits) - no exponent
     notation, unlike Python's repr() for very large/small floats, so a
     scientific-notation repr() has to be rewritten into plain decimal.
     Rewriting via `format(value, '.17f')` (an earlier version here) is a
     real, precision-losing bug for extreme magnitudes: `.17f` means 17
     digits *after the decimal point*, not 17 *significant* digits, so a
     value like 4.656612875245797e-10 silently truncates to ~7 significant
     digits (round-tripping to a different float) - found via the Lua
     frontend's real-fixture sweep hitting the identical latent bug in its
     own (since-fixed) copy of this function, see TODO.md. Converting via
     Decimal instead re-renders repr()'s exact decimal digits in plain
     notation with no precision lost, since Decimal parses that decimal
     string exactly rather than re-deriving it from the binary float."""
     text = repr(float(value))
     if 'e' in text or 'E' in text:
          text = format(decimal.Decimal(text), 'f')
          if '.' not in text:
               text += '.0'
     return text


def _print_literal(literal):
     if literal.kind == 'int':
          return str(literal.value)
     if literal.kind == 'real':
          return _format_real(literal.value)
     if literal.kind == 'string':
          return f'"{literal.value}"'
     if literal.kind == 'rawcode':
          return f"'{literal.value}'"
     if literal.kind == 'bool':
          return 'true' if literal.value else 'false'
     if literal.kind == 'null':
          return 'null'
     raise TypeError(f'unhandled literal kind: {literal.kind}')
