"""Compile function and method bodies to `wc3files.scripts.ir`.

Every vJass expression is resolved to one of the result kinds below before it
becomes JASS. `x.y` means a struct member, a static member, a method, a
property operator, `.evaluate` on a function, `.size` on an array - which one
depends on what `x` turns out to be, so resolution is a typed walk over the
tree rather than a rewrite of names.
"""
from wc3files.scripts import ir

from . import ast
from .emitter import ParenExpr, wrap, int_lit, str_lit, TRUE, FALSE, Comment
from .errors import VJassError
from .storage import offset, PlainStorage
from .symbols import (VarInfo, FieldInfo, FuncInfo, StructInfo,
                      FuncInterfaceInfo, DynamicArrayInfo, out_type, type_name,
                      VJASS_TYPES)


# --- resolution results --------------------------------------------------------

class Val(object):
    """A value: JASS expression plus its vJass type."""
    __slots__ = ('ir', 'type')

    def __init__(self, ir_expr, type):
        self.ir = ir_expr
        self.type = type


class Unknown(Val):
    """A name nothing declares. It passes through as written (as JassHelper
    does), so it can be read, assigned, indexed or called."""
    __slots__ = ('name',)

    def __init__(self, name):
        super().__init__(ir.VarExpr(name), '?')
        self.name = name


class TypeVal(object):
    __slots__ = ('type',)

    def __init__(self, t):
        self.type = t


class SuperVal(object):
    __slots__ = ('struct',)

    def __init__(self, struct):
        self.struct = struct


class FuncVal(object):
    __slots__ = ('func',)

    def __init__(self, func):
        self.func = func


class MethodVal(object):
    """A method, bound to an instance (`this`) or not. `static_type` is the
    struct the reference was typed as; `direct` skips dynamic dispatch
    (`super.m()`)."""
    __slots__ = ('method', 'this', 'static_type', 'direct')

    def __init__(self, method, this, static_type, direct=False):
        self.method = method
        self.this = this
        self.static_type = static_type
        self.direct = direct


class ArrayVal(object):
    """Something indexable: a global/local/static array, an array member.
    `dims` lists the remaining dimensions (None when unknown)."""
    __slots__ = ('storage', 'base', 'elem_type', 'dims', 'var')

    def __init__(self, storage, base, elem_type, dims, var=None):
        self.storage = storage
        self.base = base
        self.elem_type = elem_type
        self.dims = dims
        self.var = var


class Special(object):
    """A built-in member that only makes sense called: `.create`, `.destroy`,
    `.evaluate` and friends."""
    __slots__ = ('kind', 'info', 'this', 'mode', 'target')

    def __init__(self, kind, info=None, this=None, mode=None, target=None):
        self.kind = kind
        self.info = info
        self.this = this
        self.mode = mode
        self.target = target


class FuncIfacePath(object):
    """`Iface.Struct` on the way to `Iface.Struct.staticMethod`."""
    __slots__ = ('iface', 'struct')

    def __init__(self, iface, struct):
        self.iface = iface
        self.struct = struct


class InitPlaceholder(ir.Node):
    """Where `//! dovjassinit` was; the runtime puts initialization here."""
    _fields = ()


class Ctx(object):
    def __init__(self, scope, struct=None, use=None, func=None, has_this=False):
        self.scope = scope
        self.struct = struct
        self.use = use
        self.func = func
        self._has_this = has_this
        self.locals = {}

    @property
    def has_this(self):
        """Implicit `this` follows whatever `this` is in scope: the instance
        of an instance method, or a `local thistype this` a static method
        declares (a common vJass idiom JassHelper supports)."""
        return self._has_this or 'this' in self.locals


_LITERAL_TYPES = {'int': 'integer', 'real': 'real', 'string': 'string',
                  'rawcode': 'integer', 'bool': 'boolean', 'null': 'null'}


class FunctionCompiler(object):

    def __init__(self, compiler, runtime):
        self.c = compiler
        self.rt = runtime
        self.options = compiler.options
        self.ctx = None

    def error(self, message, node=None, note=None):
        loc = getattr(node, 'loc', None) if node is not None else None
        return VJassError(message, loc, 'compile', note)

    # ------------------------------------------------------------------ entry

    def compile_function(self, info, body, ctx, extra_params=()):
        self.ctx = ctx
        params = list(extra_params)
        if ctx._has_this and ctx.struct is not None:
            ctx.locals['this'] = VarInfo('this', 'this', ctx.struct)
        for ptype, pname in info.params:
            params.append(ir.Param(out_type(ptype), pname))
            ctx.locals[pname] = VarInfo(pname, pname, ptype)
        statements = self.compile_body(body)
        decl = ir.FunctionDecl(info.out, params, out_type(info.returns),
                               statements, is_constant=info.is_constant)
        decl.info = info
        self.ctx = None
        return decl

    def compile_expression(self, expr, ctx, expected=None):
        """Compile a standalone expression (a member default value)."""
        previous = self.ctx
        self.ctx = ctx
        try:
            return self.value(expr, expected)
        finally:
            self.ctx = previous

    # ------------------------------------------------------------------ bodies

    def compile_body(self, body):
        """JASS wants every local at the top. vJass already requires it, Zinc
        does not, so a local declared later (or inside a block) is hoisted
        and its initial value becomes a `set` where the declaration was."""
        hoisted = []
        statements = []
        leading = True
        for stmt in body:
            if isinstance(stmt, ast.LocalDecl) and leading:
                hoisted.append(self.local(stmt, keep_init=True))
                continue
            leading = False
            statements.extend(self.statement(stmt, hoisted))
        return hoisted + statements

    def local(self, stmt, keep_init):
        ctx = self.ctx
        if stmt.name in ctx.locals:
            existing = ctx.locals[stmt.name]
            raise self.error(f'Variable redeclared: {stmt.name}', stmt,
                             note=f'type {type_name(existing.type)}')
        ltype = self.c.resolve_type(stmt.type, ctx.scope, ctx.struct, stmt.loc)
        info = VarInfo(stmt.name, stmt.name, ltype, stmt.is_array)
        if stmt.is_array:
            info.storage = PlainStorage(stmt.name, out_type(ltype))
            if stmt.init is not None:
                raise self.error('Unable to initialize an array.', stmt)
        initial = None
        if stmt.init is not None and keep_init:
            initial = self.value(stmt.init, ltype)
        ctx.locals[stmt.name] = info
        return ir.Local(out_type(ltype), stmt.name, stmt.is_array, initial)

    def block(self, body, hoisted):
        out = []
        for stmt in body:
            out.extend(self.statement(stmt, hoisted))
        return out

    def statement(self, stmt, hoisted):
        if getattr(stmt, 'is_debug', False) and not self.options.debug:
            return []
        if isinstance(stmt, ast.LocalDecl):
            hoisted.append(self.local(stmt, keep_init=False))
            if stmt.init is None:
                return []
            info = self.ctx.locals[stmt.name]
            return [ir.SetStmt(stmt.name, self.value(stmt.init, info.type))]
        if isinstance(stmt, ast.SetStmt):
            return [self.assign(stmt.target, stmt.value)]
        if isinstance(stmt, ast.CallStmt):
            return [self.call_statement(stmt.expr)]
        if isinstance(stmt, ast.IfStmt):
            branches = [(self.condition(cond), self.block(body, hoisted))
                        for cond, body in stmt.branches]
            else_body = self.block(stmt.else_body, hoisted) \
                if stmt.else_body is not None else None
            return [ir.IfStmt(branches, else_body)]
        if isinstance(stmt, ast.LoopStmt):
            return [ir.LoopStmt(self.block(stmt.body, hoisted))]
        if isinstance(stmt, ast.ExitWhen):
            return [ir.ExitWhenStmt(self.condition(stmt.cond))]
        if isinstance(stmt, ast.ReturnStmt):
            return [self.return_statement(stmt)]
        if isinstance(stmt, ast.StaticIf):
            ctx = self.ctx
            chosen = self.c.choose_static_branch(stmt.branches, stmt.else_body,
                                                 ctx.scope, stmt.loc, ctx.struct)
            return self.block(chosen, hoisted)
        if isinstance(stmt, ast.InitMarker):
            return [InitPlaceholder()]
        raise self.error(f'Unexpected {type(stmt).__name__}', stmt)

    def condition(self, expr):
        return self.value(expr)

    def return_statement(self, stmt):
        func = self.ctx.func
        returns = func.returns if func is not None else 'nothing'
        if stmt.value is None:
            if returns != 'nothing':
                raise self.error('Expected a return value', stmt)
            return ir.ReturnStmt()
        if returns == 'nothing':
            raise self.error('Unexpected return value. (function declared to '
                             'return nothing)', stmt)
        return ir.ReturnStmt(self.value(stmt.value, returns))

    def call_statement(self, expr):
        if not isinstance(expr, ast.Call):
            result = self.resolve(expr)
            if isinstance(result, Val) and isinstance(result.ir, ir.CallExpr):
                return ir.CallStmt(result.ir.name, result.ir.args)
            raise self.error('Expected a function call after call', expr)
        result = self.call(expr)
        node = result.ir
        while isinstance(node, ParenExpr):
            node = node.expr
        if isinstance(node, ir.CallExpr):
            return ir.CallStmt(node.name, node.args)
        if isinstance(node, ir.UnaryExpr) and isinstance(node.operand, ir.CallExpr):
            return ir.CallStmt(node.operand.name, node.operand.args)
        raise self.error('Expected a function call after call', expr)

    # ------------------------------------------------------------------ values

    def value(self, expr, expected=None):
        """Resolve `expr` to a JASS expression. With `expected` a function
        interface type, a function name converts to its pointer."""
        result = self.resolve(expr)
        return self.coerce(result, expected, expr)

    def typed_value(self, expr):
        result = self.resolve(expr)
        if not isinstance(result, Val):
            raise self.expected_value(result, expr)
        return result

    def expected_value(self, result, expr):
        if isinstance(result, TypeVal):
            return self.error(f'{type_name(result.type)} is a type, a value was '
                              'expected', expr)
        if isinstance(result, (FuncVal, MethodVal)):
            name = result.func.name if isinstance(result, FuncVal) \
                else result.method.name
            return self.error(f'{name} is a function; call it, or use '
                              f'function {name} for a code value', expr)
        if isinstance(result, ArrayVal):
            return self.error('an array is not a value; index it', expr)
        if isinstance(result, Special):
            return self.error(f'.{result.kind} must be called', expr)
        return self.error('a value was expected', expr)

    def coerce(self, result, expected, expr):
        if isinstance(expected, FuncInterfaceInfo):
            pointer = self.function_pointer(result, expected, expr)
            if pointer is not None:
                return pointer
        if isinstance(expected, str) and isinstance(result, Val) and \
                result.type == 'code' and \
                expected in ('boolexpr', 'conditionfunc', 'filterfunc'):
            # `function f` where a boolexpr belongs: JassHelper wraps it.
            wrapper = 'Filter' if expected == 'filterfunc' else 'Condition'
            return ir.CallExpr(wrapper, [result.ir])
        func = None
        if isinstance(result, FuncVal):
            func = result.func
        elif isinstance(result, MethodVal) and result.method.is_static:
            func = result.method
        if func is not None:
            if func.kind in ('function', 'method'):
                # A function name used as a value is its pointer: the id it
                # has among functions of its signature, as JassHelper does.
                proto = self.rt.prototype(func.params, func.returns)
                return int_lit(proto.id_of(func))
            if not self.options.strict:
                self.c.warn(f'{func.name} is a function used as a value',
                            getattr(expr, 'loc', None))
                return ir.VarExpr(func.out)
        if not isinstance(result, Val):
            raise self.expected_value(result, expr)
        return result.ir

    def function_pointer(self, result, iface, expr):
        func = None
        if isinstance(result, FuncVal):
            func = result.func
        elif isinstance(result, MethodVal) and result.method.is_static:
            func = result.method
        if func is None:
            return None
        proto = self.rt.prototype(iface.params, iface.returns)
        if self.rt.signature_key(func.params, func.returns) != proto.key:
            raise self.error(f'{func.name} does not match function interface '
                             f'{iface.name}', expr)
        return int_lit(proto.id_of(func))

    # ------------------------------------------------------------------ resolve

    def resolve(self, expr):
        if isinstance(expr, ast.Literal):
            value = expr.raw if expr.kind not in ('string', 'bool', 'null') \
                else expr.value
            if expr.kind == 'string':
                value = expr.value
            elif expr.kind == 'bool':
                value = 'true' if expr.value else 'false'
            return Val(ir.Literal(expr.kind, value), _LITERAL_TYPES[expr.kind])
        if isinstance(expr, ast.Paren):
            inner = self.resolve(expr.expr)
            if isinstance(inner, Val):
                return Val(ParenExpr(inner.ir), inner.type)
            return inner
        if isinstance(expr, ast.Name):
            return self.resolve_name(expr)
        if isinstance(expr, ast.Member):
            if expr.implicit and not self.ctx.has_this:
                if self.ctx.struct is None:
                    raise self.error('.member syntax outside of a struct', expr)
                obj = TypeVal(self.ctx.struct)
            else:
                obj = self.resolve(expr.obj)
            return self.member(obj, expr.name, expr)
        if isinstance(expr, ast.Index):
            return self.index(self.resolve(expr.obj), expr.index, expr)
        if isinstance(expr, ast.Call):
            return self.call(expr)
        if isinstance(expr, ast.Unary):
            operand = self.typed_value(expr.operand)
            if expr.op == 'not':
                return Val(ir.UnaryExpr('not', operand.ir), 'boolean')
            return Val(ir.UnaryExpr('-', operand.ir), operand.type)
        if isinstance(expr, ast.Binary):
            return self.binary(expr)
        if isinstance(expr, ast.FuncRef):
            return self.function_ref(expr)
        raise self.error(f'Unexpected {type(expr).__name__}', expr)

    def binary(self, expr):
        op = expr.op
        left = self.typed_value(expr.left)
        right = self.resolve(expr.right)
        if isinstance(left.type, StructInfo):
            if op in ('<', '>', '<=', '>='):
                method = left.type.find_operator('<')
                if method is not None:
                    right = self.coerce(right, None, expr.right)
                    a, b = (left.ir, right) if op in ('<', '>=') else \
                        (right, left.ir)
                    call = self.method_call(method, wrap(a), left.type, [b])
                    if op in ('<=', '>='):
                        return Val(ir.UnaryExpr('not', call.ir), 'boolean')
                    return call
            if op in ('==', '!='):
                method = left.type.find_operator('==')
                if method is not None:
                    right = self.coerce(right, None, expr.right)
                    call = self.method_call(method, wrap(left.ir), left.type,
                                            [right])
                    if op == '!=':
                        return Val(ir.UnaryExpr('not', call.ir), 'boolean')
                    return call
        right_value = self.coerce(right, None, expr.right)
        right_type = right.type if isinstance(right, Val) else None
        if op in ('and', 'or', '==', '!=', '<', '>', '<=', '>='):
            result_type = 'boolean'
        elif left.type == 'real' or right_type == 'real':
            result_type = 'real'
        elif left.type == 'string' or right_type == 'string':
            result_type = 'string'
        else:
            result_type = 'integer'
        return Val(ir.BinaryExpr(op, left.ir, right_value), result_type)

    def function_ref(self, expr):
        target = self.resolve(expr.target)
        func = None
        if isinstance(target, Unknown):
            return Val(ir.FunctionRefExpr(target.name), 'code')
        if isinstance(target, FuncVal):
            func = target.func
        elif isinstance(target, MethodVal):
            if not target.method.is_static:
                raise self.error('function needs a static method, '
                                 f'{target.method.name} is an instance method',
                                 expr)
            func = target.method
        if func is None:
            raise self.error('function needs a function name', expr)
        if func.kind == 'native':
            raise self.error('code values cannot refer to natives', expr)
        if func.params:
            raise self.error(f'{func.name} takes arguments, so it cannot be used '
                             'as code', expr)
        return Val(ir.FunctionRefExpr(func.out), 'code')

    # ------------------------------------------------------------------ names

    def resolve_name(self, expr):
        name = expr.name
        ctx = self.ctx
        local = ctx.locals.get(name)
        if local is not None:
            if local.is_array:
                return ArrayVal(local.storage, None, local.type, [None], local)
            return Val(ir.VarExpr(name), local.type)
        if name == 'this':
            if not ctx.has_this:
                raise self.error('this is only available in instance methods',
                                 expr)
            return Val(ir.VarExpr('this'), ctx.struct)
        if name == 'thistype':
            if ctx.struct is None:
                raise self.error('thistype outside of a struct', expr)
            return TypeVal(ctx.struct)
        if name == 'super':
            if ctx.struct is None or ctx.struct.parent is None:
                raise self.error('The struct has no parent, cannot use super',
                                 expr)
            if not ctx.has_this:
                raise self.error('super needs an instance method', expr)
            return SuperVal(ctx.struct.parent)
        if ctx.struct is not None and self.options.implicit_this:
            found = self.struct_member(ctx.struct, name, expr, bare=True)
            if found is not None:
                return found
        if name == 'SCOPE_PREFIX':
            prefix = ctx.scope.public_prefix if ctx.scope.kind != 'global' else ''
            return Val(str_lit(prefix), 'string')
        if name == 'SCOPE_PRIVATE':
            prefix = ctx.scope.private_prefix if ctx.scope.kind != 'global' else ''
            return Val(str_lit(prefix), 'string')
        if name == 'DEBUG_MODE':
            return Val(TRUE if self.options.debug else FALSE, 'boolean')
        symbol = ctx.scope.lookup(name)
        if symbol is None:
            symbol = self.c.global_scope.names.get(name)
        if symbol is not None:
            return self.symbol_value(symbol, expr)
        api_global = self.c.api.globals.get(name)
        if api_global is not None:
            if api_global.is_array:
                return ArrayVal(PlainStorage(name, api_global.type), None,
                                api_global.type, [None])
            return Val(ir.VarExpr(name), api_global.type)
        api_func = self.c.api.functions.get(name)
        if api_func is not None:
            return FuncVal(self.rt.api_function(api_func))
        if name in self.c.api.types or name in self.c.user_types:
            return TypeVal(name)
        if name.startswith('LIBRARY_'):
            raise self.error(f'{name} is undeclared: library '
                             f'{name[len("LIBRARY_"):]} does not exist (use static '
                             'if to test for an optional library)', expr)
        if self.options.strict:
            raise self.error(f'Undeclared identifier: {name}', expr)
        # JassHelper leaves a name it does not know alone and lets the JASS
        # checker report it; a script compiled in parts relies on that.
        self.c.note_unknown(name, expr.loc)
        return Unknown(name)

    def symbol_value(self, symbol, expr):
        kind = symbol.kind
        info = symbol.info
        if kind == 'global':
            if info.is_array:
                return ArrayVal(info.storage, None, info.type,
                                info.sizes or [None], info)
            return Val(ir.VarExpr(info.out), info.type)
        if kind in ('function', 'native'):
            return FuncVal(info)
        if kind in ('struct', 'interface', 'funciface', 'darray'):
            return TypeVal(info)
        if kind == 'type':
            return TypeVal(symbol.out)
        if kind == 'keyword':
            raise self.error(f'{symbol.name} was declared with keyword but never '
                             'defined', expr)
        raise self.error(f'{symbol.name} cannot be used here ({kind})', expr)

    # ------------------------------------------------------------------ members

    def check_access(self, member, struct, node, write=False):
        ctx = self.ctx
        inside = ctx.struct is not None and (
            ctx.struct is member.owner if isinstance(member, FieldInfo)
            else ctx.struct is member.struct)
        if member.access == 'private' and not inside:
            raise self.error(f'{member.name} is a private member of '
                             f'{struct.name}', node)
        if write and member.access == 'readonly' and not inside:
            raise self.error(f'{member.name} is readonly', node)

    def struct_member(self, struct, name, node, bare=False):
        """A struct member named bare inside one of its methods."""
        ctx = self.ctx
        if ctx.use is not None and ctx.struct is struct:
            key = ctx.use.prefix + name
            field = struct.fields.get(key)
            if field is not None and field.module is ctx.use:
                return self.field_value(field, ir.VarExpr('this')
                                        if ctx.has_this else None, node)
            method = struct.methods.get(key)
            if method is not None and method.module is ctx.use:
                return MethodVal(method, ir.VarExpr('this') if ctx.has_this
                                 else None, struct)
        field = struct.find_field(name)
        if field is not None:
            if not field.is_static and not ctx.has_this:
                if bare:
                    return None
                raise self.error(f'{name} is an instance member, there is no '
                                 'instance in a static method', node)
            return self.field_value(field, ir.VarExpr('this')
                                    if ctx.has_this else None, node)
        method = struct.find_method(name)
        if method is not None and not getattr(method, 'abstract', False):
            this = ir.VarExpr('this') if ctx.has_this and not method.is_static \
                else None
            return MethodVal(method, this, struct)
        prop = struct.find_operator(('get', name))
        if prop is not None and (prop.is_static or ctx.has_this):
            this = None if prop.is_static else ir.VarExpr('this')
            return self.method_call(prop, this, struct, [])
        return None

    def field_value(self, field, this, node):
        if field.is_static:
            if field.is_array:
                return ArrayVal(field.storage, None, field.type,
                                [field.size], field)
            return Val(ir.VarExpr(field.out), field.type)
        if this is None:
            raise self.error(f'{field.name} is an instance member', node)
        if field.is_array:
            return ArrayVal(field.storage, field.offset_storage.read(this),
                            field.type, [field.size], field)
        return Val(field.storage.read(this), field.type)

    def member(self, obj, name, node):
        if isinstance(obj, Val):
            t = obj.type
            if isinstance(t, StructInfo):
                return self.instance_member(obj, t, name, node)
            if isinstance(t, DynamicArrayInfo):
                if name == 'size':
                    return Val(int_lit(t.size), 'integer')
                if name == 'destroy':
                    return Special('dadestroy', t, obj.ir)
                raise self.error('dynamic arrays only have .size and .destroy',
                                 node)
            if isinstance(t, FuncInterfaceInfo):
                if name in ('evaluate', 'execute'):
                    return Special('fi_call', t, obj.ir, mode=name)
                raise self.error('function interface values only have '
                                 '.evaluate and .execute', node)
            raise self.error(f'{type_name(t)} is not of a type that allows . '
                             'syntax', node)
        if isinstance(obj, TypeVal):
            return self.type_member(obj.type, name, node)
        if isinstance(obj, SuperVal):
            method = obj.struct.find_method(name)
            if method is None or getattr(method, 'abstract', False):
                raise self.error(f'The parent struct has no member called {name}',
                                 node)
            this = ir.VarExpr('this') if not method.is_static else None
            return MethodVal(method, this, obj.struct, direct=True)
        if isinstance(obj, FuncVal):
            if name == 'name':
                return Val(str_lit(obj.func.out), 'string')
            if name in ('evaluate', 'execute'):
                return Special('f_call', obj.func, mode=name)
            raise self.error('functions have .name, .evaluate and .execute',
                             node)
        if isinstance(obj, MethodVal):
            method = obj.method
            if name == 'name':
                return Val(str_lit(method.out or ''), 'string')
            if name == 'exists':
                return Val(self.method_exists(obj), 'boolean')
            if name in ('evaluate', 'execute'):
                return Special('m_call', method, obj.this, mode=name, target=obj)
            raise self.error('method has two members: .name , .exists', node)
        if isinstance(obj, ArrayVal):
            var = obj.var
            sizes = getattr(var, 'sizes', None) or (
                [var.size] if isinstance(var, FieldInfo) and var.size else [])
            if name == 'size' and sizes:
                total = 1
                for s in sizes:
                    total *= s
                return Val(int_lit(total), 'integer')
            if name in ('width', 'height') and len(sizes) == 2:
                return Val(int_lit(sizes[0] if name == 'width' else sizes[1]),
                           'integer')
            if name in ('width', 'height'):
                raise self.error('.width invalid since the array is '
                                 'one-dimensional.', node)
            raise self.error('vJass Arrays only accept fields: size, width, '
                             'height', node)
        if isinstance(obj, FuncIfacePath):
            method = obj.struct.find_method(name)
            if method is None or not method.is_static:
                raise self.error(f'{name} is not a static method of '
                                 f'{obj.struct.name}', node)
            return Val(self.function_pointer(MethodVal(method, None, obj.struct),
                                             obj.iface, node), obj.iface)
        raise self.error('this does not allow . syntax', node)

    def method_exists(self, method_val):
        method = method_val.method
        slot = method.dispatch
        if slot is None or method_val.this is None:
            return TRUE
        slot.needed = True
        trigger = self.rt.dispatch_trigger(slot)
        type_read = self.rt.type_of(method_val.static_type, method_val.this)
        return ir.BinaryExpr('!=', ir.ArrayRefExpr(trigger, type_read),
                             ir.Literal('null', 'null'))

    def instance_member(self, obj, struct, name, node):
        this = obj.ir
        ctx = self.ctx
        if ctx.use is not None and ctx.struct is struct:
            key = ctx.use.prefix + name
            field = struct.fields.get(key)
            if field is not None and field.module is ctx.use:
                return self.field_value(field, this, node)
            method = struct.methods.get(key)
            if method is not None and method.module is ctx.use:
                return MethodVal(method, this, struct)
        field = struct.find_field(name)
        if field is not None:
            self.check_access(field, struct, node)
            return self.field_value(field, this, node)
        method = struct.find_method(name)
        if method is not None:
            self.check_access(method, struct, node)
            return MethodVal(method, None if method.is_static else this, struct)
        prop = struct.find_operator(('get', name))
        if prop is not None:
            self.check_access(prop, struct, node)
            return self.method_call(prop, None if prop.is_static else this,
                                    struct, [])
        if name in ('destroy', 'deallocate'):
            return Special('destroy', struct, this)
        if name == 'getType':
            return Special('getType', struct, this)
        for delegate in struct.all_delegates():
            target = delegate.type
            if self.has_member(target, name):
                inner = self.field_value(delegate, this, node)
                return self.member(inner, name, node)
        raise self.error(f'{name} is not a member of {struct.name}', node)

    def has_member(self, struct, name):
        if struct.find_field(name) or struct.find_method(name) or \
                struct.find_operator(('get', name)):
            return True
        return any(self.has_member(d.type, name) for d in struct.all_delegates())

    def type_member(self, t, name, node):
        if isinstance(t, StructInfo):
            if name == 'typeid':
                return Val(ir.VarExpr(self.rt.typeid_name(t)), 'integer')
            ctx = self.ctx
            if ctx.use is not None and ctx.struct is t:
                key = ctx.use.prefix + name
                field = t.fields.get(key)
                if field is not None and field.module is ctx.use:
                    return self.field_value(field, None, node)
                method = t.methods.get(key)
                if method is not None and method.module is ctx.use:
                    return MethodVal(method, None, t)
            if name == 'create':
                own = t.methods.get('create')
                if own is not None:
                    self.check_access(own, t, node)
                    return MethodVal(own, None, t)
                return Special('create', t)
            if name == 'allocate':
                own = t.find_method('allocate')
                if own is not None:
                    self.check_access(own, t, node)
                    return MethodVal(own, None, t)
                return Special('allocate', t)
            field = t.find_field(name)
            if field is not None:
                if not field.is_static:
                    raise self.error(f'{name} is an instance member of {t.name}',
                                     node)
                self.check_access(field, t, node)
                return self.field_value(field, None, node)
            method = t.find_method(name)
            if method is not None:
                self.check_access(method, t, node)
                return MethodVal(method, None, t)
            prop = t.find_operator(('get', name))
            if prop is not None and prop.is_static:
                return self.method_call(prop, None, t, [])
            raise self.error(f'{name} is not a static member of {t.name}', node)
        if isinstance(t, DynamicArrayInfo):
            if name == 'size':
                return Val(int_lit(t.size), 'integer')
            if name == 'create':
                return Special('dacreate', t)
            raise self.error('dynamic array types only have .size and .create',
                             node)
        if isinstance(t, FuncInterfaceInfo):
            target = self.resolve_name(ast.Name(name, loc=node.loc))
            if isinstance(target, TypeVal) and isinstance(target.type, StructInfo):
                return FuncIfacePath(t, target.type)
            pointer = self.function_pointer(target, t, node)
            if pointer is None:
                raise self.error(f'{name} is not a function', node)
            return Val(pointer, t)
        raise self.error(f'{type_name(t)} is not of a type that allows . syntax',
                         node)

    # ------------------------------------------------------------------ index

    def index(self, obj, index_expr, node):
        if isinstance(obj, ArrayVal):
            index = self.value(index_expr)
            if len(obj.dims) > 1:
                stride = 1
                for size in obj.dims[1:]:
                    stride *= size
                base = ir.BinaryExpr('*', wrap(index), int_lit(stride))
                return ArrayVal(obj.storage, offset(obj.base, base) if obj.base
                                is not None else base, obj.elem_type,
                                obj.dims[1:], obj.var)
            return Val(obj.storage.read(offset(obj.base, index)), obj.elem_type)
        if isinstance(obj, Unknown):
            return Val(ir.ArrayRefExpr(obj.name, self.value(index_expr)), '?')
        if isinstance(obj, Val) and obj.type == '?':
            from .emitter import Emitter, RawExpr
            printer = Emitter()
            return Val(RawExpr(f'{printer.expr(obj.ir)}'
                               f'[{printer.expr(self.value(index_expr))}]'), '?')
        if isinstance(obj, Val):
            t = obj.type
            if isinstance(t, DynamicArrayInfo):
                index = self.value(index_expr)
                return Val(t.storage.read(offset(obj.ir, index)), t.element)
            if isinstance(t, StructInfo):
                method = t.find_operator('[]')
                if method is None:
                    raise self.error(f'{t.name} has no [] operator', node)
                return self.method_call(method, obj.ir, t,
                                        [self.value(index_expr,
                                                    method.params[0][0])])
            raise self.error(f'{type_name(t)} is not an array', node)
        if isinstance(obj, TypeVal) and isinstance(obj.type, StructInfo):
            method = obj.type.find_operator('static[]')
            if method is None:
                # `Struct[x]` without a static [] operator is the old
                # typecast syntax, the same as `Struct(x)`.
                value = self.typed_value(index_expr)
                return Val(wrap(value.ir), obj.type)
            return self.method_call(method, None, obj.type,
                                    [self.value(index_expr, method.params[0][0])])
        if isinstance(obj, TypeVal) and (isinstance(obj.type, VJASS_TYPES) or
                                         obj.type == 'integer'):
            value = self.typed_value(index_expr)
            return Val(wrap(value.ir), obj.type)
        raise self.error('this cannot be indexed', node)

    # ------------------------------------------------------------------ calls

    def args(self, params, arg_exprs, node, name):
        if len(arg_exprs) != len(params):
            raise self.error(f'{name} takes {len(params)} argument(s), '
                             f'{len(arg_exprs)} given', node)
        return [self.value(a, p[0]) for a, p in zip(arg_exprs, params)]

    def call(self, expr):
        callee = self.resolve(expr.func)
        node = expr
        if isinstance(callee, FuncVal):
            func = callee.func
            args = self.args(func.params, expr.args, node, func.name)
            name = self.rt.call_name(func)
            return Val(ir.CallExpr(name, args), func.returns)
        if isinstance(callee, MethodVal):
            method = callee.method
            args = self.args(method.params, expr.args, node, method.name)
            if not method.is_static and callee.this is None:
                raise self.error(f'{method.name} is an instance method, it needs '
                                 'an instance', node)
            return self.method_call(method, callee.this, callee.static_type,
                                    args, direct=callee.direct)
        if isinstance(callee, TypeVal):
            t = callee.type
            if len(expr.args) != 1:
                raise self.error(f'a typecast takes one value', node)
            if isinstance(t, VJASS_TYPES) or t == 'integer':
                value = self.typed_value(expr.args[0])
                return Val(wrap(value.ir), t)
            raise self.error(f'Undeclared function: {type_name(t)} (only '
                             'integer and vJass types can be typecast)', node)
        if isinstance(callee, Special):
            return self.special_call(callee, expr)
        if isinstance(callee, Unknown):
            args = [self.value(a) for a in expr.args]
            return Val(ir.CallExpr(callee.name, args), '?')
        if isinstance(callee, Val) and isinstance(callee.type, FuncInterfaceInfo):
            raise self.error('call a function interface value with .evaluate() '
                             'or .execute()', node)
        raise self.error('this is not a function', node)

    def special_call(self, special, expr):
        kind = special.kind
        node = expr
        if kind == 'create':
            t = special.info
            if t.is_interface:
                if not t.has_create:
                    raise self.error(f'interface {t.name} declares no create',
                                     node)
                args = self.args([('integer', 'typeid')], expr.args, node,
                                 'create')
                return Val(ir.CallExpr(self.rt.interface_create(t), args), t)
            if t.is_array_struct:
                raise self.error(f'{t.name} extends array and has no create '
                                 'method', node)
            params = self.rt.allocate_params(t)
            args = self.args(params, expr.args, node, 'create')
            return Val(ir.CallExpr(self.rt.allocator(t), args), t)
        if kind == 'allocate':
            t = special.info
            if t.is_interface or t.is_array_struct:
                raise self.error(f'{t.name} cannot be allocated', node)
            params = self.rt.allocate_params(t)
            args = self.args(params, expr.args, node, 'allocate')
            return Val(ir.CallExpr(self.rt.allocator(t), args), t)
        if kind == 'destroy':
            t = special.info
            self.args([], expr.args, node, 'destroy')
            if t.is_array_struct:
                raise self.error('Array structs cannot be destroyed.', node)
            return Val(ir.CallExpr(self.rt.deallocator(t), [special.this]),
                       'nothing')
        if kind == 'getType':
            self.args([], expr.args, node, 'getType')
            return Val(self.rt.type_of(special.info, special.this), 'integer')
        if kind == 'dacreate':
            self.args([], expr.args, node, 'create')
            return Val(ir.CallExpr(self.rt.darray_allocator(special.info), []),
                       special.info)
        if kind == 'dadestroy':
            self.args([], expr.args, node, 'destroy')
            return Val(ir.CallExpr(self.rt.darray_deallocator(special.info),
                                   [special.this]), 'nothing')
        if kind == 'fi_call':
            iface = special.info
            args = self.args(iface.params, expr.args, node, iface.name)
            proto = self.rt.prototype(iface.params, iface.returns)
            returns = iface.returns if special.mode == 'evaluate' else 'nothing'
            return Val(ir.CallExpr(proto.caller(special.mode),
                                   [special.this] + args), returns)
        if kind == 'f_call':
            func = special.info
            args = self.args(func.params, expr.args, node, func.name)
            return self.evaluate_function(func, args, special.mode, node)
        if kind == 'm_call':
            method = special.info
            args = self.args(method.params, expr.args, node, method.name)
            if method.is_static:
                return self.evaluate_function(method, args, special.mode, node)
            if special.this is None:
                raise self.error(f'{method.name} needs an instance', node)
            target = special.target
            if method.dispatch is not None and not target.direct and \
                    self.rt.needs_dispatch(target.static_type, method):
                method.dispatch.needed = True
                name = self.rt.dispatch_caller(method.dispatch, special.mode)
                returns = method.returns if special.mode == 'evaluate' \
                    else 'nothing'
                return Val(ir.CallExpr(name, [special.this] + args), returns)
            if getattr(method, 'abstract', False):
                raise self.error(f'{method.name} has no implementation', node)
            proto = self.rt.method_prototype(method)
            returns = method.returns if special.mode == 'evaluate' else 'nothing'
            return Val(ir.CallExpr(proto.caller(special.mode),
                                   [int_lit(proto.id_of(method)), special.this]
                                   + args), returns)
        raise self.error(f'.{kind} cannot be called here', node)

    def evaluate_function(self, func, args, mode, node):
        if func.kind in ('native', 'api') and func.kind == 'native':
            raise self.error('natives cannot be evaluated or executed', node)
        if func.kind == 'api':
            raise self.error(f'{func.name} is a Blizzard.j function; .{mode} '
                             'only works on functions of this script', node)
        proto = self.rt.prototype(func.params, func.returns)
        returns = func.returns if mode == 'evaluate' else 'nothing'
        return Val(ir.CallExpr(proto.caller(mode),
                               [int_lit(proto.id_of(func))] + args), returns)

    def method_call(self, method, this, static_type, args, direct=False):
        if method.is_static:
            return Val(ir.CallExpr(method.out, args), method.returns)
        slot = method.dispatch
        if slot is not None and not direct and \
                self.rt.needs_dispatch(static_type, method):
            slot.needed = True
            name = self.rt.dispatch_caller(slot, 'evaluate')
            return Val(ir.CallExpr(name, [this] + args), method.returns)
        if getattr(method, 'abstract', False):
            raise self.error(f'{method.name} has no implementation in '
                             f'{static_type.name}', None)
        return Val(ir.CallExpr(method.out, [this] + args), method.returns)

    # ------------------------------------------------------------------ assign

    def assign(self, target, value_expr):
        """Compile `set target = value`."""
        if isinstance(target, ast.Paren):
            target = target.expr
        if isinstance(target, ast.Name):
            return self.assign_name(target, value_expr)
        if isinstance(target, ast.Index):
            obj = self.resolve(target.obj)
            if isinstance(obj, ArrayVal):
                if len(obj.dims) > 1:
                    raise self.error('assigning a whole row of a 2D array',
                                     target)
                index = self.value(target.index)
                value = self.value(value_expr, obj.elem_type)
                return obj.storage.write(offset(obj.base, index), value)
            if isinstance(obj, Unknown):
                return ir.SetStmt(obj.name, self.value(value_expr),
                                  self.value(target.index))
            if isinstance(obj, Val) and isinstance(obj.type, DynamicArrayInfo):
                index = self.value(target.index)
                value = self.value(value_expr, obj.type.element)
                return obj.type.storage.write(offset(obj.ir, index), value)
            if isinstance(obj, Val) and isinstance(obj.type, StructInfo):
                method = obj.type.find_operator('[]=')
                if method is None:
                    raise self.error(f'{obj.type.name} has no []= operator',
                                     target)
                index = self.value(target.index, method.params[0][0])
                value = self.value(value_expr, method.params[1][0])
                call = self.method_call(method, obj.ir, obj.type, [index, value])
                return ir.CallStmt(call.ir.name, call.ir.args)
            if isinstance(obj, TypeVal) and isinstance(obj.type, StructInfo):
                method = obj.type.find_operator('static[]=')
                if method is None:
                    raise self.error(f'{obj.type.name} has no static []= '
                                     'operator', target)
                index = self.value(target.index, method.params[0][0])
                value = self.value(value_expr, method.params[1][0])
                return ir.CallStmt(method.out, [index, value])
            raise self.error('this cannot be assigned by index', target)
        if isinstance(target, ast.Member):
            if target.implicit and not self.ctx.has_this:
                obj = TypeVal(self.ctx.struct)
            else:
                obj = self.resolve(target.obj)
            return self.assign_member(obj, target.name, value_expr, target)
        raise self.error('Expected: Assignment target', target)

    def assign_name(self, target, value_expr):
        name = target.name
        ctx = self.ctx
        local = ctx.locals.get(name)
        if local is not None:
            if local.is_array:
                raise self.error(f'{name} is an array, give an index', target)
            return ir.SetStmt(name, self.value(value_expr, local.type))
        if ctx.struct is not None and self.options.implicit_this and \
                name not in ('this', 'thistype'):
            struct = ctx.struct
            if ctx.use is not None:
                field = struct.fields.get(ctx.use.prefix + name)
                if field is not None and field.module is ctx.use:
                    return self.store_field(field, struct, ir.VarExpr('this')
                                            if ctx.has_this else None,
                                            value_expr, target)
            field = struct.find_field(name)
            if field is not None and (field.is_static or ctx.has_this):
                return self.store_field(field, struct, ir.VarExpr('this')
                                        if ctx.has_this else None, value_expr,
                                        target)
            prop = struct.find_operator(('set', name))
            if prop is not None and (prop.is_static or ctx.has_this):
                value = self.value(value_expr, prop.params[0][0])
                call = self.method_call(prop, None if prop.is_static else
                                        ir.VarExpr('this'), struct, [value])
                return ir.CallStmt(call.ir.name, call.ir.args)
        result = self.resolve_name(target)
        if isinstance(result, Val) and isinstance(result.ir, ir.VarExpr):
            symbol = ctx.scope.lookup(name) or self.c.global_scope.names.get(name)
            info = symbol.info if symbol is not None else None
            if isinstance(info, VarInfo) and info.is_constant:
                raise self.error(f'{name} is a constant', target)
            api_global = self.c.api.globals.get(result.ir.name)
            if api_global is not None and api_global.is_constant:
                raise self.error(f'{name} is a constant', target)
            return ir.SetStmt(result.ir.name, self.value(value_expr, result.type))
        if isinstance(result, ArrayVal):
            raise self.error(f'{name} is an array, give an index', target)
        raise self.error(f'{name} cannot be assigned', target)

    def store_field(self, field, struct, this, value_expr, node):
        self.check_access(field, struct, node, write=True)
        if field.is_static and field.is_constant:
            raise self.error(f'{field.name} is a constant', node)
        if field.is_array:
            raise self.error(f'{field.name} is an array, give an index', node)
        value = self.value(value_expr, field.type)
        if field.is_static:
            return ir.SetStmt(field.out, value)
        if this is None:
            raise self.error(f'{field.name} is an instance member', node)
        return field.storage.write(this, value)

    def assign_member(self, obj, name, value_expr, node):
        if isinstance(obj, Val) and isinstance(obj.type, StructInfo):
            struct = obj.type
            ctx = self.ctx
            if ctx.use is not None and ctx.struct is struct:
                field = struct.fields.get(ctx.use.prefix + name)
                if field is not None and field.module is ctx.use:
                    return self.store_field(field, struct, obj.ir, value_expr,
                                            node)
            field = struct.find_field(name)
            if field is not None:
                return self.store_field(field, struct, obj.ir, value_expr, node)
            prop = struct.find_operator(('set', name))
            if prop is not None:
                self.check_access(prop, struct, node)
                value = self.value(value_expr, prop.params[0][0])
                call = self.method_call(prop, None if prop.is_static else obj.ir,
                                        struct, [value])
                return ir.CallStmt(call.ir.name, call.ir.args)
            for delegate in struct.all_delegates():
                if self.has_member(delegate.type, name):
                    inner = self.field_value(delegate, obj.ir, node)
                    return self.assign_member(inner, name, value_expr, node)
            raise self.error(f'{name} is not a member of {struct.name}', node)
        if isinstance(obj, TypeVal) and isinstance(obj.type, StructInfo):
            struct = obj.type
            ctx = self.ctx
            if ctx.use is not None and ctx.struct is struct:
                field = struct.fields.get(ctx.use.prefix + name)
                if field is not None and field.module is ctx.use:
                    return self.store_field(field, struct, None, value_expr, node)
            field = struct.find_field(name)
            if field is not None:
                if not field.is_static:
                    raise self.error(f'{name} is an instance member of '
                                     f'{struct.name}', node)
                return self.store_field(field, struct, None, value_expr, node)
            prop = struct.find_operator(('set', name))
            if prop is not None and prop.is_static:
                value = self.value(value_expr, prop.params[0][0])
                return ir.CallStmt(prop.out, [value])
            raise self.error(f'{name} is not a static member of {struct.name}',
                             node)
        if isinstance(obj, SuperVal):
            this = Val(ir.VarExpr('this'), obj.struct)
            return self.assign_member(this, name, value_expr, node)
        raise self.error('this member cannot be assigned', node)


# --- driving the units ---------------------------------------------------------------

class Placed(object):
    """One entry of the output order before the runtime fills in the rest."""
    __slots__ = ('kind', 'function', 'info', 'comment', 'struct')

    def __init__(self, kind, function=None, info=None, comment=None,
                 struct=None):
        self.kind = kind
        self.function = function
        self.info = info
        self.comment = comment
        self.struct = struct


def method_context(method):
    struct = method.struct
    use = method.module
    scope = use.module.scope if use is not None else struct.scope
    return Ctx(scope, struct, use, method, has_this=not method.is_static)


def compile_units(compiler, runtime):
    fc = FunctionCompiler(compiler, runtime)
    runtime.function_compiler = fc
    placed = []
    main_seen = False
    for unit in compiler.units:
        if unit.kind == 'begin':
            placed.append(Placed('comment', comment=f'{unit.info} '
                                 f'{unit.scope.full} begins'))
        elif unit.kind == 'end':
            placed.append(Placed('comment', comment=f'{unit.info} '
                                 f'{unit.scope.full} ends'))
        elif unit.kind == 'function':
            info = unit.info
            decl = info.decl
            body = decl.body
            if info.out == 'main' and 'main' in compiler.injects:
                body = compiler.injects['main'][0].body
                main_seen = True
            elif info.out == 'config' and 'config' in compiler.injects:
                body = compiler.injects['config'][0].body
            ctx = Ctx(unit.scope, func=info)
            placed.append(Placed('function', fc.compile_function(info, body, ctx),
                                 info))
        elif unit.kind == 'struct':
            struct = unit.info
            placed.append(Placed('struct_begin', struct=struct))
            for member in struct.member_order:
                if isinstance(member, FuncInfo):
                    ctx = method_context(member)
                    extra = [] if member.is_static else [ir.Param('integer',
                                                                  'this')]
                    function = fc.compile_function(member, member.decl.body, ctx,
                                                   extra)
                    placed.append(Placed('function', function, member))
            placed.append(Placed('struct_end', struct=struct))
        elif unit.kind in ('interface', 'darray'):
            placed.append(Placed(unit.kind, info=unit.info))
    for which in ('main', 'config'):
        if which in compiler.injects and not any(
                p.kind == 'function' and p.info.out == which for p in placed):
            inject, scope = compiler.injects[which]
            info = FuncInfo(which, which, [], 'nothing', 'function', None, scope,
                            inject.loc)
            compiler.functions[which] = info
            ctx = Ctx(scope, func=info)
            placed.append(Placed('function', fc.compile_function(
                info, inject.body, ctx), info))
    return placed
