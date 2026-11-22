"""Parser for vJass (the language JassHelper compiles), building `ast` nodes.

Line-oriented, like JASS: every declaration and statement owns its line(s).
Plain JASS is a subset, so a plain map script parses too.
"""
from . import ast
from .errors import VJassError
from .lexer import tokenize

ACCESS = ('public', 'private')

# Operator precedence, low to high. `not` sits between `and` and the
# comparisons, as in pjass's grammar: `not a == b` is `not (a == b)`.
_COMPARISON = ('==', '!=', '<', '>', '<=', '>=')
_ADDITIVE = ('+', '-')
_MULTIPLICATIVE = ('*', '/')


class TokenStream(object):

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

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
        if token.kind == 'NL':
            got = 'end of line'
        elif token.kind == 'EOF':
            got = 'end of file'
        return VJassError(f'{message} (got {got!r})', token.location, 'parse')

    def at_word(self, *words):
        return self.peek().is_word(*words)

    def at_op(self, *ops):
        return self.peek().is_op(*ops)

    def accept_word(self, *words):
        if self.peek().is_word(*words):
            return self.next()
        return None

    def accept_op(self, *ops):
        if self.peek().is_op(*ops):
            return self.next()
        return None

    def expect_word(self, word):
        if not self.peek().is_word(word):
            raise self.error(f'Expected "{word}"')
        return self.next()

    def expect_op(self, op):
        if not self.peek().is_op(op):
            raise self.error(f'Expected "{op}"')
        return self.next()

    def expect_name(self, what='a name'):
        token = self.peek()
        if token.kind != 'ID':
            raise self.error(f'Expected {what}')
        return self.next()


class Parser(TokenStream):
    """vJass parser. `parse_file()` returns the top-level item list."""

    def __init__(self, tokens):
        super().__init__(tokens)

    # -- helpers ----------------------------------------------------------------

    def skip_nl(self):
        while self.peek().kind == 'NL':
            self.next()

    def end_line(self):
        token = self.peek()
        if token.kind == 'NL':
            self.next()
            return
        if token.kind == 'EOF':
            return
        raise self.error('Expected end of line')

    def at_end_word(self, *words):
        self.skip_nl()
        return self.peek().is_word(*words)

    # -- top level ----------------------------------------------------------------

    def parse_file(self):
        items = self.parse_items(('EOF',), context='file')
        if self.peek().kind != 'EOF':
            raise self.error('Unexpected')
        return items

    def parse_items(self, terminators, context):
        """Parse declarations until one of `terminators` (end words) or EOF."""
        items = []
        while True:
            self.skip_nl()
            token = self.peek()
            if token.kind == 'EOF':
                return items
            if token.kind == 'ID' and token.value in terminators:
                return items
            if token.kind == 'DIRECTIVE':
                items.append(self.parse_directive_item(context))
                continue
            items.append(self.parse_item(context))

    def parse_directive_item(self, context):
        token = self.next()
        if token.value == 'inject':
            which = token.raw.strip().lower()
            if which not in ('main', 'config'):
                raise VJassError('Unrecognized inject option', token.location,
                                 'parse')
            self.end_line()
            body = self.parse_statements(end_directive='endinject')
            end = self.next()
            if not (end.kind == 'DIRECTIVE' and end.value == 'endinject'):
                raise VJassError('Missing endinject', token.location, 'parse')
            self.end_line()
            return ast.Inject(which, [], body, loc=token.location)
        if token.value == 'loaddata':
            path = token.raw.strip()
            if not (len(path) >= 2 and path[0] == '"' and path[-1] == '"'):
                raise VJassError('Incomplete loaddata command', token.location,
                                 'parse')
            self.end_line()
            return ast.LoadData(path[1:-1], loc=token.location)
        if token.value == 'dovjassinit':
            raise VJassError('//! dovjassinit must be inside //! inject main '
                             'block', token.location, 'parse')
        if token.value == 'endinject':
            raise VJassError('Unexpected endinject.', token.location, 'parse')
        raise VJassError(f'Unexpected //! {token.value}', token.location, 'parse')

    def parse_item(self, context):
        start = self.peek()
        loc = start.location

        if start.is_word('globals'):
            self.next()
            self.end_line()
            return self.parse_globals(loc)

        if start.is_word('library', 'library_once'):
            return self.parse_library()

        if start.is_word('static') and self.peek(1).is_word('if'):
            return self.parse_static_if_items(context)

        if start.is_word('hook'):
            self.next()
            target = self.expect_name('a function name').value
            func = self.parse_dotted_name()
            self.end_line()
            return ast.Hook(target, func, loc=loc)

        access = None
        if start.is_word(*ACCESS):
            access = self.next().value
        token = self.peek()

        if token.is_word('scope'):
            return self.parse_scope(access)
        if token.is_word('keyword'):
            self.next()
            name = self.expect_name().value
            self.end_line()
            return ast.Keyword(name, access, loc=loc)
        if token.is_word('struct'):
            return self.parse_struct(access)
        if token.is_word('interface'):
            return self.parse_interface(access)
        if token.is_word('module'):
            return self.parse_module(access)
        if token.is_word('type'):
            return self.parse_type(access)

        is_constant = bool(self.accept_word('constant'))
        token = self.peek()
        if token.is_word('native'):
            self.next()
            name = self.expect_name().value
            params, returns = self.parse_signature()
            self.end_line()
            return ast.NativeDecl(name, params, returns, is_constant, access,
                                  loc=loc)
        if token.is_word('function'):
            self.next()
            if self.at_word('interface'):
                self.next()
                if is_constant:
                    raise VJassError('Function interfaces do not use the '
                                     'constant prefix', loc, 'parse')
                name = self.expect_name().value
                params, returns = self.parse_signature()
                self.end_line()
                return ast.FunctionInterface(name, params, returns, access,
                                             loc=loc)
            return self.parse_function(access, is_constant, loc)

        if token.is_word('endglobals'):
            raise self.error('endglobals found but globals were not started')
        if token.is_word('endlibrary', 'endscope', 'endstruct', 'endfunction',
                         'endmethod', 'endinterface', 'endmodule'):
            raise self.error('Unexpected')
        raise self.error('Expected a declaration')

    def parse_dotted_name(self):
        """`name` or `Struct.method` (used by hook and initializer)."""
        first = self.expect_name()
        expr = ast.Name(first.value, loc=first.location)
        while self.accept_op('.'):
            member = self.expect_name()
            expr = ast.Member(expr, member.value, loc=member.location)
        return expr

    # -- globals ------------------------------------------------------------------

    def parse_globals(self, loc):
        block = ast.GlobalsBlock([], loc=loc)
        while True:
            self.skip_nl()
            token = self.peek()
            if token.kind == 'EOF':
                raise VJassError('Missing endglobals', loc, 'parse')
            if token.is_word('endglobals'):
                self.next()
                self.end_line()
                return block
            if token.is_word('globals'):
                raise self.error('Nested globals')
            block.vars.append(self.parse_global_var())

    def parse_global_var(self):
        start = self.peek()
        access = None
        if start.is_word(*ACCESS):
            access = self.next().value
        is_constant = bool(self.accept_word('constant'))
        if not is_constant and access is None and self.at_word(*ACCESS):
            raise self.error('Wrong keyword order (valid is [public/private] '
                             '[constant] type name)')
        if self.at_word('key'):
            token = self.next()
            name = self.expect_name().value
            self.end_line()
            return ast.KeyDecl(name, access, loc=token.location)
        if self.at_word('keyword') and self.peek(1).kind == 'ID' and \
                self.peek(2).kind in ('NL', 'EOF'):
            # `private keyword name` inside globals: JassHelper takes it the
            # same as outside.
            token = self.next()
            name = self.expect_name().value
            self.end_line()
            return ast.Keyword(name, access, loc=token.location)
        type_token = self.expect_name('a type')
        is_array = bool(self.accept_word('array'))
        name = self.expect_name('a variable name')
        sizes = []
        if is_array:
            while self.accept_op('['):
                sizes.append(self.parse_expr())
                self.expect_op(']')
        init = None
        if self.accept_op('='):
            init = self.parse_expr()
        self.end_line()
        if is_constant and init is None:
            raise VJassError('A constant requires initializing.',
                             name.location, 'parse')
        return ast.VarDecl(type_token.value, name.value, is_array, is_constant,
                           init, sizes, access, loc=name.location)

    # -- libraries and scopes ------------------------------------------------------

    def parse_library(self):
        token = self.next()
        once = token.value == 'library_once'
        name = self.expect_name('a library name').value
        initializer = None
        requires = []
        while self.peek().kind == 'ID':
            if self.at_word('initializer'):
                self.next()
                initializer = self.parse_dotted_name()
                continue
            if self.at_word('requires', 'needs', 'uses'):
                self.next()
                # Commas are optional: `uses optional A optional B` is
                # accepted by JassHelper and used by real libraries.
                while True:
                    optional = bool(self.accept_word('optional'))
                    req = self.expect_name('a library name')
                    requires.append((req.value, optional, req.location))
                    if self.accept_op(','):
                        continue
                    if self.peek().kind == 'ID' and not self.at_word(
                            'initializer', 'requires', 'needs', 'uses'):
                        continue
                    break
                continue
            raise self.error('Not a valid library declaration')
        self.end_line()
        items = self.parse_items(('endlibrary',), context='library')
        if not self.at_word('endlibrary'):
            raise VJassError('Missing endlibrary', token.location, 'parse')
        self.next()
        self.end_line()
        return ast.Library(name, once, requires, initializer, items,
                           loc=token.location)

    def parse_scope(self, access):
        token = self.next()
        name = self.expect_name('a scope name').value
        initializer = None
        if self.accept_word('initializer'):
            initializer = self.parse_dotted_name()
        self.end_line()
        items = self.parse_items(('endscope',), context='scope')
        if not self.at_word('endscope'):
            raise VJassError('Missing endscope', token.location, 'parse')
        self.next()
        self.end_line()
        return ast.Scope(name, initializer, items, access, loc=token.location)

    def parse_static_if_items(self, context):
        token = self.next()
        self.next()
        branches = []
        cond = self.parse_expr()
        self.expect_word('then')
        self.end_line()
        while True:
            items = self.parse_items(('elseif', 'else', 'endif'), context)
            branches.append((cond, items))
            if self.accept_word('elseif'):
                cond = self.parse_expr()
                self.expect_word('then')
                self.end_line()
                continue
            break
        else_items = None
        if self.accept_word('else'):
            self.end_line()
            else_items = self.parse_items(('endif',), context)
        if not self.accept_word('endif'):
            raise VJassError('Missing endif for static if', token.location,
                             'parse')
        self.end_line()
        return ast.StaticIfDecl(branches, else_items, loc=token.location)

    # -- types ----------------------------------------------------------------------

    def parse_type(self, access):
        token = self.next()
        name = self.expect_name('a type name').value
        self.expect_word('extends')
        base = self.expect_name('a base type').value
        if self.accept_word('array'):
            self.expect_op('[')
            size = self.parse_expr()
            storage = None
            if self.accept_op(','):
                storage = self.parse_expr()
            self.expect_op(']')
            self.end_line()
            return ast.TypeDecl(name, base, True, size, storage, access,
                                loc=token.location)
        self.end_line()
        return ast.TypeDecl(name, base, False, None, None, access,
                            loc=token.location)

    # -- functions -----------------------------------------------------------------

    def parse_signature(self):
        self.expect_word('takes')
        params = []
        if self.accept_word('nothing'):
            pass
        else:
            while True:
                ptype = self.expect_name('a parameter type')
                pname = self.expect_name('a parameter name')
                params.append(ast.Param(ptype.value, pname.value,
                                        loc=pname.location))
                if not self.accept_op(','):
                    break
        self.expect_word('returns')
        returns = self.expect_name('a return type').value
        if self.peek().kind == 'ID' and self.peek(1).kind in ('NL', 'EOF') \
                and not self.at_word('defaults'):
            # `returns integer timeoutId` - JassHelper reads the type and
            # ignores the rest of the line, and real libraries have it.
            self.next()
        return params, returns

    def parse_function(self, access, is_constant, loc):
        name = self.expect_name('a function name').value
        params, returns = self.parse_signature()
        self.end_line()
        body = self.parse_statements(end_words=('endfunction',))
        if not self.accept_word('endfunction'):
            raise VJassError('Unclosed function.', loc, 'parse')
        self.end_line()
        return ast.FunctionDecl(name, params, returns, body, is_constant, access,
                                loc=loc)

    # -- structs, interfaces, modules ---------------------------------------------------

    def parse_struct(self, access):
        token = self.next()
        name = self.expect_name('a struct name').value
        size = None
        if self.accept_op('['):
            size = self.parse_expr()
            self.expect_op(']')
        extends = None
        is_array_struct = False
        if self.accept_word('extends'):
            if self.accept_word('array'):
                is_array_struct = True
                extends = 'array'
                if self.accept_op('['):
                    size = self.parse_expr()
                    self.expect_op(']')
            else:
                extends = self.expect_name('a parent type').value
        self.end_line()
        tokens = self.collect_body('endstruct', 'Missing endstruct', token)
        return ast.StructDecl(name, extends, size, None, access,
                              is_array_struct, tokens, loc=token.location)

    def parse_interface(self, access):
        token = self.next()
        name = self.expect_name('an interface name').value
        self.end_line()
        members = self.parse_members(('endinterface',), kind='interface')
        if not self.accept_word('endinterface'):
            raise VJassError('Missing endinterface', token.location, 'parse')
        self.end_line()
        has_create = False
        kept = []
        for member in members:
            if isinstance(member, ast.Method) and member.is_static:
                if member.name != 'create':
                    raise VJassError('Interfaces cannot have static methods',
                                     member.loc, 'parse')
                if member.params:
                    raise VJassError('Expected: "takes nothing"', member.loc,
                                     'parse')
                has_create = True
                continue
            kept.append(member)
        return ast.InterfaceDecl(name, kept, access, has_create,
                                 loc=token.location)

    def parse_module(self, access):
        token = self.next()
        name = self.expect_name('a module name').value
        self.end_line()
        tokens = self.collect_body('endmodule', 'Could not find endmodule.',
                                   token)
        return ast.ModuleDecl(name, None, access, tokens, loc=token.location)

    def collect_body(self, end_word, message, start):
        """Take the raw tokens up to a line that starts with `end_word`."""
        tokens = []
        at_line_start = True
        while True:
            token = self.peek()
            if token.kind == 'EOF':
                raise VJassError(message, start.location, 'parse')
            if at_line_start and token.is_word(end_word):
                self.next()
                self.end_line()
                return tokens
            if at_line_start and token.is_word('struct', 'module', 'library',
                                               'library_once', 'scope',
                                               'interface', 'endstruct',
                                               'endmodule', 'endlibrary',
                                               'endscope'):
                if token.value == 'struct':
                    raise self.error('Nested struct declarations are not '
                                     'allowed.')
                raise self.error(f'Unexpected {token.value} (missing '
                                 f'{end_word}?)')
            self.next()
            tokens.append(token)
            at_line_start = token.kind in ('NL', 'DIRECTIVE')

    def parse_members(self, terminators, kind):
        members = []
        while True:
            self.skip_nl()
            token = self.peek()
            if token.kind == 'EOF':
                return members
            if token.kind == 'ID' and token.value in terminators:
                return members
            if token.kind == 'ID' and token.value in ('elseif', 'else', 'endif') \
                    and 'endif' in terminators:
                return members
            members.append(self.parse_member(kind))

    def parse_member(self, kind):
        start = self.peek()
        loc = start.location
        if start.is_word('implement'):
            self.next()
            optional = bool(self.accept_word('optional'))
            name = self.expect_name('a module name').value
            self.end_line()
            return ast.Implement(name, optional, loc=loc)
        if start.is_word('static') and self.peek(1).is_word('if'):
            self.next()
            self.next()
            branches = []
            cond = self.parse_expr()
            self.expect_word('then')
            self.end_line()
            while True:
                members = self.parse_members(('endif',), kind)
                branches.append((cond, members))
                if self.accept_word('elseif'):
                    cond = self.parse_expr()
                    self.expect_word('then')
                    self.end_line()
                    continue
                break
            else_members = None
            if self.accept_word('else'):
                self.end_line()
                else_members = self.parse_members(('endif',), kind)
            if not self.accept_word('endif'):
                raise VJassError('Missing endif for static if', loc, 'parse')
            self.end_line()
            return ast.StaticIfMember(branches, else_members, loc=loc)

        for word in ('struct', 'library', 'scope', 'globals', 'function',
                     'interface', 'module'):
            if start.is_word(word) and not (word == 'function' and kind == 'x'):
                messages = {'struct': 'Nested struct declarations are not '
                                      'allowed.',
                            'library': 'Library inside struct?',
                            'scope': 'Scope inside struct?',
                            'globals': 'Globals inside struct?',
                            'function': 'Function inside struct?',
                            'interface': 'Nested struct',
                            'module': 'Nested struct'}
                raise self.error(messages[word])

        access = None
        is_static = is_stub = is_constant = is_delegate = False
        # Modifiers. JassHelper insists on the order
        # [public/private/readonly] [static/stub] [constant] [delegate]; the
        # same set is accepted here, but in any order, since nothing is gained
        # by rejecting `static private`.
        while True:
            token = self.peek()
            if token.is_word('public', 'private', 'readonly'):
                if access is not None:
                    raise self.error('Wrong keyword order')
                access = self.next().value
            elif token.is_word('static'):
                is_static = True
                self.next()
            elif token.is_word('stub'):
                is_stub = True
                self.next()
            elif token.is_word('constant'):
                is_constant = True
                self.next()
            elif token.is_word('delegate'):
                is_delegate = True
                self.next()
            else:
                break

        if self.at_word('method'):
            if access == 'readonly':
                raise self.error('Methods cannot be readonly')
            if is_delegate:
                raise self.error('Only variable members (not method) can be '
                                 'delegates.')
            return self.parse_method(kind, access, is_static, is_stub,
                                     is_constant, loc, start.origin)

        if kind == 'interface':
            raise self.error('Interfaces can only declare methods')
        if is_stub:
            raise self.error('stub only applies to methods')
        type_token = self.expect_name('a type')
        is_array = bool(self.accept_word('array'))
        name = self.expect_name('a member name')
        size = None
        if self.accept_op('['):
            size = self.parse_expr()
            self.expect_op(']')
        init = None
        if self.accept_op('='):
            init = self.parse_expr()
        self.end_line()
        return ast.Field(type_token.value, name.value, is_static, is_constant,
                         is_array, size, init, access, is_delegate,
                         start.origin, loc=name.location)

    def parse_operator_name(self):
        """After `method operator`: `[]`, `[]=`, `<`, `==`, `name` or `name=`.
        Returns (operator, method name)."""
        if self.accept_op('['):
            self.expect_op(']')
            if self.accept_op('='):
                return '[]=', '[]='
            return '[]', '[]'
        if self.accept_op('<'):
            return '<', '<'
        if self.accept_op('=='):
            return '==', '=='
        if self.peek().kind == 'ID' and not self.at_word('takes'):
            name = self.next().value
            if self.accept_op('='):
                return 'set', name
            return 'get', name
        raise self.error('Expected "[]","[]=","<","==" or an identifier name')

    def parse_method(self, kind, access, is_static, is_stub, is_constant, loc,
                     origin=None):
        self.expect_word('method')
        operator = None
        op_name = None
        if self.at_word('operator') and not self.peek(1).is_word('takes'):
            self.next()
            operator, op_name = self.parse_operator_name()
            name = op_name
        else:
            name = self.expect_name('a method name').value
        if kind == 'interface' and is_static and name == 'create' and                 self.at_word('takes') and self.peek(1).is_word('nothing') and                 self.peek(2).kind in ('NL', 'EOF'):
            # `static method create takes nothing` - JassHelper's spelling has
            # no returns clause.
            self.next()
            self.next()
            self.end_line()
            return ast.Method(name, [], 'nothing', [], [], True, False, False,
                              access, None, None, origin, loc=loc)
        params, returns = self.parse_signature()
        defaults = None
        if self.accept_word('defaults'):
            if kind != 'interface':
                raise self.error('defaults is only compatible to interface '
                                 'methods')
            if self.accept_word('nothing'):
                defaults = 'nothing'
            else:
                defaults = self.parse_expr()
        self.end_line()
        if kind == 'interface':
            # Interface methods are declarations only.
            return ast.Method(name, params, returns, [], [], is_static, is_stub,
                              is_constant, access, operator, defaults, None,
                              loc=loc)
        body = self.parse_statements(end_words=('endmethod',))
        if not self.accept_word('endmethod'):
            raise VJassError('Missing "endmethod"', loc, 'parse')
        self.end_line()
        return ast.Method(name, params, returns, body, [], is_static, is_stub,
                          is_constant, access, operator, defaults, origin,
                          loc=loc)

    # -- statements -------------------------------------------------------------------

    def parse_statements(self, end_words=(), end_directive=None):
        body = []
        while True:
            self.skip_nl()
            token = self.peek()
            if token.kind == 'EOF':
                return body
            if token.kind == 'ID' and token.value in end_words:
                return body
            if token.kind == 'DIRECTIVE':
                if end_directive and token.value == end_directive:
                    return body
                if token.value == 'dovjassinit':
                    self.next()
                    self.end_line()
                    body.append(ast.InitMarker(loc=token.location))
                    continue
                raise VJassError(f'Unexpected //! {token.value} inside a '
                                 f'function', token.location, 'parse')
            body.append(self.parse_statement())

    def parse_statement(self):
        token = self.peek()
        loc = token.location
        is_debug = False
        if token.is_word('debug'):
            self.next()
            is_debug = True
            token = self.peek()
            if not token.is_word('set', 'call', 'if', 'loop', 'exitwhen',
                                 'return'):
                raise self.error('Incompatible statement for debug')

        if token.is_word('local'):
            self.next()
            ltype = self.expect_name('a type').value
            is_array = bool(self.accept_word('array'))
            name = self.expect_name('a local name').value
            size = None
            if is_array and self.accept_op('['):
                size = self.parse_expr()
                self.expect_op(']')
            init = None
            if self.accept_op('='):
                init = self.parse_expr()
            self.end_line()
            return ast.LocalDecl(ltype, name, is_array, init, size, loc=loc)

        if token.is_word('set'):
            self.next()
            target = self.parse_postfix()
            self.expect_op('=')
            value = self.parse_expr()
            self.end_line()
            return ast.SetStmt(target, value, is_debug, loc=loc)

        if token.is_word('call'):
            self.next()
            expr = self.parse_postfix()
            self.end_line()
            return ast.CallStmt(expr, is_debug, loc=loc)

        if token.is_word('static') and self.peek(1).is_word('if'):
            self.next()
            self.next()
            branches = []
            cond = self.parse_expr()
            self.expect_word('then')
            self.end_line()
            while True:
                body = self.parse_statements(end_words=('elseif', 'else', 'endif'))
                branches.append((cond, body))
                if self.accept_word('elseif'):
                    cond = self.parse_expr()
                    self.expect_word('then')
                    self.end_line()
                    continue
                break
            else_body = None
            if self.accept_word('else'):
                self.end_line()
                else_body = self.parse_statements(end_words=('endif',))
            if not self.accept_word('endif'):
                raise VJassError('Missing endif for static if', loc, 'parse')
            self.end_line()
            return ast.StaticIf(branches, else_body, loc=loc)

        if token.is_word('if'):
            self.next()
            branches = []
            cond = self.parse_expr()
            self.expect_word('then')
            self.end_line()
            while True:
                body = self.parse_statements(end_words=('elseif', 'else', 'endif'))
                branches.append((cond, body))
                if self.accept_word('elseif'):
                    cond = self.parse_expr()
                    self.expect_word('then')
                    self.end_line()
                    continue
                break
            else_body = None
            if self.accept_word('else'):
                self.end_line()
                else_body = self.parse_statements(end_words=('endif',))
            if not self.accept_word('endif'):
                raise VJassError('Missing endif', loc, 'parse')
            self.end_line()
            return ast.IfStmt(branches, else_body, is_debug, loc=loc)

        if token.is_word('loop'):
            self.next()
            self.end_line()
            body = self.parse_statements(end_words=('endloop',))
            if not self.accept_word('endloop'):
                raise VJassError('Missing endloop', loc, 'parse')
            self.end_line()
            return ast.LoopStmt(body, is_debug, loc=loc)

        if token.is_word('exitwhen'):
            self.next()
            cond = self.parse_expr()
            self.end_line()
            return ast.ExitWhen(cond, is_debug, loc=loc)

        if token.is_word('return'):
            self.next()
            value = None
            if self.peek().kind not in ('NL', 'EOF'):
                value = self.parse_expr()
            self.end_line()
            return ast.ReturnStmt(value, is_debug, loc=loc)

        if token.is_word('elseif', 'else', 'endif'):
            raise self.error(f'Unexpected: {token.value}')
        if token.is_word('function'):
            raise self.error('Unexpected: function')
        if token.is_word('globals'):
            raise self.error('Unexpected globals inside of function')
        raise self.error('Syntax Error, expected a statement')

    # -- expressions -----------------------------------------------------------------

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_word('or'):
            op = self.next()
            left = ast.Binary('or', left, self.parse_and(), loc=op.location)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_word('and'):
            op = self.next()
            left = ast.Binary('and', left, self.parse_not(), loc=op.location)
        return left

    def parse_not(self):
        if self.at_word('not'):
            op = self.next()
            return ast.Unary('not', self.parse_not(), loc=op.location)
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        while self.at_op(*_COMPARISON):
            op = self.next()
            left = ast.Binary(op.value, left, self.parse_additive(),
                              loc=op.location)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at_op(*_ADDITIVE):
            op = self.next()
            left = ast.Binary(op.value, left, self.parse_multiplicative(),
                              loc=op.location)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at_op(*_MULTIPLICATIVE):
            op = self.next()
            left = ast.Binary(op.value, left, self.parse_unary(), loc=op.location)
        return left

    def parse_unary(self):
        if self.at_op('-'):
            op = self.next()
            return ast.Unary('-', self.parse_unary(), loc=op.location)
        if self.at_op('+'):
            self.next()
            return self.parse_unary()
        if self.at_word('not'):
            op = self.next()
            return ast.Unary('not', self.parse_unary(), loc=op.location)
        return self.parse_postfix()

    def parse_args(self):
        args = []
        if self.accept_op(')'):
            return args
        while True:
            args.append(self.parse_expr())
            if self.accept_op(')'):
                return args
            self.expect_op(',')

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            token = self.peek()
            if token.is_op('.'):
                self.next()
                name = self.expect_name('a member name')
                expr = ast.Member(expr, name.value, loc=name.location)
            elif token.is_op('['):
                self.next()
                index = self.parse_expr()
                self.expect_op(']')
                expr = ast.Index(expr, index, loc=token.location)
            elif token.is_op('('):
                self.next()
                expr = ast.Call(expr, self.parse_args(), loc=token.location)
            elif token.is_op(':') and self.peek(1).kind == 'ID':
                # `x:name` is `name[x]`.
                self.next()
                name = self.next()
                expr = ast.Index(ast.Name(name.value, loc=name.location), expr,
                                 loc=token.location)
            else:
                return expr

    def parse_primary(self):
        token = self.peek()
        loc = token.location
        if token.kind == 'INT':
            self.next()
            return ast.Literal('int', token.value, token.raw, loc=loc)
        if token.kind == 'REAL':
            self.next()
            return ast.Literal('real', token.value, token.raw, loc=loc)
        if token.kind == 'STRING':
            self.next()
            return ast.Literal('string', token.value, token.raw, loc=loc)
        if token.kind == 'RAWCODE':
            self.next()
            return ast.Literal('rawcode', token.value, token.raw, loc=loc)
        if token.is_op('('):
            self.next()
            expr = self.parse_expr()
            self.expect_op(')')
            return ast.Paren(expr, loc=loc)
        if token.is_op('.'):
            # `.x` - this.x
            self.next()
            name = self.expect_name('a member name')
            return ast.Member(ast.Name('this', loc=loc), name.value, True,
                              loc=name.location)
        if token.kind == 'ID':
            if token.value in ('true', 'false'):
                self.next()
                return ast.Literal('bool', token.value == 'true', token.value,
                                   loc=loc)
            if token.value == 'null':
                self.next()
                return ast.Literal('null', None, 'null', loc=loc)
            if token.value == 'function':
                self.next()
                target = self.parse_function_target()
                return ast.FuncRef(target, loc=loc)
            self.next()
            return ast.Name(token.value, loc=loc)
        raise self.error('Syntax error, expected an expression')

    def parse_function_target(self):
        first = self.expect_name('a function name')
        expr = ast.Name(first.value, loc=first.location)
        while self.at_op('.'):
            self.next()
            name = self.expect_name('a member name')
            expr = ast.Member(expr, name.value, loc=name.location)
        return expr


def parse_lines(lines):
    """Parse a list of SourceLine (one vJass segment) into top-level items."""
    return Parser(tokenize(lines)).parse_file()


def parse_member_tokens(tokens, kind='struct'):
    """Parse a struct body (tokens, modules already pasted in) into members."""
    from .lexer import Token
    end = tokens[-1] if tokens else None
    eof = Token('EOF', '', '', end.file if end else None,
                (end.line + 1) if end else 1, 1)
    parser = Parser(list(tokens) + [eof])
    members = parser.parse_members((), kind)
    if parser.peek().kind != 'EOF':
        raise parser.error('Unexpected')
    return members
