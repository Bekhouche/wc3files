"""What the compiler knows about every declared name.

Types are either a plain string (`integer`, `unit`, any JASS type) or one of
the info objects below for the types vJass adds: `StructInfo` (structs and
interfaces), `FuncInterfaceInfo` and `DynamicArrayInfo`. All of them are an
`integer` once compiled.
"""
from .errors import VJassError


class ScopeInfo(object):
    """A library, a scope, or the global namespace.

    `names` maps a name as written inside this scope to its Symbol. A public
    or private declaration is entered here under its short name; a nested
    scope's public names are also entered under their relative name
    (`Inner_f` inside `Outer`), which is how JassHelper lets `Outer` call
    `Inner_f()`.
    """

    def __init__(self, name, parent, kind, library=None):
        self.name = name
        self.parent = parent
        self.kind = kind            # 'global', 'library', 'scope'
        self.library = library
        self.names = {}
        self.children = []
        if parent is None or parent.kind == 'global':
            self.full = name
        else:
            self.full = parent.full + '_' + name

    @property
    def public_prefix(self):
        return self.full + '_'

    @property
    def private_prefix(self):
        return self.full + '__'

    def chain(self):
        scope = self
        while scope is not None:
            yield scope
            scope = scope.parent

    def lookup(self, name):
        for scope in self.chain():
            symbol = scope.names.get(name)
            if symbol is not None:
                return symbol
        return None

    def __repr__(self):
        return f'<ScopeInfo {self.kind} {self.full}>'


class Symbol(object):
    """`kind` is one of: function, native, global, struct, interface, module,
    funciface, darray, type, keyword. `info` holds the matching info object."""

    __slots__ = ('kind', 'name', 'out', 'info', 'scope', 'loc', 'access')

    def __init__(self, kind, name, out, info, scope, loc, access=None):
        self.kind = kind
        self.name = name
        self.out = out
        self.info = info
        self.scope = scope
        self.loc = loc
        self.access = access

    def __repr__(self):
        return f'<Symbol {self.kind} {self.name} -> {self.out}>'


class FuncInfo(object):
    """A callable: a user function, a native, a Blizzard.j function, or a
    struct method. `params` is [(type, name)] with resolved types."""

    def __init__(self, name, out, params, returns, kind, decl=None, scope=None,
                 loc=None, is_constant=False):
        self.name = name
        self.out = out
        self.params = params
        self.returns = returns
        self.kind = kind          # 'function', 'native', 'api', 'method', 'generated'
        self.decl = decl
        self.scope = scope
        self.loc = loc
        self.is_constant = is_constant
        self.struct = None
        self.is_static = True
        self.is_stub = False
        self.access = None
        self.operator = None
        self.module = None        # ModuleUse that brought it in, if any
        self.defaults = None
        self.dispatch = None      # DispatchSlot when the method is virtual
        self.overrides = None

    @property
    def is_method(self):
        return self.kind == 'method'

    def __repr__(self):
        return f'<FuncInfo {self.kind} {self.out}>'


class VarInfo(object):
    """A global or local variable. `sizes` holds the declared dimensions of a
    sized array (`integer array a[10][20]` -> [10, 20])."""

    def __init__(self, name, out, type, is_array=False, is_constant=False,
                 sizes=None, decl=None, scope=None, loc=None, storage=None):
        self.name = name
        self.out = out
        self.type = type
        self.is_array = is_array
        self.is_constant = is_constant
        self.sizes = sizes or []
        self.decl = decl
        self.scope = scope
        self.loc = loc
        self.storage = storage
        self.constant_value = None

    @property
    def size(self):
        total = 1
        for size in self.sizes:
            total *= size
        return total if self.sizes else None

    def __repr__(self):
        return f'<VarInfo {self.out}:{type_name(self.type)}>'


class FieldInfo(object):
    """A struct member variable. Instance fields are arrays indexed by the
    instance; array fields (`integer array a[4]`) take a slot of their own."""

    def __init__(self, name, out, type, owner, is_static, is_constant=False,
                 is_array=False, size=None, init=None, access=None,
                 is_delegate=False, module=None, loc=None):
        self.name = name
        self.out = out
        self.type = type
        self.owner = owner
        self.is_static = is_static
        self.is_constant = is_constant
        self.is_array = is_array
        self.size = size
        self.init = init
        self.access = access
        self.is_delegate = is_delegate
        self.module = module
        self.loc = loc
        self.storage = None        # Storage for the values
        self.offset_storage = None  # for instance array fields

    def __repr__(self):
        return f'<FieldInfo {self.out}>'


class DispatchSlot(object):
    """A virtual method: an interface method or a stub method. Calls through
    a reference whose dynamic type is not known go through a trigger array
    indexed by type id - `st__<key>_<name>[typeid]`."""

    def __init__(self, owner, name, method):
        self.owner = owner          # StructInfo that declared it first
        self.name = name
        self.method = method        # the declaring FuncInfo (signature)
        self.impl = {}              # typeid -> FuncInfo
        self.needed = False         # set once a call actually dispatches

    @property
    def key(self):
        return f'{self.owner.out}_{self.name}'


class ModuleUse(object):
    """One `implement` of a module into a struct."""

    def __init__(self, module, struct):
        self.module = module
        self.struct = struct

    @property
    def prefix(self):
        return self.module.name + '__'


class ModuleInfo(object):
    def __init__(self, name, decl, scope):
        self.name = name
        self.decl = decl
        self.scope = scope


class StructInfo(object):
    """A struct or an interface."""

    def __init__(self, name, out, decl, scope, is_interface=False):
        self.name = name
        self.out = out
        self.decl = decl
        self.scope = scope
        self.is_interface = is_interface
        self.is_array_struct = False
        self.parent = None          # parent StructInfo (a struct or interface)
        self.children = []
        self.typeid = 0
        self.fields = {}            # name -> FieldInfo (own)
        self.methods = {}           # name -> FuncInfo (own)
        self.member_order = []      # own members in declaration order
        self.operators = {}         # '[]', '[]=', '<', '==', ('get', x), ('set', x)
        self.delegates = []
        self.on_inits = []          # FuncInfo, module ones first
        self.modules = []           # ModuleUse, in implement order
        self.max_size = None        # instance limit
        self.has_create = False     # interface: `static method create`
        self.built = False
        self.slot_fields = []       # instance array fields
        self.slot_capacity = None
        self.dispatch = {}          # name -> DispatchSlot declared here

    # -- family -------------------------------------------------------------

    @property
    def root(self):
        """The struct or interface that owns the instance pool."""
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    @property
    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def descendants(self):
        for child in self.children:
            yield child
            for grandchild in child.descendants():
                yield grandchild

    def family(self):
        root = self.root
        yield root
        for member in root.descendants():
            yield member

    @property
    def polymorphic(self):
        """Needs a per-instance type id: part of an interface family or of an
        inheritance tree, or declaring a stub method."""
        root = self.root
        if root.is_interface or root.children:
            return True
        return any(m.is_stub for m in root.methods.values())

    def is_subtype_of(self, other):
        node = self
        while node is not None:
            if node is other:
                return True
            node = node.parent
        return False

    # -- members ----------------------------------------------------------------

    def find_field(self, name):
        node = self
        while node is not None:
            field = node.fields.get(name)
            if field is not None:
                return field
            node = node.parent
        return None

    def find_method(self, name):
        node = self
        while node is not None:
            method = node.methods.get(name)
            if method is not None:
                return method
            node = node.parent
        return None

    def find_operator(self, key):
        node = self
        while node is not None:
            op = node.operators.get(key)
            if op is not None:
                return op
            node = node.parent
        return None

    def find_dispatch(self, name):
        node = self
        while node is not None:
            slot = node.dispatch.get(name)
            if slot is not None:
                return slot
            node = node.parent
        return None

    def all_delegates(self):
        node = self
        while node is not None:
            for field in node.delegates:
                yield field
            node = node.parent

    def __repr__(self):
        kind = 'interface' if self.is_interface else 'struct'
        return f'<{kind} {self.out}>'


class FuncInterfaceInfo(object):
    def __init__(self, name, out, params, returns, decl, scope):
        self.name = name
        self.out = out
        self.params = params
        self.returns = returns
        self.decl = decl
        self.scope = scope
        self.prototype = None

    def __repr__(self):
        return f'<function interface {self.out}>'


class DynamicArrayInfo(object):
    def __init__(self, name, out, element, size, storage_size, decl, scope):
        self.name = name
        self.out = out
        self.element = element
        self.size = size
        self.storage_size = storage_size
        self.decl = decl
        self.scope = scope
        self.storage = None
        self.pool = None

    def __repr__(self):
        return f'<dynamic array {self.out}>'


VJASS_TYPES = (StructInfo, FuncInterfaceInfo, DynamicArrayInfo)


def out_type(t):
    """The JASS type a vJass type compiles to."""
    if isinstance(t, VJASS_TYPES):
        return 'integer'
    return t


def type_name(t):
    if isinstance(t, (StructInfo, FuncInterfaceInfo, DynamicArrayInfo)):
        return t.name
    return str(t)


def is_integer_like(t):
    return t == 'integer' or isinstance(t, VJASS_TYPES)


def check_name_free(registry, name, loc, what='Identifier'):
    if name in registry:
        raise VJassError(f'{what} redeclared: {name}', loc,
                         note=f'first declared at {registry[name]}')
