"""Print the compiled program as JASS text.

The compiler builds `wc3files.scripts.ir` nodes, with two additions defined
here: `ParenExpr`, an explicit pair of parentheses, and literals whose value is
their source text. Binary expressions print without parentheses of their own.
Grouping that came from the source is carried by `ParenExpr` nodes the parser
kept, and every expression the compiler builds itself wraps its operands with
`wrap()`. The output therefore groups exactly the way the input did, whatever
precedence this package's parser believes `not` has.
"""
from wc3files.scripts import ir


class ParenExpr(ir.Node):
    """Parentheses. `generated` marks ones the compiler added for safety
    rather than ones from the source; those are left out where the position
    already delimits the expression (an argument, an index, a whole
    condition)."""
    _fields = ('expr',)

    def __init__(self, expr, generated=False):
        self.expr = expr
        self.generated = generated


class RawExpr(ir.Node):
    """Expression text passed through untouched: an expression over
    undeclared names that cannot be resolved, kept as written for the JASS
    checker to judge (what JassHelper does with it)."""
    _fields = ('text',)

    def __init__(self, text):
        self.text = text


class Comment(ir.Node):
    """A `//` line inside a function body."""
    _fields = ('text',)

    def __init__(self, text):
        self.text = text


_ATOMIC = (ir.VarExpr, ir.ArrayRefExpr, ir.CallExpr, ir.FunctionRefExpr,
           ParenExpr, RawExpr)


def is_atomic(expr):
    if isinstance(expr, _ATOMIC):
        return True
    if isinstance(expr, ir.Literal):
        text = literal_text(expr)
        return not text.startswith('-')
    return False


def wrap(expr):
    return expr if is_atomic(expr) else ParenExpr(expr, generated=True)


def unwrap(expr):
    while isinstance(expr, ParenExpr):
        expr = expr.expr
    return expr


def int_lit(value):
    if value < 0:
        return ir.UnaryExpr('-', ir.Literal('int', str(-value)))
    return ir.Literal('int', str(value))


def str_lit(text):
    """A string literal from plain text (escapes quotes and backslashes)."""
    escaped = text.replace('\\', '\\\\').replace('"', '\\"')
    return ir.Literal('string', escaped)


TRUE = ir.Literal('bool', 'true')
FALSE = ir.Literal('bool', 'false')
NULL = ir.Literal('null', 'null')


def literal_text(lit):
    value = lit.value
    if lit.kind == 'string':
        return f'"{value}"'
    if lit.kind == 'bool':
        if isinstance(value, bool):
            return 'true' if value else 'false'
        return str(value)
    if lit.kind == 'null':
        return 'null'
    if lit.kind == 'rawcode' and isinstance(value, str):
        return value if value.startswith("'") else f"'{value}'"
    return str(value)


INDENT = '    '


class Emitter(object):

    def __init__(self):
        self.lines = []
        self.locations = {}       # output line index -> vJass Location

    def program(self, program):
        lines = self.lines
        for type_def in program.type_defs:
            lines.append(f'type {type_def.name} extends {type_def.base}')
        lines.append('globals')
        for var in program.globals:
            comment = getattr(var, 'comment', None)
            if comment:
                lines.append('//' + comment)
            lines.append(self.global_var(var))
        lines.append('endglobals')
        lines.append('')
        for native in program.natives:
            prefix = 'constant ' if native.is_constant else ''
            lines.append(f'{prefix}native {native.name} takes '
                         f'{self.params(native.params)} returns {native.returns}')
        for function in program.functions:
            for comment in getattr(function, 'comments', None) or ():
                lines.append('//' + comment)
            self.function(function)
        return '\n'.join(lines) + '\n'

    def global_var(self, var):
        prefix = 'constant ' if var.is_constant else ''
        array = 'array ' if var.is_array else ''
        text = f'{prefix}{var.type} {array}{var.name}'
        if var.initial is not None:
            text += f' = {self.top(var.initial)}'
        return text

    @staticmethod
    def params(params):
        if not params:
            return 'nothing'
        return ', '.join(f'{p.type} {p.name}' for p in params)

    def function(self, function):
        prefix = 'constant ' if function.is_constant else ''
        loc = getattr(function, 'loc', None)
        if loc is not None:
            self.locations[len(self.lines)] = loc
        self.lines.append(f'{prefix}function {function.name} takes '
                          f'{self.params(function.params)} returns '
                          f'{function.returns}')
        self.block(function.body, 1)
        self.lines.append('endfunction')

    def block(self, statements, depth):
        for stmt in statements:
            self.statement(stmt, depth)

    def statement(self, stmt, depth):
        pad = INDENT * depth
        loc = getattr(stmt, 'loc', None)
        if loc is not None:
            self.locations[len(self.lines)] = loc
        out = self.lines.append
        if isinstance(stmt, ir.Local):
            array = 'array ' if stmt.is_array else ''
            text = f'{pad}local {stmt.type} {array}{stmt.name}'
            if stmt.initial is not None:
                text += f' = {self.top(stmt.initial)}'
            out(text)
        elif isinstance(stmt, ir.SetStmt):
            target = stmt.target
            if stmt.index is not None:
                target += f'[{self.top(stmt.index)}]'
            out(f'{pad}set {target} = {self.top(stmt.value)}')
        elif isinstance(stmt, ir.CallStmt):
            args = ', '.join(self.top(a) for a in stmt.args)
            out(f'{pad}call {stmt.name}({args})')
        elif isinstance(stmt, ir.IfStmt):
            for i, (cond, body) in enumerate(stmt.branches):
                keyword = 'if' if i == 0 else 'elseif'
                out(f'{pad}{keyword} {self.top(cond)} then')
                self.block(body, depth + 1)
            if stmt.else_body is not None:
                out(f'{pad}else')
                self.block(stmt.else_body, depth + 1)
            out(f'{pad}endif')
        elif isinstance(stmt, ir.LoopStmt):
            out(f'{pad}loop')
            self.block(stmt.body, depth + 1)
            out(f'{pad}endloop')
        elif isinstance(stmt, ir.ExitWhenStmt):
            out(f'{pad}exitwhen {self.top(stmt.condition)}')
        elif isinstance(stmt, ir.ReturnStmt):
            if stmt.value is None:
                out(f'{pad}return')
            else:
                out(f'{pad}return {self.top(stmt.value)}')
        elif isinstance(stmt, Comment):
            out(f'{pad}//{stmt.text}')
        else:
            raise TypeError(f'cannot print statement {type(stmt).__name__}')

    def top(self, expr):
        """An expression in a position that delimits it on its own."""
        while isinstance(expr, ParenExpr) and expr.generated:
            expr = expr.expr
        return self.expr(expr)

    def expr(self, expr):
        if isinstance(expr, ir.Literal):
            return literal_text(expr)
        if isinstance(expr, ir.VarExpr):
            return expr.name
        if isinstance(expr, ir.ArrayRefExpr):
            return f'{expr.name}[{self.top(expr.index)}]'
        if isinstance(expr, ir.CallExpr):
            return f'{expr.name}({", ".join(self.top(a) for a in expr.args)})'
        if isinstance(expr, ir.FunctionRefExpr):
            return f'function {expr.name}'
        if isinstance(expr, ParenExpr):
            return f'({self.expr(expr.expr)})'
        if isinstance(expr, RawExpr):
            return expr.text
        if isinstance(expr, ir.UnaryExpr):
            if expr.op == 'not':
                return f'not {self.expr(expr.operand)}'
            return f'-{self.expr(expr.operand)}'
        if isinstance(expr, ir.BinaryExpr):
            return f'{self.expr(expr.left)} {expr.op} {self.expr(expr.right)}'
        raise TypeError(f'cannot print expression {type(expr).__name__}')


def emit(program):
    return Emitter().program(program)


def emit_with_map(program):
    """(JASS text, {output line number (1-based): vJass Location})."""
    emitter = Emitter()
    text = emitter.program(program)
    return text, {index + 1: loc for index, loc in emitter.locations.items()}
