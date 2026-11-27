"""
Declarations in a script, found from its tokens, and the name under the
cursor resolved to one of them (#388).

The index is built from the lexer's tokens, not the parser's tree: the
parser keeps no position for a global, a native, a parameter or a local,
and a file being edited often does not parse at all. A line that does not
lex is left out, and the rest is still indexed.

JASS names resolve as JASS scopes them: a parameter or local of the
function the cursor is in, then the file's types, globals, natives and
functions, then the bundled `common.j` and `blizzard.j`. Lua scopes by
block, which this does not follow: a name resolves to the nearest
declaration above the cursor in the file, then to the bundled natives.
"""

import os
import re

from wc3files.scripts.checker import _DEFAULT_FILES, _GAMEDATA_DIR

_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


class Symbol:
    """A declaration. `line` and `col` (1-based) are those of its name;
    `scope` is the (first, last) line of the function it belongs to, for a
    parameter or local; `path` is the file it is in when it is not the
    document itself (a bundled file)."""

    __slots__ = ('name', 'kind', 'type', 'line', 'col', 'detail', 'scope', 'path', 'container',
                 'end_line')

    def __init__(self, name, kind, line, col, detail, type=None, scope=None, path=None,
                 container=None):
        self.name = name
        self.kind = kind
        self.type = type
        self.line = line
        self.col = col
        self.detail = detail
        self.scope = scope
        self.path = path
        self.container = container
        self.end_line = line  # a function's endfunction line, once known

    def __repr__(self):
        return f'Symbol({self.kind} {self.name} {self.line}:{self.col})'


class Index:
    def __init__(self, symbols, language):
        self.symbols = symbols
        self.language = language
        self.by_name = {}
        for symbol in symbols:
            self.by_name.setdefault(symbol.name, []).append(symbol)

    def resolve(self, name, line):
        """The declaration `name` refers to on 1-based `line`, or None."""
        candidates = self.by_name.get(name) or []
        if self.language == 'lua':
            above = [s for s in candidates if s.line <= line]
            return max(above, key=lambda s: (s.line, s.col)) if above else (
                candidates[0] if candidates else None)
        for symbol in candidates:
            if symbol.scope and symbol.scope[0] <= line <= symbol.scope[1]:
                return symbol
        for symbol in candidates:
            if symbol.scope is None:
                return symbol
        return None


def _source_line(lines, line):
    """A source line without its comment and outer spaces."""
    text = lines[line - 1] if 0 < line <= len(lines) else ''
    # The column padding of common.j collapsed, for display.
    return ' '.join(_strip_comment(text).split())


def _strip_comment(text):
    in_string = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == '\\' and in_string:
            i += 2
            continue
        if char == '"':
            in_string = not in_string
        elif not in_string and text.startswith('//', i):
            return text[:i]
        i += 1
    return text


def _tokens(tokenize, text):
    """The tokens of `text`; when it does not lex as a whole, each line that
    lexes on its own, so one bad line does not lose the rest."""
    try:
        return tokenize(text)
    except ValueError:
        pass
    tokens = []
    for number, line in enumerate(text.splitlines(), 1):
        try:
            line_tokens = tokenize(line)
        except ValueError:
            continue
        for token in line_tokens:
            if token.kind != 'EOF':
                token.line = number
                tokens.append(token)
    return tokens


def _ident(token):
    return token is not None and token.kind == 'IDENT'


def index_jass(text, path=None):
    from wc3files.scripts.jass_lexer import tokenize
    tokens = [t for t in _tokens(tokenize, text) if t.kind != 'EOF']
    lines = text.splitlines()
    symbols = []
    function = None  # (name, first line) of the function being read
    pending = []     # its parameters and locals, given their scope at endfunction

    def at(i):
        return tokens[i] if 0 <= i < len(tokens) else None

    def add(token, kind, value_type=None, scoped=False):
        symbol = Symbol(token.value, kind, token.line, token.col, _source_line(lines, token.line),
                        type=value_type, path=path,
                        container=function[0] if scoped and function else None)
        symbols.append(symbol)
        if scoped:
            pending.append(symbol)
        return symbol

    def close(last_line):
        """End the function being read on `last_line`."""
        for symbol in pending:
            symbol.scope = (function[1], last_line)
        function[2].end_line = last_line

    for i, token in enumerate(tokens):
        if token.kind != 'KEYWORD':
            continue
        word = token.value
        if word == 'type' and _ident(at(i + 1)):
            base = at(i + 3)
            add(at(i + 1), 'type', base.value if _ident(base) else None)
        elif word in ('native', 'function') and _ident(at(i + 1)):
            name = at(i + 1)
            if word == 'function' and function:
                # The one before has no endfunction yet, as while typing:
                # it ends where this one starts.
                close(token.line - 1)
            symbol = add(name, word, _header_returns(tokens, i))
            if word == 'function':
                function = (name.value, token.line, symbol)
                pending = []
                for type_token, param in _header_params(tokens, i):
                    add(param, 'param', type_token.value, scoped=True)
        elif word == 'endfunction' and function:
            close(token.line)
            function, pending = None, []
        elif word == 'local' and function and _ident(at(i + 1)):
            j = i + 2
            if at(j) is not None and at(j).kind == 'KEYWORD' and at(j).value == 'array':
                j += 1
            if _ident(at(j)):
                add(at(j), 'local', at(i + 1).value, scoped=True)
    if function:  # a function not closed yet, as while typing
        close(len(lines) + 1)
    # Globals: `[constant] type [array] name`, between globals and endglobals.
    symbols.extend(_jass_globals(tokens, lines, path))
    return Index(symbols, 'jass')


def _header_returns(tokens, i):
    for j in range(i + 1, min(i + 200, len(tokens))):
        token = tokens[j]
        if token.kind == 'KEYWORD' and token.value == 'returns':
            after = tokens[j + 1] if j + 1 < len(tokens) else None
            return after.value if after is not None else None
        if token.line != tokens[i].line:
            return None
    return None


def _header_params(tokens, i):
    """(type token, name token) of each parameter of the header at `i`."""
    out = []
    j = i + 2
    if not (j < len(tokens) and tokens[j].kind == 'KEYWORD' and tokens[j].value == 'takes'):
        return out
    j += 1
    while j + 1 < len(tokens) and tokens[j].line == tokens[i].line:
        token = tokens[j]
        if token.kind == 'KEYWORD':
            break  # nothing, or returns
        if _ident(token) and _ident(tokens[j + 1]):
            out.append((token, tokens[j + 1]))
            j += 2
            continue
        j += 1
    return out


def _jass_globals(tokens, lines, path):
    symbols = []
    inside = False
    for i, token in enumerate(tokens):
        if token.kind == 'KEYWORD' and token.value == 'globals':
            inside = True
            continue
        if token.kind == 'KEYWORD' and token.value == 'endglobals':
            inside = False
            continue
        if not inside or not _ident(token):
            continue
        previous = tokens[i - 1] if i else None
        # The type opens a declaration: first on its line, or after `constant`.
        starts = previous is None or previous.line != token.line or (
            previous.kind == 'KEYWORD' and previous.value == 'constant')
        if not starts:
            continue
        j = i + 1
        if j < len(tokens) and tokens[j].kind == 'KEYWORD' and tokens[j].value == 'array':
            j += 1
        if j < len(tokens) and _ident(tokens[j]) and tokens[j].line == token.line:
            is_constant = previous is not None and previous.kind == 'KEYWORD' and \
                previous.value == 'constant' and previous.line == token.line
            name = tokens[j]
            symbols.append(Symbol(name.value, 'constant' if is_constant else 'global',
                                  name.line, name.col, _source_line(lines, name.line),
                                  type=token.value, path=path))
    return symbols


def index_lua(text):
    from wc3files.scripts.lua_lexer import tokenize
    tokens = [t for t in _tokens(tokenize, text) if t.kind != 'EOF']
    lines = text.splitlines()
    symbols = []
    for i, token in enumerate(tokens):
        if token.kind != 'KEYWORD':
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if token.value == 'function' and nxt is not None and nxt.kind == 'NAME':
            # function a.b.c(...) names a.b.c; the last part is what is called
            j = i + 1
            while j + 2 < len(tokens) and tokens[j + 1].value in ('.', ':') and \
                    tokens[j + 2].kind == 'NAME':
                j += 2
            name = tokens[j]
            symbols.append(Symbol(name.value, 'function', name.line, name.col,
                                  _lua_line(lines, name.line)))
            symbols.extend(_lua_params(tokens, j, lines))
        elif token.value == 'local' and nxt is not None and nxt.kind == 'NAME':
            j = i + 1
            while j < len(tokens) and tokens[j].kind == 'NAME':
                symbols.append(Symbol(tokens[j].value, 'local', tokens[j].line, tokens[j].col,
                                      _lua_line(lines, tokens[j].line)))
                if j + 1 < len(tokens) and tokens[j + 1].value == ',':
                    j += 2
                else:
                    break
    return Index(symbols, 'lua')


def _lua_params(tokens, j, lines):
    out = []
    if j + 1 < len(tokens) and tokens[j + 1].value == '(':
        k = j + 2
        while k < len(tokens) and tokens[k].value != ')':
            if tokens[k].kind == 'NAME':
                out.append(Symbol(tokens[k].value, 'param', tokens[k].line, tokens[k].col,
                                  _lua_line(lines, tokens[k].line)))
            k += 1
    return out


def _lua_line(lines, line):
    text = lines[line - 1] if 0 < line <= len(lines) else ''
    return text.split('--', 1)[0].strip()


def index(language, text):
    if language == 'jass':
        return index_jass(text)
    if language == 'lua':
        return index_lua(text)
    return Index([], language)


_BUNDLED = None


def bundled():
    """The index of the bundled common.j and blizzard.j, built once."""
    global _BUNDLED
    if _BUNDLED is None:
        symbols = []
        for name in _DEFAULT_FILES:
            path = os.path.join(_GAMEDATA_DIR, name)
            with open(path, encoding='utf-8', errors='replace') as handle:
                symbols.extend(index_jass(handle.read(), path=path).symbols)
        _BUNDLED = Index([s for s in symbols if s.scope is None], 'jass')
    return _BUNDLED


def word_at(text, line, character):
    """(word, start, end) of the identifier at 0-based `line` and
    `character`, or None."""
    lines = text.splitlines()
    if not 0 <= line < len(lines):
        return None
    for match in _WORD.finditer(lines[line]):
        if match.start() <= character <= match.end():
            return match.group(0), match.start(), match.end()
    return None


_KIND_WORDS = {'native': 'native', 'function': 'function', 'global': 'global',
               'constant': 'constant', 'type': 'type', 'param': 'parameter', 'local': 'local'}


def describe(symbol, language):
    """The hover text of `symbol`, as markdown: its declaration as code, and
    where it comes from."""
    code_language = 'jass' if symbol.path or language == 'jass' else 'lua'
    where = os.path.basename(symbol.path) if symbol.path else 'this file'
    what = _KIND_WORDS.get(symbol.kind, symbol.kind)
    if symbol.container:
        origin = f'{what} of `{symbol.container}`'
    else:
        origin = f'{what}, {where}, line {symbol.line}'
    return f'```{code_language}\n{symbol.detail}\n```\n{origin}'


# The protocol's CompletionItemKind.
_ITEM_KIND = {'native': 3, 'function': 3, 'global': 6, 'local': 6, 'param': 6,
              'constant': 21, 'type': 7}
KEYWORD_ITEM = 14
COMPLETION_LIMIT = 300


def _visible(symbol, line, language):
    """Whether `symbol` can be named on 1-based `line`: a parameter or local
    only in its function (JASS) or below its declaration (Lua)."""
    if language == 'lua':
        return symbol.kind not in ('local', 'param') or symbol.line <= line
    return symbol.scope is None or symbol.scope[0] <= line <= symbol.scope[1]


def completions(document_index, language, prefix, line):
    """(items, complete) for the names that start with `prefix` on 1-based
    `line`: the file's own first, then the bundled natives, functions,
    constants and types, then keywords. With no prefix, only the names in
    scope and the keywords are offered; past COMPLETION_LIMIT the list is cut
    and `complete` is False, so the editor asks again as more is typed."""
    from wc3files.scripts.jass_lexer import KEYWORDS
    from wc3files.scripts.lua_lexer import LUA_KEYWORDS
    lowered = prefix.lower()
    sources = [document_index.symbols]
    if prefix:
        sources.append(bundled().symbols)
    items, seen = [], set()
    complete = True
    for symbols in sources:
        for symbol in symbols:
            if symbol.name in seen or not symbol.name.lower().startswith(lowered):
                continue
            if not _visible(symbol, line, language):
                continue
            if not prefix and symbol.scope is None and language == 'jass':
                continue  # with no prefix, only the function's own names
            if len(items) >= COMPLETION_LIMIT:
                complete = False
                break
            seen.add(symbol.name)
            items.append({'label': symbol.name, 'kind': _ITEM_KIND.get(symbol.kind, 6),
                          'detail': symbol.detail})
    for keyword in sorted(LUA_KEYWORDS if language == 'lua' else KEYWORDS):
        if keyword.startswith(lowered) and keyword not in seen:
            items.append({'label': keyword, 'kind': KEYWORD_ITEM})
    return items, complete


def prefix_at(text, line, character):
    """The identifier characters just before 0-based `line`, `character`."""
    lines = text.splitlines()
    if not 0 <= line < len(lines):
        return ''
    before = lines[line][:character]
    match = re.search(r'[A-Za-z_][A-Za-z0-9_]*$', before)
    return match.group(0) if match else ''


# The protocol's SymbolKind.
_SYMBOL_KIND = {'type': 5, 'function': 12, 'native': 12, 'global': 13, 'local': 13,
                'param': 13, 'constant': 14}


def document_symbols(document_index, text):
    """The outline of a document (#391): its types, globals and constants,
    natives and functions, each function with its parameters and locals; in
    Lua, its functions. A function's range runs to its endfunction."""
    lines = text.splitlines()

    def entry(symbol, children=None):
        last = min(max(symbol.end_line, symbol.line), max(len(lines), symbol.line))
        end_text = lines[last - 1] if 0 < last <= len(lines) else ''
        name_start = {'line': symbol.line - 1, 'character': symbol.col - 1}
        name_end = {'line': symbol.line - 1, 'character': symbol.col - 1 + len(symbol.name)}
        item = {'name': symbol.name, 'detail': symbol.detail,
                'kind': _SYMBOL_KIND.get(symbol.kind, 13),
                'range': {'start': {'line': symbol.line - 1, 'character': 0},
                          'end': {'line': last - 1, 'character': len(end_text)}},
                'selectionRange': {'start': name_start, 'end': name_end}}
        if children is not None:
            item['children'] = children
        return item

    if document_index.language == 'lua':
        return [entry(s) for s in document_index.symbols if s.kind == 'function']
    members = {}
    for symbol in document_index.symbols:
        if symbol.container is not None:
            members.setdefault((symbol.container, symbol.scope), []).append(symbol)
    out = []
    for symbol in sorted(document_index.symbols, key=lambda s: (s.line, s.col)):
        if symbol.container is not None:
            continue
        if symbol.kind == 'function':
            inside = [m for (name, scope), group in members.items() if name == symbol.name
                      and scope and scope[0] == symbol.line for m in group]
            out.append(entry(symbol, [entry(m) for m in sorted(inside, key=lambda s: (s.line, s.col))]))
        else:
            out.append(entry(symbol))
    return out


def lookup(document_index, name, line):
    """The declaration of `name` on 1-based `line`: in the document, then in
    the bundled files."""
    return document_index.resolve(name, line) or bundled().resolve(name, line)
