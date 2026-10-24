from .jass_lexer import tokenize
from . import ir


class JassParseError(Exception):
     def __init__(self, message, token):
          super().__init__(f'{message}, got {token.kind} {token.value!r} '
                            f'at line {token.line}, column {token.col}')
          self.line = token.line
          self.col = token.col


_LITERAL_KINDS = {'INT', 'HEXINT', 'REAL', 'STRING', 'RAWCODE'}

# Binary operator precedence, low to high. Each tier is left-associative.
_PRECEDENCE = [
     {'or'},
     {'and'},
     {'==', '!='},
     {'<', '>', '<=', '>='},
     {'+', '-'},
     {'*', '/'},
]


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

     def check_op(self, kind):
          return self.peek().kind == kind

     def expect_keyword(self, value):
          token = self.peek()
          if not (token.kind == 'KEYWORD' and token.value == value):
               raise JassParseError(f"expected '{value}'", token)
          return self.advance()

     def expect_op(self, kind, display):
          token = self.peek()
          if token.kind != kind:
               raise JassParseError(f"expected '{display}'", token)
          return self.advance()

     def expect_ident(self):
          token = self.peek()
          if token.kind != 'IDENT':
               raise JassParseError('expected an identifier', token)
          return self.advance().value


def parse(source):
     """Parse JASS source text into an ir.Program. Raises JassParseError (with
     line/col) rather than silently mis-parsing anything it doesn't recognize,
     per this package's established convention (see W3iFile's version guard)."""
     tokens = tokenize(source, keep_comments=True)
     # Comments carry no structural meaning, so they are lifted off here and
     # the parser proper never sees them. They are kept on the Program
     # because a GUI Comment action leaves nothing else behind - see
     # wtg/from_script.py.
     comments = [(token.line, token.value) for token in tokens
                 if token.kind == 'COMMENT']
     stream = _TokenStream([t for t in tokens if t.kind != 'COMMENT'])
     program = ir.Program()
     program.comments = comments

     while stream.peek().kind != 'EOF':
          if stream.check_keyword('type'):
               program.type_defs.append(_parse_type_def(stream))
          elif stream.check_keyword('globals'):
               program.globals.extend(_parse_globals_block(stream))
          elif stream.check_keyword('constant') or stream.check_keyword('native'):
               # `constant` introduces either a native or a function. The
               # function form is what JassHelper emits for a vJass constant
               # method, so it turns up in plenty of compiled map scripts;
               # assuming `native` here rejected them outright.
               is_constant = False
               if stream.check_keyword('constant'):
                    stream.advance()
                    is_constant = True
               if stream.check_keyword('function'):
                    program.functions.append(
                         _parse_function(stream, is_constant=is_constant))
               else:
                    program.natives.append(
                         _parse_native(stream, is_constant=is_constant))
          elif stream.check_keyword('function'):
               program.functions.append(_parse_function(stream))
          else:
               raise JassParseError(
                    'expected a type/globals/native/function declaration', stream.peek())

     return program


def _parse_type_def(stream):
     stream.expect_keyword('type')
     name = stream.expect_ident()
     stream.expect_keyword('extends')
     base = stream.expect_ident()
     return ir.TypeDef(name, base)


def _parse_globals_block(stream):
     stream.expect_keyword('globals')
     variables = []
     while not stream.check_keyword('endglobals'):
          variables.append(_parse_global_var(stream))
     stream.expect_keyword('endglobals')
     return variables


def _parse_global_var(stream):
     is_constant = False
     if stream.check_keyword('constant'):
          stream.advance()
          is_constant = True
     type_name = stream.expect_ident()
     is_array = False
     if stream.check_keyword('array'):
          stream.advance()
          is_array = True
     name = stream.expect_ident()
     initial = None
     if stream.check_op('ASSIGN'):
          stream.advance()
          initial = _parse_expr(stream)
     return ir.GlobalVar(type_name, name, is_array=is_array, is_constant=is_constant,
                          initial=initial)


def _parse_native(stream, is_constant=False):
     # `constant` is consumed by the caller, which has to look past it to tell
     # a native from a function.
     if stream.check_keyword('constant'):
          stream.advance()
          is_constant = True
     stream.expect_keyword('native')
     name = stream.expect_ident()
     stream.expect_keyword('takes')
     params = _parse_params(stream)
     stream.expect_keyword('returns')
     returns = _parse_return_type(stream)
     return ir.NativeDecl(name, params, returns, is_constant=is_constant)


def _parse_function(stream, is_constant=False):
     line = stream.peek().line
     stream.expect_keyword('function')
     name = stream.expect_ident()
     stream.expect_keyword('takes')
     params = _parse_params(stream)
     stream.expect_keyword('returns')
     returns = _parse_return_type(stream)
     body = _parse_statements(stream, until={'endfunction'})
     end_line = stream.peek().line
     stream.expect_keyword('endfunction')
     declaration = ir.FunctionDecl(name, params, returns, body=body,
                                   is_constant=is_constant)
     # Kept off _fields, as for statements: the span is needed to decide
     # which comments fall inside this function, including any after its
     # last statement.
     declaration.line = line
     declaration.end_line = end_line
     return declaration


def _parse_params(stream):
     if stream.check_keyword('nothing'):
          stream.advance()
          return []
     params = [_parse_param(stream)]
     while stream.check_op('COMMA'):
          stream.advance()
          params.append(_parse_param(stream))
     return params


def _parse_param(stream):
     type_name = stream.expect_ident()
     name = stream.expect_ident()
     return ir.Param(type_name, name)


def _parse_return_type(stream):
     if stream.check_keyword('nothing'):
          stream.advance()
          return 'nothing'
     return stream.expect_ident()


def _parse_statements(stream, until):
     statements = []
     while not any(stream.check_keyword(kw) for kw in until):
          statements.append(_parse_statement(stream))
     return statements


def _parse_statement(stream):
     # Where the statement began, so a comment can be matched to the
     # statement it precedes. Kept off ir.Node._fields, so structural
     # equality - and the parse -> print -> re-parse round-trip check that
     # relies on it - is unaffected.
     line = stream.peek().line
     statement = _parse_statement_inner(stream)
     if statement is not None:
          statement.line = line
     return statement


def _parse_statement_inner(stream):
     is_debug = False
     if stream.check_keyword('debug'):
          stream.advance()
          is_debug = True

     if stream.check_keyword('local'):
          if is_debug:
               raise JassParseError("'debug' cannot prefix a local declaration", stream.peek())
          return _parse_local(stream)
     if stream.check_keyword('set'):
          return _parse_set(stream, is_debug)
     if stream.check_keyword('call'):
          return _parse_call_stmt(stream, is_debug)
     if stream.check_keyword('if'):
          return _parse_if(stream, is_debug)
     if stream.check_keyword('loop'):
          return _parse_loop(stream, is_debug)
     if stream.check_keyword('exitwhen'):
          return _parse_exitwhen(stream, is_debug)
     if stream.check_keyword('return'):
          return _parse_return(stream, is_debug)

     raise JassParseError('expected a statement', stream.peek())


def _parse_local(stream):
     stream.expect_keyword('local')
     type_name = stream.expect_ident()
     is_array = False
     if stream.check_keyword('array'):
          stream.advance()
          is_array = True
     name = stream.expect_ident()
     initial = None
     if stream.check_op('ASSIGN'):
          stream.advance()
          initial = _parse_expr(stream)
     return ir.Local(type_name, name, is_array=is_array, initial=initial)


def _parse_set(stream, is_debug):
     stream.expect_keyword('set')
     target = stream.expect_ident()
     index = None
     if stream.check_op('LBRACKET'):
          stream.advance()
          index = _parse_expr(stream)
          stream.expect_op('RBRACKET', ']')
     stream.expect_op('ASSIGN', '=')
     value = _parse_expr(stream)
     return ir.SetStmt(target, value, index=index, is_debug=is_debug)


def _parse_call_stmt(stream, is_debug):
     stream.expect_keyword('call')
     name = stream.expect_ident()
     stream.expect_op('LPAREN', '(')
     args = _parse_arg_list(stream)
     stream.expect_op('RPAREN', ')')
     return ir.CallStmt(name, args=args, is_debug=is_debug)


def _parse_if(stream, is_debug):
     stream.expect_keyword('if')
     condition = _parse_expr(stream)
     stream.expect_keyword('then')
     body = _parse_statements(stream, until={'elseif', 'else', 'endif'})
     branches = [(condition, body)]

     while stream.check_keyword('elseif'):
          stream.advance()
          elseif_condition = _parse_expr(stream)
          stream.expect_keyword('then')
          elseif_body = _parse_statements(stream, until={'elseif', 'else', 'endif'})
          branches.append((elseif_condition, elseif_body))

     else_body = None
     if stream.check_keyword('else'):
          stream.advance()
          else_body = _parse_statements(stream, until={'endif'})

     stream.expect_keyword('endif')
     return ir.IfStmt(branches, else_body=else_body, is_debug=is_debug)


def _parse_loop(stream, is_debug):
     stream.expect_keyword('loop')
     body = _parse_statements(stream, until={'endloop'})
     stream.expect_keyword('endloop')
     return ir.LoopStmt(body=body, is_debug=is_debug)


def _parse_exitwhen(stream, is_debug):
     stream.expect_keyword('exitwhen')
     condition = _parse_expr(stream)
     return ir.ExitWhenStmt(condition, is_debug=is_debug)


def _parse_return(stream, is_debug):
     stream.expect_keyword('return')
     value = None
     token = stream.peek()
     is_end_of_stmt = (
          token.kind == 'KEYWORD'
          and token.value in ('endfunction', 'endif', 'endloop', 'elseif', 'else',
                               'exitwhen', 'debug', 'set', 'call', 'if', 'loop',
                               'local', 'return')
     )
     if not is_end_of_stmt:
          value = _parse_expr(stream)
     return ir.ReturnStmt(value=value, is_debug=is_debug)


def _parse_arg_list(stream):
     if stream.check_op('RPAREN'):
          return []
     args = [_parse_expr(stream)]
     while stream.check_op('COMMA'):
          stream.advance()
          args.append(_parse_expr(stream))
     return args


def _parse_expr(stream):
     return _parse_binary(stream, 0)


def _parse_binary(stream, tier):
     if tier >= len(_PRECEDENCE):
          return _parse_unary(stream)

     left = _parse_binary(stream, tier + 1)
     ops = _PRECEDENCE[tier]
     while True:
          op = _peek_binary_op(stream)
          if op not in ops:
               break
          _advance_binary_op(stream)
          right = _parse_binary(stream, tier + 1)
          left = ir.BinaryExpr(op, left, right)
     return left


def _peek_binary_op(stream):
     token = stream.peek()
     if token.kind == 'KEYWORD' and token.value in ('and', 'or'):
          return token.value
     return {
          'EQ': '==', 'NE': '!=', 'LT': '<', 'GT': '>', 'LE': '<=', 'GE': '>=',
          'PLUS': '+', 'MINUS': '-', 'STAR': '*', 'SLASH': '/',
     }.get(token.kind)


def _advance_binary_op(stream):
     stream.advance()


def _parse_unary(stream):
     if stream.check_keyword('not'):
          stream.advance()
          return ir.UnaryExpr('not', _parse_unary(stream))
     if stream.check_op('MINUS'):
          stream.advance()
          return ir.UnaryExpr('-', _parse_unary(stream))
     return _parse_primary(stream)


def _parse_primary(stream):
     token = stream.peek()

     if token.kind == 'LPAREN':
          stream.advance()
          expr = _parse_expr(stream)
          stream.expect_op('RPAREN', ')')
          return expr

     if token.kind == 'KEYWORD' and token.value == 'function':
          stream.advance()
          name = stream.expect_ident()
          return ir.FunctionRefExpr(name)

     if token.kind == 'KEYWORD' and token.value in ('true', 'false'):
          stream.advance()
          return ir.Literal('bool', token.value == 'true')

     if token.kind == 'KEYWORD' and token.value == 'null':
          stream.advance()
          return ir.Literal('null', None)

     if token.kind in _LITERAL_KINDS:
          stream.advance()
          return _literal_from_token(token)

     if token.kind == 'IDENT':
          stream.advance()
          if stream.check_op('LPAREN'):
               stream.advance()
               args = _parse_arg_list(stream)
               stream.expect_op('RPAREN', ')')
               return ir.CallExpr(token.value, args=args)
          if stream.check_op('LBRACKET'):
               stream.advance()
               index = _parse_expr(stream)
               stream.expect_op('RBRACKET', ']')
               return ir.ArrayRefExpr(token.value, index)
          return ir.VarExpr(token.value)

     raise JassParseError('expected an expression', token)


def _literal_from_token(token):
     if token.kind == 'INT':
          return ir.Literal('int', int(token.value))
     if token.kind == 'HEXINT':
          digits = token.value[1:] if token.value[0] == '$' else token.value[2:]
          return ir.Literal('int', int(digits, 16))
     if token.kind == 'REAL':
          return ir.Literal('real', float(token.value))
     if token.kind == 'STRING':
          return ir.Literal('string', token.value)
     if token.kind == 'RAWCODE':
          return ir.Literal('rawcode', token.value)
     raise JassParseError('expected a literal', token)
