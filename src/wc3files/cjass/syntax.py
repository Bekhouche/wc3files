"""
The cJass syntax layer: preprocessed tokens to JASS with vJass blocks (#227).

What it turns into JASS, per the cJass 1.4.0.2 manual:

- blocks in braces: `if (c) { } elseif (c) { } else { }`, `loop { }`,
  `library A { }`, `struct S { }`, and the other vJass blocks;
- functions written return type first, `nothing f(integer x) { ... }`,
  which are methods inside a struct, module or interface;
- locals declared anywhere, several on a line (`integer i = 7, j, k`), moved
  to the top of the function; an initializer that is not a literal stays
  where it was, as a `set`;
- `call`, `set` and `local` left out; `+=`, `-=`, `*=`, `/=`; `++` and `--`
  as statements and inside expressions (a prefix one runs before the
  statement, a postfix one after);
- `whilenot (c) { }`, and `do { } whilenot (c)` for a loop that tests at
  the end;
- `&&`, `||` and `!` for `and`, `or` and `not`;
- declarations outside any function, which go into a `globals` block.
"""

from .preprocessor import CJassError, Token, text_of

_KEYWORDS = {'set', 'call', 'return', 'exitwhen', 'if', 'elseif', 'else', 'endif', 'loop',
             'endloop', 'do', 'whilenot', 'while', 'break', 'local', 'debug', 'function', 'endfunction',
             'method', 'endmethod', 'not', 'and', 'or', 'then', 'globals', 'endglobals',
             'native', 'type', 'constant', 'private', 'public', 'static', 'stub',
             'readonly', 'implement', 'delegate', 'extends', 'requires', 'needs', 'uses',
             'initializer', 'operator', 'array', 'true', 'false', 'null',
             'keyword', 'hook', 'optional'}
_BLOCKS = {'library': 'endlibrary', 'library_once': 'endlibrary', 'scope': 'endscope',
           'struct': 'endstruct', 'module': 'endmodule', 'interface': 'endinterface'}
_MEMBER_BLOCKS = ('struct', 'module', 'interface')
_MODIFIERS = ('private', 'public', 'static', 'stub', 'constant', 'readonly')
_COMPOUND = {'+=': '+', '-=': '-', '*=': '*', '/=': '/'}
_LITERAL_KINDS = ('num', 'str', 'raw')


def statements(tokens):
    """Tokens split into statements: at a newline or `;` outside brackets;
    `{` and `}` are statements of their own, and so is a `//!` line."""
    out, current, depth = [], [], 0
    for token in tokens:
        if token.kind == 'pass':
            if current:
                out.append(current)
            out.append([token])
            current = []
            continue
        if token.value in ('(', '['):
            depth += 1
        elif token.value in (')', ']'):
            depth -= 1
        if depth == 0 and (token.kind == 'nl' or token.value == ';'):
            if current:
                out.append(current)
            current = []
            continue
        if depth == 0 and token.value in ('{', '}') and token.kind == 'op':
            if current:
                out.append(current)
            out.append([token])
            current = []
            continue
        if token.kind == 'nl':
            continue
        current.append(token)
    if current:
        out.append(current)
    return out


def _values(stmt):
    return [t.value for t in stmt]


def _is_open(stmt):
    return len(stmt) == 1 and stmt[0].value == '{' and stmt[0].kind == 'op'


def _is_close(stmt):
    return len(stmt) == 1 and stmt[0].value == '}' and stmt[0].kind == 'op'


def _top_split(tokens, separator):
    parts, current, depth = [], [], 0
    for token in tokens:
        if token.value in ('(', '['):
            depth += 1
        elif token.value in (')', ']'):
            depth -= 1
        if depth == 0 and token.value == separator:
            parts.append(current)
            current = []
        else:
            current.append(token)
    parts.append(current)
    return parts


def _find_top(tokens, values):
    depth = 0
    for n, token in enumerate(tokens):
        if token.value in ('(', '['):
            depth += 1
        elif token.value in (')', ']'):
            depth -= 1
        elif depth == 0 and token.kind == 'op' and token.value in values:
            return n
    return None


class _Function:
    def __init__(self, name):
        self.name = name
        self.locals = []      # lines
        self.declared = {}    # name -> type


class Compiler:
    def __init__(self, stmts, filename=None):
        self.stmts = stmts
        self.i = 0
        self.out = []
        self.filename = filename
        self.function = None

    # -- helpers ----------------------------------------------------------

    def error(self, message, stmt=None):
        token = stmt[0] if stmt else None
        raise CJassError(message, token.file if token else self.filename,
                         token.line if token else None)

    def peek(self, offset=0):
        k = self.i + offset
        return self.stmts[k] if k < len(self.stmts) else None

    def take(self):
        stmt = self.stmts[self.i]
        self.i += 1
        return stmt

    def emit(self, indent, text):
        self.out.append('    ' * indent + text)

    def expression(self, tokens):
        """(before, text, after): `tokens` as JASS, with the statements that
        its ++ and -- run before and after it."""
        before, after, out = [], [], []
        n = 0
        while n < len(tokens):
            token = tokens[n]
            if token.kind == 'op' and token.value in ('++', '--'):
                op = '+' if token.value == '++' else '-'
                if out and (out[-1].kind == 'id' or out[-1].value == ']'):
                    target = self._lvalue_back(out)
                    after.append(f'set {target} = {target} {op} 1')
                else:
                    end = self._lvalue_end(tokens, n + 1)
                    target_tokens = tokens[n + 1:end]
                    target = self.expression(target_tokens)[1]
                    before.append(f'set {target} = {target} {op} 1')
                n += 1
                continue
            if token.kind == 'op' and token.value in ('&&', '||', '!'):
                out.append(Token('id', {'&&': 'and', '||': 'or', '!': 'not'}[token.value],
                                 token.line, token.file))
            elif token.kind == 'id' and token.value == 'FUNCNAME' and self.function:
                out.append(Token('id', self.function.name, token.line, token.file))
            else:
                out.append(token)
            n += 1
        return before, text_of(out).strip(), after

    @staticmethod
    def _lvalue_back(out):
        """The text of the variable that ends `out` (a name, an array element
        or a member), for a postfix ++ or --."""
        k = len(out) - 1
        if out[k].value == ']':
            depth = 0
            while k >= 0:
                if out[k].value == ']':
                    depth += 1
                elif out[k].value == '[':
                    depth -= 1
                    if depth == 0:
                        break
                k -= 1
            k -= 1
        while k > 1 and out[k - 1].value == '.' and out[k - 2].kind == 'id':
            k -= 2
        return text_of(out[k:]).strip()

    @staticmethod
    def _lvalue_end(tokens, n):
        k = n
        while k < len(tokens):
            if tokens[k].kind == 'id':
                k += 1
                if k < len(tokens) and tokens[k].value == '[':
                    depth = 0
                    while k < len(tokens):
                        if tokens[k].value == '[':
                            depth += 1
                        elif tokens[k].value == ']':
                            depth -= 1
                            if depth == 0:
                                k += 1
                                break
                        k += 1
                if k < len(tokens) and tokens[k].value == '.':
                    k += 1
                    continue
            break
        return k

    def textmacro(self, line):
        """After `//! textmacro`, the body as it is, up to `//! endtextmacro`:
        it is a fragment of code that runtextmacro puts somewhere else."""
        if line.split()[:2] != ['//!', 'textmacro']:
            return
        while self.i < len(self.stmts):
            stmt = self.take()
            if stmt[0].kind == 'pass' and stmt[0].value.split()[:2] == ['//!', 'endtextmacro']:
                self.emit(0, stmt[0].value)
                return
            self.emit(0, text_of(stmt).strip())
        self.error('//! textmacro without //! endtextmacro')

    # -- top level and blocks -----------------------------------------------

    def compile(self):
        self.items(0, context='top', end=None)
        return '\n'.join(self.out) + '\n'

    def items(self, indent, context, end):
        """Declarations until `end` (a closing statement value, '}' or None)."""
        globals_open = False

        def close_globals():
            nonlocal globals_open
            if globals_open:
                self.emit(indent, 'endglobals')
                globals_open = False

        while self.i < len(self.stmts):
            stmt = self.peek()
            values = _values(stmt)
            if end == '}' and _is_close(stmt):
                close_globals()
                self.take()
                return
            if end is not None and end != '}' and values[0] == end:
                close_globals()
                self.take()
                return
            if stmt[0].kind == 'pass':
                close_globals()
                self.take()
                self.emit(0, stmt[0].value)
                self.textmacro(stmt[0].value)
                continue
            if values[0] == 'globals':
                close_globals()
                self.take()
                self.emit(indent, 'globals')
                while self.i < len(self.stmts) and _values(self.peek())[0] != 'endglobals':
                    self.emit(indent + 1, self.declaration_text(self.take(), 'global'))
                self.take()
                self.emit(indent, 'endglobals')
                continue
            head = [v for v in values if v not in _MODIFIERS]
            if head and head[0] in _BLOCKS:
                close_globals()
                self.block(indent, head[0])
                continue
            if ('function' in values[:3] and 'takes' in values
                    and values[values.index('function') + 1] != 'interface'):
                close_globals()
                self.jass_function(indent, 'function', 'endfunction')
                continue
            if 'method' in values[:4] and 'takes' in values:
                close_globals()
                if context == 'interface' or (self.peek(1) is None):
                    self.take()
                    self.emit(indent, text_of(stmt).strip())
                    continue
                self.jass_function(indent, 'method', 'endmethod')
                continue
            if self.is_function_form(stmt):
                close_globals()
                self.function_form(indent, context)
                continue
            if values[0] in ('native', 'type', 'implement', 'delegate', 'keyword', 'hook') \
                    or (values[0] in _MODIFIERS and values[1:2] == ['native']):
                close_globals()
                self.take()
                self.emit(indent, text_of(stmt).strip())
                continue
            if self.is_declaration(stmt):
                self.take()
                if context in _MEMBER_BLOCKS:
                    self.emit(indent, self.declaration_text(stmt, 'member'))
                    continue
                if not globals_open:
                    self.emit(indent, 'globals')
                    globals_open = True
                self.emit(indent + 1, self.declaration_text(stmt, 'global'))
                continue
            close_globals()
            self.take()
            self.emit(indent, text_of(stmt).strip())
        close_globals()
        if end is not None:
            self.error(f"missing {'}' if end == '}' else end}")

    def block(self, indent, kind):
        stmt = self.take()
        self.emit(indent, text_of(stmt).strip())
        if self.peek() is not None and _is_open(self.peek()):
            self.take()
            self.items(indent + 1, kind if kind in _MEMBER_BLOCKS else 'library', '}')
        else:
            self.items(indent + 1, kind if kind in _MEMBER_BLOCKS else 'library', _BLOCKS[kind])
        self.emit(indent, _BLOCKS[kind])

    def is_function_form(self, stmt):
        """`[modifiers] type name(args)` followed by a `{` statement."""
        values = _values(stmt)
        k = 0
        while k < len(values) and values[k] in _MODIFIERS:
            k += 1
        nxt = self.peek(1)
        return (len(stmt) >= k + 4 and stmt[k].kind == 'id' and stmt[k + 1].kind == 'id'
                and values[k + 2] == '(' and values[-1] == ')' and values[k] not in _KEYWORDS
                and nxt is not None and _is_open(nxt))

    def function_form(self, indent, context):
        stmt = self.take()
        values = _values(stmt)
        k = 0
        while values[k] in _MODIFIERS:
            k += 1
        modifiers = values[:k]
        returns = values[k]
        returns = 'nothing' if returns == 'void' else returns
        name = values[k + 1]
        params = [p for p in _top_split(stmt[k + 3:-1], ',') if p]
        takes = ', '.join(text_of(p).strip() for p in params) or 'nothing'
        kind = 'method' if context in _MEMBER_BLOCKS else 'function'
        prefix = ' '.join(modifiers + [kind])
        self.take()                                            # {
        self.body(indent, f'{prefix} {name} takes {takes} returns {returns}', name,
                  'end' + kind, '}')

    def jass_function(self, indent, kind, end):
        stmt = self.take()
        values = _values(stmt)
        name = values[values.index(kind) + 1]
        header = []
        for token in stmt:
            header.append(Token(token.kind, 'nothing' if token.value == 'void' else token.value,
                                token.line, token.file))
        self.body(indent, text_of(header).strip(), name, end, end)

    def body(self, indent, header, name, end_text, end):
        outer = self.function
        self.function = _Function(name)
        start = len(self.out)
        self.statements(indent + 1, end)
        code = self.out[start:]
        del self.out[start:]
        self.emit(indent, header)
        for line in self.function.locals:
            self.emit(indent + 1, line)
        self.out.extend(code)
        self.emit(indent, end_text)
        self.function = outer

    # -- declarations ---------------------------------------------------------

    def is_declaration(self, stmt):
        values = _values(stmt)
        k = 0
        while k < len(values) and values[k] in _MODIFIERS + ('local',):
            k += 1
        if len(stmt) < k + 2 or stmt[k].kind != 'id' or values[k] in _KEYWORDS - {'local'}:
            return False
        if values[k + 1] == 'array':
            return len(stmt) > k + 2 and stmt[k + 2].kind == 'id'
        return stmt[k + 1].kind == 'id' and values[k + 1] not in _KEYWORDS and (
            len(stmt) == k + 2 or values[k + 2] in ('=', ','))

    def declaration_text(self, stmt, where):
        """A global or struct member declaration, one per statement."""
        tokens = [t for t in stmt if t.value != 'local']
        return self.expression(tokens)[1]

    def local_declaration(self, indent, stmt):
        tokens = [t for t in stmt if t.value != 'local']
        k = 0
        while tokens[k].value in _MODIFIERS:
            k += 1
        type_name = tokens[k].value
        is_array = tokens[k + 1].value == 'array'
        rest = tokens[k + 2:] if is_array else tokens[k + 1:]
        for part in _top_split(rest, ','):
            if not part:
                continue
            name = part[0].value
            kind = f'{type_name} array' if is_array else type_name
            again = name in self.function.declared
            if again and self.function.declared[name] != kind:
                self.error(f"local '{name}' is declared as {self.function.declared[name]} "
                           f'and as {kind}', stmt)
            self.function.declared[name] = kind
            if is_array:
                if not again:
                    self.function.locals.append(f'local {type_name} array {name}')
                continue
            if len(part) == 1:
                if not again:
                    self.function.locals.append(f'local {type_name} {name}')
                continue
            value = part[2:]
            literal = (len(value) == 1 and (value[0].kind in _LITERAL_KINDS
                                            or value[0].value in ('true', 'false', 'null'))) \
                or (len(value) == 2 and value[0].value == '-' and value[1].kind == 'num')
            if literal and not again:
                self.function.locals.append(
                    f'local {type_name} {name} = {text_of(value).strip()}')
                continue
            # A name declared again (in another branch, say) keeps its one
            # local, and the initializer is a set either way.
            if not again:
                self.function.locals.append(f'local {type_name} {name}')
            self.simple(indent, f'set {name} =', value)

    # -- statements -------------------------------------------------------------

    def simple(self, indent, prefix, tokens, suffix=''):
        before, text, after = self.expression(tokens)
        for line in before:
            self.emit(indent, line)
        self.emit(indent, f'{prefix} {text}{suffix}'.strip())
        for line in after:
            self.emit(indent, line)

    def statements(self, indent, end):
        """Statements until `end` ('}' or a keyword such as 'endfunction')."""
        while self.i < len(self.stmts):
            stmt = self.peek()
            values = _values(stmt)
            if end == '}' and _is_close(stmt):
                self.take()
                return
            if end != '}' and values[0] in (end if isinstance(end, tuple) else (end,)):
                return self.take() if end in ('endfunction', 'endmethod') else None
            self.statement(indent)
        self.error(f'missing {end}')

    def block_body(self, indent):
        """The body after a header: `{ ... }` or a single following statement."""
        if self.peek() is not None and _is_open(self.peek()):
            self.take()
            self.statements(indent, '}')
            return True
        return False

    def statement(self, indent):
        stmt = self.take()
        values = _values(stmt)
        if stmt[0].kind == 'pass':
            self.emit(0, stmt[0].value)
            self.textmacro(stmt[0].value)
            return
        debug = ''
        if values[0] == 'debug' and len(stmt) > 1:
            debug, stmt, values = 'debug ', stmt[1:], values[1:]
        head = values[0]
        if head == 'if':
            return self.if_statement(indent, stmt)
        if values[:2] == ['static', 'if']:
            return self.if_statement(indent, stmt[1:], prefix='static ')
        if head in ('loop', 'do') and len(stmt) == 1:
            return self.loop_statement(indent, stmt)
        if head in ('whilenot', 'while'):
            return self.whilenot_statement(indent, stmt, negate=head == 'while')
        if head == 'break' and len(stmt) == 1:
            return self.emit(indent, debug + 'exitwhen true')
        if head == 'exitwhen':
            return self.simple(indent, debug + 'exitwhen', stmt[1:])
        if head == 'return':
            if len(stmt) == 1:
                return self.emit(indent, debug + 'return')
            return self.simple(indent, debug + 'return', stmt[1:])
        if head == 'local' or (head not in ('set', 'call') and self.is_declaration(stmt)):
            return self.local_declaration(indent, stmt)
        if head in ('set', 'call'):
            stmt, values = stmt[1:], values[1:]
        if _is_open([stmt[0]]) and len(stmt) == 1:
            return self.statements(indent, '}')
        if (values[0] in ('++', '--') or values[-1] in ('++', '--')) and len(stmt) >= 2 \
                and _find_top(stmt, ('=',) + tuple(_COMPOUND)) is None:
            return self._increment(indent, debug, stmt)
        at = _find_top(stmt, ('=',) + tuple(_COMPOUND))
        if at is not None:
            target = self.expression(stmt[:at])
            op = stmt[at].value
            value = stmt[at + 1:]
            for line in target[0]:
                self.emit(indent, line)
            if op != '=':
                value = stmt[:at] + [Token('op', _COMPOUND[op])] + \
                    [Token('op', '(')] + value + [Token('op', ')')] \
                    if len(value) > 1 else stmt[:at] + [Token('op', _COMPOUND[op])] + value
            self.simple(indent, f'{debug}set {target[1]} =', value)
            return
        self.simple(indent, debug + 'call', stmt)

    def _increment(self, indent, debug, stmt):
        before, _, after = self.expression(stmt)
        for line in before + after:
            self.emit(indent, debug + line)

    def condition(self, tokens):
        if tokens and tokens[-1].value == 'then':
            tokens = tokens[:-1]
        return self.expression(tokens)

    def if_statement(self, indent, stmt, prefix=''):
        before, cond, after = self.condition(stmt[1:])
        for line in before:
            self.emit(indent, line)
        braced = self.peek() is not None and _is_open(self.peek())
        self.emit(indent, f'{prefix}if {cond} then')
        for line in after:
            self.emit(indent + 1, line)
        if braced:
            self.take()
            self.statements(indent + 1, '}')
        else:
            self.statements(indent + 1, ('elseif', 'else', 'endif'))
        while True:
            nxt = self.peek()
            if nxt is None:
                self.error('missing endif', stmt)
            values = _values(nxt)
            if values[0] == 'elseif' or values[:2] == ['else', 'if']:
                self.take()
                skip = 1 if values[0] == 'elseif' else 2
                if after:
                    self.error('a postfix ++ or -- in an if condition with elseif is not '
                               'supported', nxt)
                more_before, more_cond, more_after = self.condition(nxt[skip:])
                if more_before or more_after:
                    self.error('++ or -- in the condition of an elseif is not supported', nxt)
                self.emit(indent, f'elseif {more_cond} then')
                if not self.block_body(indent + 1):
                    self.statements(indent + 1, ('elseif', 'else', 'endif'))
                continue
            if values[0] == 'else':
                self.take()
                self.emit(indent, 'else')
                for line in after:
                    self.emit(indent + 1, line)
                after = []
                if not self.block_body(indent + 1):
                    self.statements(indent + 1, ('endif',))
                    self.take()
                break
            if values[0] == 'endif':
                self.take()
                break
            if braced:
                break
            self.error('missing endif', stmt)
        if after:
            # A postfix ++ or -- in the condition runs whichever branch is taken.
            self.emit(indent, 'else')
            for line in after:
                self.emit(indent + 1, line)
        self.emit(indent, 'endif')

    def loop_statement(self, indent, stmt):
        self.emit(indent, 'loop')
        if self.peek() is not None and _is_open(self.peek()):
            self.take()
            self.statements(indent + 1, '}')
            nxt = self.peek()
            if nxt is not None and _values(nxt)[0] in ('whilenot', 'while'):
                self.take()
                before, cond, after = self.condition(nxt[1:])
                for line in before:
                    self.emit(indent + 1, line)
                self.emit(indent + 1, f"exitwhen {'not ' if nxt[0].value == 'while' else ''}{cond}")
                for line in after:
                    self.emit(indent + 1, line)
        else:
            self.statements(indent + 1, ('endloop',))
            self.take()
        self.emit(indent, 'endloop')

    def whilenot_statement(self, indent, stmt, negate=False):
        before, cond, after = self.condition(stmt[1:])
        self.emit(indent, 'loop')
        for line in before:
            self.emit(indent + 1, line)
        self.emit(indent + 1, f"exitwhen {'not ' if negate else ''}{cond}")
        for line in after:
            self.emit(indent + 1, line)
        if not self.block_body(indent + 1):
            self.statements(indent + 1, ('endloop',))
            self.take()
        self.emit(indent, 'endloop')


def compile_statements(tokens, filename=None, debug=False):
    """JASS with vJass blocks for preprocessed `tokens`. A statement that
    starts with `debug` is kept, without the word, only when `debug` is set,
    as JassHelper does with debug lines."""
    kept = []
    for stmt in statements(tokens):
        if len(stmt) > 1 and stmt[0].value == 'debug' and stmt[0].kind == 'id':
            if debug:
                kept.append(stmt[1:])
            continue
        kept.append(stmt)
    return Compiler(kept, filename).compile()
