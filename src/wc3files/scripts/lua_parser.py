"""
Lua recursive-descent parser targeting the shared `ir.py` (reusing
Literal/VarExpr/UnaryExpr/BinaryExpr/IfStmt where the shape genuinely matches
JASS's, new Lua-only node types everywhere else - see ir.py's "Lua-only nodes"
section). Grammar and operator precedence follow Lua 5.3's reference manual
(https://www.lua.org/manual/5.3/manual.html#3.4.8) rather than 5.1's - see
lua_lexer.py's module docstring for why (real fixtures use 5.3-only floor
division and bitwise operators). No reference implementation to port from -
`w3x2lni` never added Lua support either, see TODO.md.
"""

from .lua_lexer import tokenize
from . import ir


class LuaParseError(Exception):
     def __init__(self, message, token):
          super().__init__(f'{message}, got {token.kind} {token.value!r} '
                            f'at line {token.line}, column {token.col}')
          self.line = token.line
          self.col = token.col


# Binary operator precedence, low to high, each tier left-associative except
# '..' and '^' (handled separately below, both right-associative per the Lua
# manual - '^' also binds tighter than unary operators, e.g. `-2^2` is
# `-(2^2)`).
_COMPARE_OPS = {'<', '>', '<=', '>=', '~=', '=='}
_SHIFT_OPS = {'<<', '>>'}
_ADD_OPS = {'+', '-'}
_MUL_OPS = {'*', '/', '//', '%'}
_UNARY_OPS = {'not', '-', '#', '~'}

_BLOCK_END_KEYWORDS = {'end', 'else', 'elseif', 'until'}


class _TokenStream:
     def __init__(self, tokens):
          self.tokens = tokens
          self.pos = 0

     def peek(self):
          return self.tokens[self.pos]

     def advance(self):
          token = self.tokens[self.pos]
          if token.kind != 'EOF':
               self.pos += 1
          return token

     def check_keyword(self, value):
          token = self.peek()
          return token.kind == 'KEYWORD' and token.value == value

     def check_op(self, value):
          token = self.peek()
          return token.kind == 'OP' and token.value == value

     def expect_keyword(self, value):
          token = self.peek()
          if not (token.kind == 'KEYWORD' and token.value == value):
               raise LuaParseError(f"expected '{value}'", token)
          return self.advance()

     def expect_op(self, value):
          token = self.peek()
          if not (token.kind == 'OP' and token.value == value):
               raise LuaParseError(f"expected '{value}'", token)
          return self.advance()

     def expect_name(self):
          token = self.peek()
          if token.kind != 'NAME':
               raise LuaParseError('expected a name', token)
          return self.advance().value


def parse(source):
     """Parse Lua source text into an ir.Chunk. Raises LuaParseError (with
     line/col) rather than silently mis-parsing, matching jass_parser's
     established convention."""
     stream = _TokenStream(tokenize(source))
     body = _parse_block(stream, until={'end', 'else', 'elseif', 'until'})
     if stream.peek().kind != 'EOF':
          raise LuaParseError('expected end of file', stream.peek())
     return ir.Chunk(body)


def _at_block_end(stream, until):
     token = stream.peek()
     if token.kind == 'EOF':
          return True
     return token.kind == 'KEYWORD' and token.value in until


def _parse_block(stream, until):
     statements = []
     while not _at_block_end(stream, until):
          if stream.check_op(';'):
               stream.advance()
               continue
          stmt = _parse_statement(stream)
          statements.append(stmt)
          if isinstance(stmt, (ir.LuaReturnStmt, ir.BreakStmt)):
               if stream.check_op(';'):
                    stream.advance()
               break
     return statements


def _parse_statement(stream):
     if stream.check_keyword('break'):
          stream.advance()
          return ir.BreakStmt()

     if stream.check_keyword('goto'):
          stream.advance()
          return ir.GotoStmt(stream.expect_name())

     # A label is `::name::`. It is a statement in its own right and may sit
     # anywhere a statement may, including as the last thing in a block.
     if stream.check_op('::'):
          stream.advance()
          name = stream.expect_name()
          stream.expect_op('::')
          return ir.LabelStmt(name)

     if stream.check_keyword('return'):
          return _parse_return(stream)

     if stream.check_keyword('do'):
          stream.advance()
          body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
          stream.expect_keyword('end')
          return ir.DoStmt(body)

     if stream.check_keyword('while'):
          return _parse_while(stream)

     if stream.check_keyword('repeat'):
          return _parse_repeat(stream)

     if stream.check_keyword('if'):
          return _parse_if(stream)

     if stream.check_keyword('for'):
          return _parse_for(stream)

     if stream.check_keyword('function'):
          return _parse_function_stmt(stream)

     if stream.check_keyword('local'):
          return _parse_local(stream)

     return _parse_expr_or_assign_stmt(stream)


def _parse_return(stream):
     stream.expect_keyword('return')
     values = []
     if not _at_block_end(stream, _BLOCK_END_KEYWORDS) and not stream.check_op(';'):
          values = _parse_expr_list(stream)
     return ir.LuaReturnStmt(values)


def _parse_while(stream):
     stream.expect_keyword('while')
     condition = _parse_expr(stream)
     stream.expect_keyword('do')
     body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
     stream.expect_keyword('end')
     return ir.WhileStmt(condition, body)


def _parse_repeat(stream):
     stream.expect_keyword('repeat')
     body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
     stream.expect_keyword('until')
     condition = _parse_expr(stream)
     return ir.RepeatStmt(body, condition)


def _parse_if(stream):
     stream.expect_keyword('if')
     condition = _parse_expr(stream)
     stream.expect_keyword('then')
     body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
     branches = [(condition, body)]

     while stream.check_keyword('elseif'):
          stream.advance()
          elseif_condition = _parse_expr(stream)
          stream.expect_keyword('then')
          elseif_body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
          branches.append((elseif_condition, elseif_body))

     else_body = None
     if stream.check_keyword('else'):
          stream.advance()
          else_body = _parse_block(stream, until={'end'})

     stream.expect_keyword('end')
     return ir.IfStmt(branches, else_body=else_body)


def _parse_for(stream):
     stream.expect_keyword('for')
     first_name = stream.expect_name()

     if stream.check_op('='):
          stream.advance()
          start = _parse_expr(stream)
          stream.expect_op(',')
          stop = _parse_expr(stream)
          step = None
          if stream.check_op(','):
               stream.advance()
               step = _parse_expr(stream)
          stream.expect_keyword('do')
          body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
          stream.expect_keyword('end')
          return ir.NumericForStmt(first_name, start, stop, step=step, body=body)

     names = [first_name]
     while stream.check_op(','):
          stream.advance()
          names.append(stream.expect_name())
     stream.expect_keyword('in')
     exprs = _parse_expr_list(stream)
     stream.expect_keyword('do')
     body = _parse_block(stream, until=_BLOCK_END_KEYWORDS)
     stream.expect_keyword('end')
     return ir.GenericForStmt(names, exprs, body=body)


def _parse_function_stmt(stream):
     stream.expect_keyword('function')
     name_path = [stream.expect_name()]
     while stream.check_op('.'):
          stream.advance()
          name_path.append(stream.expect_name())
     method_name = None
     if stream.check_op(':'):
          stream.advance()
          method_name = stream.expect_name()
     params, is_vararg, body = _parse_funcbody(stream)
     return ir.FunctionStmt(name_path, method_name=method_name, params=params,
                             body=body, is_vararg=is_vararg)


def _parse_local(stream):
     stream.expect_keyword('local')
     if stream.check_keyword('function'):
          stream.advance()
          name = stream.expect_name()
          params, is_vararg, body = _parse_funcbody(stream)
          return ir.LocalFunctionStmt(name, params, body=body, is_vararg=is_vararg)

     names = [stream.expect_name()]
     while stream.check_op(','):
          stream.advance()
          names.append(stream.expect_name())
     values = []
     if stream.check_op('='):
          stream.advance()
          values = _parse_expr_list(stream)
     return ir.LocalStmt(names, values)


def _parse_funcbody(stream):
     stream.expect_op('(')
     params = []
     is_vararg = False
     if not stream.check_op(')'):
          while True:
               if stream.check_op('...'):
                    stream.advance()
                    is_vararg = True
                    break
               params.append(stream.expect_name())
               if stream.check_op(','):
                    stream.advance()
                    continue
               break
     stream.expect_op(')')
     body = _parse_block(stream, until={'end'})
     stream.expect_keyword('end')
     return params, is_vararg, body


def _parse_expr_or_assign_stmt(stream):
     first = _parse_prefixexp(stream)

     if stream.check_op(',') or stream.check_op('='):
          targets = [first]
          while stream.check_op(','):
               stream.advance()
               targets.append(_parse_prefixexp(stream))
          for target in targets:
               if not isinstance(target, (ir.VarExpr, ir.IndexExpr)):
                    raise LuaParseError('expected an assignable expression', stream.peek())
          stream.expect_op('=')
          values = _parse_expr_list(stream)
          return ir.AssignStmt(targets, values)

     if isinstance(first, (ir.LuaCallExpr, ir.LuaMethodCallExpr)):
          return ir.ExprStmt(first)

     raise LuaParseError('expected a statement', stream.peek())


def _parse_expr_list(stream):
     exprs = [_parse_expr(stream)]
     while stream.check_op(','):
          stream.advance()
          exprs.append(_parse_expr(stream))
     return exprs


# --- expressions ----------------------------------------------------------

def _parse_expr(stream):
     return _parse_or(stream)


def _parse_or(stream):
     left = _parse_and(stream)
     while stream.check_keyword('or'):
          stream.advance()
          right = _parse_and(stream)
          left = ir.BinaryExpr('or', left, right)
     return left


def _parse_and(stream):
     left = _parse_compare(stream)
     while stream.check_keyword('and'):
          stream.advance()
          right = _parse_compare(stream)
          left = ir.BinaryExpr('and', left, right)
     return left


def _parse_compare(stream):
     left = _parse_bor(stream)
     while True:
          token = stream.peek()
          if not (token.kind == 'OP' and token.value in _COMPARE_OPS):
               break
          stream.advance()
          right = _parse_bor(stream)
          left = ir.BinaryExpr(token.value, left, right)
     return left


def _parse_bor(stream):
     left = _parse_bxor(stream)
     while stream.check_op('|'):
          stream.advance()
          right = _parse_bxor(stream)
          left = ir.BinaryExpr('|', left, right)
     return left


def _parse_bxor(stream):
     left = _parse_band(stream)
     while stream.check_op('~'):
          stream.advance()
          right = _parse_band(stream)
          left = ir.BinaryExpr('~', left, right)
     return left


def _parse_band(stream):
     left = _parse_shift(stream)
     while stream.check_op('&'):
          stream.advance()
          right = _parse_shift(stream)
          left = ir.BinaryExpr('&', left, right)
     return left


def _parse_shift(stream):
     left = _parse_concat(stream)
     while True:
          token = stream.peek()
          if not (token.kind == 'OP' and token.value in _SHIFT_OPS):
               break
          stream.advance()
          right = _parse_concat(stream)
          left = ir.BinaryExpr(token.value, left, right)
     return left


def _parse_concat(stream):
     left = _parse_add(stream)
     if stream.check_op('..'):
          stream.advance()
          right = _parse_concat(stream)
          return ir.BinaryExpr('..', left, right)
     return left


def _parse_add(stream):
     left = _parse_mul(stream)
     while True:
          token = stream.peek()
          if not (token.kind == 'OP' and token.value in _ADD_OPS):
               break
          stream.advance()
          right = _parse_mul(stream)
          left = ir.BinaryExpr(token.value, left, right)
     return left


def _parse_mul(stream):
     left = _parse_unary(stream)
     while True:
          token = stream.peek()
          if not (token.kind == 'OP' and token.value in _MUL_OPS):
               break
          stream.advance()
          right = _parse_unary(stream)
          left = ir.BinaryExpr(token.value, left, right)
     return left


def _parse_unary(stream):
     token = stream.peek()
     is_unary = ((token.kind == 'KEYWORD' and token.value == 'not')
                 or (token.kind == 'OP' and token.value in _UNARY_OPS))
     if is_unary:
          stream.advance()
          operand = _parse_unary(stream)
          return ir.UnaryExpr(token.value, operand)
     return _parse_pow(stream)


def _parse_pow(stream):
     left = _parse_atom(stream)
     if stream.check_op('^'):
          stream.advance()
          right = _parse_unary(stream)
          return ir.BinaryExpr('^', left, right)
     return left


def _parse_atom(stream):
     token = stream.peek()

     if token.kind == 'NUMBER':
          stream.advance()
          return _literal_from_number(token)

     if token.kind == 'STRING':
          stream.advance()
          return ir.Literal('string', token.value)

     if token.kind == 'KEYWORD' and token.value == 'nil':
          stream.advance()
          return ir.Literal('nil', None)

     if token.kind == 'KEYWORD' and token.value in ('true', 'false'):
          stream.advance()
          return ir.Literal('bool', token.value == 'true')

     if token.kind == 'OP' and token.value == '...':
          stream.advance()
          return ir.VarargExpr()

     if token.kind == 'KEYWORD' and token.value == 'function':
          stream.advance()
          params, is_vararg, body = _parse_funcbody(stream)
          return ir.LuaFunctionExpr(params, body=body, is_vararg=is_vararg)

     if token.kind == 'OP' and token.value == '{':
          return _parse_table(stream)

     return _parse_prefixexp(stream)


def _literal_from_number(token):
     text = token.value
     if text[:2].lower() == '0x':
          return ir.Literal('int', int(text, 16))
     if '.' in text or 'e' in text or 'E' in text:
          return ir.Literal('real', float(text))
     return ir.Literal('int', int(text))


def _parse_prefixexp(stream):
     token = stream.peek()

     if token.kind == 'OP' and token.value == '(':
          stream.advance()
          expr = _parse_expr(stream)
          stream.expect_op(')')
     elif token.kind == 'NAME':
          stream.advance()
          expr = ir.VarExpr(token.value)
     else:
          raise LuaParseError('expected an expression', token)

     while True:
          if stream.check_op('.'):
               stream.advance()
               name = stream.expect_name()
               expr = ir.IndexExpr(expr, ir.Literal('string', name))
          elif stream.check_op('['):
               stream.advance()
               index = _parse_expr(stream)
               stream.expect_op(']')
               expr = ir.IndexExpr(expr, index)
          elif stream.check_op(':'):
               stream.advance()
               method = stream.expect_name()
               args = _parse_args(stream)
               expr = ir.LuaMethodCallExpr(expr, method, args)
          elif _at_call_args(stream):
               args = _parse_args(stream)
               expr = ir.LuaCallExpr(expr, args)
          else:
               break
     return expr


def _at_call_args(stream):
     token = stream.peek()
     if token.kind == 'OP' and token.value in ('(', '{'):
          return True
     return token.kind == 'STRING'


def _parse_args(stream):
     token = stream.peek()
     if token.kind == 'STRING':
          stream.advance()
          return [ir.Literal('string', token.value)]
     if token.kind == 'OP' and token.value == '{':
          return [_parse_table(stream)]

     stream.expect_op('(')
     args = []
     if not stream.check_op(')'):
          args = _parse_expr_list(stream)
     stream.expect_op(')')
     return args


def _parse_table(stream):
     stream.expect_op('{')
     fields = []
     while not stream.check_op('}'):
          fields.append(_parse_table_field(stream))
          if stream.check_op(',') or stream.check_op(';'):
               stream.advance()
          else:
               break
     stream.expect_op('}')
     return ir.TableExpr(fields)


def _parse_table_field(stream):
     if stream.check_op('['):
          stream.advance()
          key = _parse_expr(stream)
          stream.expect_op(']')
          stream.expect_op('=')
          value = _parse_expr(stream)
          return key, value

     if stream.peek().kind == 'NAME':
          save = stream.pos
          name = stream.advance().value
          if stream.check_op('='):
               stream.advance()
               value = _parse_expr(stream)
               return ir.Literal('string', name), value
          stream.pos = save

     value = _parse_expr(stream)
     return None, value
