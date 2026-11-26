"""
A map file as text that `git diff` can compare (#254).

git runs a textconv program on each side of a diff and compares the text it
prints. `textconv(path)` is that text: the `dump` JSON of a file wc3files can
read, laid out one record per line, so a moved doodad or a changed tile shows
as one changed line rather than twenty. Text files (scripts, war3map.wts,
SLK, FDF, MDL) are printed as they are, an MDX model as MDL, and anything
else as one line with its size and SHA-256, so the diff still says that the
file changed.

git names its temporary copy of a file `XXXXXX_<name>`, so the format is
taken from the end of the name.
"""
import base64
import hashlib
import json
import os

from .formats import FORMATS, format_for_name

# Files that are already text. git could diff them itself, but a single
# `*` pattern in .gitattributes is easier than listing every binary one.
TEXT_EXTENSIONS = frozenset((
    'j', 'lua', 'ai', 'wts', 'txt', 'slk', 'fdf', 'toc', 'mdl', 'ini', 'json', 'md',
))

# A list of scalars goes on one line while it stays this short; a longer
# one (a grid row, a long id list) gets a line per item.
_INLINE_LIST = 120

# The cell grids that dump carries as base64, shown as one hex row per line.
_GRIDS = ('wpm', 'shd')
_SHD_ROW = 64


def _format_of(path):
    name = os.path.basename(path).lower()
    # The git prefix keeps war3mapUnits.doo and war3map.doo apart only by
    # their ending.
    if name.endswith('war3mapunits.doo'):
        return 'units'
    if name.endswith('war3map.doo'):
        return 'doodads'
    fmt = format_for_name(path)
    if fmt in ('wts', 'slk'):
        return None
    return fmt


def _is_scalar(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _inline(value):
    return json.dumps(value, ensure_ascii=False, cls=_BytesEncoder)


class _BytesEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            return {'__bytes__': base64.b64encode(bytes(obj)).decode('ascii')}
        return super().default(obj)


def _short_list(value):
    return (isinstance(value, (list, tuple)) and all(_is_scalar(item) for item in value)
            and len(_inline(value)) <= _INLINE_LIST)


def _flat_record(value):
    """A dict whose values are scalars or short scalar lists: one line."""
    return isinstance(value, dict) and all(
        _is_scalar(item) or _short_list(item) for item in value.values())


def _lines(value, indent, prefix=''):
    pad = '  ' * indent
    if (_is_scalar(value) or isinstance(value, (bytes, bytearray)) or not value
            or _short_list(value) or _flat_record(value)):
        return [f'{pad}{prefix}{_inline(value)}']
    if isinstance(value, dict):
        opening, closing = '{', '}'
        children = [_lines(item, indent + 1, f'{_inline(str(key))}: ')
                    for key, item in value.items()]
    else:
        opening, closing = '[', ']'
        children = [_lines(item, indent + 1) for item in value]
    out = [f'{pad}{prefix}{opening}']
    for number, child in enumerate(children):
        if number < len(children) - 1:
            child[-1] += ','
        out.extend(child)
    out.append(f'{pad}{closing}')
    return out


def to_lines(data):
    """`data` as JSON with one record per line: a dict of scalars, or a
    short list of them, stays on one line, so a diff names the record that
    changed. The text is valid JSON."""
    return '\n'.join(_lines(data, 0)) + '\n'


def _grid_rows(data):
    data = dict(data)
    cells = base64.b64decode(data.pop('cells'))
    width = data.get('width') or len(cells) or 1
    data['rows'] = [cells[i:i + width].hex() for i in range(0, len(cells), width)]
    return data


def _shd_size_known(path):
    from .formats import _shd_size_from_siblings
    try:
        return None not in _shd_size_from_siblings(path)
    except Exception:
        return False


def _shd_without_size(raw):
    """A shadow map has no header, and git's copy of one has no war3map.w3e
    beside it to give the size, so its cells go in rows of a fixed length."""
    rows = [raw[i:i + _SHD_ROW].hex() for i in range(0, len(raw), _SHD_ROW)]
    return {'cells': len(raw), 'row_length': _SHD_ROW, 'rows': rows}


def _as_text(raw):
    """`raw` as text when it reads as UTF-8 with no NUL byte, as
    (listfile) and .gitattributes do, or None."""
    if b'\0' in raw:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _summary(raw, note=None):
    line = f'binary file, {len(raw)} bytes, sha256 {hashlib.sha256(raw).hexdigest()}'
    return line + (f'\n{note}' if note else '') + '\n'


def _model_text(raw):
    from wc3files.mdl.mdl_io import save_mdl
    from wc3files.mdl.mdx_io import load_mdx
    return save_mdl(load_mdx(raw))


def textconv(path):
    """The text form of the file at `path`."""
    with open(path, 'rb') as handle:
        raw = handle.read()
    ext = path.rsplit('.', 1)[-1].lower() if '.' in os.path.basename(path) else ''
    try:
        if ext in TEXT_EXTENSIONS:
            return raw.decode('utf-8', errors='replace')
        if ext == 'mdx':
            return _model_text(raw)
        fmt = _format_of(path)
        if fmt is None:
            text = _as_text(raw)
            return text if text is not None else _summary(raw)
        if fmt == 'shd' and not _shd_size_known(path):
            return to_lines(_shd_without_size(raw))
        data = FORMATS[fmt].dump(path, options={})
        if fmt in _GRIDS:
            data = _grid_rows(data)
        return to_lines(data)
    except Exception as error:  # a diff should still run when one side does not read
        return _summary(raw, note=f'not read: {type(error).__name__}: {error}')
