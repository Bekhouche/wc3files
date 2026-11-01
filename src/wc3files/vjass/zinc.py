"""Zinc: Vexorian's brace-style language, compiled by translating it to vJass.

JassHelper does exactly this - Zinc is turned into vJass text and compiled
from there - so this module parses Zinc and writes vJass lines, keeping each
line's original file and line number for error messages. Everything vJass
has (modules, interfaces, function interfaces, static if, ...) then works for
Zinc too, and a Zinc struct can implement a vJass module or the other way
round.

Semantics follow JassHelper's `zincparser.pas`:

* a library's members are private unless marked `public` (or inside a
  `public { }` block); struct, interface and module members are public by
  default;
* a library function called `onInit` is the library's initializer;
* `for (a <= i < b)` counts up, `for (a > i >= b)` counts down; `for (init;
  cond; step)`, `while`, `do ... while` and `break` map to `loop`/`exitwhen`;
* anonymous functions (`function() { ... }`, and `method`/`static method`
  inside structs) become private functions/methods named `anon__N`.

Two places deliberately differ from JassHelper's output, to keep what the
Zinc source means: compound assignment parenthesizes its right side
(`x *= a + b` is `set x = x * (a + b)`, where JassHelper writes `x*a+b`), and
`!` parenthesizes its operand.
"""
from .errors import SourceLine, VJassError
from .lexer import tokenize

# vJass output precedence, matching parser.py: or < and < not < comparison <
# additive < multiplicative < unary minus.
_PREC = {'or': 1, 'and': 2, '==': 4, '!=': 4, '<': 4, '>': 4, '<=': 4, '>=': 4,
         '+': 5, '-': 5, '*': 6, '/': 6}


class ZExpr(object):
    """Zinc expression tree node: kind + parts."""
    __slots__ = ('kind', 'a', 'b', 'c')

    def __init__(self, kind, a=None, b=None, c=None):
        self.kind = kind
        self.a = a
        self.b = b
        self.c = c


class Writer(object):
    def __init__(self):
        self.lines = []

    def emit(self, text, token, indent=0):
        line = token.line if token is not None else 0
        file = token.file if token is not None else None
        self.lines.append(SourceLine('    ' * indent + text, file, line))


class ZincTranslator(object):

    def __init__(self, tokens, options, counter):
        self.tokens = tokens
        self.pos = 0
        self.options = options
        self.counter = counter          # shared anonymous-function counter
        self.out = Writer()
        self.hoisted = None             # where anonymous functions go
        self.in_struct = False

    # ------------------------------------------------------------------ tokens

    def peek(self, offset=0):
        index = self.pos + offset
        if index >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[index]

    def next(self):
        token = self.tokens[self.pos]
        if token.kind != 'EOF':
            self.pos += 1
        return token

    def error(self, message, token=None):
        token = token or self.peek()
        got = token.raw or token.kind
        if token.kind == 'EOF':
            got = 'end of file'
        return VJassError(f'{message} (got {got!r})', token.location, 'parse')

    def at(self, *values):
        token = self.peek()
        return (token.kind == 'OP' or token.kind == 'ID') and token.value in values

    def accept(self, *values):
        if self.at(*values):
            return self.next()
        return None

    def expect(self, value):
        if not self.at(value):
            raise self.error(f'Expected "{value}"')
        return self.next()

    def name(self, what='a name'):
        token = self.peek()
        if token.kind != 'ID':
            raise self.error(f'Expected {what}')
        return self.next()

    # ------------------------------------------------------------------ top

    def translate(self):
        while self.peek().kind != 'EOF':
            token = self.peek()
            if token.kind == 'DIRECTIVE':
                self.next()
                self.out.emit(f'//! {token.value} {token.raw}', token)
                continue
            if token.is_word('library'):
                self.library()
                continue
            raise self.error('Expected a library (Zinc code lives in '
                             'libraries)')
        return self.out.lines

    def library(self):
        start = self.next()
        name = self.name('a library name')
        requires = []
        if self.accept('requires'):
            while True:
                optional = bool(self.accept('optional'))
                if self.accept('*'):
                    raise self.error('Unexpected: "*"')
                req = self.name('a library name')
                requires.append(('optional ' if optional else '') + req.value)
                if not self.accept(','):
                    break
        brace = self.expect('{')
        # Is there an onInit at library level? It is the initializer.
        body_start = self.pos
        has_init = self._library_has_oninit()
        self.pos = body_start
        header = f'library {name.value}'
        if has_init:
            header += ' initializer onInit'
        if requires:
            header += ' requires ' + ', '.join(requires)
        self.out.emit(header, start)
        self.library_members('private', 1, '}')
        end = self.expect('}')
        self.out.emit('endlibrary', end)

    def _library_has_oninit(self):
        depth = 0
        while self.peek().kind != 'EOF':
            token = self.next()
            if token.is_op('{'):
                depth += 1
            elif token.is_op('}'):
                if depth == 0:
                    return False
                depth -= 1
            elif depth <= 1 and token.is_word('function') and \
                    self.peek().is_word('onInit') and self.peek(1).is_op('('):
                # depth 1 is a public/private block.
                return True
        return False

    def library_members(self, access, indent, closing):
        while not self.at(closing) and self.peek().kind != 'EOF':
            token = self.peek()
            if token.kind == 'DIRECTIVE':
                self.next()
                self.out.emit(f'//! {token.value} {token.raw}', token, indent)
                continue
            member_access = access
            if self.at('private', 'public'):
                member_access = self.next().value
                if self.at('{'):
                    self.next()
                    self.library_members(member_access, indent, '}')
                    self.expect('}')
                    continue
            if self.at('function'):
                self.function(member_access, indent)
            elif self.at('struct'):
                self.struct(member_access, indent)
            elif self.at('module'):
                self.module(member_access, indent)
            elif self.at('interface'):
                self.interface(member_access, indent)
            elif self.at('type'):
                self.typedef(member_access, indent)
            else:
                self.global_variables(member_access, indent)

    @staticmethod
    def prefix(access, default):
        """The vJass access keyword. Zinc's `public` means "not private":
        JassHelper writes no keyword for it, so a public library function
        keeps its plain name rather than becoming `Library_name`."""
        return 'private ' if access == 'private' else ''

    # ------------------------------------------------------------------ globals

    def variable_list(self, allow_sizes=True):
        """`a`, `a = 1`, `a[]`, `a[10]`, `a[10][20]`, comma separated."""
        items = []
        while True:
            name = self.name('a variable name')
            sizes = None
            init = None
            if self.accept('['):
                sizes = []
                if not self.at(']'):
                    sizes.append(self.expr_text())
                self.expect(']')
                if self.accept('['):
                    sizes.append(self.expr_text())
                    self.expect(']')
            elif self.accept('='):
                init = self.expr_text()
            items.append((name, sizes, init))
            if not self.accept(','):
                break
        self.expect(';')
        return items

    def global_variables(self, access, indent):
        start = self.peek()
        constant = 'constant ' if self.accept('constant') else ''
        type_token = self.name('a type')
        for name, sizes, init in self.variable_list():
            text = self.prefix(access, 'library') + constant + type_token.value
            if sizes is not None:
                text += ' array ' + name.value + ''.join(f'[{s}]' for s in sizes)
            else:
                text += ' ' + name.value
                if init is not None:
                    text += ' = ' + init
            self.out.emit('globals', start, indent)
            self.out.emit(text, name, indent + 1)
            self.out.emit('endglobals', start, indent)

    # ------------------------------------------------------------------ types

    def typedef(self, access, indent):
        start = self.next()
        name = self.name('a type name')
        self.expect('extends')
        prefix = self.prefix(access, 'library')
        if self.accept('function'):
            self.expect('(')
            types = []
            if not self.at(')'):
                while True:
                    types.append(self.name('a type').value)
                    if not self.accept(','):
                        break
            self.expect(')')
            returns = self.return_type()
            self.expect(';')
            takes = ', '.join(f'{t} a{i + 1}' for i, t in enumerate(types)) \
                or 'nothing'
            self.out.emit(f'{prefix}function interface {name.value} takes '
                          f'{takes} returns {returns}', start, indent)
            return
        base = self.name('a type')
        self.expect('[')
        size = self.expr_text()
        storage = ''
        if self.accept(','):
            storage = ', ' + self.expr_text()
        self.expect(']')
        self.expect(';')
        self.out.emit(f'{prefix}type {name.value} extends {base.value} array '
                      f'[{size}{storage}]', start, indent)

    # ------------------------------------------------------------------ functions

    def return_type(self):
        if self.accept('->'):
            return self.name('a return type').value
        return 'nothing'

    def arguments(self):
        self.expect('(')
        args = []
        if not self.at(')'):
            while True:
                arg_type = self.name('a parameter type')
                arg_name = self.name('a parameter name')
                if self.accept('='):
                    # Accepted by Zinc's grammar; JassHelper turns it into a
                    # `defaults` that only interface methods understand.
                    self.expr_text()
                args.append(f'{arg_type.value} {arg_name.value}')
                if not self.accept(','):
                    break
        self.expect(')')
        return ', '.join(args) or 'nothing'

    def function(self, access, indent):
        start = self.next()
        name = self.name('a function name')
        takes = self.arguments()
        returns = self.return_type()
        prefix = self.prefix(access, 'library')
        if name.value == 'onInit':
            prefix = 'private '
        header = f'{prefix}function {name.value} takes {takes} returns {returns}'
        self.callable_body(header, 'endfunction', start, indent, in_method=False)

    def callable_body(self, header, end_word, start, indent, in_method,
                      is_static=True):
        """Translate a body. Anonymous functions found in it are written
        before it, so it can refer to them."""
        saved = self.out
        body = Writer()
        self.out = body
        hoisted = Writer()
        previous_hoisted, previous_method = self.hoisted, getattr(
            self, 'in_method', False)
        self.hoisted = hoisted
        self.in_method = in_method
        brace = self.expect('{')
        self.statements(indent + 1)
        end = self.expect('}')
        self.hoisted = previous_hoisted
        self.in_method = previous_method
        self.out = saved
        self.out.lines.extend(hoisted.lines)
        self.out.emit(header, start, indent)
        self.out.lines.extend(body.lines)
        self.out.emit(end_word, end, indent)

    # ------------------------------------------------------------------ structs

    def struct(self, access, indent):
        start = self.next()
        limit = ''
        if self.accept('['):
            limit = '[' + self.expr_text() + ']'
            self.expect(']')
        name = self.name('a struct name')
        array = ''
        if self.accept('['):
            if self.accept(']'):
                array = ' extends array'
            else:
                array = ' extends array [' + self.expr_text() + ']'
                self.expect(']')
        extends = ''
        if self.accept('extends'):
            extends = ' extends ' + self.name('a parent type').value
        if extends and (limit or array):
            raise self.error('Children structs must not specify a storage '
                             'limit / array structs cannot extend', start)
        self.out.emit(f'{self.prefix(access, "library")}struct {name.value}'
                      f'{limit}{extends}{array}', start, indent)
        self.expect('{')
        self.struct_body('public', indent + 1, interface=False)
        end = self.expect('}')
        self.out.emit('endstruct', end, indent)

    def module(self, access, indent):
        start = self.next()
        name = self.name('a module name')
        self.out.emit(f'{self.prefix(access, "library")}module {name.value}',
                      start, indent)
        self.expect('{')
        self.struct_body('public', indent + 1, interface=False)
        end = self.expect('}')
        self.out.emit('endmodule', end, indent)

    def interface(self, access, indent):
        start = self.next()
        if self.accept('['):
            self.expr_text()
            self.expect(']')
        name = self.name('an interface name')
        self.out.emit(f'{self.prefix(access, "library")}interface {name.value}',
                      start, indent)
        self.expect('{')
        self.struct_body('public', indent + 1, interface=True)
        end = self.expect('}')
        self.out.emit('endinterface', end, indent)

    def struct_body(self, access, indent, interface):
        while not self.at('}') and self.peek().kind != 'EOF':
            token = self.peek()
            if token.kind == 'DIRECTIVE':
                self.next()
                self.out.emit(f'//! {token.value} {token.raw}', token, indent)
                continue
            member_access = access
            if self.at('private', 'public'):
                member_access = self.next().value
                if self.at('{'):
                    self.next()
                    self.struct_body(member_access, indent, interface)
                    self.expect('}')
                    continue
            if self.at('optional') and self.peek(1).is_word('module') or \
                    self.at('module'):
                start = self.peek()
                optional = 'optional ' if self.accept('optional') else ''
                self.expect('module')
                module = self.name('a module name')
                self.expect(';')
                self.out.emit(f'implement {optional}{module.value}', start,
                              indent)
                continue
            modifiers = []
            start = self.peek()
            if self.accept('static'):
                modifiers.append('static')
            if self.at('method'):
                self.method(member_access, modifiers, indent, interface, start)
                continue
            if self.accept('delegate'):
                modifiers.append('delegate')
            if self.accept('constant'):
                modifiers.append('constant')
            type_token = self.name('a member type')
            prefix = self.prefix(member_access, 'public')
            for name, sizes, init in self.variable_list():
                text = prefix + ''.join(m + ' ' for m in modifiers) + \
                    type_token.value
                if sizes is not None:
                    if 'static' not in modifiers and not sizes:
                        raise self.error('Non-static arrays require an explicit '
                                         'size.', name)
                    text += ' array ' + name.value + ''.join(
                        f'[{s}]' for s in sizes)
                else:
                    text += ' ' + name.value
                    if init is not None:
                        text += ' = ' + init
                self.out.emit(text, name, indent)

    def method(self, access, modifiers, indent, interface, start):
        self.expect('method')
        if self.accept('operator'):
            if self.accept('<'):
                name = 'operator <'
            elif self.accept('=='):
                name = 'operator =='
            elif self.accept('['):
                self.expect(']')
                name = 'operator []=' if self.accept('=') else 'operator []'
            else:
                ident = self.name('an operator name').value
                name = 'operator ' + ident + ('=' if self.accept('=') else '')
        else:
            name = self.name('a method name').value
        takes = self.arguments()
        returns = self.return_type()
        prefix = self.prefix(access, 'public') + ''.join(m + ' ' for m in
                                                         modifiers)
        header = f'{prefix}method {name} takes {takes} returns {returns}'
        if interface:
            if self.accept('='):
                header += ' defaults ' + self.expr_text()
            self.expect(';')
            self.out.emit(header, start, indent)
            return
        self.callable_body(header, 'endmethod', start, indent, in_method=True,
                           is_static='static' in modifiers)

    # ------------------------------------------------------------------ statements

    def statements(self, indent):
        while not self.at('}') and self.peek().kind != 'EOF':
            self.statement(indent)

    def block_or_statement(self, indent):
        if self.at('{'):
            self.next()
            self.statements(indent)
            self.expect('}')
        else:
            self.statement(indent)

    def statement(self, indent):
        token = self.peek()
        out = self.out
        if token.is_op('{'):
            self.next()
            self.statements(indent)
            self.expect('}')
            return
        if token.is_op(';'):
            self.next()
            return
        if token.kind == 'DIRECTIVE':
            self.next()
            out.emit(f'//! {token.value} {token.raw}', token, indent)
            return
        if token.is_word('static') and self.peek(1).is_word('if'):
            self.next()
            self.next()
            self.expect('(')
            cond = self.static_condition()
            self.expect(')')
            out.emit(f'static if {cond} then', token, indent)
            self.block_or_statement(indent + 1)
            if self.accept('else'):
                out.emit('else', token, indent)
                self.block_or_statement(indent + 1)
            out.emit('endif', token, indent)
            return
        if token.is_word('if'):
            self.if_statement(indent, token)
            return
        if token.is_word('while'):
            self.next()
            cond = self.paren_condition()
            out.emit('loop', token, indent)
            if cond != 'false':
                out.emit(f'exitwhen not ({cond})', token, indent + 1)
            self.block_or_statement(indent + 1)
            out.emit('endloop', token, indent)
            return
        if token.is_word('do'):
            self.next()
            out.emit('loop', token, indent)
            self.block_or_statement(indent + 1)
            self.expect('while')
            cond = self.paren_condition()
            self.expect(';')
            if cond != 'false':
                out.emit(f'exitwhen not ({cond})', token, indent + 1)
            out.emit('endloop', token, indent)
            return
        if token.is_word('for'):
            self.for_statement(indent, token)
            return
        if token.is_word('debug'):
            self.next()
            if self.options.debug:
                self.block_or_statement(indent)
            else:
                saved = self.out
                self.out = Writer()
                self.block_or_statement(indent)
                self.out = saved
            return
        if token.is_word('return'):
            self.next()
            if self.accept(';'):
                out.emit('return', token, indent)
                return
            value = self.expr_text()
            self.expect(';')
            out.emit(f'return {value}', token, indent)
            return
        if token.is_word('break'):
            self.next()
            self.expect(';')
            out.emit('exitwhen true', token, indent)
            return
        if token.kind == 'ID' and self.peek(1).kind == 'ID' and \
                not token.is_word('not', 'and', 'or', 'function', 'method'):
            self.local_variables(indent)
            return
        self.assignments_or_call(indent, terminator=';')

    def paren_condition(self):
        self.expect('(')
        expr = self.expression()
        self.expect(')')
        return self.render(expr)

    def if_statement(self, indent, token, keyword='if'):
        out = self.out
        self.next()
        self.expect('(')
        cond = self.expression()
        self.expect(')')
        if self.at('break') and self.peek(1).is_op(';'):
            self.next()
            self.next()
            if keyword != 'if':
                raise self.error('if (...) break; cannot be an else branch')
            out.emit(f'exitwhen {self.render(cond)}', token, indent)
            return
        out.emit(f'{keyword} {self.render(cond)} then', token, indent)
        self.block_or_statement(indent + 1)
        if self.at('else'):
            else_token = self.next()
            if self.at('if'):
                self.if_statement(indent, self.peek(), keyword='elseif')
            else:
                out.emit('else', else_token, indent)
                self.block_or_statement(indent + 1)
        if keyword == 'if':
            out.emit('endif', token, indent)

    def for_statement(self, indent, token):
        out = self.out
        self.next()
        self.expect('(')
        # for (init; cond; step) vs for (a < i <= b)
        save = self.pos
        depth = 0
        is_c_style = False
        while self.peek().kind != 'EOF':
            t = self.next()
            if t.is_op('('):
                depth += 1
            elif t.is_op(')'):
                if depth == 0:
                    break
                depth -= 1
            elif t.is_op(';') and depth == 0:
                is_c_style = True
                break
        self.pos = save
        if is_c_style:
            self.assignments_or_call(indent, terminator=';')
            cond = self.expression()
            self.expect(';')
            step_out = Writer()
            saved = self.out
            self.out = step_out
            self.assignments_or_call(indent + 1, terminator=')')
            self.out = saved
            out.emit('loop', token, indent)
            out.emit(f'exitwhen not ({self.render(cond)})', token, indent + 1)
            self.block_or_statement(indent + 1)
            out.lines.extend(step_out.lines)
            out.emit('endloop', token, indent)
            return
        start = self.additive()
        op1 = self.next()
        if not op1.is_op('<', '<=', '>', '>='):
            raise self.error('Expected a relational operator in for', op1)
        var = self.name('the loop variable')
        op2 = self.next()
        if not op2.is_op('<', '<=', '>', '>='):
            raise self.error('Expected a relational operator in for', op2)
        end = self.additive()
        self.expect(')')
        if op1.value[0] != op2.value[0]:
            raise self.error('for requires the direction of both relational '
                             'operators to be the same.', op2)
        ascending = op1.value[0] == '<'
        start_text = self.render(start, 5)
        if len(op1.value) == 1:
            start_text = f'({self.render(start)}) {"+" if ascending else "-"} 1'
        stop = {'<': '>=', '<=': '>', '>': '<=', '>=': '<'}[op2.value]
        out.emit(f'set {var.value} = {start_text}', token, indent)
        out.emit('loop', token, indent)
        out.emit(f'exitwhen {var.value} {stop} {self.render(end, 5)}', token,
                 indent + 1)
        self.block_or_statement(indent + 1)
        out.emit(f'set {var.value} = {var.value} {"+" if ascending else "-"} 1',
                 token, indent + 1)
        out.emit('endloop', token, indent)

    def local_variables(self, indent):
        type_token = self.next()
        for name, sizes, init in self.variable_list():
            if sizes:
                raise self.error('local arrays cannot have explicit sizes', name)
            text = f'local {type_token.value} '
            if sizes is not None:
                text += 'array '
            text += name.value
            if init is not None:
                text += ' = ' + init
            self.out.emit(text, name, indent)

    def assignments_or_call(self, indent, terminator):
        while True:
            start = self.peek()
            target = self.postfix()
            if self.at('=', '+=', '-=', '*=', '/='):
                op = self.next().value
                value = self.expression()
                target_text = self.render(target)
                if op == '=':
                    text = f'set {target_text} = {self.render(value)}'
                else:
                    text = (f'set {target_text} = {target_text} {op[0]} '
                            f'{self.render(value, _PREC[op[0]] + 1)}')
                self.out.emit(text, start, indent)
            elif target.kind in ('call', 'mcall'):
                self.out.emit('call ' + self.render(target), start, indent)
            else:
                raise self.error('Expected an assignment or a call')
            if self.accept(','):
                continue
            self.expect(terminator)
            return

    def static_condition(self):
        parts = []
        while True:
            negate = bool(self.accept('!'))
            name = self.name('a constant or library name').value
            while self.accept('.'):
                name += '.' + self.name('a member name').value
            parts.append(('not ' if negate else '') + name)
            if not self.accept('&&'):
                break
        return ' and '.join(parts)

    # ------------------------------------------------------------------ expressions

    def expr_text(self):
        return self.render(self.expression())

    def expression(self):
        left = self.equality()
        if self.at('&&', '||'):
            op = self.next().value
            right = self.expression()
            return ZExpr('bin', 'and' if op == '&&' else 'or', left, right)
        return left

    def equality(self):
        left = self.relation()
        while self.at('==', '!='):
            op = self.next().value
            left = ZExpr('bin', op, left, self.relation())
        return left

    def relation(self):
        left = self.additive()
        if self.at('<', '>', '<=', '>='):
            op = self.next().value
            left = ZExpr('bin', op, left, self.additive())
        return left

    def additive(self):
        left = self.multiplicative()
        while self.at('+', '-'):
            op = self.next().value
            left = ZExpr('bin', op, left, self.multiplicative())
        return left

    def multiplicative(self):
        left = self.unary()
        while self.at('*', '/'):
            op = self.next().value
            left = ZExpr('bin', op, left, self.unary())
        return left

    def unary(self):
        if self.at('-', '+'):
            op = self.next().value
            operand = self.unary()
            return operand if op == '+' else ZExpr('neg', operand)
        if self.at('!'):
            self.next()
            return ZExpr('not', self.unary())
        return self.postfix()

    def call_args(self):
        args = []
        if self.accept(')'):
            return args
        while True:
            args.append(self.expression())
            if self.accept(')'):
                return args
            self.expect(',')

    def postfix(self):
        expr = self.primary()
        while True:
            if self.accept('.'):
                member = self.name('a member name').value
                if self.accept('('):
                    expr = ZExpr('mcall', expr, member, self.call_args())
                else:
                    expr = ZExpr('member', expr, member)
            elif self.accept('['):
                index = self.expression()
                self.expect(']')
                expr = ZExpr('index', expr, index)
            elif self.at(':') and self.peek(1).kind == 'ID':
                self.next()
                expr = ZExpr('colon', expr, self.next().value)
            elif self.at('(') and expr.kind == 'name':
                self.next()
                expr = ZExpr('call', expr.a, self.call_args())
            else:
                return expr

    def primary(self):
        token = self.peek()
        if token.kind in ('INT', 'REAL', 'RAWCODE'):
            self.next()
            return ZExpr('raw', token.raw)
        if token.kind == 'STRING':
            self.next()
            return ZExpr('raw', token.raw)
        if token.is_op('('):
            self.next()
            inner = self.expression()
            self.expect(')')
            return ZExpr('paren', inner)
        if token.is_word('function'):
            self.next()
            if self.at('('):
                return self.anonymous(token, 'function')
            return ZExpr('raw', 'function ' + self.name('a function name').value)
        if token.is_word('static') and self.peek(1).is_word('method'):
            self.next()
            self.next()
            if self.at('('):
                return self.anonymous(token, 'static method')
            struct = self.name('a struct name').value
            self.expect('.')
            method = self.name('a method name').value
            return ZExpr('raw', f'function {struct}.{method}')
        if token.is_word('method') and self.peek(1).is_op('('):
            self.next()
            return self.anonymous(token, 'method')
        if token.kind == 'ID':
            self.next()
            return ZExpr('name', token.value)
        raise self.error('Syntax error, expected an expression')

    def anonymous(self, token, tag):
        """Hoist an anonymous function; the expression becomes a reference."""
        number = self.counter[0]
        self.counter[0] += 1
        name = f'anon__{number}'
        takes = self.arguments()
        returns = self.return_type()
        in_method = getattr(self, 'in_method', False)
        is_static = tag != 'method'
        if not in_method and not is_static:
            raise self.error('Anonymous methods not allowed outside structs.',
                             token)
        if in_method:
            keyword = 'private static method' if is_static else 'private method'
            header = f'{keyword} {name} takes {takes} returns {returns}'
            end_word = 'endmethod'
            if takes == 'nothing' and is_static:
                value = f'function thistype.{name}'
            else:
                value = f'thistype.{name}'
        else:
            header = f'private function {name} takes {takes} returns {returns}'
            end_word = 'endfunction'
            value = f'function {name}' if takes == 'nothing' else name
        target = self.hoisted if self.hoisted is not None else self.out
        saved = self.out
        self.out = target
        self.callable_body(header, end_word, token, 1, in_method=in_method,
                           is_static=is_static)
        self.out = saved
        return ZExpr('raw', value)

    # ------------------------------------------------------------------ render

    def render(self, expr, min_prec=0):
        """vJass text for `expr`; parenthesized when it binds looser than
        `min_prec` needs."""
        kind = expr.kind
        if kind == 'raw' or kind == 'name':
            return expr.a
        if kind == 'paren':
            return '(' + self.render(expr.a) + ')'
        if kind == 'member':
            return self.render(expr.a, 9) + '.' + expr.b
        if kind == 'index':
            return self.render(expr.a, 9) + '[' + self.render(expr.b) + ']'
        if kind == 'colon':
            return expr.b + '[' + self.render(expr.a) + ']'
        if kind == 'call':
            return expr.a + '(' + ', '.join(self.render(a) for a in expr.b) + ')'
        if kind == 'mcall':
            return (self.render(expr.a, 9) + '.' + expr.b + '(' +
                    ', '.join(self.render(a) for a in expr.c) + ')')
        if kind == 'neg':
            text = '-' + self.render(expr.a, 7)
            return text if min_prec <= 7 else '(' + text + ')'
        if kind == 'not':
            inner = self.render(expr.a)
            if expr.a.kind not in ('raw', 'name', 'paren', 'call', 'mcall',
                                   'member', 'index', 'colon'):
                inner = '(' + inner + ')'
            text = 'not ' + inner
            return '(' + text + ')' if min_prec > 3 else text
        if kind == 'bin':
            op = expr.a
            prec = _PREC[op]
            left = self.render(expr.b, prec)
            right = self.render(expr.c, prec + 1)
            text = f'{left} {op} {right}'
            return text if prec >= min_prec else '(' + text + ')'
        raise VJassError(f'cannot render Zinc expression {kind}', None, 'parse')


def translate_zinc(lines, options, counter=None):
    """Zinc source lines -> vJass SourceLines."""
    if counter is None:
        counter = [1]
    tokens = tokenize(lines, zinc=True)
    return ZincTranslator(tokens, options, counter).translate()


def parse_zinc_lines(lines, compiler):
    from .parser import parse_lines
    counter = compiler.__dict__.setdefault('_zinc_counter', [1])
    return parse_lines(translate_zinc(lines, compiler.options, counter))
