"""FDF - Frame Definition Files, the game's UI layout language.

The game describes every UI panel in `.fdf` text files under `UI/FrameDef/`,
and a map that builds custom UI ships its own. A `.toc` file lists which
`.fdf` files the game should load.

Five keywords open a block - `Frame`, `String`, `Texture`, `Layer` and
`StringList`; everything else is a property, a name followed by values::

    IncludeFile "UI\\FrameDef\\UI\\EscMenuTemplates.fdf",

    StringList {
        ACCEPT      "Accept",
    }

    Texture "ResourceBarIconTemplate" {
        Width 0.01640625,
    }

    Frame "SIMPLEFRAME" "ResourceBarFrame" {
        DecorateFileNames,
        Texture INHERITS "ResourceBarIconTemplate" {
            Anchor TOPLEFT, 0.0000, -0.003125,
            File "GoldIcon",
        }
    }

A block header may name a type and a name (`Frame`), only a name, or
neither - `Texture INHERITS "..."` is anonymous. `INHERITS` may carry
`WITHCHILDREN`.

**Where a statement ends** is the one genuinely ambiguous part of the
grammar, because a comma both separates values (`Anchor TOPLEFT, 0.0,
-0.003125,`) and terminates the statement. Blizzard's own parser resolves
this by knowing each property's arity, which is not published. Measured
across the 114 `.fdf` files in the installed game - 21,441 significant
lines - every statement but five ends at the last token of its line, so
this reader ends a statement at a comma that ends a line. The five
exceptions are a block header split before its `{` and one multi-line
string, both of which this reader handles.

Round-tripping is **structural, not byte-exact**: comments, blank lines and
the original indentation are dropped, since nothing in the format depends
on them. Numeric lexemes keep their exact spelling (`0.384f`, `8`) and bare
keywords stay bare, so a written file re-reads to an equal structure.
"""
import re

BLOCK_KEYWORDS = ('Frame', 'String', 'Texture', 'Layer', 'StringList')

_TOKEN = re.compile(r'''
      (?P<ws>[^\S\n]+)
    | (?P<newline>\n)
    | (?P<line_comment>//[^\n]*)
    | (?P<block_comment>/\*.*?\*/)
    | (?P<string>"(?:[^"\\]|\\.)*")
    | (?P<open>\{)
    | (?P<close>\})
    | (?P<comma>,)
    | (?P<number>-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?[fF]?)
    | (?P<word>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<other>\S)
''', re.X | re.S)


class FdfError(ValueError):
    pass


class Word(str):
    """A bare keyword value such as `TOPLEFT` - written back unquoted."""
    __slots__ = ()


class Number(float):
    """A numeric value that remembers how it was spelled (`0.384f`, `8`)."""
    __slots__ = ('raw',)

    def __new__(cls, raw):
        value = cls._to_float(raw)
        self = super().__new__(cls, value)
        self.raw = str(raw)
        return self

    @staticmethod
    def _to_float(raw):
        text = str(raw)
        return float(text[:-1] if text[-1:] in 'fF' else text)

    def __repr__(self):
        return self.raw


def _read_text(path_or_text):
    if isinstance(path_or_text, (bytes, bytearray)):
        return bytes(path_or_text).decode('utf-8-sig', errors='replace')
    if '\n' not in path_or_text and len(path_or_text) < 4096:
        with open(path_or_text, 'rb') as handle:
            return handle.read().decode('utf-8-sig', errors='replace')
    return path_or_text


class FdfFile(object):
    """A parsed frame definition file.

    `items` is a list of entries, each one of

        {'kind': 'include', 'path': str}
        {'kind': 'property', 'name': str, 'values': [...]}
        {'kind': 'block', 'keyword': str, 'type': str|None, 'name': str|None,
         'inherits': str|None, 'with_children': bool, 'items': [...]}

    For a `Frame` the header carries both a type and a name; for `String`,
    `Texture` and `Layer` `type` is the keyword itself and the name may be
    absent; a `StringList` has neither.
    """

    def __init__(self):
        self.items = []

    # -- reading ----------------------------------------------------------

    def read(self, path_or_text):
        tokens = self._tokenize(_read_text(path_or_text))
        self.items, position = self._parse_items(tokens, 0, top_level=True)
        if position != len(tokens):
            raise FdfError(f'unexpected {tokens[position][1]!r} '
                           f'on line {tokens[position][2]}')
        return self

    @staticmethod
    def _tokenize(text):
        """Tokens as (kind, value, line). Line numbers decide where a
        statement ends, so they are part of the token, not a side table."""
        tokens = []
        position = 0
        line = 1
        length = len(text)
        while position < length:
            match = _TOKEN.match(text, position)
            if match is None:  # pragma: no cover - `other` matches any char
                raise FdfError(f'cannot tokenize at offset {position}')
            kind = match.lastgroup
            value = match.group()
            position = match.end()
            if kind in ('ws', 'line_comment'):
                continue
            if kind in ('newline', 'block_comment'):
                line += value.count('\n')
                continue
            start_line = line
            line += value.count('\n')       # a string may span lines
            if kind == 'string':
                value = value[1:-1]
            tokens.append((kind, value, start_line))
        return tokens

    def _parse_items(self, tokens, position, top_level=False):
        items = []
        while position < len(tokens):
            kind, value, line = tokens[position]

            if kind == 'close':
                if top_level:
                    raise FdfError(f'unbalanced "}}" on line {line}')
                return items, position + 1

            if kind == 'comma':
                position += 1               # stray separator
                continue

            if kind != 'word':
                raise FdfError(f'expected a statement name, found {value!r} '
                               f'on line {line}')

            if value == 'IncludeFile':
                if position + 1 >= len(tokens) or tokens[position + 1][0] != 'string':
                    raise FdfError(f'IncludeFile on line {line} is not '
                                   f'followed by a quoted path')
                items.append({'kind': 'include', 'path': tokens[position + 1][1]})
                position += 2
                continue

            if value in BLOCK_KEYWORDS:
                item, position = self._parse_block(tokens, position)
                if item is not None:
                    items.append(item)
                    continue
                # No `{` followed the header - it was an ordinary property
                # after all, so fall through and read it as one.

            values, position = self._parse_values(tokens, position + 1)
            items.append({'kind': 'property', 'name': value, 'values': values})

        if not top_level:
            raise FdfError('a block is never closed with "}"')
        return items, position

    def _parse_block(self, tokens, position):
        """Read `Keyword header... { ... }`.

        Returns (None, position) unchanged if no `{` follows the header, so
        the caller can treat the keyword as a plain property instead.
        """
        keyword = tokens[position][1]
        scan = position + 1
        header = []
        while scan < len(tokens) and tokens[scan][0] in ('string', 'word', 'number'):
            header.append(tokens[scan])
            scan += 1
        if scan >= len(tokens) or tokens[scan][0] != 'open':
            return None, position

        names = [token[1] for token in header if token[0] == 'string']
        words = [token[1] for token in header if token[0] == 'word']

        inherits = None
        with_children = 'WITHCHILDREN' in words
        if 'INHERITS' in words:
            # The template is the string that follows INHERITS.
            index = next(i for i, t in enumerate(header)
                         if t[0] == 'word' and t[1] == 'INHERITS')
            following = [t[1] for t in header[index:] if t[0] == 'string']
            if not following:
                raise FdfError(f'INHERITS on line {header[index][2]} names '
                               f'no template')
            inherits = following[0]
            names = [n for n in names if n != inherits] or \
                [t[1] for t in header[:index] if t[0] == 'string']

        if keyword == 'Frame':
            frame_type = names[0] if names else None
            frame_name = names[1] if len(names) > 1 else None
        else:
            frame_type = keyword
            frame_name = names[0] if names else None

        block = {
            'kind': 'block',
            'keyword': keyword,
            'type': frame_type,
            'name': frame_name,
            'inherits': inherits,
            'with_children': with_children,
            'items': [],
        }
        block['items'], position = self._parse_items(tokens, scan + 1)
        return block, position

    @staticmethod
    def _parse_values(tokens, position):
        """Values up to the comma that ends the line (see module docstring)."""
        values = []
        while position < len(tokens):
            kind, value, line = tokens[position]
            if kind in ('close', 'open'):
                return values, position
            if kind == 'comma':
                position += 1
                nxt = tokens[position] if position < len(tokens) else None
                if nxt is None or nxt[2] != line:
                    return values, position     # the comma ended the line
                continue                        # mid-line: a separator
            if kind == 'number':
                values.append(Number(value))
            elif kind == 'string':
                values.append(value)
            else:
                values.append(Word(value))
            position += 1
        return values, position

    # -- writing ----------------------------------------------------------

    def write(self, path):
        with open(path, 'w', encoding='utf-8', newline='\r\n') as handle:
            handle.write(self.to_text())

    def to_text(self):
        lines = []
        self._write_items(self.items, lines, 0)
        return '\n'.join(lines) + '\n'

    def _write_items(self, items, lines, depth):
        indent = '    ' * depth
        for item in items:
            if item['kind'] == 'include':
                lines.append(f'{indent}IncludeFile "{item["path"]}",')
            elif item['kind'] == 'property':
                rendered = ', '.join(_render(value) for value in item['values'])
                lines.append(f'{indent}{item["name"]}'
                             + (f' {rendered},' if rendered else ','))
            else:
                lines.append(indent + _block_header(item) + ' {')
                self._write_items(item['items'], lines, depth + 1)
                lines.append(indent + '}')

    # -- convenience ------------------------------------------------------

    def blocks(self, keyword=None, recursive=True):
        """Every block, optionally only those opened by one keyword."""
        out = []

        def walk(items):
            for item in items:
                if item['kind'] == 'block':
                    if keyword is None or item['keyword'] == keyword:
                        out.append(item)
                    if recursive:
                        walk(item['items'])
        walk(self.items)
        return out

    def frames(self, recursive=True):
        return self.blocks('Frame', recursive)

    def includes(self):
        return [item['path'] for item in self.items if item['kind'] == 'include']

    def strings(self):
        """Every `StringList` entry, as {key: text}.

        `UI/FrameDef/GlobalStrings.fdf` is where the game keeps the UI text
        that `.wts` does not cover.
        """
        out = {}
        for block in self.blocks('StringList'):
            for item in block['items']:
                if item['kind'] == 'property' and item['values']:
                    out[item['name']] = str(item['values'][0])
        return out

    def get_data(self):
        return self.items

    def import_data(self, items):
        self.items = items
        return self


def _block_header(block):
    parts = [block['keyword']]
    if block['keyword'] == 'Frame':
        if block.get('type'):
            parts.append(f'"{block["type"]}"')
        if block.get('name'):
            parts.append(f'"{block["name"]}"')
    elif block.get('name'):
        parts.append(f'"{block["name"]}"')
    if block.get('inherits'):
        parts.append('INHERITS')
        if block.get('with_children'):
            parts.append('WITHCHILDREN')
        parts.append(f'"{block["inherits"]}"')
    return ' '.join(parts)


def _render(value):
    if isinstance(value, Number):
        return value.raw
    if isinstance(value, Word):
        return str(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        text = repr(value)
        return text
    if isinstance(value, int):
        return str(value)
    return '"%s"' % value


class TocFile(object):
    """A `.toc` - the `.fdf` files the game should load, one path per line."""

    def __init__(self):
        self.entries = []

    def read(self, path_or_text):
        text = _read_text(path_or_text)
        self.entries = [line.strip() for line in text.splitlines()
                        if line.strip() and not line.strip().startswith('//')]
        return self

    def write(self, path):
        with open(path, 'w', encoding='utf-8', newline='\r\n') as handle:
            handle.write('\n'.join(self.entries) + '\n')

    def get_data(self):
        return self.entries

    def import_data(self, entries):
        self.entries = list(entries)
        return self
