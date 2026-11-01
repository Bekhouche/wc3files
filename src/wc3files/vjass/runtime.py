"""The code vJass needs at runtime, and the final layout of the script.

JASS only lets a function call functions declared above it, has no pointers,
and has no objects. vJass gets around all three the way JassHelper does:

* instances are integers into parallel arrays, recycled through a free list
  (`si__S_F`, `si__S_I`, `si__S_V`);
* anything that has to be called "from above" - a method declared further
  down, a virtual method, `.evaluate`, a function interface value - goes
  through `TriggerEvaluate` on a trigger whose condition is a small adapter
  placed at the very end, with arguments passed in `f__arg_*` globals;
* initialization runs from `main`.

`Runtime.assemble()` puts it together: globals, natives, the callers for all
of the above, the generated helpers, the user code, the adapters, and the
initializer.
"""
from wc3files.scripts import ir

from . import ast
from .codegen import Ctx, InitPlaceholder, Placed
from .emitter import wrap, int_lit, str_lit, NULL, ParenExpr, unwrap, Comment
from .errors import VJassError
from .storage import make_storage, PlainStorage
from .symbols import (FuncInfo, StructInfo, FieldInfo, out_type, type_name)

INIT_CHUNK = 400


def _var(name):
    return ir.VarExpr(name)


def _call(name, *args):
    return ir.CallExpr(name, list(args))


def _call_stmt(name, *args):
    return ir.CallStmt(name, list(args))


def _set(name, value, index=None):
    return ir.SetStmt(name, value, index)


def _minus_one():
    return ir.UnaryExpr('-', ir.Literal('int', '1'))


def _children(expr):
    if isinstance(expr, ParenExpr):
        return [expr.expr]
    if isinstance(expr, ir.UnaryExpr):
        return [expr.operand]
    if isinstance(expr, ir.BinaryExpr):
        return [expr.left, expr.right]
    if isinstance(expr, ir.CallExpr):
        return list(expr.args)
    if isinstance(expr, ir.ArrayRefExpr):
        return [expr.index]
    return []


def _statement_expressions(statements):
    for stmt in statements:
        if isinstance(stmt, ir.Local) and stmt.initial is not None:
            yield stmt.initial
        elif isinstance(stmt, ir.SetStmt):
            if stmt.index is not None:
                yield stmt.index
            yield stmt.value
        elif isinstance(stmt, ir.CallStmt):
            for arg in stmt.args:
                yield arg
        elif isinstance(stmt, ir.IfStmt):
            for cond, body in stmt.branches:
                yield cond
                for inner in _statement_expressions(body):
                    yield inner
            if stmt.else_body is not None:
                for inner in _statement_expressions(stmt.else_body):
                    yield inner
        elif isinstance(stmt, ir.LoopStmt):
            for inner in _statement_expressions(stmt.body):
                yield inner
        elif isinstance(stmt, ir.ExitWhenStmt):
            yield stmt.condition
        elif isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
            yield stmt.value


def _replace_function_refs(statements, name, copy_name, position, here_name):
    """Point `function name` at its copy wherever the original is not yet
    declared."""
    here = position[here_name]

    def fix(expr):
        if isinstance(expr, ir.FunctionRefExpr) and expr.name == name and \
                position[name] > here:
            expr.name = copy_name
        for child in _children(expr):
            fix(child)

    for expr in _statement_expressions(statements):
        fix(expr)


class Prototype(object):
    """Functions sharing one JASS signature, callable by id through a trigger
    array: `.evaluate`, `.execute`, function interface values, and calls from
    above a declaration."""

    def __init__(self, runtime, index, key):
        self.runtime = runtime
        self.index = index
        self.key = key                 # ((param out types...), return out type)
        self.ids = {}                  # function out name -> id
        self.callers_used = set()

    @property
    def trigger(self):
        return f'st___prototype{self.index}'

    def caller(self, mode):
        self.callers_used.add(mode)
        return f'sc___prototype{self.index}_{mode}'

    def id_of(self, func):
        name = func.out if hasattr(func, 'out') else func
        if name not in self.ids:
            self.ids[name] = len(self.ids) + 1
        return self.ids[name]

    def adapter(self, name):
        return f'sa___prototype{self.index}_{name}'


class Runtime(object):

    def __init__(self, compiler):
        self.c = compiler
        self.options = compiler.options
        self.function_compiler = None
        self.prototypes = {}
        self.api_functions = {}
        self.hooks = {}                # target name -> [hook FuncInfo]
        self.hook_targets = {}         # target name -> FuncInfo of the target
        self.arg_globals = {}          # name -> type
        self.result_globals = {}
        self.extra_globals = []        # ir.GlobalVar
        self.used_allocators = set()
        self.used_deallocators = set()
        self.used_darrays = set()
        self.used_interface_create = set()
        self.used_typeids = set()
        self.init_statements = []
        self.generated = {}            # name -> FunctionDecl
        self.prepare_hooks()

    def error(self, message, loc=None, note=None):
        return VJassError(message, loc, 'compile', note)

    # ------------------------------------------------------------------ names

    def typeid_name(self, struct):
        self.used_typeids.add(struct)
        return f'si__{struct.out}'

    def pool(self, struct):
        root = struct.root
        return f'si__{root.out}'

    def api_function(self, api):
        info = self.api_functions.get(api.name)
        if info is None:
            info = FuncInfo(api.name, api.name, list(api.params), api.returns,
                            'native' if api.is_native else 'api')
            self.api_functions[api.name] = info
        return info

    def signature_key(self, params, returns):
        return (tuple(out_type(p[0]) for p in params), out_type(returns))

    def prototype(self, params, returns):
        return self.prototype_for_key(self.signature_key(params, returns))

    def prototype_for_key(self, key):
        proto = self.prototypes.get(key)
        if proto is None:
            proto = Prototype(self, len(self.prototypes) + 1, key)
            self.prototypes[key] = proto
        return proto

    def method_prototype(self, method):
        return self.prototype([('integer', 'this')] + list(method.params),
                              method.returns)

    def arg_global(self, type_name_, position):
        name = f'f__arg_{type_name_}{position}'
        self.arg_globals[name] = type_name_
        return name

    def result_global(self, type_name_):
        name = f'f__result_{type_name_}'
        self.result_globals[name] = type_name_
        return name

    def arg_names(self, types):
        counts = {}
        names = []
        for t in types:
            counts[t] = counts.get(t, 0) + 1
            names.append(self.arg_global(t, counts[t]))
        return names

    # ------------------------------------------------------------------ hooks

    def prepare_hooks(self):
        c = self.c
        for hook, scope in c.hooks:
            target = c.api.functions.get(hook.target)
            if target is not None:
                target_info = self.api_function(target)
            else:
                symbol = scope.lookup(hook.target) or \
                    c.global_scope.names.get(hook.target)
                if symbol is None or symbol.kind != 'native':
                    raise self.error(f'Unable to find native: {hook.target}',
                                     hook.loc, note='hook works on natives and '
                                     'Blizzard.j functions')
                target_info = symbol.info
            self.hook_targets[hook.target] = target_info
            self.hooks.setdefault(hook.target, []).append((hook, scope))

    def call_name(self, func):
        if func.out in self.hooks:
            return 'h__' + func.out
        return func.out

    # ------------------------------------------------------------------ dispatch

    def needs_dispatch(self, static_type, method):
        slot = method.dispatch
        if slot is None:
            return False
        if static_type.is_interface:
            return True
        key = method.key
        for descendant in static_type.descendants():
            override = descendant.methods.get(key)
            if override is not None and override.dispatch is slot:
                return True
        return getattr(method, 'abstract', False)

    def dispatch_trigger(self, slot):
        return f'st__{slot.key}'

    def dispatch_caller(self, slot, mode):
        slot.needed = True
        modes = getattr(slot, 'modes', None)
        if modes is None:
            modes = slot.modes = set()
        modes.add(mode)
        return f'sc__{slot.key}' if mode == 'evaluate' else f'sx__{slot.key}'

    def type_storage(self, root):
        storage = getattr(root, 'type_storage', None)
        if storage is None:
            storage = make_storage(f'si__{root.out}_type', 'integer',
                                   root.max_size + 1, self.options)
            root.type_storage = storage
        return storage

    def pool_storage(self, root):
        storage = getattr(root, 'pool_storage', None)
        if storage is None:
            storage = make_storage(f'si__{root.out}_V', 'integer',
                                   root.max_size + 1, self.options)
            root.pool_storage = storage
        return storage

    def type_of(self, struct, this):
        if struct.polymorphic:
            return self.type_storage(struct.root).read(this)
        return _var(self.typeid_name(struct))

    # ------------------------------------------------------------------ alloc

    def allocate_params(self, struct):
        parent = struct.parent
        if parent is None or parent.is_interface:
            return []
        create = parent.methods.get('create')
        if create is not None:
            return list(create.params)
        return self.allocate_params(parent)

    def allocator(self, struct):
        self.used_allocators.add(struct)
        return f's__{struct.out}__allocate'

    def deallocator(self, struct):
        if struct.polymorphic:
            root = struct.root
            self.used_deallocators.add(root)
            return f'sc__{root.out}_deallocate'
        self.used_deallocators.add(struct)
        return f's__{struct.out}_deallocate'

    def interface_create(self, iface):
        self.used_interface_create.add(iface)
        return f'sc__{iface.out}__create'

    def darray_allocator(self, darray):
        self.used_darrays.add(darray)
        return f's__{darray.out}__allocate'

    def darray_deallocator(self, darray):
        self.used_darrays.add(darray)
        return f's__{darray.out}_deallocate'

    def debug_message(self, text):
        if not self.options.debug:
            return []
        return [_call_stmt('DisplayTimedTextToPlayer', _call('GetLocalPlayer'),
                           ir.Literal('real', '0'), ir.Literal('real', '0'),
                           ir.Literal('real', '1000.'), str_lit(text))]

    def gen_allocator(self, struct):
        root = struct.root
        pool = self.pool(struct)
        pool_v = self.pool_storage(root)
        params = self.allocate_params(struct)
        ir_params = [ir.Param(out_type(t), n) for t, n in params]
        body = []
        locals_ = [ir.Local('integer', 'this')]
        if struct.slot_fields:
            locals_.append(ir.Local('integer', 'kthis'))
        this = _var('this')
        if struct.parent is None or struct.parent.is_interface:
            body.append(_set('this', _var(pool + '_F')))
            body.append(ir.IfStmt([(ir.BinaryExpr('!=', this, int_lit(0)), [
                _set(pool + '_F', pool_v.read(this))])], [
                _set(pool + '_I', ir.BinaryExpr('+', _var(pool + '_I'),
                                                int_lit(1))),
                _set('this', _var(pool + '_I'))]))
            body.append(ir.IfStmt([(ir.BinaryExpr('>', this,
                                                  int_lit(root.max_size)),
                                    self.debug_message(
                                        'Unable to allocate id for an object of '
                                        f'type: {struct.name}') +
                                    [ir.ReturnStmt(int_lit(0))])]))
            if struct.polymorphic:
                body.append(self.type_storage(root).write(
                    this, _var(self.typeid_name(struct))))
            body.append(pool_v.write(this, _minus_one()))
        else:
            parent = struct.parent
            create = parent.methods.get('create')
            args = [_var(n) for _, n in params]
            target = create.out if create is not None else self.allocator(parent)
            body.append(_set('this', _call(target, *args)))
            body.append(ir.IfStmt([(ir.BinaryExpr('==', this, int_lit(0)),
                                    [ir.ReturnStmt(int_lit(0))])]))
            body.append(self.type_storage(root).write(
                this, _var(self.typeid_name(struct))))
        if struct.slot_fields:
            body.extend(self.slot_allocation(struct, pool, pool_v))
        body.extend(self.field_defaults(struct))
        body.append(ir.ReturnStmt(this))
        decl = ir.FunctionDecl(self.allocator(struct), ir_params, 'integer',
                               locals_ + body)
        decl.comments = [f'Generated allocator of {struct.name}']
        return decl

    def slot_names(self, struct):
        base = f'si__{struct.out}_arr'
        return base + 'I', base + 'N', base + 'V', base

    def slot_storage(self, struct):
        storage = getattr(struct, 'slot_storage', None)
        if storage is None:
            storage = make_storage(self.slot_names(struct)[3], 'integer',
                                   struct.root.max_size + 1, self.options)
            struct.slot_storage = storage
        return storage

    def slot_allocation(self, struct, pool, pool_v):
        counter, free_count, free_stack, _ = self.slot_names(struct)
        kthis = _var('kthis')
        this = _var('this')
        fail = self.debug_message('Unable to allocate array members for an '
                                  f'object of type: {struct.name}')
        if struct.parent is None or struct.parent.is_interface:
            fail = fail + [pool_v.write(this, _var(pool + '_F')),
                           _set(pool + '_F', this)]
        body = [ir.IfStmt([(ir.BinaryExpr('==', _var(free_count), int_lit(0)), [
            _set(counter, ir.BinaryExpr('+', _var(counter), int_lit(1))),
            _set('kthis', _var(counter)),
            ir.IfStmt([(ir.BinaryExpr('>', kthis, int_lit(struct.slot_capacity)),
                        fail + [ir.ReturnStmt(int_lit(0))])])])], [
            _set('kthis', ir.ArrayRefExpr(free_stack, _var(free_count))),
            _set(free_count, ir.BinaryExpr('-', _var(free_count), int_lit(1)))])]
        body.append(self.slot_storage(struct).write(this, kthis))
        for field in struct.slot_fields:
            body.append(field.offset_storage.write(this, ir.BinaryExpr(
                '*', ParenExpr(ir.BinaryExpr('-', kthis, int_lit(1))),
                int_lit(field.size))))
        return body

    def slot_release(self, struct):
        _, free_count, free_stack, _ = self.slot_names(struct)
        return [_set(free_count, ir.BinaryExpr('+', _var(free_count), int_lit(1))),
                _set(free_stack, self.slot_storage(struct).read(_var('this')),
                     _var(free_count))]

    def field_defaults(self, struct):
        fc = self.function_compiler
        out = []
        for field in struct.member_order:
            if not isinstance(field, FieldInfo) or field.is_static or \
                    field.init is None:
                continue
            ctx = Ctx(field.scope, struct, field.module, None, has_this=True)
            value = fc.compile_expression(field.init, ctx, field.type)
            out.append(field.storage.write(_var('this'), value))
        return out

    def gen_deallocator(self, struct):
        """For a struct outside any family: onDestroy is called directly."""
        root = struct.root
        pool = self.pool(struct)
        pool_v = self.pool_storage(root)
        this = _var('this')
        body = [ir.IfStmt([
            (ir.BinaryExpr('==', this, int_lit(0)),
             self.debug_message('Attempt to destroy a null struct of type: '
                                f'{struct.name}') + [ir.ReturnStmt()]),
            (ir.BinaryExpr('!=', pool_v.read(this), _minus_one()),
             self.debug_message('Double free of type: ' + struct.name) +
             [ir.ReturnStmt()])])]
        on_destroy = struct.methods.get('onDestroy')
        if on_destroy is not None:
            body.append(_call_stmt(on_destroy.out, this))
        if struct.slot_fields:
            body.extend(self.slot_release(struct))
        body.append(pool_v.write(this, _var(pool + '_F')))
        body.append(_set(pool + '_F', this))
        decl = ir.FunctionDecl(f's__{struct.out}_deallocate',
                               [ir.Param('integer', 'this')], 'nothing', body)
        decl.comments = [f'Generated destructor of {struct.name}']
        return decl

    def destroy_chain(self, struct):
        chain = []
        node = struct
        while node is not None and not node.is_interface:
            on_destroy = node.methods.get('onDestroy')
            if on_destroy is not None:
                chain.append(('onDestroy', node, on_destroy))
            if node.slot_fields:
                chain.append(('dispose', node, None))
            node = node.parent
        return chain

    def gen_poly_deallocator(self, root):
        pool = f'si__{root.out}'
        pool_v = self.pool_storage(root)
        this = _var('this')
        body = [ir.IfStmt([
            (ir.BinaryExpr('==', this, int_lit(0)),
             self.debug_message('Attempt to destroy a null struct of type: '
                                f'{root.name}') + [ir.ReturnStmt()]),
            (ir.BinaryExpr('!=', pool_v.read(this), _minus_one()),
             self.debug_message('Double free of type: ' + root.name) +
             [ir.ReturnStmt()])])]
        concrete = [s for s in root.family() if not s.is_interface]
        chains = {s: self.destroy_chain(s) for s in concrete}
        if any(chains.values()):
            trigger = f'st__{root.out}_onDestroy'
            self.extra_globals.append(ir.GlobalVar('trigger', trigger,
                                                   is_array=True))
            body.append(_set('f__arg_this', this))
            self.arg_globals['f__arg_this'] = 'integer'
            body.append(_call_stmt('TriggerEvaluate', ir.ArrayRefExpr(
                trigger, self.type_storage(root).read(this))))
            for struct in concrete:
                chain = chains[struct]
                if not chain:
                    continue
                index = int_lit(struct.typeid)
                self.init_statements.append(_set(trigger, _call('CreateTrigger'),
                                                 index))
                for kind, node, method in chain:
                    if kind == 'onDestroy':
                        adapter = self.on_destroy_adapter(node, method)
                    else:
                        adapter = self.dispose_adapter(node)
                    self.init_statements.append(_call_stmt(
                        'TriggerAddCondition', ir.ArrayRefExpr(trigger, index),
                        _call('Condition', ir.FunctionRefExpr(adapter))))
        body.append(pool_v.write(this, _var(pool + '_F')))
        body.append(_set(pool + '_F', this))
        decl = ir.FunctionDecl(f'sc__{root.out}_deallocate',
                               [ir.Param('integer', 'this')], 'nothing', body)
        decl.comments = [f'Generated destructor of {root.name}']
        return decl

    def on_destroy_adapter(self, struct, method):
        name = f'sa__{struct.out}_onDestroy'
        if name not in self.generated:
            self.arg_globals['f__arg_this'] = 'integer'
            self.generated[name] = ir.FunctionDecl(name, [], 'boolean', [
                ir.Local('integer', 'this', initial=_var('f__arg_this')),
                _call_stmt(method.out, _var('this')),
                # The onDestroy may have run other evaluations that reuse
                # f__arg_this; the next link of the chain needs it back.
                _set('f__arg_this', _var('this')),
                ir.ReturnStmt(ir.Literal('bool', 'true'))])
        return name

    def dispose_adapter(self, struct):
        name = f'sa__{struct.out}__disposeArrays'
        if name not in self.generated:
            self.arg_globals['f__arg_this'] = 'integer'
            self.generated[name] = ir.FunctionDecl(name, [], 'boolean', [
                ir.Local('integer', 'this', initial=_var('f__arg_this'))] +
                self.slot_release(struct) + [
                ir.ReturnStmt(ir.Literal('bool', 'true'))])
        return name

    def gen_darray(self, darray):
        pool = f'si__{darray.out}'
        this = _var('this')
        limit = darray.storage_size - darray.size + 1
        allocate = ir.FunctionDecl(f's__{darray.out}__allocate', [], 'integer', [
            ir.Local('integer', 'this', initial=_var(pool + '_F')),
            ir.IfStmt([(ir.BinaryExpr('!=', this, int_lit(0)), [
                _set(pool + '_F', darray.pool.read(this))])], [
                _set(pool + '_I', ir.BinaryExpr('+', _var(pool + '_I'),
                                                int_lit(darray.size))),
                _set('this', _var(pool + '_I'))]),
            ir.IfStmt([(ir.BinaryExpr('>', this, int_lit(limit)),
                        self.debug_message('Unable to allocate id for an array '
                                           f'of type: {darray.name}') +
                        [ir.ReturnStmt(int_lit(0))])]),
            darray.pool.write(this, _minus_one()),
            ir.ReturnStmt(this)])
        allocate.comments = [f'Generated allocator of {darray.name}']
        deallocate = ir.FunctionDecl(f's__{darray.out}_deallocate',
                                     [ir.Param('integer', 'this')], 'nothing', [
            ir.IfStmt([
                (ir.BinaryExpr('==', this, int_lit(0)),
                 self.debug_message('Attempt to destroy a null array of type: '
                                    f'{darray.name}') + [ir.ReturnStmt()]),
                (ir.BinaryExpr('!=', darray.pool.read(this), _minus_one()),
                 self.debug_message('Double free of type: ' + darray.name) +
                 [ir.ReturnStmt()])]),
            darray.pool.write(this, _var(pool + '_F')),
            _set(pool + '_F', this)])
        deallocate.comments = [f'Generated destructor of {darray.name}']
        return [allocate, deallocate]

    # ------------------------------------------------------------------ callers

    def gen_prototype(self, proto):
        params, returns = proto.key
        names = self.arg_names(params)
        functions = []
        for mode in ('execute', 'evaluate'):
            if mode not in proto.callers_used:
                continue
            ir_params = [ir.Param('integer', 'i')] + [
                ir.Param(t, f'a{n + 1}') for n, t in enumerate(params)]
            body = [_set(g, _var(f'a{n + 1}')) for n, g in enumerate(names)]
            native = 'TriggerExecute' if mode == 'execute' else 'TriggerEvaluate'
            body.append(_call_stmt(native, ir.ArrayRefExpr(proto.trigger,
                                                           _var('i'))))
            result = 'nothing'
            if mode == 'evaluate' and returns != 'nothing':
                body.append(ir.ReturnStmt(_var(self.result_global(returns))))
                result = returns
            functions.append(ir.FunctionDecl(proto.caller(mode), ir_params,
                                             result, body))
        return functions

    def gen_prototype_adapters(self, proto):
        params, returns = proto.key
        names = self.arg_names(params)
        functions = []
        for name, index in proto.ids.items():
            adapter = proto.adapter(name)
            locals_ = [ir.Local(t, f'a{n + 1}', initial=_var(g))
                       for n, (t, g) in enumerate(zip(params, names))]
            args = [_var(f'a{n + 1}') for n in range(len(params))]
            body = list(locals_)
            if returns != 'nothing':
                body.append(_set(self.result_global(returns), _call(name, *args)))
            else:
                body.append(_call_stmt(name, *args))
            body.append(ir.ReturnStmt(ir.Literal('bool', 'true')))
            functions.append(ir.FunctionDecl(adapter, [], 'boolean', body))
            idx = int_lit(index)
            self.init_statements.extend([
                _set(proto.trigger, _call('CreateTrigger'), idx),
                _call_stmt('TriggerAddCondition',
                           ir.ArrayRefExpr(proto.trigger, idx),
                           _call('Condition', ir.FunctionRefExpr(adapter))),
                _call_stmt('TriggerAddAction',
                           ir.ArrayRefExpr(proto.trigger, idx),
                           ir.FunctionRefExpr(adapter))])
        if proto.ids:
            self.extra_globals.append(ir.GlobalVar('trigger', proto.trigger,
                                                   is_array=True))
        return functions

    def gen_dispatch(self, slot):
        """Callers for a virtual method, and the per-type triggers."""
        method = slot.method
        owner = slot.owner
        root = owner.root
        param_types = [out_type(t) for t, _ in method.params]
        returns = out_type(method.returns)
        names = self.arg_names(param_types)
        self.arg_globals['f__arg_this'] = 'integer'
        trigger = self.dispatch_trigger(slot)
        type_read = self.type_storage(root).read(_var('this'))
        callers = []
        modes = getattr(slot, 'modes', None) or {'evaluate'}
        default = None
        if method.defaults is not None and method.defaults != 'nothing':
            fc = self.function_compiler
            ctx = Ctx(owner.scope, owner, None, None, has_this=False)
            default = fc.compile_expression(method.defaults, ctx, method.returns)
        for mode in ('evaluate', 'execute'):
            if mode not in modes:
                continue
            ir_params = [ir.Param('integer', 'this')] + [
                ir.Param(t, f'a{n + 1}') for n, t in enumerate(param_types)]
            body = []
            if default is not None and mode == 'evaluate':
                body.append(ir.IfStmt([(ir.BinaryExpr(
                    '==', ir.ArrayRefExpr(trigger, type_read), NULL),
                    [ir.ReturnStmt(default)])]))
            body.append(_set('f__arg_this', _var('this')))
            body.extend(_set(g, _var(f'a{n + 1}')) for n, g in enumerate(names))
            native = 'TriggerExecute' if mode == 'execute' else 'TriggerEvaluate'
            body.append(_call_stmt(native, ir.ArrayRefExpr(trigger, type_read)))
            result = 'nothing'
            if mode == 'evaluate' and returns != 'nothing':
                body.append(ir.ReturnStmt(_var(self.result_global(returns))))
                result = returns
            name = f'sc__{slot.key}' if mode == 'evaluate' else f'sx__{slot.key}'
            decl = ir.FunctionDecl(name, ir_params, result, body)
            decl.comments = [f'Generated method caller for {owner.name}.'
                             f'{slot.name}']
            callers.append(decl)
        self.extra_globals.append(ir.GlobalVar('trigger', trigger, is_array=True))

        # One trigger per implementation, shared by every type that uses it.
        adapters = []
        made = {}
        family = [owner] + list(owner.descendants())
        for struct in family:
            if struct.is_interface:
                continue
            impl = struct.find_method(slot.name)
            if impl is None or impl.dispatch is not slot or \
                    getattr(impl, 'abstract', False):
                continue
            index = int_lit(struct.typeid)
            if impl.out in made:
                self.init_statements.append(_set(
                    trigger, ir.ArrayRefExpr(trigger, int_lit(made[impl.out])),
                    index))
                continue
            made[impl.out] = struct.typeid
            adapter = 'sa__' + impl.out[3:]
            if adapter not in self.generated:
                locals_ = [ir.Local('integer', 'this',
                                    initial=_var('f__arg_this'))]
                locals_ += [ir.Local(t, f'a{n + 1}', initial=_var(g))
                            for n, (t, g) in enumerate(zip(param_types, names))]
                args = [_var('this')] + [_var(f'a{n + 1}')
                                         for n in range(len(param_types))]
                body = list(locals_)
                if returns != 'nothing':
                    body.append(_set(self.result_global(returns),
                                     _call(impl.out, *args)))
                else:
                    body.append(_call_stmt(impl.out, *args))
                body.append(ir.ReturnStmt(ir.Literal('bool', 'true')))
                self.generated[adapter] = ir.FunctionDecl(adapter, [], 'boolean',
                                                          body)
            self.init_statements.extend([
                _set(trigger, _call('CreateTrigger'), index),
                _call_stmt('TriggerAddCondition', ir.ArrayRefExpr(trigger, index),
                           _call('Condition', ir.FunctionRefExpr(adapter))),
                _call_stmt('TriggerAddAction', ir.ArrayRefExpr(trigger, index),
                           ir.FunctionRefExpr(adapter))])
        return callers

    def gen_interface_create(self, iface):
        trigger = f'st__{iface.out}__create'
        result = self.result_global('integer')
        self.extra_globals.append(ir.GlobalVar('trigger', trigger, is_array=True))
        caller = ir.FunctionDecl(f'sc__{iface.out}__create',
                                 [ir.Param('integer', 'typeid')], 'integer', [
            _call_stmt('TriggerEvaluate', ir.ArrayRefExpr(trigger,
                                                          _var('typeid'))),
            ir.ReturnStmt(_var(result))])
        for struct in iface.descendants():
            own = struct.methods.get('create')
            target = own.out if own is not None else self.allocator(struct)
            adapter = f'sa__{struct.out}__icreate'
            self.generated[adapter] = ir.FunctionDecl(adapter, [], 'boolean', [
                _set(result, _call(target)),
                ir.ReturnStmt(ir.Literal('bool', 'true'))])
            index = int_lit(struct.typeid)
            self.init_statements.extend([
                _set(trigger, _call('CreateTrigger'), index),
                _call_stmt('TriggerAddCondition', ir.ArrayRefExpr(trigger, index),
                           _call('Condition', ir.FunctionRefExpr(adapter)))])
        return caller

    def gen_hook(self, target_name):
        target = self.hook_targets[target_name]
        params = [ir.Param(out_type(t), f'a{n}')
                  for n, (t, _) in enumerate(target.params)]
        args = [_var(p.name) for p in params]
        body = []
        fc = self.function_compiler
        for hook, scope in self.hooks[target_name]:
            ctx = Ctx(scope)
            fc.ctx = ctx
            found = fc.resolve(hook.func)
            fc.ctx = None
            from .codegen import FuncVal, MethodVal
            if isinstance(found, FuncVal):
                func = found.func
            elif isinstance(found, MethodVal) and found.method.is_static:
                func = found.method
            else:
                raise self.error(f'Unable to find function : {hook.func}',
                                 hook.loc)
            if [out_type(t) for t, _ in func.params] != [p.type for p in params]:
                raise self.error(f'hook function {func.name} must take the same '
                                 f'arguments as {target_name}', hook.loc)
            body.append(Comment(f'hook: {func.name}'))
            body.append(_call_stmt(func.out, *args))
        returns = out_type(target.returns)
        if returns == 'nothing':
            body.append(_call_stmt(target_name, *args))
        else:
            body.append(ir.ReturnStmt(_call(target_name, *args)))
        return ir.FunctionDecl('h__' + target_name, params, returns, body)

    # ------------------------------------------------------------------ init

    def resolve_initializer(self, expr, scope):
        fc = self.function_compiler
        fc.ctx = Ctx(scope)
        try:
            found = fc.resolve(expr)
        except VJassError:
            found = None
        fc.ctx = None
        from .codegen import FuncVal, MethodVal
        func = None
        if isinstance(found, FuncVal):
            func = found.func
        elif isinstance(found, MethodVal) and found.method.is_static:
            func = found.method
        if func is None:
            name = expr.name if isinstance(expr, ast.Name) else 'initializer'
            raise self.error(f'Unable to find initializer: {name}', expr.loc)
        if func.params:
            raise self.error(f'Invalid initializer name : {func.name} (it must '
                             'take nothing)', expr.loc)
        return func

    def gen_loaddata(self):
        statements = []
        if not self.c.loaddata:
            return statements
        from .loaddata import loaddata_statements
        for item, scope in self.c.loaddata:
            statements.extend(loaddata_statements(self, item, scope))
        return statements

    # ------------------------------------------------------------------ assemble

    def assemble(self, placed):
        c = self.c
        top = []            # generated helpers at the top
        user = []           # user code in order, with struct helpers
        pending_comments = []

        # Hooks and initializers resolve names, so they need the compiler.
        hook_functions = [self.gen_hook(name) for name in self.hooks]
        initializers = []
        for kind, expr, scope in c.scope_inits:
            initializers.append((kind, self.resolve_initializer(expr, scope)))

        for entry in placed:
            if entry.kind == 'comment':
                pending_comments.append(entry.comment)
            elif entry.kind == 'function':
                if pending_comments:
                    entry.function.comments = pending_comments + list(
                        getattr(entry.function, 'comments', []) or [])
                    pending_comments = []
                user.append(entry.function)
            elif entry.kind == 'struct_end':
                user.append(('dealloc_slot', entry.struct))

        # Allocators and deallocators, as needed. A child's allocator needs
        # its parent's (unless the parent has its own create).
        changed = True
        while changed:
            changed = False
            for struct in list(self.used_allocators):
                parent = struct.parent
                if parent is not None and not parent.is_interface and \
                        parent.methods.get('create') is None and \
                        parent not in self.used_allocators:
                    self.used_allocators.add(parent)
                    changed = True
        for iface in self.used_interface_create:
            for struct in iface.descendants():
                if struct.methods.get('create') is None:
                    self.used_allocators.add(struct)
        # A parent's allocator before its children's.
        allocators = []
        for struct in sorted(c.structs,
                             key=lambda s: sum(1 for _ in s.ancestors)):
            if struct in self.used_allocators and not struct.is_interface:
                allocators.append(self.gen_allocator(struct))

        poly_deallocators = []
        late_deallocators = {}
        for struct in c.structs:
            if struct not in self.used_deallocators:
                continue
            if struct.is_interface or struct.polymorphic:
                poly_deallocators.append(self.gen_poly_deallocator(struct.root))
            elif struct.methods.get('onDestroy') is not None:
                late_deallocators[struct] = self.gen_deallocator(struct)
            else:
                poly_deallocators.append(self.gen_deallocator(struct))

        darray_functions = []
        for darray in c.dynamic_arrays:
            if darray in self.used_darrays:
                darray_functions.extend(self.gen_darray(darray))

        iface_creates = [self.gen_interface_create(i)
                         for i in c.structs if i in self.used_interface_create]

        big = []
        for storage in self.all_big_storages():
            big.extend(storage.functions())

        top = big + poly_deallocators + iface_creates + allocators + \
            darray_functions + hook_functions

        body_functions = []
        for item in user:
            if isinstance(item, tuple):
                decl = late_deallocators.get(item[1])
                if decl is not None:
                    body_functions.append(decl)
            else:
                body_functions.append(item)

        functions = top + body_functions

        # Dispatch callers can be requested while generating the above
        # (a default value that calls a method), so they are made after.
        self.route_forward_calls(functions)

        callers = []
        slots_done = set()
        while True:
            pending = [slot for struct in c.structs
                       for slot in struct.dispatch.values()
                       if slot.needed and slot not in slots_done]
            if not pending:
                break
            for slot in pending:
                slots_done.add(slot)
                callers.extend(self.gen_dispatch(slot))

        tail = []
        protos_done = {}
        while True:
            progress = False
            for proto in list(self.prototypes.values()):
                done = protos_done.get(proto.key, (0, frozenset()))
                if done == (len(proto.ids), frozenset(proto.callers_used)):
                    continue
                progress = True
                protos_done[proto.key] = (len(proto.ids),
                                          frozenset(proto.callers_used))
            if not progress:
                break
        proto_callers = []
        for proto in self.prototypes.values():
            proto_callers.extend(self.gen_prototype(proto))
            tail.extend(self.gen_prototype_adapters(proto))
        tail = list(self.generated.values()) + tail

        main_calls = self.init_calls(initializers, body_functions, tail)
        functions = proto_callers + callers + functions + tail
        self.insert_init(functions, main_calls)

        globals_ = self.collect_globals()
        if self.options.optimize:
            from .inliner import inline_program
            self.inlined = inline_program(functions, self, globals_)

        program = ir.Program()
        program.type_defs = list(c.type_defs)
        program.globals = globals_
        program.natives = [ir.NativeDecl(n.out, [ir.Param(out_type(t), p)
                                                 for t, p in n.params],
                                         out_type(n.returns), n.is_constant)
                           for n in c.natives]
        program.functions = functions
        return program

    def all_big_storages(self):
        seen = set()
        out = []
        storages = list(self.c.big_arrays)
        for struct in self.c.structs:
            for attr in ('pool_storage', 'type_storage', 'slot_storage'):
                storage = getattr(struct, attr, None)
                if storage is not None:
                    storages.append(storage)
        for storage in storages:
            if isinstance(storage, PlainStorage) or id(storage) in seen:
                continue
            seen.add(id(storage))
            out.append(storage)
        return out

    def init_calls(self, initializers, body_functions, tail):
        c = self.c
        statements = list(self.init_statements)
        for struct in c.structs:
            for method in struct.on_inits:
                statements.append(_call_stmt('ExecuteFunc', str_lit(method.out)))
        statements.extend(self.gen_loaddata())
        main_calls = []
        if statements:
            chunks = [statements[i:i + INIT_CHUNK]
                      for i in range(0, len(statements), INIT_CHUNK)]
            names = []
            for n, chunk in enumerate(chunks):
                name = 'jasshelper__initstructs' + (f'_{n}' if n else '')
                names.append(name)
                tail.append(ir.FunctionDecl(name, [], 'nothing', chunk))
            for name in names:
                main_calls.append(_call_stmt('ExecuteFunc', str_lit(name)))
        positions = {f.name: i for i, f in enumerate(body_functions)}
        main_pos = positions.get('main')
        for kind, func in initializers:
            if kind == 'library':
                main_calls.append(_call_stmt('ExecuteFunc', str_lit(func.out)))
            elif main_pos is not None and positions.get(func.out, 1 << 30) < \
                    main_pos:
                main_calls.append(_call_stmt(func.out))
            else:
                main_calls.append(_call_stmt('ExecuteFunc', str_lit(func.out)))
        return main_calls

    def insert_init(self, functions, calls):
        main = next((f for f in functions if f.name == 'main'), None)
        if main is None:
            if calls:
                self.c.warn('no main function: initialization (struct onInit, '
                            'library initializers, trigger setup) is not called; '
                            'call it from your own main')
                self.init_entry = calls
            return

        def replace(statements):
            for i, stmt in enumerate(statements):
                if isinstance(stmt, InitPlaceholder):
                    statements[i:i + 1] = calls
                    return True
                for attr in ('body', 'else_body'):
                    inner = getattr(stmt, attr, None)
                    if isinstance(inner, list) and replace(inner):
                        return True
                if isinstance(stmt, ir.IfStmt):
                    for _, body in stmt.branches:
                        if replace(body):
                            return True
            return False

        if 'main' in self.c.injects:
            if not replace(main.body):
                raise self.error('A well placed //! dovjassinit is required',
                                 self.c.injects['main'][0].loc)
            return
        # After `call InitBlizzard()`, as JassHelper does; a main without one
        # (a script that is not a World Editor map) gets it at the end.
        body = main.body
        for i, stmt in enumerate(body):
            if isinstance(stmt, ir.CallStmt) and stmt.name == 'InitBlizzard':
                body[i + 1:i + 1] = calls
                return
        if body and isinstance(body[-1], ir.ReturnStmt):
            body[len(body) - 1:len(body) - 1] = calls
        else:
            body.extend(calls)

    # ------------------------------------------------------------------ forward calls

    def copy_forward_code_refs(self, functions):
        """`function X` needs X declared above, and a code value cannot be
        routed through a trigger. JassHelper's answer, used here too: put a
        copy of X right above the first function that refers to it, and
        point the reference at the copy (`sf__X`). The copy's own calls are
        then routed like any other."""
        import copy as _copy
        made = {}
        for _ in range(64):
            position = {f.name: i for i, f in enumerate(functions)}
            wanted = {}

            def scan(expr, here):
                if isinstance(expr, ir.FunctionRefExpr):
                    target = position.get(expr.name)
                    if target is not None and target > here:
                        wanted[expr.name] = min(wanted.get(expr.name, here),
                                                here)
                for child in _children(expr):
                    scan(child, here)

            for here, function in enumerate(functions):
                for expr in _statement_expressions(function.body):
                    scan(expr, here)
            if not wanted:
                return
            name, earliest = min(wanted.items(), key=lambda kv: kv[1])
            source = functions[position[name]]
            if source.params:
                raise self.error(f'function {name} takes arguments, so it '
                                 'cannot be used as code')
            copy_name = made.get(name)
            if copy_name is None:
                copy_name = 'sf__' + name
                made[name] = copy_name
                clone = ir.FunctionDecl(copy_name, [], source.returns,
                                        _copy.deepcopy(source.body),
                                        is_constant=source.is_constant)
                clone.comments = [f'Copy of {name}, used as code above its '
                                  'declaration']
                if hasattr(source, 'info'):
                    clone.info = source.info
                functions.insert(earliest, clone)
                self.c.functions.setdefault(copy_name,
                                            self.c.functions.get(name))
            else:
                # The copy exists but sits below this reference: move it up.
                current = next(i for i, f in enumerate(functions)
                               if f.name == copy_name)
                if current > earliest:
                    functions.insert(earliest, functions.pop(current))
            position = {f.name: i for i, f in enumerate(functions)}
            for function in functions:
                _replace_function_refs(function.body, name, copy_name,
                                       position, function.name)
        raise self.error('code values refer to each other in a cycle that '
                         'cannot be ordered')

    def route_forward_calls(self, functions):
        """JASS cannot call a function declared below the caller. Calls to
        methods and generated functions that end up below their caller are
        routed through a trigger evaluation (JassHelper's "method called from
        above its declaration")."""
        self.copy_forward_code_refs(functions)
        position = {f.name: i for i, f in enumerate(functions)}
        decls = {f.name: f for f in functions}

        def reroute(name, args, here, node):
            target = position.get(name)
            if target is None or target <= here:
                return None
            info = self.c.functions.get(name)
            decl = decls[name]
            caller = getattr(functions[here], 'info', None)
            # A plain function called from above is an error in JASS and in
            # JassHelper alike; only generated code (a hook wrapper, an
            # allocator) is routed silently.
            if info is not None and info.kind == 'function' and \
                    caller is not None:
                if not self.options.strict:
                    # JassHelper leaves it for the JASS checker to report.
                    self.c.warn(f'{info.name} is called before it is declared '
                                f'(in {caller.name}); JASS will reject this',
                                caller.loc)
                    return None
                raise self.error(
                    f'{info.name} is called before it is declared',
                    caller.loc,
                    note=f'move it above {caller.name} or call '
                    f'{info.name}.evaluate(...) instead')
            if info is not None and info.kind == 'method' and \
                    caller is not None and not self.options.auto_method_evaluate:
                raise self.error(
                    'Since you added [forcemethodevaluate] to jasshelper.conf, '
                    'calling methods "from above their declaration" requires '
                    'you to add .evaluate, correct this mistake.', info.loc)
            key = (tuple(p.type for p in decl.params), decl.returns)
            proto = self.prototype_for_key(key)
            return proto.caller('evaluate'), [int_lit(proto.id_of(name))] + args

        def walk_expr(expr, here):
            if isinstance(expr, ir.CallExpr):
                expr.args = [walk_expr(a, here) for a in expr.args]
                routed = reroute(expr.name, expr.args, here, expr)
                if routed:
                    return ir.CallExpr(routed[0], routed[1])
                return expr
            if isinstance(expr, ir.FunctionRefExpr):
                target = position.get(expr.name)
                if target is not None and target > here:
                    raise self.error(
                        f'function {expr.name} is used before it is declared; '
                        'a code value cannot be routed through a trigger, so '
                        'the function has to be declared above',
                        None, note=f'in {functions[here].name}')
                return expr
            if isinstance(expr, ParenExpr):
                expr.expr = walk_expr(expr.expr, here)
                return expr
            if isinstance(expr, ir.UnaryExpr):
                expr.operand = walk_expr(expr.operand, here)
                return expr
            if isinstance(expr, ir.BinaryExpr):
                expr.left = walk_expr(expr.left, here)
                expr.right = walk_expr(expr.right, here)
                return expr
            if isinstance(expr, ir.ArrayRefExpr):
                expr.index = walk_expr(expr.index, here)
                return expr
            return expr

        def walk(statements, here):
            for i, stmt in enumerate(statements):
                if isinstance(stmt, ir.Local) and stmt.initial is not None:
                    stmt.initial = walk_expr(stmt.initial, here)
                elif isinstance(stmt, ir.SetStmt):
                    stmt.value = walk_expr(stmt.value, here)
                    if stmt.index is not None:
                        stmt.index = walk_expr(stmt.index, here)
                elif isinstance(stmt, ir.CallStmt):
                    stmt.args = [walk_expr(a, here) for a in stmt.args]
                    routed = reroute(stmt.name, stmt.args, here, stmt)
                    if routed:
                        statements[i] = ir.CallStmt(routed[0], routed[1])
                elif isinstance(stmt, ir.IfStmt):
                    stmt.branches = [(walk_expr(cond, here), body)
                                     for cond, body in stmt.branches]
                    for _, body in stmt.branches:
                        walk(body, here)
                    if stmt.else_body is not None:
                        walk(stmt.else_body, here)
                elif isinstance(stmt, ir.LoopStmt):
                    walk(stmt.body, here)
                elif isinstance(stmt, ir.ExitWhenStmt):
                    stmt.condition = walk_expr(stmt.condition, here)
                elif isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
                    stmt.value = walk_expr(stmt.value, here)

        for here, function in enumerate(functions):
            walk(function.body, here)

    # ------------------------------------------------------------------ globals

    def collect_globals(self):
        c = self.c
        fc = self.function_compiler
        out = []
        # Type ids first: user globals may be initialized from them.
        for struct in c.structs:
            out.append(ir.GlobalVar('integer', f'si__{struct.out}',
                                    is_constant=True,
                                    initial=int_lit(struct.typeid)))
        # Instance pools.
        roots = []
        for struct in c.structs:
            root = struct.root
            if root in roots or struct.is_array_struct:
                continue
            if any(s in self.used_allocators or s in self.used_deallocators
                   for s in root.family()) or root.is_interface:
                roots.append(root)
        for root in roots:
            out.append(ir.GlobalVar('integer', f'si__{root.out}_F',
                                    initial=int_lit(0)))
            out.append(ir.GlobalVar('integer', f'si__{root.out}_I',
                                    initial=int_lit(0)))
            out.extend(self.pool_storage(root).globals())
            if root.polymorphic:
                out.extend(self.type_storage(root).globals())
        for struct in c.structs:
            if struct.slot_fields:
                counter, free_count, free_stack, _ = self.slot_names(struct)
                out.append(ir.GlobalVar('integer', counter, initial=int_lit(0)))
                out.append(ir.GlobalVar('integer', free_count,
                                        initial=int_lit(0)))
                out.append(ir.GlobalVar('integer', free_stack, is_array=True))
                out.extend(self.slot_storage(struct).globals())
        for darray in c.dynamic_arrays:
            pool = f'si__{darray.out}'
            out.append(ir.GlobalVar('integer', pool + '_F', initial=int_lit(0)))
            out.append(ir.GlobalVar('integer', pool + '_I', initial=int_lit(0)))
            out.extend(darray.pool.globals())
            out.extend(darray.storage.globals())
        for name, t in sorted(self.arg_globals.items()):
            out.append(ir.GlobalVar(t, name))
        for name, t in sorted(self.result_globals.items()):
            out.append(ir.GlobalVar(t, name))
        seen = set()
        for var in self.extra_globals:
            if var.name not in seen:
                seen.add(var.name)
                out.append(var)

        # User globals, in order.
        for entry in c.globals:
            if entry.var is not None:
                if entry.comment:
                    entry.var.comment = entry.comment
                out.append(entry.var)
                continue
            info = entry.info
            if info.is_array:
                out.extend(info.storage.globals())
                continue
            initial = None
            if info.decl.init is not None:
                initial = fc.compile_expression(info.decl.init, Ctx(info.scope),
                                                info.type)
            out.append(ir.GlobalVar(out_type(info.type), info.out,
                                    is_constant=info.is_constant,
                                    initial=initial))

        # Struct members.
        for struct in c.structs:
            for field in struct.member_order:
                if not isinstance(field, FieldInfo):
                    continue
                if field.is_static:
                    if field.is_array:
                        out.extend(field.storage.globals())
                        continue
                    initial = None
                    if field.init is not None:
                        ctx = Ctx(field.scope, struct, field.module)
                        initial = fc.compile_expression(field.init, ctx,
                                                        field.type)
                    out.append(ir.GlobalVar(out_type(field.type), field.out,
                                            is_constant=field.is_constant,
                                            initial=initial))
                elif field.is_array:
                    out.extend(field.storage.globals())
                    out.extend(field.offset_storage.globals())
                else:
                    out.extend(field.storage.globals())
        self.check_duplicates(out)
        return out

    def check_duplicates(self, globals_):
        seen = {}
        for var in globals_:
            if var.name in seen:
                raise self.error(f'Identifier redeclared : "{var.name}" (two '
                                 'declarations compile to the same name)')
            seen[var.name] = var
