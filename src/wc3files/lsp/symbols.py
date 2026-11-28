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
                 'end_line', 'owner')

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
        # The struct, interface or module a vJass member or method is in
        # (#403); such a name is found after a `.`, not on its own.
        self.owner = None

    def __repr__(self):
        return f'Symbol({self.kind} {self.name} {self.line}:{self.col})'


class Index:
    """The declarations of a document, and `names`: the identifier tokens
    that can refer to one, which references and rename search (#400). A
    name after `.` or `:` is a field or method, not a variable, and is left
    out."""

    def __init__(self, symbols, language, names=(), dynamic=(), bases=None, tokens=None):
        self.symbols = symbols
        self.language = language
        self.names = list(names)
        # (string token, 'function' or 'global') for a name given as a
        # string to ExecuteFunc or TriggerRegisterVariableEvent, which a
        # rename changes too (#401).
        self.dynamic = list(dynamic)
        # vJass (#403): the structs each struct extends and the modules it
        # implements, and every token, to read what comes before a `.`.
        self.bases = bases or {}
        self.tokens = tokens
        self._positions = None
        self.by_name = {}
        self.members = {}
        for symbol in symbols:
            if symbol.owner is None:
                self.by_name.setdefault(symbol.name, []).append(symbol)
            elif symbol.scope is None:
                self.members.setdefault(symbol.owner, {}).setdefault(symbol.name, symbol)

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


def _names(tokens, kind):
    """The tokens of `kind` that are not after `.` or `:`."""
    return [token for i, token in enumerate(tokens) if token.kind == kind and
            not (i and tokens[i - 1].value in ('.', ':'))]


# The words vJass adds to JASS's keywords, as the indexer reads them (#403).
VJASS_WORDS = frozenset((
    'library', 'library_once', 'endlibrary', 'scope', 'endscope', 'struct', 'endstruct',
    'interface', 'endinterface', 'module', 'endmodule', 'method', 'endmethod', 'static',
    'private', 'public', 'readonly', 'stub', 'delegate', 'operator', 'implement', 'requires',
    'uses', 'needs', 'initializer', 'optional', 'defaults'))
_BLOCK_ENDS = {'endlibrary': ('library', 'library_once'), 'endscope': ('scope',),
               'endstruct': ('struct',), 'endinterface': ('interface',),
               'endmodule': ('module',)}
_STRUCTS = ('struct', 'interface', 'module')
# Words before a declaration's type: `private static constant integer x`.
_MODIFIERS = frozenset(('constant', 'private', 'public', 'readonly', 'static', 'stub',
                        'delegate'))


def index_jass(text, path=None, vjass=False):
    """The index of a JASS script, or with `vjass` of a vJass one: its
    libraries, scopes, structs, interfaces and modules too, with their
    methods and members."""
    if vjass:
        tokens = _vjass_tokens(text)
    else:
        from wc3files.scripts.jass_lexer import tokenize
        tokens = [t for t in _tokens(tokenize, text) if t.kind != 'EOF']
    lines = text.splitlines()
    symbols = []
    function = None  # (name, first line, symbol) of the function or method being read
    pending = []     # its parameters and locals, given their scope at its end
    blocks = []      # the library, scope, struct, interface and module symbols open
    bases = {}       # struct or module name: what it extends and implements

    def at(i):
        return tokens[i] if 0 <= i < len(tokens) else None

    def is_word(token, *words):
        return token is not None and token.kind == 'KEYWORD' and token.value in words

    def owner():
        """The struct, interface or module being read, or None."""
        return blocks[-1].name if blocks and blocks[-1].kind in _STRUCTS else None

    def add(token, kind, value_type=None, scoped=False, owned=False):
        symbol = Symbol(token.value, kind, token.line, token.col, _source_line(lines, token.line),
                        type=value_type, path=path,
                        container=function[0] if scoped and function else None)
        if owned:
            symbol.owner = owner()
        symbols.append(symbol)
        if scoped:
            pending.append(symbol)
        return symbol

    def close(last_line):
        """End the function being read on `last_line`."""
        for symbol in pending:
            symbol.scope = (function[1], last_line)
        function[2].end_line = last_line

    def open_function(i, name, symbol):
        nonlocal function, pending
        function = (name.value, tokens[i].line, symbol)
        pending = []
        for type_token, param in _header_params(tokens, i):
            add(param, 'param', type_token.value, scoped=True)

    def starts_declaration(i):
        """Whether the identifier at `i` is the type that opens its line,
        after any modifiers."""
        j = i - 1
        while j >= 0 and tokens[j].line == tokens[i].line and is_word(tokens[j], *_MODIFIERS):
            j -= 1
        return j < 0 or tokens[j].line != tokens[i].line

    for i, token in enumerate(tokens):
        if token.kind != 'KEYWORD':
            # A struct member: `[modifiers] type [array] name`, outside methods.
            if vjass and owner() and function is None and _ident(token) and \
                    starts_declaration(i):
                j = i + 2 if is_word(at(i + 1), 'array') else i + 1
                if _ident(at(j)) and at(j).line == token.line:
                    add(at(j), 'member', token.value, owned=True)
                    k = i - 1
                    while k >= 0 and tokens[k].line == token.line:
                        if is_word(tokens[k], 'delegate'):  # its members are the struct's
                            bases.setdefault(owner(), []).append(token.value)
                        k -= 1
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
                open_function(i, name, symbol)
        elif word in ('endfunction', 'endmethod') and function:
            close(token.line)
            function, pending = None, []
        elif word == 'local' and function and _ident(at(i + 1)):
            j = i + 2
            if is_word(at(j), 'array'):
                j += 1
            if _ident(at(j)):
                add(at(j), 'local', at(i + 1).value, scoped=True)
        elif not vjass:
            continue
        elif word in ('library', 'library_once', 'scope', 'struct', 'interface', 'module') \
                and _ident(at(i + 1)) and not is_word(at(i - 1), 'function'):
            parent = at(i + 3) if is_word(at(i + 2), 'extends') and _ident(at(i + 3)) else None
            symbol = add(at(i + 1), word, parent.value if parent else None)
            if parent is not None and word == 'struct':
                bases.setdefault(symbol.name, []).append(parent.value)
            blocks.append(symbol)
        elif word in _BLOCK_ENDS and blocks:
            if function:  # a method with no endmethod yet
                close(token.line - 1)
                function, pending = None, []
            for k in range(len(blocks) - 1, -1, -1):
                if blocks[k].kind in _BLOCK_ENDS[word]:
                    blocks[k].end_line = token.line
                    del blocks[k:]
                    break
        elif word == 'implement' and owner():
            j = i + 2 if is_word(at(i + 1), 'optional') else i + 1
            if _ident(at(j)):
                bases.setdefault(owner(), []).append(at(j).value)
        elif word == 'method' and owner():
            j = i + 2 if is_word(at(i + 1), 'operator') else i + 1
            if not _ident(at(j)):
                continue
            if function:
                close(token.line - 1)
                function, pending = None, []
            symbol = add(at(j), 'method', _header_returns(tokens, i), owned=True)
            if blocks[-1].kind != 'interface':  # an interface's methods have no body
                open_function(j - 1, at(j), symbol)
    if function:  # a function not closed yet, as while typing
        close(len(lines) + 1)
    for block in blocks:  # blocks not closed yet
        block.end_line = len(lines) + 1
    # Globals: `[constant] type [array] name`, between globals and endglobals.
    symbols.extend(_jass_globals(tokens, lines, path))
    return Index(symbols, 'jass', _names(tokens, 'IDENT'), _dynamic_names(tokens), bases,
                 tokens if vjass else None)


class _VToken:
    """A vJass token in the JASS lexer's kinds, which the indexer reads."""

    __slots__ = ('kind', 'value', 'line', 'col')

    def __init__(self, kind, value, line, col):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return f'Token({self.kind}, {self.value!r}, {self.line}:{self.col})'


_OP_KINDS = {'(': 'LPAREN', ')': 'RPAREN', '[': 'LBRACKET', ']': 'RBRACKET', ',': 'COMMA'}


def _vjass_tokens(text):
    """The tokens of a vJass script from the vJass compiler's lexer, which
    reads `.`, `/* */` and the words JASS does not have; when the whole text
    does not lex, each line that lexes on its own."""
    from wc3files.scripts.jass_lexer import KEYWORDS
    from wc3files.vjass.errors import SourceLine, VJassError
    from wc3files.vjass.lexer import tokenize
    lines = text.splitlines()
    try:
        raw = tokenize([SourceLine(line, None, n) for n, line in enumerate(lines, 1)])
    except VJassError:
        raw = []
        for n, line in enumerate(lines, 1):
            try:
                raw.extend(tokenize([SourceLine(line, None, n)]))
            except VJassError:
                continue
    out = []
    for token in raw:
        if token.kind in ('NL', 'EOF', 'DIRECTIVE'):
            continue
        if token.kind == 'ID':
            kind = 'KEYWORD' if token.value in KEYWORDS or token.value in VJASS_WORDS else 'IDENT'
        elif token.kind == 'OP':
            kind = _OP_KINDS.get(token.value, 'OP')
        else:
            kind = token.kind
        value = token.value if token.kind in ('ID', 'OP', 'STRING') else token.raw
        out.append(_VToken(kind, value, token.line, token.col))
    return out


def _dynamic_names(tokens):
    """The string tokens that name a function to ExecuteFunc, or a global to
    TriggerRegisterVariableEvent, as the optimizer reads them."""
    from wc3files.scripts.optimizer import DYNAMIC_DISPATCH_NATIVES, VARIABLE_EVENT_NATIVE
    out = []
    for i, token in enumerate(tokens):
        if token.kind != 'IDENT' or i + 2 >= len(tokens) or tokens[i + 1].kind != 'LPAREN':
            continue
        if token.value in DYNAMIC_DISPATCH_NATIVES and tokens[i + 2].kind == 'STRING':
            out.append((tokens[i + 2], 'function'))
        elif token.value == VARIABLE_EVENT_NATIVE:
            depth = 0
            for j in range(i + 2, min(i + 40, len(tokens) - 1)):
                kind = tokens[j].kind
                if kind in ('LPAREN', 'LBRACKET'):
                    depth += 1
                elif kind in ('RPAREN', 'RBRACKET'):
                    if depth == 0:
                        break
                    depth -= 1
                elif kind == 'COMMA' and depth == 0:
                    if tokens[j + 1].kind == 'STRING':
                        out.append((tokens[j + 1], 'global'))
                    break
    return out


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
    # A vJass operator's name is followed by `=` or `[]` before `takes`.
    while j < len(tokens) and tokens[j].line == tokens[i].line and tokens[j].kind in (
            'OP', 'LBRACKET', 'RBRACKET'):
        j += 1
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
        # The type opens a declaration: first on its line, or after
        # `constant` (and in vJass `private` or `public`).
        k = i - 1
        modifiers = set()
        while k >= 0 and tokens[k].line == token.line and tokens[k].kind == 'KEYWORD' and \
                tokens[k].value in _MODIFIERS:
            modifiers.add(tokens[k].value)
            k -= 1
        if k >= 0 and tokens[k].line == token.line:
            continue
        j = i + 1
        if j < len(tokens) and tokens[j].kind == 'KEYWORD' and tokens[j].value == 'array':
            j += 1
        if j < len(tokens) and _ident(tokens[j]) and tokens[j].line == token.line:
            is_constant = 'constant' in modifiers
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
    return Index(symbols, 'lua', _names(tokens, 'NAME'))


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
    # Zinc gets the JASS index: its functions, globals and natives are found,
    # its structs and methods are not (Zinc's syntax is not read here).
    if language == 'vjass':
        return index_jass(text, vjass=True)
    if language in ('jass', 'zinc'):
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
               'constant': 'constant', 'type': 'type', 'param': 'parameter', 'local': 'local',
               'library_once': 'library'}


def describe(symbol, language):
    """The hover text of `symbol`, as markdown: its declaration as code, and
    where it comes from."""
    code_language = 'lua' if language == 'lua' and not symbol.path else 'jass'
    where = os.path.basename(symbol.path) if symbol.path else 'this file'
    what = _KIND_WORDS.get(symbol.kind, symbol.kind)
    if not symbol.line:
        origin = f'{what} of `{symbol.owner}`, made by the vJass compiler'
    elif symbol.container or symbol.owner:
        origin = f'{what} of `{symbol.container or symbol.owner}`'
    else:
        origin = f'{what}, {where}, line {symbol.line}'
    return f'```{code_language}\n{symbol.detail}\n```\n{origin}'


# The protocol's CompletionItemKind.
_ITEM_KIND = {'native': 3, 'function': 3, 'global': 6, 'local': 6, 'param': 6,
              'constant': 21, 'type': 7, 'method': 2, 'member': 5, 'struct': 22,
              'interface': 8, 'module': 9, 'library': 9, 'library_once': 9, 'scope': 9}
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
            if symbol.owner is not None:
                continue  # a member or method comes after a `.`
            if not _visible(symbol, line, language):
                continue
            if not prefix and symbol.scope is None and language != 'lua':
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
                'param': 13, 'constant': 14, 'library': 4, 'library_once': 4, 'scope': 3,
                'struct': 23, 'interface': 11, 'module': 2, 'method': 6, 'member': 8}
# What other declarations nest in, in the outline.
_CONTAINERS = frozenset(('library', 'library_once', 'scope', 'struct', 'interface', 'module',
                         'function', 'method'))


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
    # Each declaration goes in the innermost library, scope, struct,
    # interface, module, function or method whose lines hold it (#403).
    out = []
    stack = []  # (container symbol, its entry)
    for symbol in sorted(document_index.symbols, key=lambda s: (s.line, s.col)):
        if symbol.container is not None:
            continue
        while stack and stack[-1][0].end_line < symbol.line:
            stack.pop()
        children = None
        if symbol.kind in _CONTAINERS:
            children = []
            if symbol.kind in ('function', 'method'):
                inside = [m for (name, scope), group in members.items() if name == symbol.name
                          and scope and scope[0] == symbol.line for m in group]
                children = [entry(m) for m in sorted(inside, key=lambda s: (s.line, s.col))]
        item = entry(symbol, children)
        (stack[-1][1]['children'] if stack else out).append(item)
        if children is not None and symbol.end_line > symbol.line:
            stack.append((symbol, item))
    return out


def references(document_index, symbol, include_declaration=True):
    """The (line, col, is_declaration) of each use of `symbol` in the
    document, 1-based, in order: each name token that resolves to it, as
    hover resolves it. The declaration is one of them when `symbol` is
    declared in the document and `include_declaration` is set.

    Each token is resolved by bisecting the declarations of its name, not
    with `Index.resolve`, which reads them all: a war3map.j can have
    thousands of parameters of one name."""
    found = {}
    if symbol.path is None and include_declaration and symbol.line:
        found[(symbol.line, symbol.col)] = True
    if symbol.owner is not None:
        # A member or method: the names after a `.` that resolve to it.
        tokens = document_index.tokens or []
        for i, token in enumerate(tokens):
            if token.value == symbol.name and _ident(token) and i and \
                    _is_dot(tokens[i - 1]) and (token.line, token.col) not in found and \
                    member(document_index, owner_before(document_index, i - 1),
                           token.value) is symbol:
                found[(token.line, token.col)] = False
        return [(line, col, found[(line, col)]) for line, col in sorted(found)]
    resolves = _resolver(document_index, symbol.name)
    for token in document_index.names:
        if token.value != symbol.name or (token.line, token.col) in found:
            continue
        if symbol.scope and not symbol.scope[0] <= token.line <= symbol.scope[1]:
            continue
        if resolves(token.line) is symbol:
            is_declaration = symbol.path is None and (token.line, token.col) == (
                symbol.line, symbol.col)
            if is_declaration and not include_declaration:
                continue
            found[(token.line, token.col)] = is_declaration
    return [(line, col, found[(line, col)]) for line, col in sorted(found)]


_JASS_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
_LUA_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')


class RenameError(ValueError):
    """A rename that cannot be done; the message says why."""


def can_rename(symbol):
    """Raise RenameError for a symbol a rename cannot change: one declared in
    the bundled files."""
    if symbol.path is not None:
        raise RenameError(f'{symbol.name} is declared in {os.path.basename(symbol.path)}, '
                          f'not in this file')
    if not symbol.line:
        raise RenameError(f'{symbol.name} is made by the vJass compiler')


def rename_edits(document_index, symbol, new_name):
    """The (line, col, length) of each text to replace by `new_name` to
    rename `symbol`, 1-based: its declaration, its uses, and in JASS the
    ExecuteFunc and TriggerRegisterVariableEvent strings that name it.
    Raises RenameError when `new_name` is not a name, is a keyword, or would
    change what another name refers to."""
    from wc3files.scripts.jass_lexer import KEYWORDS
    from wc3files.scripts.lua_lexer import LUA_KEYWORDS
    can_rename(symbol)
    lua = document_index.language == 'lua'
    if not (_LUA_NAME if lua else _JASS_NAME).match(new_name):
        raise RenameError(f'{new_name!r} is not a name')
    if new_name in (LUA_KEYWORDS if lua else KEYWORDS):
        raise RenameError(f'{new_name} is a keyword')
    uses = references(document_index, symbol)
    if new_name != symbol.name:
        _check_conflict(document_index, symbol, new_name, uses)
    edits = [(line, col, len(symbol.name)) for line, col, _ in uses]
    if not lua and symbol.scope is None and symbol.kind in ('function', 'global'):
        for token, kind in document_index.dynamic:
            if token.value == symbol.name and kind == symbol.kind:
                edits.append((token.line, token.col + 1, len(symbol.name)))  # after the quote
    return sorted(set(edits))


def _check_conflict(document_index, symbol, new_name, uses):
    """Raise RenameError when a declaration or use of `new_name` would be
    shadowed by the renamed symbol, or would shadow it."""
    if symbol.owner is not None:
        if member(document_index, symbol.owner, new_name) is not None:
            raise RenameError(f'{new_name} is already a member of {symbol.owner}')
        return
    taken = [s for s in document_index.by_name.get(new_name, [])]
    outside = bundled().resolve(new_name, 0)
    if document_index.language == 'lua':
        tokens = [t for t in document_index.names if t.value == new_name]
        if taken or tokens or outside:
            raise RenameError(f'{new_name} is already used in this file'
                              if taken or tokens else f'{new_name} is a native or BJ name')
        return
    if symbol.scope:
        first, last = symbol.scope
        for token in document_index.names:
            if token.value == new_name and first <= token.line <= last:
                raise RenameError(f'{new_name} is already used in {symbol.container}')
        return
    if outside is not None:
        raise RenameError(f'{new_name} is declared in {os.path.basename(outside.path)}')
    if any(s.scope is None for s in taken):
        raise RenameError(f'{new_name} is already declared in this file')
    resolves = _resolver(document_index, new_name)
    for line, _, _ in uses:
        shadow = resolves(line)
        if shadow is not None and shadow.scope:
            raise RenameError(f'{new_name} is a {_KIND_WORDS.get(shadow.kind, shadow.kind)} '
                              f'of {shadow.container}, where {symbol.name} is used')


def _resolver(document_index, name):
    """A function of a 1-based line giving the declaration `name` resolves
    to there, as `lookup` does."""
    import bisect
    candidates = document_index.by_name.get(name) or []
    outside = bundled().resolve(name, 0)
    if document_index.language == 'lua':
        ordered = sorted(candidates, key=lambda s: (s.line, s.col))
        lines = [s.line for s in ordered]

        def lua(line):
            i = bisect.bisect_right(lines, line)
            if i:
                return ordered[i - 1]
            return ordered[0] if ordered else outside
        return lua
    file_level = next((s for s in candidates if s.scope is None), None) or outside
    scoped = {}
    for s in candidates:  # the first declaration of a scope wins, as in resolve
        if s.scope:
            scoped.setdefault(s.scope, s)
    ranges = sorted(scoped)
    firsts = [r[0] for r in ranges]

    def jass(line):
        i = bisect.bisect_right(firsts, line)
        # Scopes are functions, which do not nest; look back past the one
        # that starts here in case two share a first line.
        for j in range(i - 1, max(i - 3, -1), -1):
            first, last = ranges[j]
            if first <= line <= last:
                return scoped[ranges[j]]
        return file_level
    return jass


_OPENERS = {'(': ')', '[': ']', '{': '}'}


def call_at(text, line, character, language):
    """(function name, 1-based line and column of the name, index of the
    argument) of the innermost call the 0-based position is in, or None. In
    JASS a statement is one line, so only the line is read; in Lua a call can
    span lines, so up to 30 lines before it are. In vJass a method call
    (`.name(`) counts too, and its name is resolved from the document."""
    lines = text.splitlines()
    if not 0 <= line < len(lines):
        return None
    first = max(line - 30, 0) if language == 'lua' else line
    before = '\n'.join(lines[first:line] + [lines[line][:character]])
    tokens = _call_tokens(before, language)
    if tokens is None:
        return None
    stack = []  # [name token or None, opener, commas so far]
    previous, second = None, None
    for token in tokens:
        # A string's value is its text, which can be "(" or ",".
        value = None if token.kind in ('STRING', 'RAWCODE') else token.value
        if value in _OPENERS:
            is_call = (value == '(' and previous is not None
                       and previous.kind in ('IDENT', 'NAME')
                       and (language == 'vjass' or not (
                           second is not None and second.value in ('.', ':'))))
            stack.append([previous if is_call else None, value, 0])
        elif value in (')', ']', '}') and stack:
            stack.pop()
        elif value == ',' and stack:
            stack[-1][2] += 1
        second, previous = previous, token
    for name, opener, commas in reversed(stack):
        if opener == '(':
            if name is None:
                return None  # a parenthesis, not a call, or a method call
            return name.value, name.line + first, name.col, commas
    return None


def _call_tokens(text, language):
    """The tokens of `text`, an unfinished statement; a string still open
    at the end is closed so that the rest lexes."""
    if language == 'lua':
        from wc3files.scripts.lua_lexer import tokenize
    elif language == 'vjass':
        def tokenize(source):
            from wc3files.vjass.errors import SourceLine, VJassError
            from wc3files.vjass.lexer import tokenize as lex
            try:
                lex([SourceLine(source, None, 1)])
            except VJassError as error:
                raise ValueError(str(error)) from None
            return _vjass_tokens(source)
    else:
        from wc3files.scripts.jass_lexer import tokenize
    for tail in ('', '"', "'"):
        try:
            tokens = tokenize(text + tail)
        except ValueError:
            continue
        return [t for t in tokens if t.kind != 'EOF']
    return None


def signature(symbol):
    """(label, [(start, end) of each parameter in it]) for a function or
    native, from its declaration: JASS `takes a x, b y returns`, or Lua
    `function f(x, y)`."""
    label = symbol.detail
    if ' takes ' in label:
        start = label.index(' takes ') + len(' takes ')
        end = label.find(' returns', start)
        end = len(label) if end < 0 else end
    elif '(' in label:
        start = label.index('(') + 1
        end = label.find(')', start)
        end = len(label) if end < 0 else end
    else:
        return label, []
    params = []
    if label[start:end].strip() != 'nothing':
        offset = start
        for part in label[start:end].split(','):
            stripped = part.strip()
            if stripped:
                at = label.index(stripped, offset)
                params.append((at, at + len(stripped)))
            offset += len(part) + 1
    return label, params


def lookup(document_index, name, line):
    """The declaration of `name` on 1-based `line`: in the document, then in
    the bundled files."""
    return document_index.resolve(name, line) or bundled().resolve(name, line)


# -- vJass names after a dot (#403) -----------------------------------------

def token_index(document_index, line, col):
    """The position in `document_index.tokens` of the token at 1-based
    `line` and `col`, or None (and always None outside vJass)."""
    if document_index.tokens is None:
        return None
    if document_index._positions is None:
        document_index._positions = {(t.line, t.col): i
                                     for i, t in enumerate(document_index.tokens)}
    return document_index._positions.get((line, col))


def enclosing(document_index, line):
    """The innermost struct, interface or module around 1-based `line`."""
    best = None
    for symbol in document_index.symbols:
        if symbol.kind in _STRUCTS and symbol.line <= line <= symbol.end_line:
            if best is None or symbol.line > best.line:
                best = symbol
    return best


def _struct_named(document_index, name, line):
    """The struct, interface or module `name` stands for on `line`."""
    if name == 'thistype':
        around = enclosing(document_index, line)
        return around.name if around else None
    for symbol in document_index.by_name.get(name, ()):
        if symbol.kind in _STRUCTS:
            return symbol.name
    return None


# What the compiler gives every struct, as JassHelper's manual names them.
GENERATED = {
    'create': ('method', 'static method create takes nothing returns thistype'),
    'allocate': ('method', 'static method allocate takes nothing returns thistype'),
    'destroy': ('method', 'method destroy takes nothing returns nothing'),
    'deallocate': ('method', 'method deallocate takes nothing returns nothing'),
    'getType': ('method', 'method getType takes nothing returns integer'),
    'typeid': ('member', 'static constant integer typeid'),
}


def _generated(document_index, owner, name):
    """The member `name` the compiler gives `owner`, a struct or module, as
    a symbol with no line; the same object each time, so references can
    compare it."""
    if name not in GENERATED or not any(
            s.kind in ('struct', 'module') for s in document_index.by_name.get(owner, ())):
        return None
    cache = document_index.__dict__.setdefault('_generated', {})
    if (owner, name) not in cache:
        kind, detail = GENERATED[name]
        symbol = Symbol(name, kind, 0, 0, detail, type='thistype' if name in (
            'create', 'allocate') else None)
        symbol.owner = owner
        cache[(owner, name)] = symbol
    return cache[(owner, name)]


def member(document_index, owner, name, seen=None):
    """The member or method `name` of `owner`, its own first, then those of
    what it extends, implements and delegates to, then what the compiler
    gives it."""
    top = seen is None
    seen = set() if seen is None else seen
    if owner is None or owner in seen:
        return None
    seen.add(owner)
    found = document_index.members.get(owner, {}).get(name)
    if found is not None:
        return found
    for base in document_index.bases.get(owner, ()):
        found = member(document_index, base, name, seen)
        if found is not None:
            return found
    return _generated(document_index, owner, name) if top else None


def members_of(document_index, owner, seen=None):
    """Every member and method of `owner`, by name, its own first."""
    top = seen is None
    seen = set() if seen is None else seen
    if owner is None or owner in seen:
        return {}
    seen.add(owner)
    out = dict(document_index.members.get(owner, {}))
    for base in document_index.bases.get(owner, ()):
        for name, symbol in members_of(document_index, base, seen).items():
            out.setdefault(name, symbol)
    if top:
        for name in GENERATED:
            if name not in out:
                symbol = _generated(document_index, owner, name)
                if symbol is not None:
                    out[name] = symbol
    return out


def _type_struct(document_index, symbol):
    """The struct a symbol's value has, from its type, or None."""
    if symbol is None:
        return None
    if symbol.kind in _STRUCTS:
        return symbol.name
    if symbol.type == 'thistype':
        if symbol.owner is not None:
            return symbol.owner
        around = enclosing(document_index, symbol.line)
        return around.name if around else None
    if symbol.type is None or symbol.path is not None:
        return None
    return _struct_named(document_index, symbol.type, symbol.line)


def _matching(tokens, close):
    """The position of the bracket that opens the one at `close`."""
    pair = {'RPAREN': 'LPAREN', 'RBRACKET': 'LBRACKET'}[tokens[close].kind]
    depth = 0
    for k in range(close, -1, -1):
        if tokens[k].line != tokens[close].line:
            return None
        if tokens[k].kind == tokens[close].kind:
            depth += 1
        elif tokens[k].kind == pair:
            depth -= 1
            if depth == 0:
                return k
    return None


def _is_dot(token):
    return token is not None and token.kind == 'OP' and token.value == '.'


def _symbol_of(document_index, i):
    """The declaration the identifier token at `i` names."""
    tokens = document_index.tokens
    token = tokens[i]
    if i and _is_dot(tokens[i - 1]):
        return member(document_index, owner_before(document_index, i - 1), token.value)
    return lookup(document_index, token.value, token.line)


def owner_before(document_index, dot):
    """The struct of what comes before the `.` token at `dot`: `this`,
    `thistype` or a struct name; a name, call or array element of a struct
    type; or, with nothing before it (`.x`), the struct the line is in."""
    tokens = document_index.tokens
    line = tokens[dot].line
    j = dot - 1
    previous = tokens[j] if j >= 0 and tokens[j].line == line else None
    if previous is not None and _ident(previous):
        if previous.value in ('this', 'thistype', 'super'):
            around = enclosing(document_index, line)
            if around is None:
                return None
            # super: the struct this one extends.
            return around.type if previous.value == 'super' else around.name
        if not (j and _is_dot(tokens[j - 1])):
            named = _struct_named(document_index, previous.value, line)
            if named is not None:
                return named
        return _type_struct(document_index, _symbol_of(document_index, j))
    if previous is not None and previous.kind in ('RPAREN', 'RBRACKET'):
        k = _matching(tokens, j)
        if k and _ident(tokens[k - 1]):
            return _type_struct(document_index, _symbol_of(document_index, k - 1))
        return None
    around = enclosing(document_index, line)
    return around.name if around else None


def resolve_at(document_index, name, line, col):
    """The declaration of the name at 1-based `line` and `col`: after a
    `.`, a member or method of the struct before it; otherwise as
    `lookup` finds it."""
    i = token_index(document_index, line, col)
    if i is not None and i and _is_dot(document_index.tokens[i - 1]):
        return member(document_index, owner_before(document_index, i - 1), name)
    for members in document_index.members.values():
        declared = members.get(name)
        if declared is not None and (declared.line, declared.col) == (line, col):
            return declared  # a member or method where it is declared
    return lookup(document_index, name, line)


def member_completions(document_index, line, dot_col, prefix):
    """The members and methods that start with `prefix` of the struct
    before the `.` at 1-based `line` and `dot_col`, or None when that is
    not a struct."""
    i = token_index(document_index, line, dot_col)
    if i is None or not _is_dot(document_index.tokens[i]):
        return None
    owner = owner_before(document_index, i)
    if owner is None:
        return None
    lowered = prefix.lower()
    return [{'label': name, 'kind': _ITEM_KIND.get(symbol.kind, 6), 'detail': symbol.detail}
            for name, symbol in sorted(members_of(document_index, owner).items())
            if name.lower().startswith(lowered)]
