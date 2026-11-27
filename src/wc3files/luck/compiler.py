"""
Luck to JASS (#228).

Luck is the indentation-based language of LuckyParser (MIT): a header line
ends with a colon and its body is indented by four spaces. This compiler
follows LuckyParser's language reference (docs/LUCK_LANGUAGE.md, compiler
0.3) and lowers each construct as LuckyParser does, with the same generated
names, so JASS written against its output keeps working:

- `library`, `scope` and `module` namespaces: a function is named
  `<namespace>_<name>` unless it is `public`; top-level functions keep their
  names;
- functions as `nothing Name(...)` or `fn Name(...) -> nothing`, typed and
  grouped parameters (`real(x = 0, y = 0)`), default values and named
  arguments, filled in at each call;
- `local`, typed and inferred `let`/`var` locals, moved to the top of the
  function; `global`, `constant` and `readonly` globals, in one `globals`
  block;
- `if`/`elif`/`else`, `while`, `loop`, `foreach(i, count)`, `for i in a..b`,
  `break`, `return`, assignments, compound assignments, `++` and `--`, calls
  without `call` and assignments without `set`;
- `print(...)`, with numbers and booleans turned into strings;
- `onInit`, `onInit.priority` and `onLoad` blocks, run by
  `LuckyParser_InitAll` (priority blocks first);
- array-backed structs: fields, static and instance methods, `allocate` and
  `deallocate`;
- `handle.id` for `GetHandleId(handle)`;
- `//! language jass|vjass|zinc|cjass|luck` islands in any file.

The JASS is put in an order where every function comes after the ones it
calls, as plain JASS needs.
"""

import os
import re

from wc3files.scripts.ir import CallStmt

_MODIFIERS = {'public', 'private', 'protected', 'static', 'constant', 'readonly', 'abstract',
              'override', 'stub', 'operator', 'native', 'extern'}
_RESERVED = {'class', 'interface', 'extension', 'enum', 'event', 'define', 'comptime',
             'hook', 'hook.before', 'hook.after', 'replace'}
_NAMESPACES = ('library', 'scope', 'module')
_ALIASES = {'void': 'nothing', 'bool': 'boolean', 'int': 'integer', 'float': 'real'}
_TOKEN = re.compile(r'''
    (?P<str>"(?:\\.|[^"\\])*")
  | (?P<raw>'(?:\\.|[^'\\])*')
  | (?P<num>\d+\.\d*|\.\d+|0[xX][0-9a-fA-F]+|\$[0-9a-fA-F]+|\d+)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.|==|!=|<=|>=|\+=|-=|\*=|/=|\+\+|--|->|[-+*/%<>=!(),.\[\]:])
  | (?P<ws>\s+)
''', re.X)
_ISLAND = re.compile(r'^\s*//!\s*language\s+(\w+)\s*$')
_IMPORT = re.compile(r'^(?:import\s+"([^"]+)"|from\s+"([^"]+)"\s+import\s+(.+))$')


class LuckError(ValueError):
    def __init__(self, message, filename=None, line=None):
        where = f'{filename}:{line}: ' if filename and line else (f'{filename}: ' if filename else '')
        super().__init__(where + message)
        self.filename, self.line = filename, line


# -- parsing ------------------------------------------------------------------

class Node:
    def __init__(self, kind, line, file, **fields):
        self.kind, self.line, self.file = kind, line, file
        self.children = []
        self.__dict__.update(fields)


def _strip_comment(text):
    quote = None
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if char == '\\' and quote == '"':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in '"\'':
            quote = char
        elif char == '#' or text.startswith('//', i):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def split_top(text, separator=','):
    """`text` split at `separator` outside brackets and quotes."""
    parts, depth, quote, current = [], 0, None, ''
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            current += char
            if char == '\\' and quote == '"' and i + 1 < len(text):
                current += text[i + 1]
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in '"\'':
            quote = char
            current += char
        elif char in '([{':
            depth += 1
            current += char
        elif char in ')]}':
            depth -= 1
            current += char
        elif char == separator and depth == 0:
            parts.append(current.strip())
            current = ''
        else:
            current += char
        i += 1
    if current.strip():
        parts.append(current.strip())
    return parts


def _top_equals(text):
    """The index of a lone `=` outside brackets and quotes, or -1."""
    depth, quote = 0, None
    for i, char in enumerate(text):
        if quote:
            if char == quote and text[i - 1] != '\\':
                quote = None
            continue
        if char in '"\'':
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == '=' and depth == 0 and text[i - 1:i] not in ('=', '!', '<', '>', '+', '-',
                                                                  '*', '/') \
                and text[i + 1:i + 2] != '=':
            return i
    return -1


def _parameters(text, where):
    text = text.strip()
    if not text or text == 'nothing':
        return []
    out = []
    for part in split_top(text):
        at = _top_equals(part)
        left = (part[:at] if at >= 0 else part).strip()
        default = part[at + 1:].strip() if at >= 0 else None
        grouped = re.match(r'^([A-Za-z_]\w*\??)\s*\((.*)\)$', left)
        if grouped:
            for item in split_top(grouped.group(2)):
                item_at = _top_equals(item)
                name = (item[:item_at] if item_at >= 0 else item).strip()
                if not re.match(r'^[A-Za-z_]\w*$', name):
                    raise LuckError(f"invalid parameter '{item}'", *where)
                out.append((grouped.group(1), name,
                            item[item_at + 1:].strip() if item_at >= 0 else default))
            continue
        match = re.match(r'^([A-Za-z_]\w*(?:<[^>]+>)?\??)\s+([A-Za-z_]\w*)$', left)
        if not match:
            raise LuckError(f"expected a parameter as '<type> <name>', found '{part}'", *where)
        out.append((match.group(1), match.group(2), default))
    return out


def _function_header(content, where):
    if not content.endswith(':'):
        return None
    header = content[:-1].strip()
    modifiers = []
    while True:
        match = re.match(r'^([A-Za-z_]\w*)\b\s*', header)
        if match and match.group(1) in _MODIFIERS:
            modifiers.append(match.group(1))
            header = header[match.end():]
        else:
            break
    match = re.match(r'^(?:fn|function)\s+([A-Za-z_]\w*)\s*\((.*)\)\s*'
                     r'(?:->\s*([A-Za-z_]\w*(?:<[^>]+>)?\??))?$', header)
    if match:
        name, params, returns = match.group(1), match.group(2), match.group(3) or 'nothing'
    else:
        match = re.match(r'^([A-Za-z_]\w*(?:<[^>]+>)?\??)\s+([A-Za-z_]\w*)\s*\((.*)\)$', header)
        if not match or match.group(1) in ('if', 'elif', 'while', 'foreach'):
            return None
        returns, name, params = match.group(1), match.group(2), match.group(3)
    return Node('function', where[1], where[0], name=name, returns=returns,
                modifiers=modifiers, params=_parameters(params, where))


def _declaration(content):
    if re.match(r'^(?:return|throw|print|call|set|exitwhen|assert|break|continue)\b', content):
        return None
    match = re.match(r'^(let|var)\s+([A-Za-z_]\w*)\s*=\s*(.+)$', content)
    if match:
        return dict(storage=match.group(1), type='auto', name=match.group(2),
                    value=match.group(3).strip())
    match = re.match(r'^(?:(local|global|let|var|constant|readonly)\s+)?'
                     r'([A-Za-z_]\w*(?:<[^>]+>)?\??)(?:\s+|\s*:\s*)([A-Za-z_]\w*)'
                     r'(?:\s*=\s*(.+))?$', content)
    if not match:
        return None
    return dict(storage=match.group(1), type=match.group(2), name=match.group(3),
                value=match.group(4).strip() if match.group(4) else None)


def _header(content, where):
    node = _function_header(content, where)
    if node is not None:
        return node
    line, file = where[1], where[0]
    match = re.match(r'^(library|scope|module|struct|class|interface|extension|enum|event)\s+'
                     r'([A-Za-z_]\w*)(?:\s*\(([^)]*)\))?(?:\s+(?:extends|requires)\s+(.+?))?:$',
                     content)
    if match:
        return Node(match.group(1), line, file, name=match.group(2))
    if re.match(r'^globals\s*:$', content):
        return Node('globals', line, file)
    match = re.match(r'^(onInit|onLoad)(\.priority)?\s*:$', content)
    if match:
        return Node(match.group(1), line, file, priority=bool(match.group(2)))
    if re.match(r'^else\s*:$', content):
        return Node('else', line, file)
    match = re.match(r'^(if|elif|while)\s+(.+):$', content)
    if match:
        return Node(match.group(1), line, file, condition=match.group(2).strip())
    if re.match(r'^loop\s*:$', content):
        return Node('loop', line, file)
    match = re.match(r'^foreach\s*\((.*)\)\s*:$', content)
    if match:
        return Node('foreach', line, file, arguments=split_top(match.group(1)))
    match = re.match(r'^for\s+([A-Za-z_]\w*)\s+in\s+(.+):$', content)
    if match:
        return Node('for', line, file, variable=match.group(1), iterable=match.group(2).strip())
    match = re.match(r'^(hook(?:\.(?:before|after))?|replace|default|case\s+.+)\s*:$', content)
    if match:
        return Node('reserved', line, file, name=match.group(1).split()[0])
    match = re.match(r'^(define|comptime)\s+([A-Za-z_]\w*)\s*\((.*)\)\s*:$', content)
    if match:
        return Node(match.group(1), line, file, name=match.group(2))
    return None


def parse_luck(text, filename='source.luck', line_offset=0):
    """The tree of one Luck source (or island)."""
    root = Node('program', line_offset, filename)
    stack = [(-1, root)]
    for number, raw in enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n')):
        line = line_offset + number + 1
        expanded = raw.expandtabs(4)
        indent = len(expanded) - len(expanded.lstrip(' '))
        stripped = expanded.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith('//'):
            continue
        if indent % 4:
            raise LuckError('indentation must be a multiple of four spaces', filename, line)
        content = _strip_comment(expanded[indent:]).strip()
        if not content:
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        imported = _IMPORT.match(content)
        if imported:
            if indent:
                raise LuckError('imports must be at file scope', filename, line)
            names = [n.strip() for n in (imported.group(3) or '').split(',') if n.strip()]
            parent.children.append(Node('import', line, filename,
                                        path=imported.group(1) or imported.group(2), names=names))
            continue
        node = _header(content, (filename, line))
        if node is not None:
            node.indent = indent
            parent.children.append(node)
            stack.append((indent, node))
            continue
        if content.endswith(':'):
            raise LuckError(f"unrecognized block header '{content}'", filename, line)
        declaration = _declaration(content)
        if declaration is not None:
            parent.children.append(Node('declaration', line, filename, **declaration))
            continue
        parent.children.append(Node('statement', line, filename, text=content))
    return root


def split_islands(text, filename, language='luck'):
    """[(language, text, first line)] for the `//! language` islands of a file."""
    out, current, start = [], [], 0
    for number, line in enumerate(text.replace('\r\n', '\n').split('\n')):
        match = _ISLAND.match(line)
        if match:
            if any(l.strip() for l in current):
                out.append((language, '\n'.join(current), start))
            language = match.group(1).lower()
            if language not in ('luck', 'jass', 'vjass', 'zinc', 'cjass'):
                raise LuckError(f"unknown language '{language}'", filename, number + 1)
            current, start = [], number + 1
            continue
        current.append(line)
    if any(l.strip() for l in current):
        out.append((language, '\n'.join(current), start))
    return out


# -- symbols ------------------------------------------------------------------

class Function:
    def __init__(self, name, generated, returns, params, static=False, struct=None,
                 namespace='', node=None):
        self.name, self.generated, self.returns, self.params = name, generated, returns, params
        self.static, self.struct, self.namespace, self.node = static, struct, namespace, node


class Struct:
    def __init__(self, name, full):
        self.name, self.full = name, full
        self.fields = {}      # name -> type
        self.methods = {}     # name -> Function


def _sanitize(name):
    return re.sub(r'\W', '_', name)


class Symbols:
    def __init__(self):
        self.structs = {}
        self.functions = {}    # (namespace, name) and generated name -> Function
        self.globals = {}      # (namespace, name) -> (generated, type, node)
        self.global_order = []

    def map_type(self, type_name):
        name = (type_name or 'nothing').rstrip('?')
        name = _ALIASES.get(name, name)
        if name in self.structs or re.match(r'^\w+<.*>$', name):
            return 'integer'
        return name

    def function(self, name, namespace):
        return self.functions.get((namespace, name)) or self.functions.get(('', name)) \
            or self.functions.get(name)

    def global_name(self, name, namespace):
        found = self.globals.get((namespace, name)) or self.globals.get(('', name))
        return found


def collect(programs):
    symbols = Symbols()

    def visit(node, namespace, struct):
        for child in node.children:
            if child.kind in _NAMESPACES:
                visit(child, f'{namespace}_{child.name}' if namespace else child.name, None)
            elif child.kind == 'struct':
                full = f'{namespace}_{child.name}' if namespace else child.name
                info = Struct(child.name, full)
                symbols.structs[child.name] = info
                symbols.structs[full] = info
                visit(child, namespace, info)
            elif child.kind == 'function':
                public = 'public' in child.modifiers or not namespace
                if struct is not None:
                    generated = f'{_sanitize(struct.full)}_{child.name}'
                else:
                    generated = child.name if public else f'{namespace}_{child.name}'
                info = Function(child.name, generated, child.returns, child.params,
                                static='static' in child.modifiers, struct=struct,
                                namespace=namespace, node=child)
                if struct is not None:
                    struct.methods[child.name] = info
                else:
                    key = ('' if public else namespace, child.name)
                    symbols.functions[key] = info
                    if namespace:
                        symbols.functions[(namespace, child.name)] = info
                symbols.functions[generated] = info
            elif child.kind == 'declaration':
                if struct is not None and child.storage is None:
                    struct.fields[child.name] = child.type
                elif child.storage in ('global', 'constant', 'readonly'):
                    generated = f'{namespace}_{child.name}' if namespace else child.name
                    if (namespace, child.name) not in symbols.globals:
                        symbols.globals[(namespace, child.name)] = (generated, child.type, child)
                        symbols.global_order.append((namespace, child.name))
            elif child.kind == 'globals':
                visit(child, namespace, struct)

    for program in programs:
        visit(program, '', None)
    return symbols


def add_foreign(symbols, text):
    """Functions of a JASS-family island, for calls with named arguments."""
    for line in text.split('\n'):
        match = re.match(r'^\s*(?:(?:private|public|static|constant)\s+)*(?:function|native)\s+'
                         r'([A-Za-z_]\w*)\s+takes\s+(.+?)\s+returns\s+([A-Za-z_]\w*)', line)
        if not match or (match.group(1) in symbols.functions):
            continue
        params = []
        if match.group(2).strip() != 'nothing':
            for n, part in enumerate(match.group(2).split(',')):
                pieces = part.split()
                params.append((pieces[0], pieces[1] if len(pieces) > 1 else f'arg{n + 1}', None))
        info = Function(match.group(1), match.group(1), match.group(3), params)
        symbols.functions.setdefault(('', match.group(1)), info)
        symbols.functions.setdefault(match.group(1), info)


# -- emitting -----------------------------------------------------------------

def _tokens(text):
    out, pos = [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            raise LuckError(f'unexpected character {text[pos]!r} in {text!r}')
        if match.lastgroup != 'ws':
            out.append((match.lastgroup, match.group(0)))
        pos = match.end()
    return out


def _join(tokens):
    out = []
    for n, (kind, value) in enumerate(tokens):
        if n:
            previous_kind, previous = tokens[n - 1]
            glue = (value in (',', ')', ']') or previous in ('(', '[')
                    or (value in ('(', '[') and previous_kind == 'id'
                        and previous not in ('and', 'or', 'not', 'return'))
                    or (previous == '-' and (n == 1 or tokens[n - 2][1] in
                                             ('(', ',', '=', '==', '!=', '<', '>', '<=', '>=',
                                              '+', '-', '*', '/', 'and', 'or', 'not',
                                              'return'))))
            if not glue:
                out.append(' ')
            if value == ',':
                out.append(value)
                continue
        out.append(value)
    return ''.join(out).replace(',', ', ').replace(',  ', ', ')


def _matching(tokens, i):
    depth = 0
    for k in range(i, len(tokens)):
        if tokens[k][1] in ('(', '['):
            depth += 1
        elif tokens[k][1] in (')', ']'):
            depth -= 1
            if depth == 0:
                return k
    raise LuckError('unbalanced brackets in ' + _join(tokens))


def _split_args(tokens):
    args, current, depth = [], [], 0
    for token in tokens:
        if token[1] in ('(', '['):
            depth += 1
        elif token[1] in (')', ']'):
            depth -= 1
        if token[1] == ',' and depth == 0:
            args.append(current)
            current = []
        else:
            current.append(token)
    if current or args:
        args.append(current)
    return args


def _default_for(type_name):
    return {'boolean': 'false', 'integer': '0', 'real': '0.0', 'string': '""',
            'nothing': None}.get(type_name, 'null')


class Context:
    def __init__(self, namespace='', struct=None, function=None, locals_=None, loops=0):
        self.namespace, self.struct, self.function = namespace, struct, function
        self.locals = dict(locals_ or {})
        self.loops = loops

    def child(self, **changes):
        made = Context(self.namespace, self.struct, self.function, self.locals, self.loops)
        made.__dict__.update(changes)
        return made


class Emitter:
    def __init__(self, symbols):
        from wc3files.scripts.checker import NativeSignatures
        self.symbols = symbols
        self.natives = NativeSignatures.default()
        self.globals = []
        self.functions = []    # [(name, [lines])]
        self.inits = []        # [(name, priority)]
        self.warnings = []
        self.lines = None
        self.depth = 0
        self.need_b2s = False

    def warn(self, message, node):
        self.warnings.append(f'{node.file}:{node.line}: {message}')

    def emit(self, text):
        self.lines.append('    ' * self.depth + text)

    # -- expressions --------------------------------------------------------

    def owner_struct(self, owner, ctx):
        if owner == 'this' and ctx.struct is not None:
            return ctx.struct
        type_name = ctx.locals.get(owner)
        return self.symbols.structs.get(type_name) if type_name else None

    def expr(self, text, ctx):
        return self.rewrite(_tokens(text), ctx)

    def rewrite(self, tokens, ctx):
        out = []
        i = 0
        while i < len(tokens):
            kind, value = tokens[i]
            if kind == 'id' and i + 2 < len(tokens) and tokens[i + 1][1] == '.' \
                    and tokens[i + 2][0] == 'id' and (not out or out[-1][1] != '.'):
                member = tokens[i + 2][1]
                after = i + 3
                if after < len(tokens) and tokens[after][1] == '(':
                    close = _matching(tokens, after)
                    args = _split_args(tokens[after + 1:close])
                    out.extend(self.member_call(value, member, args, ctx))
                    i = close + 1
                    continue
                struct = self.owner_struct(value, ctx)
                if struct is not None and member in struct.fields:
                    out.extend([('id', f'{_sanitize(struct.full)}_{member}'), ('op', '['),
                                ('id', value), ('op', ']')])
                elif member == 'id':
                    out.extend([('id', 'GetHandleId'), ('op', '('), ('id', self.name(value, ctx)),
                                ('op', ')')])
                else:
                    out.extend([('id', self.name(value, ctx)), ('op', '.'), ('id', member)])
                i = after
                continue
            if kind == 'id' and i + 1 < len(tokens) and tokens[i + 1][1] == '(' \
                    and (not out or out[-1][1] != '.'):
                close = _matching(tokens, i + 1)
                args = _split_args(tokens[i + 2:close])
                function = self.symbols.function(value, ctx.namespace)
                if ctx.struct is not None and value in ctx.struct.methods and function is None:
                    function = ctx.struct.methods[value]
                out.extend(self.call(function, value, args, ctx))
                i = close + 1
                continue
            if kind == 'id':
                out.append(('id', self.name(value, ctx)))
            else:
                out.append((kind, value))
            i += 1
        return _join(out)

    def name(self, value, ctx):
        """A bare name: a namespace global gets its generated name."""
        if value in ctx.locals:
            return value
        found = self.symbols.global_name(value, ctx.namespace)
        return found[0] if found else value

    def member_call(self, owner, member, args, ctx):
        struct = self.owner_struct(owner, ctx)
        if struct is not None and member in struct.methods and not struct.methods[member].static:
            return self.call(struct.methods[member], member, args, ctx, this=owner)
        static = self.symbols.structs.get(owner)
        if static is not None:
            prefix = _sanitize(static.full)
            if member == 'allocate':
                return [('id', f'{prefix}_allocate'), ('op', '('), ('op', ')')]
            if member == 'deallocate':
                return self.call(Function('deallocate', f'{prefix}_deallocate', 'nothing',
                                          [('integer', 'this', None)]), member, args, ctx)
            if member in static.methods:
                return self.call(static.methods[member], member, args, ctx)
        text = f'{owner}.{member}(' + ', '.join(self.rewrite(a, ctx) for a in args) + ')'
        return [('id', text)]

    def call(self, function, name, args, ctx, this=None):
        if function is None:
            text = ', '.join(self.rewrite(a, ctx) for a in args)
            return [('id', f'{name}({text})')]
        params = list(function.params)
        if function.struct is not None and not function.static:
            params.insert(0, ('integer', 'this', None))
            if this is not None:
                args = [[('id', this)]] + args
        positional, named = [], {}
        for arg in args:
            if len(arg) >= 2 and arg[0][0] == 'id' and arg[1][1] == '=':
                named[arg[0][1]] = arg[2:]
            else:
                positional.append(arg)
        values = []
        for n, (type_name, param, default) in enumerate(params):
            if n < len(positional):
                values.append(self.rewrite(positional[n], ctx))
            elif param in named:
                values.append(self.rewrite(named.pop(param), ctx))
            elif default is not None:
                values.append(self.expr(default, ctx))
            else:
                fallback = _default_for(self.symbols.map_type(type_name))
                values.append(fallback if fallback is not None else '0')
        values.extend(self.rewrite(a, ctx) for a in positional[len(params):])
        if named:
            raise LuckError(f"'{name}' has no parameter {', '.join(sorted(named))}")
        return [('id', f"{function.generated}({', '.join(values)})")]

    # -- types, for print -----------------------------------------------------

    def type_of(self, tokens, ctx):
        while tokens and tokens[0][1] == '(' and _matching(tokens, 0) == len(tokens) - 1:
            tokens = tokens[1:-1]
        parts = _split_plus(tokens)
        if len(parts) > 1:
            types = [self.type_of(p, ctx) for p in parts]
            if 'string' in types:
                return 'string'
            if 'real' in types:
                return 'real'
            if all(t == 'integer' for t in types):
                return 'integer'
        if not tokens:
            return 'string'
        if any(t[1] in ('==', '!=', '<', '>', '<=', '>=', 'and', 'or', 'not') for t in tokens):
            return 'boolean'
        if len(tokens) == 1:
            kind, value = tokens[0]
            if kind == 'str':
                return 'string'
            if kind == 'raw':
                return 'integer'
            if kind == 'num':
                return 'real' if '.' in value else 'integer'
            if value in ('true', 'false'):
                return 'boolean'
            if value in ctx.locals:
                return self.symbols.map_type(ctx.locals[value])
            found = self.symbols.global_name(value, ctx.namespace)
            if found:
                return self.symbols.map_type(found[1])
        if len(tokens) == 2 and tokens[0][1] == '-' and tokens[1][0] == 'num':
            return 'real' if '.' in tokens[1][1] else 'integer'
        if len(tokens) >= 3 and tokens[0][0] == 'id' and tokens[1][1] == '.':
            owner, member = tokens[0][1], tokens[2][1]
            if member == 'id' and len(tokens) == 3:
                return 'integer'
            struct = self.owner_struct(owner, ctx)
            if struct is not None:
                if len(tokens) == 3 and member in struct.fields:
                    return self.symbols.map_type(struct.fields[member])
                if member in struct.methods:
                    return self.symbols.map_type(struct.methods[member].returns)
            static = self.symbols.structs.get(owner)
            if static is not None and member in static.methods:
                return self.symbols.map_type(static.methods[member].returns)
        if tokens[0][0] == 'id' and len(tokens) > 1 and tokens[1][1] == '(' \
                and _matching(tokens, 1) == len(tokens) - 1:
            function = self.symbols.function(tokens[0][1], ctx.namespace)
            if function is not None:
                return self.symbols.map_type(function.returns)
            native = self.natives.lookup(tokens[0][1])
            if native is not None:
                return native.returns
        if any(t[1] in ('*', '/', '-') for t in tokens):
            if any(t[0] == 'num' and '.' in t[1] for t in tokens):
                return 'real'
            if any(t[0] == 'id' and self.symbols.map_type(ctx.locals.get(t[1], '')) == 'real'
                   for t in tokens):
                return 'real'
            return 'integer'
        return 'string'

    def to_string(self, tokens, ctx):
        text = self.rewrite(tokens, ctx)
        kind = self.type_of(tokens, ctx)
        if kind == 'integer':
            return f'I2S({text})'
        if kind == 'real':
            return f'R2S({text})'
        if kind == 'boolean':
            self.need_b2s = True
            return f'LuckyParser_B2S({text})'
        return text

    def print_text(self, text, ctx):
        tokens = _tokens(text)
        parts = _split_plus(tokens)
        if self.type_of(tokens, ctx) != 'string' or len(parts) == 1:
            return self.to_string(tokens, ctx)
        return ' + '.join(self.to_string(p, ctx) for p in parts)

    # -- declarations ---------------------------------------------------------

    def program(self, programs):
        for key in self.symbols.global_order:
            generated, type_name, node = self.symbols.globals[key]
            ctx = Context(namespace=key[0])
            constant = 'constant ' if node.storage == 'constant' else ''
            value = f' = {self.expr(node.value, ctx)}' if node.value else ''
            self.globals.append(f'{constant}{self.symbols.map_type(type_name)} {generated}{value}')
        self.struct_runtime()
        for program in programs:
            self.items(program, Context())
        if self.inits:
            lines = [f'    call {name}()' for name, priority in sorted(
                self.inits, key=lambda i: not i[1])]
            self.functions.append(('LuckyParser_InitAll',
                                   ['function LuckyParser_InitAll takes nothing returns nothing']
                                   + lines + ['endfunction']))
        if self.need_b2s:
            self.functions.append(('LuckyParser_B2S', [
                'function LuckyParser_B2S takes boolean value returns string',
                '    if value then', '        return "true"', '    endif',
                '    return "false"', 'endfunction']))

    def struct_runtime(self):
        done = set()
        for struct in self.symbols.structs.values():
            if struct.full in done:
                continue
            done.add(struct.full)
            prefix = _sanitize(struct.full)
            self.globals += [f'integer {prefix}__next = 0', f'integer {prefix}__freeTop = 0',
                             f'integer array {prefix}__free', f'boolean array {prefix}__alive']
            for field, type_name in struct.fields.items():
                self.globals.append(f'{self.symbols.map_type(type_name)} array {prefix}_{field}')
            self.functions.append((f'{prefix}_allocate', [
                f'function {prefix}_allocate takes nothing returns integer',
                '    local integer this',
                f'    if {prefix}__freeTop > 0 then',
                f'        set {prefix}__freeTop = {prefix}__freeTop - 1',
                f'        set this = {prefix}__free[{prefix}__freeTop]',
                '    else',
                f'        set {prefix}__next = {prefix}__next + 1',
                f'        set this = {prefix}__next',
                '    endif',
                f'    set {prefix}__alive[this] = true',
                '    return this',
                'endfunction']))
            reset = []
            for field, type_name in struct.fields.items():
                value = _default_for(self.symbols.map_type(type_name))
                if value is not None:
                    reset.append(f'    set {prefix}_{field}[this] = {value}')
            self.functions.append((f'{prefix}_deallocate', [
                f'function {prefix}_deallocate takes integer this returns nothing',
                f'    if not {prefix}__alive[this] then',
                f'        call BJDebugMsg("LuckyParser: double destroy of {struct.name} #" + I2S(this))',
                '        return',
                '    endif',
                f'    set {prefix}__alive[this] = false'] + reset + [
                f'    set {prefix}__free[{prefix}__freeTop] = this',
                f'    set {prefix}__freeTop = {prefix}__freeTop + 1',
                'endfunction']))

    def items(self, node, ctx):
        for child in node.children:
            if child.kind in _NAMESPACES:
                namespace = f'{ctx.namespace}_{child.name}' if ctx.namespace else child.name
                self.items(child, ctx.child(namespace=namespace))
            elif child.kind == 'struct':
                struct = self.symbols.structs.get(f'{ctx.namespace}_{child.name}'
                                                  if ctx.namespace else child.name)
                for member in child.children:
                    if member.kind == 'function':
                        self.function(member, struct.methods[member.name], ctx.child(struct=struct))
            elif child.kind == 'function':
                public = 'public' in child.modifiers or not ctx.namespace
                info = self.symbols.functions[('' if public else ctx.namespace, child.name)]
                self.function(child, info, ctx)
            elif child.kind in ('onInit', 'onLoad'):
                prefix = f'{ctx.namespace}_' if ctx.namespace else ''
                name = f'{prefix}{child.kind}_{len(self.inits) + 1}'
                self.inits.append((name, child.priority))
                self.body(child, name, [], 'nothing', f'function {name} takes nothing returns nothing',
                          ctx)
            elif child.kind in _RESERVED or child.kind == 'reserved':
                self.warn(f'{getattr(child, "name", child.kind)}: {child.kind} is reserved in Luck '
                          'and not compiled', child)
            elif child.kind == 'statement':
                raise LuckError(f"a statement outside any function: '{child.text}'",
                                child.file, child.line)

    def function(self, node, info, ctx):
        params = list(node.params)
        if info.struct is not None and not info.static:
            params.insert(0, ('integer', 'this', None))
        takes = ', '.join(f'{self.symbols.map_type(t)} {n}' for t, n, _ in params) or 'nothing'
        returns = self.symbols.map_type(node.returns)
        header = f'function {info.generated} takes {takes} returns {returns}'
        self.body(node, info.generated, params, returns, header, ctx.child(function=info))

    def body(self, node, name, params, returns, header, ctx):
        locals_ = {n: t for t, n, _ in params}
        if ctx.struct is not None:
            locals_.setdefault('this', ctx.struct.name)
        declared = []

        def collect_locals(nodes):
            for child in nodes:
                if child.kind == 'function':
                    continue
                if child.kind == 'declaration' and child.storage not in ('global', 'constant',
                                                                         'readonly'):
                    add(child.name, child.type, child)
                elif child.kind == 'foreach':
                    add(child.arguments[0] if child.arguments else 'i', 'integer', child)
                elif child.kind == 'for':
                    add(child.variable, 'integer', child)
                collect_locals(child.children)

        def add(local, type_name, child):
            if local in locals_ and local != 'this':
                raise LuckError(f"local '{local}' is declared more than once in the function",
                                child.file, child.line)
            if type_name == 'auto':
                type_name = self.type_of(_tokens(child.value), ctx.child(locals=locals_))
            locals_[local] = type_name
            declared.append((local, type_name))

        collect_locals(node.children)
        ctx = ctx.child(locals=locals_, loops=0)
        self.lines, self.depth = [header], 1
        for local, type_name in declared:
            self.emit(f'local {self.symbols.map_type(type_name)} {local}')
        self.statements(node.children, ctx)
        if returns != 'nothing' and not any(c.kind == 'statement' and re.match(r'^return\b', c.text)
                                            for c in node.children):
            self.warn(f"'{name}' may not return a value on every path", node)
            fallback = _default_for(returns)
            self.emit(f'return {fallback}')
        self.depth = 0
        self.emit('endfunction')
        self.functions.append((name, self.lines))

    # -- statements -----------------------------------------------------------

    def statements(self, nodes, ctx):
        i = 0
        while i < len(nodes):
            node = nodes[i]
            if node.kind == 'declaration':
                if node.value and node.storage not in ('global', 'constant', 'readonly'):
                    self.emit(f'set {node.name} = {self.expr(node.value, ctx)}')
            elif node.kind == 'statement':
                self.statement(node, ctx)
            elif node.kind == 'if':
                self.emit(f'if {self.expr(node.condition, ctx)} then')
                self.block(node.children, ctx)
                while i + 1 < len(nodes) and nodes[i + 1].kind == 'elif':
                    i += 1
                    self.emit(f'elseif {self.expr(nodes[i].condition, ctx)} then')
                    self.block(nodes[i].children, ctx)
                if i + 1 < len(nodes) and nodes[i + 1].kind == 'else':
                    i += 1
                    self.emit('else')
                    self.block(nodes[i].children, ctx)
                self.emit('endif')
            elif node.kind in ('elif', 'else'):
                raise LuckError(f'{node.kind} without an if before it', node.file, node.line)
            elif node.kind == 'while':
                self.emit('loop')
                self.depth += 1
                self.emit(f'exitwhen not ({self.expr(node.condition, ctx)})')
                self.statements(node.children, ctx.child(loops=ctx.loops + 1))
                self.depth -= 1
                self.emit('endloop')
            elif node.kind == 'loop':
                self.emit('loop')
                self.block(node.children, ctx.child(loops=ctx.loops + 1))
                self.emit('endloop')
            elif node.kind == 'foreach':
                variable = node.arguments[0] if node.arguments else 'i'
                count = node.arguments[1] if len(node.arguments) > 1 else '0'
                self.emit(f'set {variable} = 0')
                self.counted(variable, f'{variable} >= {self.expr(count, ctx)}', node, ctx)
            elif node.kind == 'for':
                bounds = node.iterable.split('..')
                if len(bounds) != 2:
                    raise LuckError('for ... in takes an integer range such as 0..15',
                                    node.file, node.line)
                self.emit(f'set {node.variable} = {self.expr(bounds[0], ctx)}')
                self.counted(node.variable, f'{node.variable} > {self.expr(bounds[1], ctx)}',
                             node, ctx)
            else:
                raise LuckError(f"'{node.kind}' cannot be used inside a function",
                                node.file, node.line)
            i += 1

    def block(self, nodes, ctx):
        self.depth += 1
        self.statements(nodes, ctx)
        self.depth -= 1

    def counted(self, variable, stop, node, ctx):
        self.emit('loop')
        self.depth += 1
        self.emit(f'exitwhen {stop}')
        self.statements(node.children, ctx.child(loops=ctx.loops + 1))
        self.emit(f'set {variable} = {variable} + 1')
        self.depth -= 1
        self.emit('endloop')

    def statement(self, node, ctx):
        text = node.text.strip().rstrip(';').strip()
        if text in ('', 'pass'):
            return
        if text == 'break':
            if not ctx.loops:
                raise LuckError('break can only appear inside a loop', node.file, node.line)
            return self.emit('exitwhen true')
        if text == 'continue':
            raise LuckError('continue is not supported (LuckyParser does not lower it either)',
                            node.file, node.line)
        if re.match(r'^return\b', text):
            value = text[len('return'):].strip()
            return self.emit(f'return {self.expr(value, ctx)}' if value else 'return')
        if re.match(r'^throw\b', text):
            self.emit(f'call BJDebugMsg({self.expr(text[5:].strip(), ctx)})')
            return self.emit('return')
        match = re.match(r'^print\s*\((.*)\)$', text)
        if match:
            return self.emit(f'call BJDebugMsg({self.print_text(match.group(1), ctx)})')
        for keyword in ('call ', 'set '):
            if text.startswith(keyword):
                text = text[len(keyword):].strip()
        match = re.match(r'^(.+?)\s*(\+\+|--)$', text)
        if match and _is_target(match.group(1)):
            target = self.expr(match.group(1), ctx)
            return self.emit(f"set {target} = {target} {match.group(2)[0]} 1")
        match = re.match(r'^(.+?)\s*(\+=|-=|\*=|/=)\s*(.+)$', text)
        if match and _is_target(match.group(1)):
            target = self.expr(match.group(1), ctx)
            value = self.expr(match.group(3), ctx)
            if len(_tokens(match.group(3))) > 1:
                value = f'({value})'
            return self.emit(f'set {target} = {target} {match.group(2)[0]} {value}')
        at = _top_equals(text)
        if at > 0 and _is_target(text[:at].strip()):
            return self.emit(f'set {self.expr(text[:at].strip(), ctx)} = '
                             f'{self.expr(text[at + 1:].strip(), ctx)}')
        if re.match(r'^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(.*\)$', text):
            return self.emit(f'call {self.expr(text, ctx)}')
        raise LuckError(f"could not compile the statement '{text}'", node.file, node.line)


def _is_target(text):
    return re.match(r'^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:\[.+\])?$', text.strip()) is not None


def _split_plus(tokens):
    parts, current, depth = [], [], 0
    for token in tokens:
        if token[1] in ('(', '['):
            depth += 1
        elif token[1] in (')', ']'):
            depth -= 1
        if token[1] == '+' and depth == 0 and current:
            parts.append(current)
            current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    return parts


# -- the whole program --------------------------------------------------------

class LuckResult:
    def __init__(self, jass, init_function, warnings):
        self.jass, self.init_function, self.warnings = jass, init_function, warnings


def compile_sources(sources, debug=False):
    """`sources` is [(file name, text)]; files ending .luck start in Luck, the
    others in the language of their extension. Returns a LuckResult."""
    programs, islands = [], []
    extension_language = {'.luck': 'luck', '.j': 'jass', '.jass': 'jass', '.vj': 'vjass',
                          '.vjass': 'vjass', '.zn': 'zinc', '.zinc': 'zinc', '.cj': 'cjass',
                          '.cjass': 'cjass'}
    for filename, text in sources:
        language = extension_language.get(os.path.splitext(filename)[1].lower(), 'luck')
        for island_language, island_text, start in split_islands(text, filename, language):
            if island_language == 'luck':
                programs.append(parse_luck(island_text, filename, start))
            else:
                islands.append((island_language, island_text, filename))
    symbols = collect(programs)
    plain = []
    vjass = []
    for language, text, filename in islands:
        if language == 'cjass':
            from wc3files.cjass import cjass_to_vjass
            text = cjass_to_vjass(text, filename, debug=debug)
            language = 'vjass'
        if language == 'zinc':
            text = f'//! zinc\n{text}\n//! endzinc'
            language = 'vjass'
        add_foreign(symbols, text)
        (vjass if language == 'vjass' else plain).append(text)
    emitter = Emitter(symbols)
    emitter.program(programs)
    parts = []
    if emitter.globals:
        parts.append('globals\n' + '\n'.join('    ' + g for g in emitter.globals) + '\nendglobals')
    parts.extend('\n'.join(lines) for _, lines in emitter.functions)
    parts.extend(plain)
    text = '\n\n'.join(parts) + '\n'
    if vjass:
        from wc3files.vjass import VJassCompiler
        text = VJassCompiler(debug=debug).compile(text + '\n'.join(vjass), '<luck>').jass
    jass = normalize(text)
    return LuckResult(jass, 'LuckyParser_InitAll' if emitter.inits else None, emitter.warnings)


def normalize(text, extra=None):
    """Plain JASS with one globals block and every function after the ones it
    calls. Raises LuckError for functions that call each other in a cycle."""
    from wc3files.scripts.jass_parser import parse
    from wc3files.scripts.jass_printer import print_program
    program = parse(text)
    if extra is not None:
        program.globals.extend(extra.globals)
        program.functions.extend(extra.functions)
        program.natives.extend(extra.natives)
        program.type_defs.extend(extra.type_defs)
    program.functions = order_functions(program.functions)
    return print_program(program)


def order_functions(functions):
    """`functions` (ir.FunctionDecl) with each after the functions it calls
    or names with `function X`, keeping the given order otherwise."""
    from wc3files.scripts import ir
    from wc3files.scripts.leaks import _expressions
    by_name = {f.name: f for f in functions}
    needs = {}
    for function in functions:
        found = []
        for node in _expressions(function.body):
            if isinstance(node, (ir.CallExpr, ir.CallStmt, ir.FunctionRefExpr)) \
                    and node.name in by_name and node.name != function.name:
                found.append(node.name)
        needs[function.name] = list(dict.fromkeys(found))
    ordered, state = [], {}

    def place(name, path):
        if state.get(name) == 'done':
            return
        if state.get(name) == 'busy':
            cycle = path[path.index(name):] + [name]
            raise LuckError('functions that call each other cannot be ordered in plain JASS: '
                            + ' -> '.join(cycle))
        state[name] = 'busy'
        for other in needs[name]:
            place(other, path + [name])
        state[name] = 'done'
        ordered.append(by_name[name])

    for function in functions:
        place(function.name, [])
    return ordered


def merge_into_script(script, result):
    """A map's war3map.j with the compiled Luck in it and, when the program has
    init blocks, a call to LuckyParser_InitAll at the end of main."""
    from wc3files.scripts.jass_parser import parse
    from wc3files.scripts.jass_printer import print_program
    base = parse(script)
    extra = parse(result.jass)
    names = {f.name for f in base.functions}
    clash = sorted(names & {f.name for f in extra.functions})
    if clash:
        raise LuckError('the map script already has ' + ', '.join(clash[:5]))
    base.globals.extend(extra.globals)
    base.functions.extend(extra.functions)
    if result.init_function:
        main = next((f for f in base.functions if f.name == 'main'), None)
        if main is None:
            raise LuckError('the map script has no main to call the init blocks from')
        if not any(isinstance(s, CallStmt) and s.name == result.init_function for s in main.body):
            main.body.append(CallStmt(result.init_function, []))
    base.functions = order_functions(base.functions)
    return print_program(base)
