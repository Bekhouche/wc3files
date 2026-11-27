"""
The cJass preprocessor: tokens, defines, conditional compilation, include
and enum, as the cJass 1.4.0.2 manual describes them (#227).

The output is a token list for `syntax.py`, with newlines kept as tokens.
"""

import datetime
import os
import re

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib')

_TOKEN = re.compile(r'''
    (?P<merge>\\[ \t]*\r?\n)
  | (?P<nl>\r?\n)
  | (?P<ws>[ \t\f\v]+)
  | (?P<pass>//![^\r\n]*)
  | (?P<comment>//[^\r\n]*|/\*.*?\*/)
  | (?P<str>"(?:\\.|[^"\\\r\n])*")
  | (?P<raw>'(?:\\.|[^'\\\r\n])*')
  | (?P<num>0[xX][0-9a-fA-F]+|\$[0-9a-fA-F]+|\d+\.\d*|\.\d+|\d+)
  | (?P<concat>\#\#)
  | (?P<dir>\#[ \t]*[A-Za-z_]+)
  | (?P<tick>`[^`\r\n]*`)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\+\+|--|\+=|-=|\*=|/=|==|!=|<=|>=|&&|\|\||[-+*/%<>=!(){}\[\],.;:])
''', re.S | re.X)

_MERGE = re.compile(r'\\[ \t]*\r?\n')
_SPACED = ('and', 'or', 'not', 'return', 'exitwhen', 'if', 'elseif', 'then')
_PREDEFINED = ('COUNTER', 'DATE', 'TIME', 'DEBUG', 'WAR3VER')
BLOCK_KINDS = ('library', 'library_once', 'scope', 'struct', 'module', 'interface')
BLOCK_ENDS = {'endlibrary': 'library', 'endscope': 'scope', 'endstruct': 'struct',
              'endmodule': 'module', 'endinterface': 'interface'}
_WORD_DIRECTIVES = ('define', 'include', 'enum', 'setdef', 'undef')


class CJassError(ValueError):
    def __init__(self, message, filename=None, line=None):
        where = f'{filename}:{line}: ' if filename and line else (f'{filename}: ' if filename else '')
        super().__init__(where + message)
        self.filename, self.line = filename, line


class Token:
    __slots__ = ('kind', 'value', 'line', 'file')

    def __init__(self, kind, value, line=0, file=None):
        self.kind, self.value, self.line, self.file = kind, value, line, file

    def __repr__(self):
        return f'{self.kind}:{self.value!r}'


def tokenize(text, filename=None):
    # A backslash ending a line joins the next one to it, inside a string
    # too, before anything else (later line numbers shift by the lines
    # joined).
    text = _MERGE.sub('', text)
    tokens, line, pos = [], 1, 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise CJassError(f'unexpected character {text[pos]!r}', filename, line)
        kind = match.lastgroup
        value = match.group(0)
        pos = match.end()
        if kind in ('merge', 'nl'):
            if kind == 'nl':
                tokens.append(Token('nl', '\n', line, filename))
            line += 1
            continue
        if kind == 'comment':
            line += value.count('\n')
            continue
        if kind == 'ws':
            continue
        if kind == 'dir':
            value = '#' + value[1:].strip()
        tokens.append(Token(kind, value, line, filename))
    tokens.append(Token('nl', '\n', line, filename))
    return tokens


def text_of(tokens):
    """Tokens joined back into code, with JASS-like spacing."""
    out = []
    previous = None
    for token in tokens:
        value = token.value
        if token.kind == 'nl':
            out.append('\n')
            previous = None
            continue
        if previous is not None:
            glue = (value in (',', ')', ']', '.', ';') or previous.value in ('(', '[', '.')
                    or (value in ('(', '[') and previous.kind == 'id'
                        and previous.value not in _SPACED))
            if not glue:
                out.append(' ')
        out.append(value)
        previous = token
    return ''.join(out)


class Define:
    def __init__(self, name, params, body, scope):
        self.name, self.params, self.body, self.scope = name, params, body, scope


class Preprocessor:
    """Runs the directives of one compilation: defines are shared by every
    included file."""

    def __init__(self, include_paths=(), debug=False, war3ver='WAR3VER_24', now=None):
        self.include_paths = list(include_paths)
        self.defines = {}          # name -> [Define]
        self.patterns = []         # names in <...>: tuples of token values
        self.guards = set()
        self.counter = 0
        self.enum_counter = 0
        self.debug, self.war3ver = debug, war3ver
        self.now = now or datetime.datetime.now()
        self.scopes = []           # [(kind, brace depth or None, id)]
        self.next_scope = 0
        self.depth = 0

    # -- defines ------------------------------------------------------------

    def visible(self, define):
        return define.scope is None or any(s[2] == define.scope for s in self.scopes)

    def lookup(self, name, argc):
        """The define for `name` called with `argc` arguments (None: no
        parentheses), the innermost private one first."""
        found = [d for d in self.defines.get(name, ()) if self.visible(d)]
        order = {s[2]: n for n, s in enumerate(self.scopes)}
        found.sort(key=lambda d: order.get(d.scope, -1), reverse=True)
        for define in found:
            if argc is None and define.params is None:
                return define
            if argc is not None and define.params is not None and len(define.params) == argc:
                return define
        return None

    def add_define(self, name, params, body, private, token, replace=False):
        scope = self.scopes[-1][2] if private and self.scopes else None
        if isinstance(name, tuple) and name not in self.patterns:
            # a name in <...>, matched as a run of tokens; longest first
            self.patterns.append(name)
            self.patterns.sort(key=lambda p: -len(p))
        existing = self.defines.setdefault(name, [])
        same = [d for d in existing if d.scope == scope
                and (d.params is None) == (params is None)
                and (params is None or len(d.params) == len(params))]
        identical = all([t.value for t in d.body] == [t.value for t in body]
                        and d.params == params for d in same)
        if same and not replace and not identical:
            label = ' '.join(name) if isinstance(name, tuple) else name
            raise CJassError(f"'{label}' is defined twice (use setdef to change it)",
                             token.file, token.line)
        for define in same:
            existing.remove(define)
        existing.append(Define(name, params, body, scope))

    def predefined(self, token):
        name = token.value
        if name == 'COUNTER':
            value = str(self.counter)
            self.counter += 1
            return [Token('num', value, token.line, token.file)]
        if name == 'DATE':
            return [Token('str', '"' + self.now.strftime('%Y.%m.%d') + '"', token.line, token.file)]
        if name == 'TIME':
            return [Token('str', '"' + self.now.strftime('%H:%M:%S') + '"', token.line, token.file)]
        if name == 'DEBUG':
            return [Token('num', '1' if self.debug else '0', token.line, token.file)]
        if name == 'WAR3VER':
            return [Token('id', self.war3ver, token.line, token.file)]
        return None

    def expand(self, tokens, hidden=frozenset()):
        """`tokens` with every define replaced, then `##` joined."""
        out = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            name, width = token.value, 1
            for pattern in self.patterns:
                if pattern not in hidden and \
                        tuple(t.value for t in tokens[i:i + len(pattern)]) == pattern:
                    name, width = pattern, len(pattern)
                    break
            if width > 1 or (token.kind == 'id' and name not in hidden and (
                    name in self.defines or name in _PREDEFINED)):
                special = self.predefined(token) if width == 1 else None
                if special is not None:
                    out.extend(special)
                    i += 1
                    continue
                args, end = None, i + width
                if end < len(tokens) and tokens[end].value == '(':
                    args, end = _arguments(tokens, end)
                define = self.lookup(name, len(args) if args is not None else None)
                if define is None and args is not None:
                    define = self.lookup(name, None)
                    end = i + width if define is not None else end
                    args = None if define is not None else args
                if define is not None:
                    body = _substitute(define, args or [], token)
                    out.extend(self.expand(body, hidden | {name}))
                    i = end
                    continue
            out.append(token)
            i += 1
        return _join(out)

    # -- the token stream -----------------------------------------------------

    def run(self, tokens):
        out = []
        conditions = []            # [(active, taken)]
        i = 0
        line_start = True

        def active():
            return all(c[0] for c in conditions)

        while i < len(tokens):
            token = tokens[i]
            directive = token.value[1:] if token.kind == 'dir' else (
                token.value if token.kind == 'id' and line_start
                and token.value in _WORD_DIRECTIVES else None)
            if directive in ('if', 'ifdef', 'elseif', 'else', 'endif'):
                end = _line_end(tokens, i)
                expression = tokens[i + 1:end]
                if directive in ('if', 'ifdef'):
                    value = active() and self.condition(expression, directive == 'ifdef')
                    conditions.append([value, value])
                elif not conditions:
                    raise CJassError(f'#{directive} without #if', token.file, token.line)
                elif directive == 'elseif':
                    value = not conditions[-1][1] and self.condition(expression)
                    conditions[-1] = [value, conditions[-1][1] or value]
                elif directive == 'else':
                    conditions[-1] = [not conditions[-1][1], True]
                else:
                    conditions.pop()
                i = end
                continue
            if not active():
                i += 1
                line_start = token.kind == 'nl'
                continue
            if directive == 'error':
                end = _line_end(tokens, i)
                message = ' '.join(t.value.strip('"') for t in tokens[i + 1:end])
                raise CJassError(message or '#error', token.file, token.line)
            if directive == 'guard':
                end = _line_end(tokens, i)
                i = end
                continue
            if directive == 'include':
                end = _line_end(tokens, i)
                out.extend(self.include(tokens[i + 1:end], token))
                i = end
                continue
            if directive in ('define', 'setdef'):
                i = self.define(tokens, i, replace=directive == 'setdef')
                continue
            if directive == 'undef':
                end = _line_end(tokens, i)
                for name in tokens[i + 1:end]:
                    self.defines.pop(name.value, None)
                i = end
                continue
            if directive == 'enum':
                i = self.enum(tokens, i)
                continue
            if token.kind == 'dir':
                raise CJassError(f'unknown directive {token.value}', token.file, token.line)
            # a run of ordinary tokens up to the end of the line
            end = i
            while end < len(tokens) and tokens[end].kind != 'nl' and tokens[end].kind != 'dir':
                end += 1
            chunk = tokens[i:end]
            self.track(chunk)
            out.extend(self.expand(chunk))
            if end < len(tokens) and tokens[end].kind == 'nl':
                out.append(tokens[end])
                end += 1
            line_start = True
            i = end
        if conditions:
            raise CJassError('#if without #endif')
        return out

    def track(self, chunk):
        """Follow library/scope/struct/module blocks, for private defines."""
        for n, token in enumerate(chunk):
            if token.kind != 'op' and token.kind != 'id':
                continue
            if token.value in BLOCK_KINDS and n + 1 < len(chunk) and chunk[n + 1].kind == 'id':
                brace = any(t.value == '{' for t in chunk[n + 1:])
                self.next_scope += 1
                self.scopes.append((token.value, self.depth if brace else None, self.next_scope))
            elif token.value in BLOCK_ENDS and self.scopes:
                self.scopes.pop()
            elif token.value == '{':
                self.depth += 1
            elif token.value == '}':
                self.depth -= 1
                if self.scopes and self.scopes[-1][1] == self.depth:
                    self.scopes.pop()

    def condition(self, expression, defined=False):
        if defined:
            return bool(expression) and (expression[0].value in self.defines)
        values = self.expand(expression)
        text = [t.value for t in values]
        for op in ('==', '!='):
            if op in text:
                at = text.index(op)
                equal = ' '.join(text[:at]) == ' '.join(text[at + 1:])
                return equal if op == '==' else not equal
        return ' '.join(text) in ('true', '1')

    def include(self, arguments, token):
        if not arguments or arguments[0].kind != 'str':
            raise CJassError('include takes a file name in quotes', token.file, token.line)
        name = bytes(arguments[0].value[1:-1], 'utf-8').decode('unicode_escape')
        here = os.path.dirname(token.file) if token.file and os.path.isfile(token.file) else None
        candidates = [name] if os.path.isabs(name) else [
            os.path.join(d, name) for d in ([here] if here else []) + self.include_paths + [LIB_DIR]]
        path = next((c for c in candidates if os.path.isfile(c)), None)
        if path is None:
            raise CJassError(f'include file {name!r} not found', token.file, token.line)
        with open(path, 'rb') as handle:
            text = handle.read().decode('utf-8-sig', 'replace')
        tokens = tokenize(text, path)
        if tokens and tokens[0].value == '#guard':
            guard = tokens[1].value if len(tokens) > 1 else path
            if guard in self.guards:
                return []
            self.guards.add(guard)
        return self.run(tokens)

    def define(self, tokens, i, replace=False):
        """Parse one define (or a block of them) starting at tokens[i]."""
        start = tokens[i]
        i += 1
        private = False
        if i < len(tokens) and tokens[i].value == 'private':
            private, i = True, i + 1
        if i < len(tokens) and tokens[i].value == '{':
            close = _matching(tokens, i)
            self._define_lines(tokens[i + 1:close], private, start, replace)
            return close + 1
        if i < len(tokens) and tokens[i].kind == 'nl':
            close = next((k for k in range(i, len(tokens)) if tokens[k].value == 'enddefine'), None)
            if close is None:
                raise CJassError('define block without enddefine', start.file, start.line)
            self._define_lines(tokens[i:close], private, start, replace)
            return close + 1
        return self._define_one(tokens, i, private, start, replace)

    def _define_lines(self, tokens, private, start, replace):
        k = 0
        while k < len(tokens):
            if tokens[k].kind == 'nl':
                k += 1
                continue
            k = self._define_one(tokens, k, private, start, replace)

    def _define_one(self, tokens, i, private, start, replace):
        if tokens[i].value == '<':
            close = next(k for k in range(i, len(tokens)) if tokens[k].value == '>')
            name = tuple(t.value for t in tokens[i + 1:close])
            i = close + 1
        else:
            name = tokens[i].value
            i += 1
        params = None
        if i < len(tokens) and tokens[i].value == '(':
            close = _matching(tokens, i)
            params = [t.value for t in tokens[i + 1:close] if t.value != ',']
            i = close + 1
        body = []
        if i < len(tokens) and tokens[i].value == '=':
            i += 1
            if i < len(tokens) and tokens[i].value == '{':
                close = _matching(tokens, i)
                body = tokens[i + 1:close]
                i = close + 1
            else:
                end = _line_end(tokens, i)
                body = tokens[i:end]
                i = end
        else:
            i = _line_end(tokens, i)
        self.add_define(name, params, body, private, start, replace)
        return i

    def enum(self, tokens, i):
        start = tokens[i]
        i += 1
        named = False
        if tokens[i].value == '(':
            close = _matching(tokens, i)
            named = True
            i = close + 1
        while tokens[i].kind == 'nl':
            i += 1
        if tokens[i].value == '{':
            close = _matching(tokens, i)
        else:
            close = next((k for k in range(i, len(tokens)) if tokens[k].value == 'endenum'), None)
            if close is None:
                raise CJassError('enum without endenum', start.file, start.line)
            i -= 1
        names = [t for t in tokens[i + 1:close] if t.kind == 'id']
        value = 0
        for name in names:
            number = value if named else self.enum_counter
            self.add_define(name.value, None, [Token('num', str(number), name.line, name.file)],
                            False, name)
            if named:
                value += 1
            else:
                self.enum_counter += 1
        return close + 1


def _retarget(body, at):
    return [Token(t.kind, t.value, at.line, at.file) for t in body]


def _line_end(tokens, i):
    while i < len(tokens) and tokens[i].kind != 'nl':
        i += 1
    return i


def _matching(tokens, i):
    """The index of the bracket closing the one at tokens[i]."""
    pairs = {'(': ')', '{': '}', '[': ']'}
    opening, closing = tokens[i].value, pairs[tokens[i].value]
    depth = 0
    for k in range(i, len(tokens)):
        if tokens[k].value == opening:
            depth += 1
        elif tokens[k].value == closing:
            depth -= 1
            if depth == 0:
                return k
    raise CJassError(f'{opening} is not closed', tokens[i].file, tokens[i].line)


def _arguments(tokens, i):
    """([argument tokens], index after ')') for the call at tokens[i] == '('."""
    close = _matching(tokens, i)
    args, current, depth = [], [], 0
    for token in tokens[i + 1:close]:
        if token.value in ('(', '[', '{'):
            depth += 1
        elif token.value in (')', ']', '}'):
            depth -= 1
        if token.value == ',' and depth == 0:
            args.append(current)
            current = []
        else:
            current.append(token)
    if current or args:
        args.append(current)
    return args, close + 1


def _substitute(define, args, at):
    if define.params is None:
        return _retarget(define.body, at)
    values = dict(zip(define.params, args))
    out = []
    for token in define.body:
        if token.kind == 'id' and token.value in values:
            out.extend(_retarget(values[token.value], at))
        elif token.kind == 'tick':
            inner = token.value[1:-1].strip()
            text = text_of(values[inner]).strip() if inner in values else inner
            out.append(Token('str', '"' + text.replace('"', '\\"') + '"', at.line, at.file))
        else:
            out.append(Token(token.kind, token.value, at.line, at.file))
    return out


def _join(tokens):
    if not any(t.kind == 'concat' for t in tokens):
        return tokens
    out = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.kind == 'concat' and out and i + 1 < len(tokens):
            left = out.pop()
            right = tokens[i + 1]
            value = left.value + right.value
            kind = 'id' if re.match(r'[A-Za-z_]\w*$', value) else left.kind
            out.append(Token(kind, value, left.line, left.file))
            i += 2
            continue
        out.append(token)
        i += 1
    return out
