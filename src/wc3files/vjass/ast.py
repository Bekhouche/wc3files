"""Syntax tree for vJass (and Zinc, which parses into the same nodes).

This is the source-level tree, before any name is resolved: a `Name` is just
the identifier as written, a `Member` is `x.y` whatever `x` turns out to be.
The compiler resolves it and lowers it to plain JASS (`wc3files.scripts.ir`).
"""


class Node(object):
    _fields = ()
    __slots__ = ('loc',)

    def __repr__(self):
        values = ', '.join(f'{f}={getattr(self, f, None)!r}' for f in self._fields)
        return f'{type(self).__name__}({values})'


def _node(name, fields, defaults=None):
    """Declare a node class with `fields` as constructor arguments; `loc` is a
    keyword argument on all of them."""
    defaults = defaults or {}

    def __init__(self, *args, **kwargs):
        loc = kwargs.pop('loc', None)
        if len(args) > len(fields):
            raise TypeError(f'{name} takes {len(fields)} arguments')
        for field, value in zip(fields, args):
            setattr(self, field, value)
        for field in fields[len(args):]:
            if field in kwargs:
                setattr(self, field, kwargs.pop(field))
            else:
                default = defaults.get(field)
                setattr(self, field, default() if callable(default) else default)
        if kwargs:
            raise TypeError(f'{name}: unexpected {sorted(kwargs)}')
        self.loc = loc

    cls = type(name, (Node,), {'__slots__': tuple(fields), '_fields': tuple(fields),
                               '__init__': __init__})
    return cls


# --- expressions ---------------------------------------------------------------

# kind: 'int', 'real', 'string', 'rawcode', 'bool', 'null'. `raw` is the source
# spelling, used for output.
Literal = _node('Literal', ('kind', 'value', 'raw'))
Name = _node('Name', ('name',))
# `obj.name`. `implicit` is set for the leading-dot shorthand `.x`, which means
# `this.x` (or the struct itself inside a static method).
Member = _node('Member', ('obj', 'name', 'implicit'), {'implicit': lambda: False})
Index = _node('Index', ('obj', 'index'))
Call = _node('Call', ('func', 'args'), {'args': list})
Unary = _node('Unary', ('op', 'operand'))
Binary = _node('Binary', ('op', 'left', 'right'))
# `function name` - `target` is an expression (Name or Member chain).
FuncRef = _node('FuncRef', ('target',))
Paren = _node('Paren', ('expr',))
# Zinc anonymous function: `function(integer x) -> integer { ... }`.
Lambda = _node('Lambda', ('params', 'returns', 'body', 'locals'),
               {'locals': list, 'body': list})


# --- statements ----------------------------------------------------------------

LocalDecl = _node('LocalDecl', ('type', 'name', 'is_array', 'init', 'size'))
SetStmt = _node('SetStmt', ('target', 'value', 'is_debug'))
CallStmt = _node('CallStmt', ('expr', 'is_debug'))
IfStmt = _node('IfStmt', ('branches', 'else_body', 'is_debug'))
LoopStmt = _node('LoopStmt', ('body', 'is_debug'))
ExitWhen = _node('ExitWhen', ('cond', 'is_debug'))
ReturnStmt = _node('ReturnStmt', ('value', 'is_debug'))
StaticIf = _node('StaticIf', ('branches', 'else_body'))
# Inside an injected main: where the generated initialization goes.
InitMarker = _node('InitMarker', ())


# --- declarations ---------------------------------------------------------------

Param = _node('Param', ('type', 'name'))

# access: None, 'public', 'private' (or 'readonly' on struct members).
VarDecl = _node('VarDecl', (
    'type', 'name', 'is_array', 'is_constant', 'init', 'sizes', 'access'),
    {'sizes': list})
KeyDecl = _node('KeyDecl', ('name', 'access'))
GlobalsBlock = _node('GlobalsBlock', ('vars',), {'vars': list})
NativeDecl = _node('NativeDecl', ('name', 'params', 'returns', 'is_constant',
                                  'access'))
FunctionDecl = _node('FunctionDecl', (
    'name', 'params', 'returns', 'body', 'is_constant', 'access', 'locals',
    'lambdas'), {'body': list, 'locals': list, 'lambdas': list})
FunctionInterface = _node('FunctionInterface', ('name', 'params', 'returns',
                                                'access'))
# `type name extends base` - base a handle type, or a dynamic array:
# `type name extends T array [size]` / `[size, storage]`.
TypeDecl = _node('TypeDecl', ('name', 'base', 'is_array', 'size', 'storage',
                              'access'))
Keyword = _node('Keyword', ('name', 'access'))
Hook = _node('Hook', ('target', 'func'))
Library = _node('Library', ('name', 'once', 'requires', 'initializer', 'items'),
                {'requires': list, 'items': list})
# requires entry: (name, optional, loc)
Scope = _node('Scope', ('name', 'initializer', 'items', 'access'),
              {'items': list})
StaticIfDecl = _node('StaticIfDecl', ('branches', 'else_items'))
Inject = _node('Inject', ('which', 'locals', 'body'), {'locals': list,
                                                       'body': list})
LoadData = _node('LoadData', ('path',))

# Struct / interface / module members.
Field = _node('Field', (
    'type', 'name', 'is_static', 'is_constant', 'is_array', 'size', 'init',
    'access', 'is_delegate', 'module'))
Method = _node('Method', (
    'name', 'params', 'returns', 'body', 'locals', 'is_static', 'is_stub',
    'is_constant', 'access', 'operator', 'defaults', 'module', 'lambdas'),
    {'body': list, 'locals': list, 'lambdas': list})
Implement = _node('Implement', ('module', 'optional'))
StaticIfMember = _node('StaticIfMember', ('branches', 'else_members'))

# extends: None, 'array', or a type name. size: max-index expression or None.
# A struct's (and a module's) body is kept as tokens until every module is
# known: `implement` pastes a module's text in, the way JassHelper does, and
# only then is the body parsed - a module may even open a method another
# module closes.
StructDecl = _node('StructDecl', ('name', 'extends', 'size', 'members',
                                  'access', 'is_array_struct', 'tokens'),
                   {'members': list})
InterfaceDecl = _node('InterfaceDecl', ('name', 'members', 'access',
                                        'has_create'), {'members': list})
ModuleDecl = _node('ModuleDecl', ('name', 'members', 'access', 'tokens'),
                   {'members': list})
