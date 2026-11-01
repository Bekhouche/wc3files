"""Function inlining - the optimization JassHelper calls "inline".

A function whose body is a single statement - `return expr`, `call f(...)` or
`set x = expr` - is substituted into its callers. That is what turns vJass's
many small accessors (`method operator x takes nothing returns real`, struct
member getters) back into plain array reads.

Unlike a textual substitution, this only inlines when the result means the
same thing:

* every argument that is not trivially pure (a literal, a local, a code
  value) is used exactly once, in argument order, and nothing in the body
  that could observe or cause side effects (a call, a read of a global) runs
  before the last of them - JASS evaluates all arguments before the call, so
  anything else could reorder side effects;
* an argument dropped by the body must be pure;
* a `real` parameter given an integer argument, or a `real` function whose
  body returns an integer, gets an explicit `I2R` (the conversion the call
  boundary used to do);
* a name the body reads is not shadowed by a local of the caller;
* a function never inlines into itself, and parameters assigned in the body
  rule it out.
"""
import copy

from wc3files.scripts import ir

from .emitter import ParenExpr, RawExpr, Comment, wrap

MAX_PASSES = 6


class _Info(object):
    __slots__ = ('decl', 'kind', 'stmt', 'params')

    def __init__(self, decl, kind, stmt):
        self.decl = decl
        self.kind = kind
        self.stmt = stmt
        self.params = [p.name for p in decl.params]


def _single_statement(decl):
    body = [s for s in decl.body if not isinstance(s, Comment)]
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
        return 'return', stmt
    if decl.returns == 'nothing' and isinstance(stmt, (ir.CallStmt, ir.SetStmt)):
        return 'stmt', stmt
    return None


def _walk(expr, visit):
    """Visit nodes in JASS evaluation order (children first)."""
    if isinstance(expr, ParenExpr):
        _walk(expr.expr, visit)
    elif isinstance(expr, ir.UnaryExpr):
        _walk(expr.operand, visit)
    elif isinstance(expr, ir.BinaryExpr):
        _walk(expr.left, visit)
        _walk(expr.right, visit)
    elif isinstance(expr, ir.CallExpr):
        for arg in expr.args:
            _walk(arg, visit)
    elif isinstance(expr, ir.ArrayRefExpr):
        _walk(expr.index, visit)
    visit(expr)


def _statement_exprs(stmt):
    if isinstance(stmt, ir.ReturnStmt):
        return [stmt.value]
    if isinstance(stmt, ir.CallStmt):
        return list(stmt.args)
    if isinstance(stmt, ir.SetStmt):
        return ([stmt.index] if stmt.index is not None else []) + [stmt.value]
    return []


class Inliner(object):

    def __init__(self, functions, runtime):
        self.functions = functions
        self.runtime = runtime
        api = runtime.c.api
        self.api_returns = {name: f.returns for name, f in api.functions.items()}
        self.global_types = {name: g.type for name, g in api.globals.items()}
        self.function_returns = {f.name: f.returns for f in functions}
        self.global_arrays = set(n for n, g in api.globals.items() if g.is_array)
        self.candidates = {}

    def set_globals(self, globals_):
        for var in globals_:
            self.global_types[var.name] = var.type
            if var.is_array:
                self.global_arrays.add(var.name)

    # ------------------------------------------------------------------ analysis

    def find_candidates(self, protected):
        self.candidates = {}
        for decl in self.functions:
            if decl.name in protected:
                continue
            found = _single_statement(decl)
            if found is None:
                continue
            kind, stmt = found
            if isinstance(stmt, ir.SetStmt) and stmt.target in \
                    [p.name for p in decl.params]:
                continue
            exprs = _statement_exprs(stmt)
            if any(self.contains(e, RawExpr) for e in exprs):
                continue
            if any(self.calls(e, decl.name) for e in exprs) or (
                    isinstance(stmt, ir.CallStmt) and stmt.name == decl.name):
                continue
            self.candidates[decl.name] = _Info(decl, kind, stmt)

    @staticmethod
    def contains(expr, cls):
        found = []
        _walk(expr, lambda e: found.append(e) if isinstance(e, cls) else None)
        return bool(found)

    @staticmethod
    def calls(expr, name):
        found = []
        _walk(expr, lambda e: found.append(e) if isinstance(e, ir.CallExpr) and
              e.name == name else None)
        return bool(found)

    def expr_type(self, expr, local_types):
        if isinstance(expr, ParenExpr):
            return self.expr_type(expr.expr, local_types)
        if isinstance(expr, ir.Literal):
            return {'int': 'integer', 'rawcode': 'integer', 'real': 'real',
                    'string': 'string', 'bool': 'boolean'}.get(expr.kind, 'null')
        if isinstance(expr, ir.VarExpr):
            return local_types.get(expr.name) or self.global_types.get(expr.name)
        if isinstance(expr, ir.ArrayRefExpr):
            return local_types.get(expr.name) or self.global_types.get(expr.name)
        if isinstance(expr, ir.CallExpr):
            return self.function_returns.get(expr.name) or \
                self.api_returns.get(expr.name)
        if isinstance(expr, ir.UnaryExpr):
            if expr.op == 'not':
                return 'boolean'
            return self.expr_type(expr.operand, local_types)
        if isinstance(expr, ir.BinaryExpr):
            if expr.op in ('and', 'or', '==', '!=', '<', '>', '<=', '>='):
                return 'boolean'
            left = self.expr_type(expr.left, local_types)
            right = self.expr_type(expr.right, local_types)
            if left is None or right is None:
                return None
            if 'real' in (left, right):
                return 'real'
            if 'string' in (left, right):
                return 'string'
            return left
        if isinstance(expr, ir.FunctionRefExpr):
            return 'code'
        return None

    def is_pure(self, expr, local_names):
        """Evaluating it has no side effect and reads no global."""
        if isinstance(expr, ParenExpr):
            return self.is_pure(expr.expr, local_names)
        if isinstance(expr, (ir.Literal, ir.FunctionRefExpr)):
            return True
        if isinstance(expr, ir.VarExpr):
            return expr.name in local_names
        if isinstance(expr, ir.UnaryExpr):
            return self.is_pure(expr.operand, local_names) and not (
                expr.op == '-' and not isinstance(expr.operand, ir.Literal))
        return False

    # ------------------------------------------------------------------ substitution

    def substitute(self, info, args, caller_locals, local_types):
        """The inlined body for one call, or None if it is unsafe."""
        callee = info.decl
        params = info.params
        exprs = _statement_exprs(info.stmt)
        callee_types = {p.name: p.type for p in callee.params}

        # Events in evaluation order: ('param', i), ('effect',).
        events = []

        def visit(expr):
            if isinstance(expr, ir.VarExpr):
                if expr.name in callee_types:
                    events.append(('param', params.index(expr.name)))
                else:
                    events.append(('effect',))
                    free.add(expr.name)
            elif isinstance(expr, ir.ArrayRefExpr):
                events.append(('effect',))
                free.add(expr.name)
            elif isinstance(expr, ir.CallExpr):
                events.append(('effect',))
                free.add(expr.name)
            elif isinstance(expr, ir.FunctionRefExpr):
                free.add(expr.name)

        free = set()
        for expr in exprs:
            _walk(expr, visit)
        if isinstance(info.stmt, ir.SetStmt):
            free.add(info.stmt.target)
        if isinstance(info.stmt, ir.CallStmt):
            events.append(('effect',))
            free.add(info.stmt.name)

        # A name the body uses must not be a local of the caller.
        if free & caller_locals:
            return None

        uses = [e[1] for e in events if e[0] == 'param']
        impure = [i for i, a in enumerate(args)
                  if not self.is_pure(a, caller_locals)]
        for i in impure:
            if uses.count(i) != 1:
                return None
        if impure:
            order = [u for u in uses if u in impure]
            if order != sorted(order):
                return None
            last = max(idx for idx, e in enumerate(events)
                       if e[0] == 'param' and e[1] in impure)
            if any(e[0] == 'effect' for e in events[:last]):
                return None

        replacements = {}
        for name, arg in zip(params, args):
            ptype = callee_types[name]
            if ptype == 'real':
                atype = self.expr_type(arg, local_types)
                if atype is None:
                    return None
                if atype == 'integer':
                    arg = ir.CallExpr('I2R', [arg])
            replacements[name] = arg

        def replace(expr):
            if isinstance(expr, ir.VarExpr) and expr.name in replacements:
                return wrap(copy.deepcopy(replacements[expr.name]))
            if isinstance(expr, ParenExpr):
                return ParenExpr(replace(expr.expr))
            if isinstance(expr, ir.UnaryExpr):
                return ir.UnaryExpr(expr.op, replace(expr.operand))
            if isinstance(expr, ir.BinaryExpr):
                return ir.BinaryExpr(expr.op, replace(expr.left),
                                     replace(expr.right))
            if isinstance(expr, ir.CallExpr):
                return ir.CallExpr(expr.name, [replace(a) for a in expr.args])
            if isinstance(expr, ir.ArrayRefExpr):
                return ir.ArrayRefExpr(expr.name, replace(expr.index))
            return copy.deepcopy(expr)

        stmt = info.stmt
        if isinstance(stmt, ir.ReturnStmt):
            value = replace(stmt.value)
            if callee.returns == 'real':
                vtype = self.expr_type(stmt.value, callee_types)
                if vtype is None:
                    return None
                if vtype == 'integer':
                    value = ir.CallExpr('I2R', [value])
            return value
        if isinstance(stmt, ir.CallStmt):
            return ir.CallStmt(stmt.name, [replace(a) for a in stmt.args])
        return ir.SetStmt(stmt.target, replace(stmt.value),
                          replace(stmt.index) if stmt.index is not None else None)

    # ------------------------------------------------------------------ driving

    def run(self, protected):
        total = 0
        for _ in range(MAX_PASSES):
            self.find_candidates(protected)
            if not self.candidates:
                break
            count = 0
            for decl in self.functions:
                count += self.inline_function(decl)
            total += count
            if not count:
                break
        return total

    def inline_function(self, decl):
        local_types = {p.name: p.type for p in decl.params}
        for stmt in decl.body:
            if isinstance(stmt, ir.Local):
                local_types[stmt.name] = stmt.type
        caller_locals = set(local_types)
        counter = [0]

        def expr(e):
            if isinstance(e, ir.CallExpr):
                e.args = [expr(a) for a in e.args]
                info = self.candidates.get(e.name)
                if info is not None and info.kind == 'return' and \
                        info.decl is not decl:
                    result = self.substitute(info, e.args, caller_locals,
                                             local_types)
                    if result is not None:
                        counter[0] += 1
                        return wrap(result)
                return e
            if isinstance(e, ParenExpr):
                e.expr = expr(e.expr)
                return e
            if isinstance(e, ir.UnaryExpr):
                e.operand = expr(e.operand)
                return e
            if isinstance(e, ir.BinaryExpr):
                e.left = expr(e.left)
                e.right = expr(e.right)
                return e
            if isinstance(e, ir.ArrayRefExpr):
                e.index = expr(e.index)
                return e
            return e

        def block(statements):
            for i, stmt in enumerate(statements):
                if isinstance(stmt, ir.Local) and stmt.initial is not None:
                    stmt.initial = expr(stmt.initial)
                elif isinstance(stmt, ir.SetStmt):
                    if stmt.index is not None:
                        stmt.index = expr(stmt.index)
                    stmt.value = expr(stmt.value)
                elif isinstance(stmt, ir.CallStmt):
                    stmt.args = [expr(a) for a in stmt.args]
                    info = self.candidates.get(stmt.name)
                    if info is not None and info.decl is not decl:
                        result = self.substitute(info, stmt.args, caller_locals,
                                                 local_types)
                        if isinstance(result, (ir.CallStmt, ir.SetStmt)):
                            statements[i] = result
                            counter[0] += 1
                        elif result is not None:
                            inner = result
                            while isinstance(inner, ParenExpr):
                                inner = inner.expr
                            if isinstance(inner, ir.CallExpr):
                                statements[i] = ir.CallStmt(inner.name,
                                                            inner.args)
                                counter[0] += 1
                elif isinstance(stmt, ir.IfStmt):
                    stmt.branches = [(expr(c), b) for c, b in stmt.branches]
                    for _, body in stmt.branches:
                        block(body)
                    if stmt.else_body is not None:
                        block(stmt.else_body)
                elif isinstance(stmt, ir.LoopStmt):
                    block(stmt.body)
                elif isinstance(stmt, ir.ExitWhenStmt):
                    stmt.condition = expr(stmt.condition)
                elif isinstance(stmt, ir.ReturnStmt) and stmt.value is not None:
                    stmt.value = expr(stmt.value)

        block(decl.body)
        return counter[0]


def inline_program(functions, runtime, globals_=None):
    inliner = Inliner(functions, runtime)
    if globals_ is not None:
        inliner.set_globals(globals_)
    # Functions the game calls by name keep their bodies intact anyway;
    # inlining never removes a function, so nothing needs protecting except
    # what would be wrong to substitute.
    return inliner.run(protected=set())
