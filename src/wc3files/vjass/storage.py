"""Logical arrays.

Every struct member, dynamic array and sized global is a logical array of
some length. Up to the JASS array size it is one JASS array. Beyond it
(`struct S[20000]`, `integer array big[40000]`) it is split over several
arrays, read and written through generated accessor functions - JassHelper's
"big arrays".
"""
from wc3files.scripts import ir

from .emitter import wrap, int_lit


class Storage(object):
    elem_type = 'integer'

    def read(self, index):
        raise NotImplementedError

    def write(self, index, value):
        raise NotImplementedError

    def globals(self):
        return []

    def functions(self):
        return []


class PlainStorage(Storage):

    def __init__(self, name, elem_type):
        self.name = name
        self.elem_type = elem_type

    def read(self, index):
        return ir.ArrayRefExpr(self.name, index)

    def write(self, index, value):
        return ir.SetStmt(self.name, value, index)

    def globals(self):
        return [ir.GlobalVar(self.elem_type, self.name, is_array=True)]


class BigStorage(Storage):
    """`length` slots over ceil(length / chunk) JASS arrays."""

    def __init__(self, name, elem_type, length, chunk):
        self.name = name
        self.elem_type = elem_type
        self.length = length
        self.chunk = chunk
        count = (length + chunk - 1) // chunk
        self.arrays = [name if i == 0 else f'{name}__{i}' for i in range(count)]
        self.getter = f'{name}__get'
        self.setter = f'{name}__set'

    def read(self, index):
        return ir.CallExpr(self.getter, [index])

    def write(self, index, value):
        return ir.CallStmt(self.setter, [index, value])

    def globals(self):
        return [ir.GlobalVar(self.elem_type, n, is_array=True)
                for n in self.arrays]

    def _chain(self, make):
        """if/elseif chain over the chunks; `make(array, local_index)`
        returns the body for one chunk."""
        branches = []
        for i, array in enumerate(self.arrays):
            local = ir.VarExpr('i') if i == 0 else ir.BinaryExpr(
                '-', ir.VarExpr('i'), int_lit(i * self.chunk))
            body = make(array, local)
            if i == len(self.arrays) - 1:
                return branches, body
            cond = ir.BinaryExpr('<', ir.VarExpr('i'),
                                 int_lit((i + 1) * self.chunk))
            branches.append((cond, body))
        return branches, []

    def functions(self):
        getter_branches, getter_else = self._chain(
            lambda a, i: [ir.ReturnStmt(ir.ArrayRefExpr(a, i))])
        getter_body = [ir.IfStmt(getter_branches, getter_else)] \
            if getter_branches else getter_else
        if getter_branches:
            # JASS demands a return at the end of a function with a value,
            # even when every branch above already returned.
            getter_body.append(ir.ReturnStmt(ir.ArrayRefExpr(
                self.arrays[-1], ir.BinaryExpr(
                    '-', ir.VarExpr('i'),
                    int_lit((len(self.arrays) - 1) * self.chunk)))))
        getter = ir.FunctionDecl(self.getter, [ir.Param('integer', 'i')],
                                 self.elem_type, getter_body)
        setter_branches, setter_else = self._chain(
            lambda a, i: [ir.SetStmt(a, ir.VarExpr('v'), i)])
        setter_body = [ir.IfStmt(setter_branches, setter_else)] \
            if setter_branches else setter_else
        setter = ir.FunctionDecl(self.setter, [ir.Param('integer', 'i'),
                                               ir.Param(self.elem_type, 'v')],
                                 'nothing', setter_body)
        return [getter, setter]


def make_storage(name, elem_type, length, options):
    """A PlainStorage when `length` slots fit in one JASS array."""
    if length is None or length <= options.max_array_size:
        return PlainStorage(name, elem_type)
    return BigStorage(name, elem_type, length, options.max_array_size)


def offset(base, index):
    """`base + index`, folding a zero base away."""
    if base is None:
        return index
    return ir.BinaryExpr('+', wrap(base), wrap(index))
