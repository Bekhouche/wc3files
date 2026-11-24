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

# What tokenize() walks with finditer: the tokens, then MISMATCH for any
# other character, which is reported. Skipping whitespace inside the next
# match instead was tried, and was slower: the prefix is tried before every
# alternative.
_SCAN_RE = re.compile('|'.join(f'(?P<{name}>{pattern})'
                               for name, pattern in _TOKEN_SPEC + [('MISMATCH', r'.')]))

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


class JassLexError(ValueError):
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
     append = tokens.append
     keywords = KEYWORDS
     line = 1
     line_start = 0              # index of the first character of `line`
     for match in _SCAN_RE.finditer(source):
          kind = match.lastgroup
          if kind == 'WHITESPACE':
               continue
          if kind == 'NEWLINE':
               line += 1
               line_start = match.end()
               continue
          value = match.group()
          start = match.start()
          if kind == 'IDENT':
               append(Token('KEYWORD' if value in keywords else 'IDENT', value,
                            line, start - line_start + 1))
          elif kind == 'COMMENT':
               if keep_comments:
                    append(Token('COMMENT', value[2:].strip(), line,
                                 start - line_start + 1))
          elif kind == 'STRING' or kind == 'RAWCODE':
               append(Token(kind, value[1:-1], line, start - line_start + 1))
               # A string can span lines.
               newlines = value.count('\n')
               if newlines:
                    line += newlines
                    line_start = start + value.rfind('\n') + 1
          elif kind == 'MISMATCH':
               raise JassLexError(f'Unexpected character {value!r}', line,
                                  start - line_start + 1)
          else:
               append(Token(kind, value, line, start - line_start + 1))
     col = len(source) - line_start + 1
     tokens.append(Token('EOF', '', line, col))
     return tokens
