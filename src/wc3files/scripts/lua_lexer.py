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
_WHITESPACE = {' ', '\t', '\r', '\n', '\f', '\v'}
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
     __slots__ = ('kind', 'value', 'line', 'col', 'raw')

     def __init__(self, kind, value, line, col):
          self.kind = kind
          self.value = value
          self.line = line
          self.col = col
          # The token's source text, quotes and brackets included (for
          # error messages: `near '[[a]]'`).
          self.raw = None

     def __repr__(self):
          return f'Token({self.kind}, {self.value!r}, {self.line}:{self.col})'


class LuaLexError(Exception):
     """`lua_message`, when set, is the error as Lua words it (with its
     `near` part); the interpreter reports that one."""

     def __init__(self, message, line, col, lua_message=None):
          super().__init__(f'{message} at line {line}, column {col}')
          self.line = line
          self.col = col
          self.message = message
          self.lua_message = lua_message


class _Scanner:
     lua51 = False

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


def tokenize(source, version='5.3'):
     """Tokenize Lua source text into a flat list of Token. Keywords are kept
     as kind 'KEYWORD' (value = the keyword text, matching jass_lexer's
     convention); comments/whitespace are dropped entirely (Lua's grammar,
     like JASS's, is keyword/punctuation-driven, not layout-sensitive)."""
     scanner = _Scanner(source)
     # Lua 5.1 takes an unknown escape such as `\/` as the character itself;
     # 5.2 and later reject it.
     scanner.lua51 = version == '5.1'

     tokens = []

     while not scanner.at_end():
          ch = scanner.peek()

          if ch in _WHITESPACE:
               scanner.advance()
               continue

          if ch == '-' and scanner.peek(1) == '-':
               _consume_comment(scanner)
               continue

          start = scanner.pos

          if ch in _QUOTES:
               token = _read_short_string(scanner)
          elif ch == '[' and scanner.peek(1) in _LONG_BRACKET_START and \
                    _peek_long_bracket(scanner):
               token = _try_read_long_bracket(scanner)
          elif ch in _DIGITS or (ch == '.' and scanner.peek(1) in _DIGITS):
               token = _read_number(scanner)
          elif ch in _NAME_START:
               token = _read_name(scanner)
          else:
               token = _read_operator(scanner)
          token.raw = scanner.source[start:scanner.pos]
          tokens.append(token)

     tokens.append(Token('EOF', '', scanner.line, scanner.col))
     return tokens


def _peek_long_bracket(scanner):
     """Whether a long bracket starts here, without consuming anything."""
     save = scanner.pos, scanner.line, scanner.col
     level = _long_bracket_level(scanner)
     scanner.pos, scanner.line, scanner.col = save
     return level is not None


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
               what = 'comment' if as_comment else 'string'
               where = '' if scanner.lua51 else f' (starting at line {start_line})'
               raise LuaLexError('unterminated long bracket', start_line, start_col,
                                 f"unfinished long {what}{where} near '<eof>'")
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
               # Lua names what it has read so far, or <eof>
               near = '<eof>' if scanner.at_end() else f"'{quote}{''.join(chars)}'"
               raise LuaLexError('unterminated string', start_line, start_col,
                                 f'unfinished string near {near}')
          ch = scanner.peek()
          if ch == quote:
               scanner.advance()
               break
          if ch == '\\':
               scanner.advance()
               begin = scanner.pos
               try:
                    chars.append(_read_escape(scanner, start_line, start_col))
               except LuaLexError as error:
                    if error.lua_message is None:
                         # as Lua's lexer buffer holds it: the string so far,
                         # the escape, and the character that broke it
                         text = scanner.source[begin:scanner.pos]
                         if not scanner.at_end():
                              text += scanner.peek()
                         base = error.message.split(' \\')[0]
                         error.lua_message = (f"{base} near "
                                              f"'{quote}{''.join(chars)}\\{text}'")
                    raise
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
          if int(digits) > 255:
               raise LuaLexError('decimal escape too large', scanner.line, scanner.col)
          return chr(int(digits))
     if ch == 'u' and not scanner.lua51:
          return _read_utf8_escape(scanner)
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
          if scanner.lua51 and ch not in ('', '\r'):
               scanner.advance()
               return ch
          raise LuaLexError(f'invalid escape sequence \\{ch}', scanner.line, scanner.col)
     scanner.advance()
     return _ESCAPES[ch]


def _read_utf8_escape(scanner):
     """Lua 5.3's `\\u{XXX}`: a code point up to 2^31, written as (extended)
     UTF-8. Strings hold bytes, one per character."""
     scanner.advance()                               # 'u'
     if scanner.peek() != '{':
          raise LuaLexError("missing '{'", scanner.line, scanner.col)
     scanner.advance()
     digits = ''
     while scanner.peek() in _HEX_DIGITS:
          # checked before the digit is taken, as Lua 5.3 does (5.3 stops at
          # U+10FFFF; 5.4 allows up to 2^31)
          if int(digits + scanner.peek(), 16) > 0x10FFFF:
               raise LuaLexError('UTF-8 value too large', scanner.line, scanner.col)
          digits += scanner.advance()
     if not digits:
          raise LuaLexError('hexadecimal digit expected', scanner.line, scanner.col)
     if scanner.peek() != '}':
          raise LuaLexError("missing '}'", scanner.line, scanner.col)
     scanner.advance()
     return utf8_encode(int(digits, 16))


def utf8_encode(code):
     """Lua's luaO_utf8esc: UTF-8, extended to six bytes for values up to
     0x7FFFFFFF. Returned as a str of byte values."""
     if code < 0x80:
          return chr(code)
     out = []
     limit = 0x3F
     while code > limit:
          out.append(0x80 | (code & 0x3F))
          code >>= 6
          limit >>= 1
     out.append(((~limit << 1) & 0xFF) | code)
     return ''.join(chr(b) for b in reversed(out))


def _read_number(scanner):
     """Read a numeral the way Lua's read_numeral does: take every hex digit,
     '.' and exponent (with its sign) in a row, then check the whole thing is
     a valid number - so `0xF0.0`, `0x.8p4` and `3e-2` read in full, and
     `0.23e` or `3..2` is a malformed number rather than two tokens."""
     start_line, start_col = scanner.line, scanner.col
     text = scanner.advance()
     exponent = {'e', 'E'}
     if text == '0' and scanner.peek() in _HEX_MARKER:
          text += scanner.advance()
          exponent = {'p', 'P'}
     while True:
          ch = scanner.peek()
          if ch in exponent:
               text += scanner.advance()
               if scanner.peek() in _SIGN:
                    text += scanner.advance()
          elif ch in _HEX_DIGITS or ch == '.':
               text += scanner.advance()
          elif scanner.lua51 and (ch in _NAME_CONT):
               text += scanner.advance()       # 5.1 also takes letters and '_'
          else:
               break
     from .lua_vm import str_to_number
     if str_to_number(text) is None or (scanner.lua51 and _is_hex_float(text)):
          raise LuaLexError(f"malformed number near '{text}'", start_line, start_col)
     return Token('NUMBER', text, start_line, start_col)


def _is_hex_float(text):
     return text[:2] in ('0x', '0X') and any(c in text for c in '.pP')


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
