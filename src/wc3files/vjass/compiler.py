"""The vJass compiler: declarations, libraries, scopes, structs.

`Compiler.compile(preprocessed)` runs every step after preprocessing:

1. parse each segment (vJass or Zinc) into one list of top-level items;
2. order libraries by their requirements, then everything else in file order;
3. declare every name, applying the scope rules (`public` -> `Scope_name`,
   `private` -> `Scope__name`);
4. resolve types and build structs - members, modules, inheritance,
   interfaces, operators, type ids;
5. compile every function body (`codegen.FunctionCompiler`);
6. generate the runtime (`runtime.Runtime`): allocators, dispatch triggers,
   `.evaluate` prototypes, hooks, initialization;
7. route calls to functions declared further down through triggers, and
   optionally inline.

Naming follows JassHelper's, so compiled code that names generated functions
(`ExecuteFunc("s__Foo_onInit")`) keeps working: struct members are
`s__Struct_member`, type ids `si__Struct`, pool arrays `si__Struct_V`, and so
on.
"""
from wc3files.scripts import ir

from . import ast
from .errors import VJassError, VJassWarning, Location
from .symbols import (ScopeInfo, Symbol, FuncInfo, VarInfo, FieldInfo,
                      StructInfo, ModuleInfo, ModuleUse, FuncInterfaceInfo,
                      DynamicArrayInfo, DispatchSlot, out_type, type_name)
from .storage import make_storage, PlainStorage
from .emitter import int_lit, str_lit
from .parser import parse_lines, parse_member_tokens


class Unit(object):
    """One thing in the output order: a function, a struct, a marker."""
    __slots__ = ('kind', 'node', 'scope', 'info')

    def __init__(self, kind, node, scope, info=None):
        self.kind = kind
        self.node = node
        self.scope = scope
        self.info = info


class GlobalEntry(object):
    __slots__ = ('var', 'info', 'scope', 'struct_field', 'comment')

    def __init__(self, var=None, info=None, scope=None, struct_field=None,
                 comment=None):
        self.var = var
        self.info = info
        self.scope = scope
        self.struct_field = struct_field
        self.comment = comment


class LibraryInfo(object):
    def __init__(self, decl, scope):
        self.decl = decl
        self.scope = scope
        self.name = decl.name


OPERATOR_SUFFIX = {
    '[]': '_getindex', '[]=': '_setindex', '<': '_lessthan', '==': '_equals',
}


class Compiler(object):

    def __init__(self, options, api):
        self.options = options
        self.api = api
        self.warnings = []
        self.global_scope = ScopeInfo('', None, 'global')
        self.out_names = {}            # every emitted global name -> loc
        self.functions = {}            # out name -> FuncInfo (user + generated)
        self.type_defs = []            # ir.TypeDef for `type x extends handle`
        self.user_types = {}           # typedef name -> parent
        self.units = []                # output order
        self.globals = []              # GlobalEntry in output order
        self.natives = []
        self.structs = []
        self.func_interfaces = []
        self.dynamic_arrays = []
        self.libraries = {}            # name -> LibraryInfo
        self.library_order = []
        self.scope_inits = []          # (initializer ast, scope)
        self.hooks = []                # (Hook ast, scope)
        self.injects = {}              # 'main'/'config' -> Inject
        self.loaddata = []             # (LoadData, scope)
        self.next_id = 1               # shared by type ids and keys
        self.big_arrays = []           # storages needing accessor functions
        self.lambda_counter = 0

    # ------------------------------------------------------------------ errors

    def error(self, message, loc=None, note=None):
        return VJassError(message, loc, 'compile', note)

    def warn(self, message, loc=None):
        self.warnings.append(VJassWarning(message, loc))

    def note_unknown(self, name, loc):
        """One warning per undeclared name, not one per use."""
        seen = self.__dict__.setdefault('_unknown_names', set())
        if name not in seen:
            seen.add(name)
            self.warn(f'undeclared identifier {name} left as is', loc)

    # ------------------------------------------------------------------ parse

    def parse(self, preprocessed):
        items = []
        for kind, lines in preprocessed.segments:
            if kind == 'zinc':
                from .zinc import parse_zinc_lines
                items.extend(parse_zinc_lines(lines, self))
            else:
                items.extend(parse_lines(lines))
        return items

    # ------------------------------------------------------------------ top

    def compile(self, preprocessed):
        from .codegen import compile_units
        from .runtime import Runtime

        items = self.parse(preprocessed)
        items = self.expand_top_static_ifs(items)
        ordered = self.order_libraries(items)
        for item, scope in ordered:
            self.declare_item(item, scope)
        self.resolve_declarations()
        self.build_structs()
        runtime = Runtime(self)
        self.runtime = runtime
        functions = compile_units(self, runtime)
        program = runtime.assemble(functions)
        return program

    # ------------------------------------------------------------------ libraries

    def _library_names(self, items, out):
        for item in items:
            if isinstance(item, ast.Library):
                out.add(item.name)
            elif isinstance(item, ast.StaticIfDecl):
                for _, branch in item.branches:
                    self._library_names(branch, out)
                if item.else_items:
                    self._library_names(item.else_items, out)
        return out

    def expand_top_static_ifs(self, items):
        names = self._library_names(items, set())
        self.known_libraries = names
        out = []
        for item in items:
            if isinstance(item, ast.StaticIfDecl) and any(
                    isinstance(i, ast.Library) for _, b in item.branches
                    for i in b):
                out.extend(self.expand_top_static_ifs(
                    self.choose_static_branch(item.branches, item.else_items,
                                              self.global_scope, item.loc)))
            else:
                out.append(item)
        return out

    def order_libraries(self, items):
        libraries = []
        others = []
        by_name = {}
        for item in items:
            if isinstance(item, ast.Library):
                previous = by_name.get(item.name)
                if previous is not None:
                    if item.once or previous.once:
                        continue
                    raise self.error(f'library redeclared : {item.name}',
                                     item.loc, note=f'first at {previous.loc}')
                by_name[item.name] = item
                libraries.append(item)
            else:
                others.append(item)
        self.check_no_nested_libraries(others)

        order = []
        state = {}

        def visit(lib, path):
            mark = state.get(lib.name)
            if mark == 'done':
                return
            if mark == 'active':
                cycle = path[path.index(lib.name):] + [lib.name]
                raise self.error('Library requirements cycle, involved '
                                 'libraries are: ' + ', '.join(cycle), lib.loc)
            state[lib.name] = 'active'
            for name, optional, loc in lib.requires:
                required = by_name.get(name)
                if required is None:
                    if optional:
                        continue
                    raise self.error(f'Missing requirement: {name}', loc,
                                     note=f'required by library {lib.name}')
                visit(required, path + [lib.name])
            state[lib.name] = 'done'
            order.append(lib)

        for lib in libraries:
            visit(lib, [])

        # The order itself is JassHelper's, not a plain topological sort: take
        # the alphabetically first library with no pending requirement; only
        # when none is left, drop the requirements already written out. Real
        # maps lean on it - a library calling another it never `requires`
        # works because the callee sorts first.
        pending = {}
        for lib in libraries:
            pending[lib.name] = [name for name, optional, _ in lib.requires
                                 if name in by_name]
        written = []
        removed = set()
        while pending:
            ready = [name for name, reqs in pending.items() if not reqs]
            if not ready:
                for name in pending:
                    pending[name] = [r for r in pending[name] if r not in removed]
                ready = [name for name, reqs in pending.items() if not reqs]
            pick = min(ready)
            written.append(by_name[pick])
            removed.add(pick)
            del pending[pick]
        self.library_order = [lib.name for lib in written]
        return [(lib, self.global_scope) for lib in written] + \
               [(item, self.global_scope) for item in others]

    def check_no_nested_libraries(self, items):
        for item in items:
            if isinstance(item, ast.Scope):
                for inner in item.items:
                    if isinstance(inner, ast.Library):
                        raise self.error('declaration of libraries inside of '
                                         'scope blocks not allowed', inner.loc)
                self.check_no_nested_libraries(item.items)

    # ------------------------------------------------------------------ names

    def register_out(self, out, loc, what='Identifier'):
        if out in self.out_names:
            raise self.error(f'{what} redeclared : "{out}"', loc,
                             note=f'first declared at {self.out_names[out]}')
        self.out_names[out] = loc or Location()

    def mangle(self, scope, name, access, loc):
        if access == 'private':
            if scope.kind == 'global':
                raise self.error('private outside library/scope definition', loc)
            return scope.private_prefix + name
        if access == 'public':
            if scope.kind == 'global':
                raise self.error('public outside library/scope definition', loc)
            return scope.public_prefix + name
        return name

    def declare(self, scope, name, access, kind, info, loc, register=True):
        out = self.mangle(scope, name, access, loc)
        symbol = Symbol(kind, name, out, info, scope, loc, access)
        existing = scope.names.get(name) if access else \
            self.global_scope.names.get(out)
        if existing is not None and existing.kind != 'keyword':
            raise self.error(f'Identifier redeclared : "{name}"', loc,
                             note=f'first declared at {existing.loc}')
        if access:
            scope.names[name] = symbol
            self.global_scope.names[out] = symbol
            if access == 'public':
                # A nested scope's public name is reachable from every
                # enclosing scope by its relative name: Outer sees
                # Outer_Inner_f as Inner_f.
                relative = name
                inner = scope
                outer = scope.parent
                while outer is not None and outer.kind != 'global':
                    relative = inner.name + '_' + relative
                    if relative not in outer.names:
                        outer.names[relative] = symbol
                    inner = outer
                    outer = outer.parent
        else:
            self.global_scope.names[out] = symbol
        if register:
            self.register_out(out, loc)
        return symbol

    # ------------------------------------------------------------------ declare

    def declare_item(self, item, scope):
        loc = item.loc
        if isinstance(item, ast.GlobalsBlock):
            for var in item.vars:
                self.declare_global(var, scope)
            return
        if isinstance(item, ast.KeyDecl):
            info = VarInfo(item.name, None, 'integer', is_constant=True,
                           decl=item, scope=scope, loc=loc)
            symbol = self.declare(scope, item.name, item.access, 'global', info,
                                  loc)
            info.out = symbol.out
            info.constant_value = self.next_id
            self.next_id += 1
            self.globals.append(GlobalEntry(
                ir.GlobalVar('integer', info.out, is_constant=True,
                             initial=int_lit(info.constant_value)), info, scope))
            return
        if isinstance(item, ast.NativeDecl):
            info = FuncInfo(item.name, None, None, None, 'native', item, scope,
                            loc, item.is_constant)
            symbol = self.declare(scope, item.name, item.access, 'native', info,
                                  loc)
            info.out = symbol.out
            self.natives.append(info)
            return
        if isinstance(item, ast.FunctionDecl):
            info = FuncInfo(item.name, None, None, None, 'function', item, scope,
                            loc, item.is_constant)
            info.access = item.access
            symbol = self.declare(scope, item.name, item.access, 'function',
                                  info, loc)
            info.out = symbol.out
            self.functions[info.out] = info
            self.units.append(Unit('function', item, scope, info))
            return
        if isinstance(item, ast.FunctionInterface):
            info = FuncInterfaceInfo(item.name, None, None, None, item, scope)
            symbol = self.declare(scope, item.name, item.access, 'funciface',
                                  info, loc, register=False)
            info.out = symbol.out
            self.func_interfaces.append(info)
            return
        if isinstance(item, ast.TypeDecl):
            if item.is_array:
                info = DynamicArrayInfo(item.name, None, None, None, None, item,
                                        scope)
                symbol = self.declare(scope, item.name, item.access, 'darray',
                                      info, loc, register=False)
                info.out = symbol.out
                self.dynamic_arrays.append(info)
                self.units.append(Unit('darray', item, scope, info))
            else:
                symbol = self.declare(scope, item.name, item.access, 'type',
                                      item.name, loc, register=False)
                symbol.info = symbol.out
                self.user_types[symbol.out] = item.base
                self.type_defs.append((item, scope, symbol.out))
            return
        if isinstance(item, ast.StructDecl):
            info = StructInfo(item.name, None, item, scope)
            symbol = self.declare(scope, item.name, item.access, 'struct', info,
                                  loc, register=False)
            info.out = symbol.out
            info.typeid = self.next_id
            self.next_id += 1
            self.structs.append(info)
            self.units.append(Unit('struct', item, scope, info))
            return
        if isinstance(item, ast.InterfaceDecl):
            info = StructInfo(item.name, None, item, scope, is_interface=True)
            symbol = self.declare(scope, item.name, item.access, 'interface',
                                  info, loc, register=False)
            info.out = symbol.out
            info.typeid = self.next_id
            self.next_id += 1
            self.structs.append(info)
            self.units.append(Unit('interface', item, scope, info))
            return
        if isinstance(item, ast.ModuleDecl):
            # Modules live in a namespace of their own: a struct and a module
            # may share a name, and real libraries do exactly that.
            info = ModuleInfo(item.name, item, scope)
            out = self.mangle(scope, item.name, item.access, loc)
            key = '#module:' + item.name
            previous = scope.names.get(key) if item.access else                 self.global_scope.names.get('#module:' + out)
            if previous is not None:
                raise self.error(f'module redeclared: {item.name}', loc,
                                 note=f'first declared at {previous.loc}')
            symbol = Symbol('module', item.name, out, info, scope, loc,
                            item.access)
            if item.access:
                scope.names[key] = symbol
            self.global_scope.names['#module:' + out] = symbol
            return
        if isinstance(item, ast.Library):
            lib_scope = ScopeInfo(item.name, self.global_scope, 'library')
            info = LibraryInfo(item, lib_scope)
            lib_scope.library = info
            self.global_scope.children.append(lib_scope)
            self.libraries[item.name] = info
            flag = VarInfo('LIBRARY_' + item.name, 'LIBRARY_' + item.name,
                           'boolean', is_constant=True, loc=loc)
            flag.constant_value = True
            self.global_scope.names[flag.out] = Symbol(
                'global', flag.out, flag.out, flag, self.global_scope, loc)
            self.register_out(flag.out, loc)
            self.globals.append(GlobalEntry(
                ir.GlobalVar('boolean', flag.out, is_constant=True,
                             initial=ir.Literal('bool', 'true')), flag,
                self.global_scope, comment=f'library {item.name}'))
            self.units.append(Unit('begin', item, lib_scope, 'library'))
            for inner in item.items:
                if isinstance(inner, ast.Library):
                    raise self.error('Library nesting is not allowed.', inner.loc)
                self.declare_item(inner, lib_scope)
            self.units.append(Unit('end', item, lib_scope, 'library'))
            if item.initializer is not None:
                self.scope_inits.append(('library', item.initializer, lib_scope))
            return
        if isinstance(item, ast.Scope):
            if scope.kind == 'global' and item.access:
                raise self.error('public/private scopes must be nested', loc)
            inner_scope = ScopeInfo(item.name, scope, 'scope',
                                    scope.library)
            if scope.names.get('#scope:' + item.name):
                raise self.error(f'Scope redeclared : {item.name}', loc)
            scope.names['#scope:' + item.name] = Symbol(
                'scope', item.name, inner_scope.full, inner_scope, scope, loc)
            scope.children.append(inner_scope)
            self.units.append(Unit('begin', item, inner_scope, 'scope'))
            for inner in item.items:
                self.declare_item(inner, inner_scope)
            self.units.append(Unit('end', item, inner_scope, 'scope'))
            if item.initializer is not None:
                self.scope_inits.append(('scope', item.initializer, inner_scope))
            return
        if isinstance(item, ast.Keyword):
            out = self.mangle(scope, item.name, item.access, loc)
            symbol = Symbol('keyword', item.name, out, None, scope, loc,
                            item.access)
            if item.access:
                scope.names.setdefault(item.name, symbol)
            self.global_scope.names.setdefault(out, symbol)
            return
        if isinstance(item, ast.Hook):
            self.hooks.append((item, scope))
            return
        if isinstance(item, ast.StaticIfDecl):
            for inner in self.choose_static_branch(item.branches,
                                                   item.else_items, scope,
                                                   item.loc):
                self.declare_item(inner, scope)
            return
        if isinstance(item, ast.Inject):
            if item.which in self.injects:
                raise self.error(f'//! inject {item.which} used twice', loc)
            self.injects[item.which] = (item, scope)
            return
        if isinstance(item, ast.LoadData):
            self.loaddata.append((item, scope))
            return
        raise self.error(f'Unexpected {type(item).__name__}', loc)

    def declare_global(self, var, scope):
        if isinstance(var, (ast.KeyDecl, ast.Keyword)):
            self.declare_item(var, scope)
            return
        info = VarInfo(var.name, None, None, var.is_array, var.is_constant,
                       decl=var, scope=scope, loc=var.loc)
        if var.access is None:
            # The same plain global declared twice with the same type is
            # accepted by pjass and the game alike; real maps do it (two
            # trigger files each declaring a shared array). Keep the first.
            existing = self.global_scope.names.get(var.name)
            if existing is not None and existing.kind == 'global' and \
                    isinstance(existing.info, VarInfo) and \
                    existing.info.decl is not None and \
                    not isinstance(existing.info.decl, ast.KeyDecl) and \
                    existing.info.decl.type == var.type and \
                    existing.info.is_array == var.is_array and \
                    existing.access is None:
                self.warn(f'global {var.name} declared again; the first '
                          'declaration is kept', var.loc)
                return
        symbol = self.declare(scope, var.name, var.access, 'global', info,
                              var.loc)
        info.out = symbol.out
        if var.is_constant and var.is_array:
            raise self.error('constant arrays are not supported', var.loc)
        if var.is_array and var.init is not None:
            raise self.error('Unable to initialize an array.', var.loc)
        # The constant value is wanted now, for `static if` and array sizes
        # further down; the type is resolved with everything else later.
        if var.is_constant and var.init is not None:
            info.constant_value = self.const_value(var.init, scope,
                                                   quiet=True)
        self.globals.append(GlobalEntry(None, info, scope))

    # ------------------------------------------------------------------ constants

    def const_value(self, expr, scope, quiet=False, struct=None):
        """Fold a constant integer/boolean/real expression, or None."""
        try:
            return self._const(expr, scope, struct)
        except _NotConstant:
            if quiet:
                return None
            raise self.error('Expected a constant expression', expr.loc)

    def _const(self, expr, scope, struct):
        if isinstance(expr, ast.Paren):
            return self._const(expr.expr, scope, struct)
        if isinstance(expr, ast.Literal):
            if expr.kind in ('int', 'rawcode', 'real'):
                return expr.value
            if expr.kind == 'bool':
                return expr.value
            raise _NotConstant()
        if isinstance(expr, ast.Name):
            name = expr.name
            if name == 'DEBUG_MODE':
                return bool(self.options.debug)
            if name in self.options.constant_overrides:
                return self.options.constant_overrides[name]
            symbol = scope.lookup(name) if scope else None
            if symbol is None:
                symbol = self.global_scope.names.get(name)
            if symbol is not None and symbol.kind == 'global' and \
                    isinstance(symbol.info, VarInfo) and \
                    symbol.info.constant_value is not None:
                return symbol.info.constant_value
            if struct is not None:
                field = struct.find_field(name)
                if field is not None and field.is_static and field.is_constant \
                        and field.init is not None:
                    return self._const(field.init, field.owner.scope, struct)
            api = self.api.globals.get(name)
            if api is not None and api.is_constant:
                raise _NotConstant()
            raise _NotConstant()
        if isinstance(expr, ast.Unary):
            value = self._const(expr.operand, scope, struct)
            if expr.op == 'not':
                return not value
            return -value
        if isinstance(expr, ast.Binary):
            left = self._const(expr.left, scope, struct)
            op = expr.op
            if op == 'and':
                return bool(left) and bool(self._const(expr.right, scope, struct))
            if op == 'or':
                return bool(left) or bool(self._const(expr.right, scope, struct))
            right = self._const(expr.right, scope, struct)
            if op == '+':
                return left + right
            if op == '-':
                return left - right
            if op == '*':
                return left * right
            if op == '/':
                if right == 0:
                    raise _NotConstant()
                if isinstance(left, int) and isinstance(right, int):
                    q = abs(left) // abs(right)
                    return q if (left >= 0) == (right >= 0) else -q
                return left / right
            if op == '==':
                return left == right
            if op == '!=':
                return left != right
            if op == '<':
                return left < right
            if op == '>':
                return left > right
            if op == '<=':
                return left <= right
            if op == '>=':
                return left >= right
        if isinstance(expr, ast.Member) and isinstance(expr.obj, ast.Name):
            # Struct.constantMember, Array.size and the like.
            symbol = scope.lookup(expr.obj.name) if scope else None
            if expr.obj.name == 'thistype' and struct is not None:
                target = struct
            elif symbol is not None and symbol.kind in ('struct', 'interface'):
                target = symbol.info
            elif symbol is not None and symbol.kind == 'darray':
                if expr.name == 'size':
                    info = symbol.info
                    if info.size is None:
                        info.size = self.const_int(info.decl.size, info.scope)
                    return info.size
                raise _NotConstant()
            else:
                raise _NotConstant()
            if expr.name == 'typeid':
                return target.typeid
            field = target.find_field(expr.name) if target.built or \
                expr.name in target.fields else None
            if field is None:
                # Not built yet: look at the declaration directly.
                for member in getattr(target.decl, 'members', []):
                    if isinstance(member, ast.Field) and member.name == expr.name \
                            and member.is_static and member.is_constant \
                            and member.init is not None:
                        return self._const(member.init, target.scope, target)
                raise _NotConstant()
            if field.is_static and field.is_constant and field.init is not None:
                return self._const(field.init, field.owner.scope, target)
        raise _NotConstant()

    def const_int(self, expr, scope, what='size', struct=None):
        value = self.const_value(expr, scope, quiet=True, struct=struct)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self.error(f'Expected a constant integer {what}', expr.loc)
        return value

    def static_condition(self, expr, scope, struct=None):
        """Evaluate a `static if` condition."""
        def walk(e):
            if isinstance(e, ast.Paren):
                return walk(e.expr)
            if isinstance(e, ast.Unary) and e.op == 'not':
                return not walk(e.operand)
            if isinstance(e, ast.Binary) and e.op in ('and', 'or'):
                left = walk(e.left)
                if e.op == 'and':
                    return left and walk(e.right)
                return left or walk(e.right)
            if isinstance(e, ast.Member) and e.name == 'exists' and \
                    isinstance(e.obj, ast.Member):
                target = self.static_struct(e.obj.obj, scope, struct)
                if target is not None:
                    return self.struct_declares(target, e.obj.name)
            if isinstance(e, ast.Name) and e.name.startswith('LIBRARY_'):
                if e.name in self.options.constant_overrides:
                    return bool(self.options.constant_overrides[e.name])
                return e.name[len('LIBRARY_'):] in self.known_libraries
            value = self.const_value(e, scope, quiet=True, struct=struct)
            if value is None or not isinstance(value, bool):
                got = e.name if isinstance(e, ast.Name) else type(e).__name__
                raise self.error('Expected: not, library name or constant '
                                 f'boolean name but got "{got}"', e.loc)
            return value
        return walk(expr)

    def static_struct(self, expr, scope, struct):
        if isinstance(expr, ast.Name):
            if expr.name == 'thistype':
                return struct
            symbol = scope.lookup(expr.name) if scope else None
            if symbol is None:
                symbol = self.global_scope.names.get(expr.name)
            if symbol is not None and symbol.kind in ('struct', 'interface'):
                return symbol.info
        return None

    def choose_static_branch(self, branches, else_items, scope, loc,
                             struct=None):
        for cond, items in branches:
            if self.static_condition(cond, scope, struct):
                return items
        return else_items or []

    # ------------------------------------------------------------------ types

    def resolve_type(self, name, scope, struct=None, loc=None, allow_nothing=False):
        if name == 'nothing':
            if allow_nothing:
                return 'nothing'
            raise self.error('nothing is not a type here', loc)
        if name == 'thistype':
            if struct is None:
                raise self.error('thistype outside of a struct', loc)
            return struct
        symbol = scope.lookup(name) if scope is not None else None
        if symbol is None:
            symbol = self.global_scope.names.get(name)
        if symbol is not None:
            if symbol.kind in ('struct', 'interface', 'funciface', 'darray'):
                return symbol.info
            if symbol.kind == 'type':
                return symbol.out
        if name in self.api.types or name in self.user_types:
            return name
        raise self.error(f'Undeclared type: {name}', loc)

    def type_parent(self, t):
        if t in self.user_types:
            return self.user_types[t]
        return self.api.types.get(t)

    def is_handle_type(self, t):
        seen = set()
        while isinstance(t, str) and t not in seen:
            if t == 'handle':
                return True
            seen.add(t)
            t = self.type_parent(t)
        return False

    def resolve_params(self, params, scope, struct=None):
        seen = set()
        out = []
        for param in params:
            if param.name in seen:
                raise self.error('The same argument name was used multiple '
                                 'times', param.loc)
            seen.add(param.name)
            out.append((self.resolve_type(param.type, scope, struct, param.loc),
                        param.name))
        return out

    def resolve_declarations(self):
        for item, scope, out in self.type_defs_pending():
            parent = self.resolve_type(item.base, scope, loc=item.loc)
            if not isinstance(parent, str):
                raise self.error('a type can only extend a handle type',
                                 item.loc)
            self.user_types[out] = parent
            self.type_defs.append(ir.TypeDef(out, parent))
        for entry in self.globals:
            info = entry.info
            if info is None or info.type is not None:
                continue
            var = info.decl
            info.type = self.resolve_type(var.type, info.scope, loc=var.loc)
            if var.is_array and var.sizes:
                info.sizes = [self.const_int(s, info.scope) for s in var.sizes]
                for size in info.sizes:
                    if size <= 0:
                        raise self.error('Zero array size?' if size == 0 else
                                         'Negative array size?', var.loc)
                info.storage = make_storage(info.out, out_type(info.type),
                                            info.size, self.options)
                if not isinstance(info.storage, PlainStorage):
                    self.big_arrays.append(info.storage)
            elif var.is_array:
                info.storage = PlainStorage(info.out, out_type(info.type))
        for info in self.natives:
            decl = info.decl
            info.params = self.resolve_params(decl.params, info.scope)
            info.returns = self.resolve_type(decl.returns, info.scope,
                                             loc=decl.loc, allow_nothing=True)
        for info in list(self.functions.values()):
            if info.kind != 'function':
                continue
            decl = info.decl
            info.params = self.resolve_params(decl.params, info.scope)
            info.returns = self.resolve_type(decl.returns, info.scope,
                                             loc=decl.loc, allow_nothing=True)
        for info in self.func_interfaces:
            decl = info.decl
            info.params = self.resolve_params(decl.params, info.scope)
            info.returns = self.resolve_type(decl.returns, info.scope,
                                             loc=decl.loc, allow_nothing=True)
        for info in self.dynamic_arrays:
            decl = info.decl
            info.element = self.resolve_type(decl.base, info.scope, loc=decl.loc)
            if info.size is None:
                info.size = self.const_int(decl.size, info.scope)
            if info.size <= 0:
                raise self.error('Zero array size?', decl.loc)
            info.storage_size = self.const_int(decl.storage, info.scope) \
                if decl.storage is not None else self.options.max_index
            if info.storage_size // info.size < 8:
                raise self.error('Not enough storage space for more than 8 '
                                 'instances, considered impractical, array '
                                 f'size={info.size}', decl.loc)
            info.storage = make_storage('s__' + info.out, out_type(info.element),
                                        info.storage_size + 1, self.options)
            info.pool = make_storage('si__' + info.out + '_V', 'integer',
                                     info.storage_size + 1, self.options)
            for storage in (info.storage, info.pool):
                if not isinstance(storage, PlainStorage):
                    self.big_arrays.append(storage)

    def type_defs_pending(self):
        pending = [d for d in self.type_defs if isinstance(d, tuple)]
        self.type_defs = [d for d in self.type_defs if not isinstance(d, tuple)]
        return pending

    # ------------------------------------------------------------------ structs

    def find_module(self, name, scope, loc):
        key = '#module:' + name
        symbol = scope.lookup(key) or self.global_scope.names.get(key)
        if symbol is None or symbol.kind != 'module':
            return None
        return symbol.info

    def build_structs(self):
        for struct in self.structs:
            self.build_struct(struct, [])
        for struct in self.structs:
            self.finish_struct(struct)

    def build_struct(self, struct, stack):
        if struct.built:
            return
        if struct in stack:
            raise self.error('Found an "extends" cycle', struct.decl.loc)
        decl = struct.decl
        if struct.is_interface:
            self.build_interface(struct)
            struct.built = True
            return

        if decl.extends == 'array':
            struct.is_array_struct = True
        elif decl.extends is not None:
            parent = self.resolve_type(decl.extends, struct.scope, loc=decl.loc)
            if not isinstance(parent, StructInfo):
                raise self.error(f'Unable to extend {decl.extends}', decl.loc)
            if decl.size is not None:
                raise self.error('Cannot use extends on an struct that specifies '
                                 'max indexes', decl.loc)
            self.build_struct(parent, stack + [struct])
            if parent.is_array_struct:
                raise self.error('Unable to extend an array struct: '
                                 f'{parent.name}', decl.loc)
            struct.parent = parent
            parent.children.append(struct)

        if decl.size is not None:
            size = self.const_int(decl.size, struct.scope, 'max indexes')
            if size <= 0:
                raise self.error('Invalid max indexes specifier', decl.loc)
            struct.max_size = size
        elif struct.parent is not None:
            struct.max_size = struct.root.max_size
        else:
            struct.max_size = self.options.max_index

        tokens = self.expand_body(struct, decl.tokens, None, set())
        members = parse_member_tokens(tokens)
        struct.declared_names = set()
        self.collect_declared_names(members, struct.declared_names)
        for member, use in self.flatten_members(members, struct, None, set()):
            if isinstance(member, ast.Field):
                self.add_field(struct, member, use)
            else:
                self.add_method(struct, member, use)
        struct.built = True

    def collect_declared_names(self, members, names):
        for member in members:
            if isinstance(member, (ast.Field, ast.Method)):
                names.add(member.name)
            elif isinstance(member, ast.StaticIfMember):
                for _, branch in member.branches:
                    self.collect_declared_names(branch, names)
                if member.else_members:
                    self.collect_declared_names(member.else_members, names)

    def struct_declares(self, struct, name):
        """For `static if thistype.name.exists`: does the struct (or a
        parent) declare a member called `name`?"""
        node = struct
        while node is not None:
            names = getattr(node, 'declared_names', None)
            if names is not None and name in names:
                return True
            if name in node.fields or name in node.methods:
                return True
            node = node.parent
        return False

    # -- modules ---------------------------------------------------------------

    def module_privates(self, module):
        """Names of the members a module declares `private`. JassHelper
        renames them to `Module__name` inside the module's text, which is what
        keeps them out of reach of the struct and of other modules."""
        cached = getattr(module, 'privates', None)
        if cached is not None:
            return cached
        privates = set()
        line = []
        for token in module.decl.tokens + [None]:
            if token is None or token.kind in ('NL', 'DIRECTIVE'):
                words = [t for t in line]
                line = []
                if not words or not words[0].is_word('private'):
                    continue
                rest = [t for t in words[1:]]
                while rest and rest[0].is_word('static', 'stub', 'constant',
                                               'delegate', 'readonly'):
                    rest = rest[1:]
                if not rest:
                    continue
                if rest[0].is_word('method'):
                    if len(rest) > 1 and rest[1].kind == 'ID' and \
                            not rest[1].is_word('operator'):
                        privates.add(rest[1].value)
                    continue
                # A variable: type [array] name
                rest = rest[1:]
                if rest and rest[0].is_word('array'):
                    rest = rest[1:]
                if rest and rest[0].kind == 'ID':
                    privates.add(rest[0].value)
                continue
            line.append(token)
        module.privates = privates
        return privates

    def expand_body(self, struct, tokens, origin, implemented, depth=0):
        """Paste every `implement`ed module into a struct body."""
        if depth > 32:
            raise self.error('modules implement each other in a cycle',
                             struct.decl.loc)
        out = []
        i = 0
        count = len(tokens)
        line_start = True
        while i < count:
            token = tokens[i]
            if line_start and token.is_word('implement'):
                j = i + 1
                optional = False
                if j < count and tokens[j].is_word('optional'):
                    optional = True
                    j += 1
                if j >= count or tokens[j].kind != 'ID':
                    raise self.error('Expected a module name after implement',
                                     token.location)
                name_token = tokens[j]
                j += 1
                if j < count and tokens[j].kind != 'NL':
                    raise self.error('Expected end of line after implement',
                                     tokens[j].location)
                j += 1
                scope = origin.module.scope if origin is not None else \
                    struct.scope
                module = self.find_module(name_token.value, scope,
                                          token.location)
                if module is None:
                    if not optional:
                        raise self.error('Could not find module: '
                                         f'{name_token.value}', token.location)
                elif module.name not in implemented:
                    implemented.add(module.name)
                    use = ModuleUse(module, struct)
                    struct.modules.append(use)
                    privates = self.module_privates(module)
                    pasted = []
                    for t in module.decl.tokens:
                        if t.kind == 'ID' and t.value in privates:
                            pasted.append(t.copy(origin=use,
                                                 value=use.prefix + t.value,
                                                 raw=use.prefix + t.value))
                        else:
                            pasted.append(t.copy(origin=use))
                    if pasted and pasted[-1].kind != 'NL':
                        pasted.append(name_token.copy(kind='NL', value='\n',
                                                      raw='', origin=use))
                    out.extend(self.expand_body(struct, pasted, use,
                                                implemented, depth + 1))
                i = j
                line_start = True
                continue
            out.append(token)
            line_start = token.kind in ('NL', 'DIRECTIVE')
            i += 1
        return out

    def flatten_members(self, members, struct, use, implemented):
        for member in members:
            member_use = getattr(member, 'module', None)
            scope = member_use.module.scope if member_use else struct.scope
            if isinstance(member, ast.Implement):
                if not isinstance(struct, StructInfo) or struct.is_interface:
                    raise self.error('Cannot call implement outside structs or '
                                     'modules', member.loc)
                module = self.find_module(member.module, scope, member.loc)
                if module is None:
                    if member.optional:
                        continue
                    raise self.error(f'Could not find module: {member.module}',
                                     member.loc)
                if module.name in implemented:
                    continue
                implemented.add(module.name)
                module_use = ModuleUse(module, struct)
                struct.modules.append(module_use)
                for item in self.flatten_members(module.decl.members, struct,
                                                 module_use, implemented):
                    yield item
            elif isinstance(member, ast.StaticIfMember):
                chosen = self.choose_static_branch(member.branches,
                                                   member.else_members, scope,
                                                   member.loc, struct)
                for item in self.flatten_members(chosen, struct, use,
                                                 implemented):
                    yield item
            else:
                yield member, member_use

    def member_key(self, member, use):
        # A module's private members arrive already renamed (Module__name).
        return member.name

    def check_member_free(self, struct, key, loc, member_name):
        if key in struct.fields or key in struct.methods:
            raise self.error(f'Member redeclared : {member_name}', loc)
        for ancestor in struct.ancestors:
            if ancestor.is_interface:
                continue
            if key in ancestor.fields:
                raise self.error('Member name already in use by a parent type',
                                 loc, note=f'{member_name} in {ancestor.name}')

    def add_field(self, struct, member, use):
        key = self.member_key(member, use)
        self.check_member_free(struct, key, member.loc, member.name)
        scope = use.module.scope if use else struct.scope
        ftype = self.resolve_type(member.type, scope, struct, member.loc)
        out = f's__{struct.out}_{key}'
        field = FieldInfo(key, out, ftype, struct, member.is_static,
                          member.is_constant, member.is_array, None, member.init,
                          member.access, member.is_delegate, use, member.loc)
        field.scope = scope
        if member.is_array and member.size is not None:
            field.size = self.const_int(member.size, scope, struct=struct)
            if field.size <= 0:
                raise self.error('Zero array size?', member.loc)
        if member.is_constant and not member.is_static:
            raise self.error('constant instance members are not supported (Did '
                             'you want an static variable instead?)', member.loc)
        if member.is_constant and member.init is None:
            raise self.error('A constant requires initializing.', member.loc)
        if member.is_delegate:
            if member.is_static or member.is_array:
                raise self.error('a delegate must be a plain instance member',
                                 member.loc)
            if not isinstance(ftype, StructInfo):
                raise self.error('a delegate must be of a struct type',
                                 member.loc)
            struct.delegates.append(field)
        if member.is_static:
            self.register_out(out, member.loc)
            if member.is_array:
                length = field.size if field.size else None
                field.storage = make_storage(out, out_type(ftype), length,
                                             self.options)
                if not isinstance(field.storage, PlainStorage):
                    self.big_arrays.append(field.storage)
                if member.init is not None:
                    raise self.error('Unable to initialize an array.', member.loc)
        else:
            if struct.is_array_struct and member.init is not None:
                raise self.error('Array structs cannot have default values.',
                                 member.loc)
            if member.is_array:
                if struct.is_array_struct:
                    raise self.error('Array structs cannot have array members '
                                     'yet, use a dynamic array instead, if '
                                     'necessary.', member.loc)
                if not field.size:
                    raise self.error('array instance variables are not '
                                     'supported (give it a size: '
                                     f'{member.type} array {member.name}[n])',
                                     member.loc)
                if field.size > self.options.max_index:
                    raise self.error('Size breaks internal limits.', member.loc)
                struct.slot_fields.append(field)
            self.register_out(out, member.loc)
        struct.fields[key] = field
        struct.member_order.append(field)
        return field

    def add_method(self, struct, member, use):
        key = self.member_key(member, use)
        scope = use.module.scope if use else struct.scope
        if member.operator is not None:
            if member.operator in OPERATOR_SUFFIX:
                op_key = member.operator
                suffix = OPERATOR_SUFFIX[op_key]
                if member.is_static and op_key in ('[]', '[]='):
                    suffix = '_static' + suffix[1:]
                    op_key = 'static' + op_key
            else:
                op_key = (member.operator, member.name)
                suffix = ('_get_' if member.operator == 'get' else '_set_') + \
                    member.name
            if use is not None and member.access == 'private':
                raise self.error('module private operators are not supported',
                                 member.loc)
            if struct.operators.get(op_key) is not None:
                raise self.error(f'Member redeclared : operator {member.name}',
                                 member.loc)
            key = '#op:' + suffix
            out = f's__{struct.out}_{suffix}'
        else:
            out = f's__{struct.out}_{key}'
            if key in struct.methods or key in struct.fields:
                raise self.error(f'Member redeclared : {member.name}', member.loc)
            for ancestor in struct.ancestors:
                if key in ancestor.fields and not ancestor.is_interface:
                    raise self.error('Member name already in use by a parent '
                                     'type', member.loc)
            if isinstance(member.name, str) and any(
                    op_key == ('get', member.name) or op_key == ('set', member.name)
                    for op_key in struct.operators):
                raise self.error(f'Member name already in use by an operator: '
                                 f'{member.name}', member.loc)

        params = self.resolve_params(member.params, scope, struct)
        returns = self.resolve_type(member.returns, scope, struct, member.loc,
                                    allow_nothing=True)
        info = FuncInfo(member.name, out, params, returns, 'method', member,
                        scope, member.loc, member.is_constant)
        info.struct = struct
        info.is_static = member.is_static
        info.is_stub = member.is_stub
        info.access = member.access
        info.operator = member.operator
        info.module = use
        info.key = key
        self.check_special_method(struct, info, member)

        if member.operator is not None:
            struct.operators[op_key] = info
            self.check_operator(info, member, op_key)
        else:
            # Overriding: allowed only for interface methods and stubs.
            slot = struct.parent.find_dispatch(key) if struct.parent else None
            inherited = struct.parent.find_method(key) if struct.parent else None
            if slot is not None:
                if member.is_static:
                    raise self.error(f'{member.name} should be a method.',
                                     member.loc)
                self.check_signature(slot.method, info, member.loc)
                info.dispatch = slot
                info.overrides = inherited
            elif inherited is not None and key not in ('onInit',):
                if inherited.is_static and member.is_static and key == 'create':
                    pass
                elif key in ('onDestroy', 'destroy') and not member.is_static:
                    # Each level runs its own onDestroy; the chain is built at
                    # runtime, so this is not an override.
                    pass
                else:
                    raise self.error('Member name already in use by a parent '
                                     'type', member.loc,
                                     note=f'{member.name} is declared in '
                                     f'{inherited.struct.name}; only stub and '
                                     'interface methods can be overridden')
            if member.is_stub and info.dispatch is None:
                if member.is_static:
                    raise self.error('a static method cannot be stub', member.loc)
                slot = DispatchSlot(struct, key, info)
                struct.dispatch[key] = slot
                info.dispatch = slot
            struct.methods[key] = info

        self.register_out(out, member.loc)
        self.functions[out] = info
        struct.member_order.append(info)
        return info

    def check_special_method(self, struct, info, member):
        name = member.name
        if member.operator is not None:
            return
        if name == 'onDestroy':
            if member.is_static:
                raise self.error('onDestroy must be an instance method.',
                                 member.loc)
            if info.returns != 'nothing' or info.params:
                raise self.error('Wrong onDestroy definition', member.loc,
                                 note='must take nothing and return nothing')
            if struct.is_array_struct:
                raise self.error('Array structs cannot be destroyed.', member.loc)
        if name == 'onInit' or (info.key.endswith('__onInit')):
            if not member.is_static:
                raise self.error('onInit must be static', member.loc)
            if info.params:
                raise self.error('onInit must have no argument list', member.loc)
        if name == 'create' and info.key == 'create':
            if not member.is_static:
                raise self.error('method create must be static', member.loc)
            if not (info.returns is struct or info.returns == 'integer' or
                    (isinstance(info.returns, StructInfo) and
                     struct.is_subtype_of(info.returns))):
                raise self.error(f'method create must return {struct.name}',
                                 member.loc)
        if name == 'destroy' and info.key == 'destroy':
            if member.is_static:
                raise self.error('method destroy must not be static', member.loc)
            if info.params:
                raise self.error('method destroy must have 0 arguments',
                                 member.loc)
            if info.returns != 'nothing':
                raise self.error('method destroy must return nothing', member.loc)

    def check_operator(self, info, member, op_key):
        if op_key in ('[]', 'static[]'):
            if len(info.params) != 1:
                raise self.error('operator [] requires 1 argument.', member.loc)
            if info.returns == 'nothing':
                raise self.error('[] operator must have return a value',
                                 member.loc)
        elif op_key in ('[]=', 'static[]='):
            if len(info.params) != 2:
                raise self.error('operator []= requires 2 arguments.',
                                 member.loc)
        elif op_key in ('<', '=='):
            if info.is_static:
                raise self.error('< and == are not supported for static '
                                 'operators.', member.loc)
            if len(info.params) != 1:
                raise self.error(f'operator {op_key} requires 1 argument',
                                 member.loc)
            if info.returns != 'boolean':
                raise self.error(f'{op_key} operator must return a boolean value',
                                 member.loc)
        elif op_key[0] == 'get':
            if info.params:
                raise self.error('Property operator must take nothing.',
                                 member.loc)
            if info.returns == 'nothing':
                raise self.error('Property operator must have a return type.',
                                 member.loc)
        elif op_key[0] == 'set':
            if len(info.params) != 1:
                raise self.error('Property assignment operator must have '
                                 'exactly one argument.', member.loc)

    def check_signature(self, expected, actual, loc):
        def norm(t):
            return out_type(t) if not isinstance(t, StructInfo) else t

        same = len(expected.params) == len(actual.params) and all(
            out_type(a[0]) == out_type(b[0])
            for a, b in zip(expected.params, actual.params)) and \
            out_type(expected.returns) == out_type(actual.returns)
        if not same:
            raise self.error(f'{actual.name} signatures don\'t match with the '
                             'declaration in the parent type', loc,
                             note=f'declared at {expected.loc}')

    def build_interface(self, struct):
        decl = struct.decl
        struct.max_size = self.options.max_index
        struct.has_create = decl.has_create
        for member in decl.members:
            if member.operator is not None:
                raise self.error('Operator < not supported as interface method '
                                 'yet.', member.loc)
            if member.name in struct.methods:
                raise self.error(f'Member redeclared : {member.name}', member.loc)
            params = self.resolve_params(member.params, struct.scope, struct)
            returns = self.resolve_type(member.returns, struct.scope, struct,
                                        member.loc, allow_nothing=True)
            info = FuncInfo(member.name, None, params, returns, 'method', member,
                            struct.scope, member.loc)
            info.struct = struct
            info.is_static = False
            info.key = member.name
            info.abstract = True
            if member.defaults is not None:
                if member.defaults == 'nothing':
                    if returns != 'nothing':
                        raise self.error(f'Cannot default nothing, method returns '
                                         f'{type_name(returns)}', member.loc)
                elif returns == 'nothing':
                    raise self.error('Only possible default is nothing since '
                                     'method returns nothing', member.loc)
                info.defaults = member.defaults
            slot = DispatchSlot(struct, member.name, info)
            info.dispatch = slot
            struct.dispatch[member.name] = slot
            struct.methods[member.name] = info

    def finish_struct(self, struct):
        """Checks and storage that need the whole family built."""
        if struct.is_interface:
            return
        decl = struct.decl
        # Every interface method must be implemented (or have a default).
        for ancestor in struct.ancestors:
            if not ancestor.is_interface:
                continue
            for name, method in ancestor.methods.items():
                impl = struct.find_method(name)
                if (impl is None or getattr(impl, 'abstract', False)) and \
                        method.defaults is None:
                    raise self.error(f'Missing method : {name}', decl.loc,
                                     note=f'declared in interface '
                                     f'{ancestor.name}')
            if ancestor.has_create:
                create = struct.find_method('create')
                if create is not None and create.params:
                    raise self.error(f'Since {struct.name} is a child of '
                                     f'{ancestor.name} create must be an static '
                                     'method that takes nothing.', create.loc)
        on_init = []
        for use in struct.modules:
            method = struct.methods.get(use.prefix + 'onInit')
            if method is not None:
                on_init.append(method)
        own = struct.methods.get('onInit')
        if own is not None:
            on_init.append(own)
        struct.on_inits = on_init

        length = struct.root.max_size + 1
        for field in struct.member_order:
            if not isinstance(field, FieldInfo) or field.is_static:
                continue
            if field.is_array:
                continue
            field.storage = make_storage(field.out, out_type(field.type), length,
                                         self.options)
            if not isinstance(field.storage, PlainStorage):
                self.big_arrays.append(field.storage)
        if struct.slot_fields:
            capacity = min(self.options.max_array_size // f.size
                           for f in struct.slot_fields)
            if capacity < 1:
                raise self.error('Size breaks internal limits.', decl.loc)
            struct.slot_capacity = capacity
            for field in struct.slot_fields:
                field.storage = PlainStorage('s__' + field.out[1:], out_type(
                    field.type))
                field.storage.name = 's___' + field.out[3:]
                field.offset_storage = make_storage(field.out, 'integer', length,
                                                    self.options)
                if not isinstance(field.offset_storage, PlainStorage):
                    self.big_arrays.append(field.offset_storage)


class _NotConstant(Exception):
    pass
