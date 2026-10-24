import re


KEYWORDS = {
     'globals', 'endglobals', 'constant', 'native', 'type', 'extends',
     'function', 'endfunction', 'takes', 'returns', 'nothing',
     'local', 'set', 'call', 'if', 'then', 'elseif', 'else', 'endif',
     'loop', 'endloop', 'exitwhen', 'return', 'debug', 'array',
     'and', 'or', 'not', 'true', 'false', 'null',
}

# Order matters: longer/more-specific alternatives must come before shorter ones
# that would otherwise shadow them (e.g. REAL before INT, '==' before '=').
_TOKEN_SPEC = [
     ('COMMENT',    r'//[^\n]*'),
     ('NEWLINE',    r'\n'),
     ('WHITESPACE', r'[ \t\r]+'),
     ('REAL',       r'\d+\.\d*|\.\d+'),
     ('HEXINT',     r'\$[0-9A-Fa-f]+|0[xX][0-9A-Fa-f]+'),
     ('INT',        r'\d+'),
     ('RAWCODE',    r"'(?:[^'\\]|\\.)*'"),
     ('STRING',     r'"(?:[^"\\]|\\.)*"'),
     ('IDENT',      r'[A-Za-z_][A-Za-z0-9_]*'),
     ('EQ',         r'=='),
     ('NE',         r'!='),
     ('LE',         r'<='),
     ('GE',         r'>='),
     ('ASSIGN',     r'='),
     ('LT',         r'<'),
     ('GT',         r'>'),
     ('PLUS',       r'\+'),
     ('MINUS',      r'-'),
     ('STAR',       r'\*'),
     ('SLASH',      r'/'),
     ('LPAREN',     r'\('),
     ('RPAREN',     r'\)'),
     ('LBRACKET',   r'\['),
     ('RBRACKET',   r'\]'),
     ('COMMA',      r','),
]

_MASTER_RE = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in _TOKEN_SPEC))

_SKIP_KINDS = {'COMMENT', 'NEWLINE', 'WHITESPACE'}


class Token:
     __slots__ = ('kind', 'value', 'line', 'col')

     def __init__(self, kind, value, line, col):
          self.kind = kind
          self.value = value
          self.line = line
          self.col = col

     def __repr__(self):
          return f'Token({self.kind}, {self.value!r}, {self.line}:{self.col})'


class JassLexError(Exception):
     def __init__(self, message, line, col):
          super().__init__(f'{message} at line {line}, column {col}')
          self.line = line
          self.col = col


def tokenize(source, keep_comments=False):
     """Tokenize JASS source text into a flat list of Token, keywords/identifiers
     kept distinct via KEYWORD kind, comments/whitespace/newlines dropped (JASS's
     grammar is keyword-driven, not layout-sensitive, so they carry no structural
     meaning - only line/col on surrounding tokens matters for error messages).

     `keep_comments` emits COMMENT tokens as well. They still carry no
     structural meaning, but a GUI comment compiles to a plain `//` line and
     is the only trace left of a trigger's Comment action, so reconstructing
     the editor's triggers needs them kept rather than dropped."""
     tokens = []
     pos = 0
     line = 1
     col = 1
     length = len(source)

     while pos < length:
          match = _MASTER_RE.match(source, pos)
          if not match:
               raise JassLexError(f'Unexpected character {source[pos]!r}', line, col)

          kind = match.lastgroup
          value = match.group()

          if kind == 'COMMENT' and keep_comments:
               tokens.append(Token('COMMENT', value[2:].strip(), line, col))
          elif kind not in _SKIP_KINDS:
               if kind == 'IDENT' and value in KEYWORDS:
                    tokens.append(Token('KEYWORD', value, line, col))
               elif kind == 'STRING':
                    tokens.append(Token('STRING', value[1:-1], line, col))
               elif kind == 'RAWCODE':
                    tokens.append(Token('RAWCODE', value[1:-1], line, col))
               else:
                    tokens.append(Token(kind, value, line, col))

          newline_count = value.count('\n')
          if newline_count:
               line += newline_count
               col = len(value) - value.rfind('\n')
          else:
               col += len(value)
          pos = match.end()

     tokens.append(Token('EOF', '', line, col))
     return tokens
