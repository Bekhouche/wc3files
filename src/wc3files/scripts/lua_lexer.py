"""
Lua lexer for Reforged's embedded `war3map.lua` dialect. Targets Lua 5.3, not
5.1: a real-fixture sweep against every local `war3map.lua` found (see
TODO.md) turned up genuine 5.3-only syntax in real, non-synthetic map
scripts - floor division (`//`) and the bitwise operators (`&`, `|`, `~` as
both binary xor and unary bnot, `<<`, `>>`) all appear in real files, which
5.1 doesn't have at all. goto and labels (`::name::`) are supported too: an
earlier sweep found none and the gap was disclosed rather than hidden, but a
189-map corpus turns them up readily - `goto continue` is the usual way to
emulate the continue statement Lua does not have, and transpiled scripts
emit generated labels such as `::__continue11::`.

Hand-written character scanner rather than JASS's single master regex:
long-bracket strings and comments (`[[`, `[=[`, `[==[`, ...) are
context-sensitive on their bracket "level" in a way one regex alternative
can't express, and need to consume up to their matching closer verbatim (no
escape processing inside them at all).
"""

LUA_KEYWORDS = {
     'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function',
     'goto', 'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return',
     'then', 'true', 'until', 'while',
}

_ESCAPES = {
     'a': '\a', 'b': '\b', 'f': '\f', 'n': '\n', 'r': '\r', 't': '\t',
     'v': '\v', '\\': '\\', '"': '"', "'": "'", '\n': '\n',
}

_DIGITS = set('0123456789')
_HEX_DIGITS = set('0123456789abcdefABCDEF')
_NAME_START = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_')
_NAME_CONT = _NAME_START | _DIGITS
_HEX_MARKER = {'x', 'X'}
_EXPONENT_MARKER = {'e', 'E'}
_SIGN = {'+', '-'}
_WHITESPACE = {' ', '\t', '\r', '\n'}
_QUOTES = {'"', "'"}
_LONG_BRACKET_START = {'[', '='}

# `scanner.peek()` returns '' at EOF as a "no character here" sentinel - every
# single-character membership test below must use a set (`char in {...}`), not
# `char in 'some string'`: the empty string is a substring of *any* string, so
# `'' in 'eE'` is True and would misread EOF as a real exponent/sign/etc.
# character, then crash trying to `advance()` past the end of the source (this
# was a real bug here, caught by fuzzing plain trailing-digit/`[` input, not
# just by inspection - see TODO.md).


class Token:
     __slots__ = ('kind', 'value', 'line', 'col')

     def __init__(self, kind, value, line, col):
          self.kind = kind
          self.value = value
          self.line = line
          self.col = col

     def __repr__(self):
          return f'Token({self.kind}, {self.value!r}, {self.line}:{self.col})'


class LuaLexError(Exception):
     def __init__(self, message, line, col):
          super().__init__(f'{message} at line {line}, column {col}')
          self.line = line
          self.col = col


class _Scanner:
     def __init__(self, source):
          self.source = source
          self.length = len(source)
          self.pos = 0
          self.line = 1
          self.col = 1

     def peek(self, offset=0):
          index = self.pos + offset
          return self.source[index] if index < self.length else ''

     def advance(self):
          ch = self.source[self.pos]
          self.pos += 1
          if ch == '\n':
               self.line += 1
               self.col = 1
          else:
               self.col += 1
          return ch

     def at_end(self):
          return self.pos >= self.length


def tokenize(source):
     """Tokenize Lua source text into a flat list of Token. Keywords are kept
     as kind 'KEYWORD' (value = the keyword text, matching jass_lexer's
     convention); comments/whitespace are dropped entirely (Lua's grammar,
     like JASS's, is keyword/punctuation-driven, not layout-sensitive)."""
     scanner = _Scanner(source)
     tokens = []

     while not scanner.at_end():
          ch = scanner.peek()

          if ch in _WHITESPACE:
               scanner.advance()
               continue

          if ch == '-' and scanner.peek(1) == '-':
               _consume_comment(scanner)
               continue

          if ch in _QUOTES:
               tokens.append(_read_short_string(scanner))
               continue

          if ch == '[' and scanner.peek(1) in _LONG_BRACKET_START:
               long_bracket = _try_read_long_bracket(scanner)
               if long_bracket is not None:
                    tokens.append(long_bracket)
                    continue

          if ch in _DIGITS or (ch == '.' and scanner.peek(1) in _DIGITS):
               tokens.append(_read_number(scanner))
               continue

          if ch in _NAME_START:
               tokens.append(_read_name(scanner))
               continue

          tokens.append(_read_operator(scanner))

     tokens.append(Token('EOF', '', scanner.line, scanner.col))
     return tokens


def _consume_comment(scanner):
     scanner.advance()
     scanner.advance()
     if scanner.peek() == '[':
          save = scanner.pos, scanner.line, scanner.col
          long_bracket = _try_read_long_bracket(scanner, as_comment=True)
          if long_bracket is not None:
               return
          scanner.pos, scanner.line, scanner.col = save
     while not scanner.at_end() and scanner.peek() != '\n':
          scanner.advance()


def _long_bracket_level(scanner):
     """At an opening `[`, returns the `=`-level if this is a genuine long
     bracket (`[`, then N `=`, then `[`), else None with the scanner
     untouched."""
     save = scanner.pos, scanner.line, scanner.col
     scanner.advance()
     level = 0
     while scanner.peek() == '=':
          scanner.advance()
          level += 1
     if scanner.peek() == '[':
          scanner.advance()
          return level
     scanner.pos, scanner.line, scanner.col = save
     return None


def _try_read_long_bracket(scanner, as_comment=False):
     start_line, start_col = scanner.line, scanner.col
     level = _long_bracket_level(scanner)
     if level is None:
          return None

     if scanner.peek() == '\n':
          scanner.advance()

     closer = ']' + '=' * level + ']'
     text_start = scanner.pos
     while True:
          if scanner.at_end():
               raise LuaLexError('unterminated long bracket', start_line, start_col)
          if scanner.peek() == ']' and scanner.source.startswith(closer, scanner.pos):
               text = scanner.source[text_start:scanner.pos]
               for _ in closer:
                    scanner.advance()
               break
          scanner.advance()

     if as_comment:
          return Token('_LONG_COMMENT', text, start_line, start_col)
     return Token('STRING', text, start_line, start_col)


def _read_short_string(scanner):
     start_line, start_col = scanner.line, scanner.col
     quote = scanner.advance()
     chars = []
     while True:
          if scanner.at_end() or scanner.peek() == '\n':
               raise LuaLexError('unterminated string', start_line, start_col)
          ch = scanner.peek()
          if ch == quote:
               scanner.advance()
               break
          if ch == '\\':
               scanner.advance()
               chars.append(_read_escape(scanner, start_line, start_col))
               continue
          chars.append(scanner.advance())
     return Token('STRING', ''.join(chars), start_line, start_col)


def _read_escape(scanner, start_line, start_col):
     """Also handles two Lua 5.2+ escapes confirmed in real fixtures (see
     lua_lexer.py's module docstring): `\\xXX` (exactly 2 hex digits) and `\\z`
     (skips all following whitespace, including newlines, and contributes no
     character itself - used to break a long string literal across lines in
     generated/obfuscated code without embedding a real newline in the
     value)."""
     if scanner.at_end():
          raise LuaLexError('unterminated string', start_line, start_col)
     ch = scanner.peek()
     if ch in _DIGITS:
          digits = ''
          for _ in range(3):
               if scanner.peek() in _DIGITS:
                    digits += scanner.advance()
               else:
                    break
          return chr(int(digits))
     if ch == 'x':
          scanner.advance()
          hex_digits = ''
          for _ in range(2):
               if scanner.peek() in _HEX_DIGITS:
                    hex_digits += scanner.advance()
               else:
                    raise LuaLexError('invalid \\x escape sequence', scanner.line, scanner.col)
          return chr(int(hex_digits, 16))
     if ch == 'z':
          scanner.advance()
          while scanner.peek() in _WHITESPACE:
               scanner.advance()
          return ''
     if ch not in _ESCAPES:
          raise LuaLexError(f'invalid escape sequence \\{ch}', scanner.line, scanner.col)
     scanner.advance()
     return _ESCAPES[ch]


def _read_number(scanner):
     start_line, start_col = scanner.line, scanner.col
     text = ''
     if scanner.peek() == '0' and scanner.peek(1) in _HEX_MARKER:
          text += scanner.advance()
          text += scanner.advance()
          while scanner.peek() in _HEX_DIGITS:
               text += scanner.advance()
          return Token('NUMBER', text, start_line, start_col)

     while scanner.peek() in _DIGITS:
          text += scanner.advance()
     if scanner.peek() == '.':
          text += scanner.advance()
          while scanner.peek() in _DIGITS:
               text += scanner.advance()
     if scanner.peek() in _EXPONENT_MARKER:
          text += scanner.advance()
          if scanner.peek() in _SIGN:
               text += scanner.advance()
          while scanner.peek() in _DIGITS:
               text += scanner.advance()
     return Token('NUMBER', text, start_line, start_col)


def _read_name(scanner):
     start_line, start_col = scanner.line, scanner.col
     text = ''
     while scanner.peek() in _NAME_CONT:
          text += scanner.advance()
     kind = 'KEYWORD' if text in LUA_KEYWORDS else 'NAME'
     return Token(kind, text, start_line, start_col)


_THREE_CHAR_OPS = {'...'}
_TWO_CHAR_OPS = {'==', '~=', '<=', '>=', '..', '//', '<<', '>>', '::'}
_ONE_CHAR_OPS = set('+-*/%^#<>=(){}[];:,.&|~')


def _read_operator(scanner):
     start_line, start_col = scanner.line, scanner.col
     three = scanner.source[scanner.pos:scanner.pos + 3]
     if three in _THREE_CHAR_OPS:
          for _ in range(3):
               scanner.advance()
          return Token('OP', three, start_line, start_col)

     two = scanner.source[scanner.pos:scanner.pos + 2]
     if two in _TWO_CHAR_OPS:
          scanner.advance()
          scanner.advance()
          return Token('OP', two, start_line, start_col)

     ch = scanner.peek()
     if ch in _ONE_CHAR_OPS:
          scanner.advance()
          return Token('OP', ch, start_line, start_col)

     raise LuaLexError(f'unexpected character {ch!r}', start_line, start_col)
