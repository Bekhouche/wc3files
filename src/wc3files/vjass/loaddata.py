"""`//! loaddata "file.slk"` - fill struct members from an SLK table.

The table's top-left cell names a struct. That struct needs a static method
`getFromKey` taking one argument and returning an instance. The first column
holds the keys, the first row the names of instance members. Every other row
becomes, at map initialization:

    set s = Struct.getFromKey(key)
    set s.member = value      // for each filled cell

Values are written as the member's type wants them: integers and booleans
bare, strings quoted. Empty cells and `-` are skipped.
"""
import os

from wc3files.scripts import ir

from .emitter import str_lit, RawExpr
from .errors import VJassError
from .symbols import StructInfo, out_type


def _read_cells(path):
    from wc3files.table.slk_file import SlkFile
    return SlkFile()._read_records(path)


def _literal(value, type_name, loc):
    if type_name == 'string':
        return str_lit(str(value))
    if type_name == 'boolean':
        text = str(value).strip().lower()
        return ir.Literal('bool', 'true' if text in ('1', 'true', 'yes')
                          else 'false')
    if type_name == 'real':
        try:
            return ir.Literal('real', repr(float(value)))
        except (TypeError, ValueError):
            raise VJassError(f'{value!r} is not a real', loc)
    if type_name == 'integer':
        text = str(value).strip()
        if len(text) == 4 and not text.lstrip('-').isdigit():
            return ir.Literal('rawcode', "'" + text + "'")
        try:
            return ir.Literal('int', str(int(float(text))))
        except ValueError:
            raise VJassError(f'{value!r} is not an integer', loc)
    # Any other type: the cell is written as JASS text (a constant's name).
    return RawExpr(str(value))


def loaddata_statements(runtime, item, scope):
    compiler = runtime.c
    path = item.path
    if not os.path.isabs(path):
        base = item.loc.file if item.loc and item.loc.file else None
        candidates = []
        if base and os.path.exists(base):
            candidates.append(os.path.join(os.path.dirname(base), path))
        candidates.extend(os.path.join(f, path)
                          for f in compiler.options.lookup_folders)
        candidates.append(path)
        path = next((c for c in candidates if os.path.isfile(c)), path)
    try:
        cells = _read_cells(path)
    except OSError as exc:
        raise VJassError(f'Unable to open file: {item.path}', item.loc,
                         note=str(exc))
    if not cells:
        raise VJassError(f'Too few cells in slk: {item.path}', item.loc)
    width = max(x for x, _ in cells)
    height = max(y for _, y in cells)
    type_name = cells.get((1, 1))
    struct = compiler.resolve_type(str(type_name), scope, loc=item.loc) \
        if type_name is not None else None
    if not isinstance(struct, StructInfo):
        raise VJassError(f'(row 1, column 1) Undeclared struct type: '
                         f'{type_name}', item.loc)
    getter = struct.find_method('getFromKey')
    if getter is None:
        raise VJassError(f'(row 1, column 1) Struct {struct.name} does not have '
                         'a getFromKey static method', item.loc)
    if not getter.is_static:
        raise VJassError('getFromKey must be a static method', getter.loc)
    if len(getter.params) != 1 or getter.returns == 'nothing':
        raise VJassError('getFromKey must take one argument and a return value',
                         getter.loc)
    key_type = out_type(getter.params[0][0])
    columns = {}
    for x in range(2, width + 1):
        name = cells.get((x, 1))
        if name is None or name == '':
            continue
        field = struct.find_field(str(name))
        if field is None:
            raise VJassError(f'(row 1, column {x}) {name} is not a member of '
                             f'{struct.name}', item.loc)
        if field.is_static or field.is_array:
            raise VJassError(f'{name} is not an instance variable of '
                             f'{struct.name}', item.loc)
        columns[x] = field
    statements = []
    local = 'jasshelper__loaddata'
    for y in range(2, height + 1):
        key = cells.get((1, y))
        if key is None or key == '':
            continue
        statements.append(ir.SetStmt(local, ir.CallExpr(getter.out, [
            _literal(key, key_type, item.loc)])))
        for x, field in columns.items():
            value = cells.get((x, y))
            if value is None or value == '' or str(value).strip() == '-':
                continue
            statements.append(field.storage.write(
                ir.VarExpr(local), _literal(value, out_type(field.type),
                                            item.loc)))
    if statements:
        if not any(g.name == local for g in runtime.extra_globals):
            runtime.extra_globals.append(ir.GlobalVar('integer', local))
    return statements
