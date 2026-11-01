"""Tokenizer for vJass and Zinc.

vJass is line-oriented like JASS - a statement ends at the end of its line -
so the vJass mode emits a NL token per line. Zinc is free-form with braces
and semicolons, so its mode emits no NL tokens and knows a few more operators.

Literals keep their source text (`raw`) so the output spells them the way the
input did: `0x10` stays `0x10`, `'A000'` stays `'A000'`.
"""
import re

from .errors import Location, VJassError

_VJASS_OPS = ['==', '!=', '<=', '>=', '=', '<', '>', '+', '-', '*', '/', '(',
              ')', '[', ']', ',', '.', ':']
_ZINC_OPS = ['->', '&&', '||', '==', '!=', '<=', '>=', '+=', '-=', '*=', '/=',
             '++', '--', '=', '<', '>', '+', '-', '*', '/', '(', ')', '[', ']',
             ',', '.', '{', '}', ';', '!', '%', ':']


def _op_pattern(ops):
    return '|'.join(re.escape(op) for op in sorted(ops, key=len, reverse=True))


_COMMON = [
    ('WS', r'[ \t\f\v\x00]+'),
    ('REAL', r'\d+\.\d*|\.\d+'),
    ('HEX', r'0[xX][0-9A-Fa-f]+|\$[0-9A-Fa-f]+'),
    ('INT', r'\d+'),
    ('RAWCODE', r"'(?:[^'\\\n]|\\.)*'"),
    ('ID', r'[A-Za-z_][A-Za-z0-9_]*'),
]


def _master(ops):
    spec = _COMMON + [('OP', _op_pattern(ops))]
    return re.compile('|'.join(f'(?P<{n}>{p})' for n, p in spec))


_MASTER_VJASS = _master(_VJASS_OPS)
_MASTER_ZINC = _master(_ZINC_OPS)
_DIRECTIVE = re.compile(r'\s*//!\s*(\w+)\s*(.*?)\s*$')


class Token(object):
    __slots__ = ('kind', 'value', 'raw', 'file', 'line', 'col', 'origin')

    def __init__(self, kind, value, raw, file, line, col, origin=None):
        self.kind = kind
        self.value = value
        self.raw = raw
        self.file = file
        self.line = line
        self.col = col
        # The module use a token was pasted in from (see compiler.py).
        self.origin = origin

    def copy(self, **changes):
        token = Token(self.kind, self.value, self.raw, self.file, self.line,
                      self.col, self.origin)
        for name, value in changes.items():
            setattr(token, name, value)
        return token

    @property
    def location(self):
        return Location(self.file, self.line, self.col)

    def is_op(self, *ops):
        return self.kind == 'OP' and self.value in ops

    def is_word(self, *words):
        return self.kind == 'ID' and self.value in words

    def __repr__(self):
        return f'Token({self.kind}, {self.value!r}, {self.line}:{self.col})'


def _int_value(raw):
    if raw.startswith('$'):
        return int(raw[1:], 16)
    if raw[:2] in ('0x', '0X'):
        return int(raw[2:], 16)
    if len(raw) > 1 and raw.startswith('0'):
        try:
            return int(raw, 8)
        except ValueError:
            return int(raw)
    return int(raw)


def _rawcode_value(raw):
    body = raw[1:-1]
    chars = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == '\\' and i + 1 < len(body):
            i += 1
            c = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f'}.get(
                body[i], body[i])
        chars.append(c)
        i += 1
    value = 0
    for c in chars:
        value = (value * 256 + (ord(c) & 0xFF)) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    return value


def tokenize(lines, zinc=False):
    """Tokenize a list of SourceLine. Returns a list of Token ending in EOF."""
    master = _MASTER_ZINC if zinc else _MASTER_VJASS
    tokens = []
    in_block_comment = None
    last = None
    # A /* comment */ that runs over several lines joins them into one
    # logical line, which is how vJass libraries write long headers:
    #     library Alloc /* docs ...
    #     */ uses /* more docs
    #     */ optional MemoryAnalysis
    pending_line = False
    for src in lines:
        text = src.text
        pos = 0
        n = len(text)
        line_tokens = []
        if in_block_comment is None and not pending_line:
            directive = _DIRECTIVE.match(text)
            if directive:
                tokens.append(Token('DIRECTIVE', directive.group(1).lower(),
                                    directive.group(2), src.file, src.line, 1))
                if not zinc:
                    tokens.append(Token('NL', '\n', '', src.file, src.line, n + 1))
                continue
        while pos < n:
            if in_block_comment is not None:
                end = text.find('*/', pos)
                if end < 0:
                    pos = n
                    break
                pos = end + 2
                in_block_comment = None
                continue
            c = text[pos]
            if c == '/' and text.startswith('//', pos):
                break
            if c == '/' and text.startswith('/*', pos):
                in_block_comment = Location(src.file, src.line, pos + 1)
                pos += 2
                continue
            if c == '"':
                j = pos + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == '\\' else 1
                if j >= n:
                    # JASS strings may span lines; vJass sources almost never
                    # do it on purpose, so it is reported as unclosed.
                    raise VJassError('Unclosed string', Location(
                        src.file, src.line, pos + 1), 'parse')
                raw = text[pos:j + 1]
                line_tokens.append(Token('STRING', raw[1:-1], raw, src.file,
                                         src.line, pos + 1))
                pos = j + 1
                continue
            match = master.match(text, pos)
            if not match:
                raise VJassError(f'Unexpected character {c!r}',
                                 Location(src.file, src.line, pos + 1), 'parse')
            kind = match.lastgroup
            raw = match.group()
            col = pos + 1
            pos = match.end()
            if kind == 'WS':
                continue
            if kind in ('INT', 'HEX'):
                line_tokens.append(Token('INT', _int_value(raw), raw, src.file,
                                         src.line, col))
            elif kind == 'REAL':
                line_tokens.append(Token('REAL', float(raw), raw, src.file,
                                         src.line, col))
            elif kind == 'RAWCODE':
                line_tokens.append(Token('RAWCODE', _rawcode_value(raw), raw,
                                         src.file, src.line, col))
            elif kind == 'ID':
                line_tokens.append(Token('ID', raw, raw, src.file, src.line, col))
            else:
                line_tokens.append(Token('OP', raw, raw, src.file, src.line, col))
        tokens.extend(line_tokens)
        if not zinc:
            if line_tokens:
                pending_line = True
            if pending_line and in_block_comment is None:
                tokens.append(Token('NL', '\n', '', src.file, src.line, n + 1))
                pending_line = False
        last = src
    if in_block_comment is not None:
        raise VJassError('Unclosed /* comment', in_block_comment, 'parse')
    file = last.file if last else None
    line = last.line + 1 if last else 1
    tokens.append(Token('EOF', '', '', file, line, 1))
    return tokens
