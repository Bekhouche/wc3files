"""
Merging two sets of object data (#217).

`merge_objects(a, b)` puts the objects of `b` into `a`, as W3O Merger does.
Two kinds of conflict can happen, and each takes one of three policies:

1. a custom object of `b` whose rawcode `a` uses too (`on_conflict`);
2. an edit of the same default object in both (`on_edit_conflict`).

    'remap'  : `b`'s object takes a free rawcode. For an edit of a default
               object, `b`'s edit becomes a new custom object based on it;
    'first'  : `a`'s object is kept and `b`'s dropped;
    'second' : `b`'s object replaces `a`'s.

The returned table maps each of `b`'s old rawcodes to its new one. The
string fields of `b`'s own objects (ability lists, build and train lists,
requirements) are rewritten through it here; the caller applies it to the
script and the other files of `b`'s map.

Both sides are object data in the shape ObjectFile.export_data() gives,
{'w3u': {'original': {...}, 'custom': {...}}, 'w3a': ...}, so one call merges
every type at once, and a free rawcode is free across all types.
"""
import copy
import re

from wc3files.data import ObjectFileType

POLICIES = ('remap', 'first', 'second')
TYPES = ('w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q')
STRING = 3
_ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
# The first letter the World Editor gives a new custom object of each type;
# a unit takes `H` when its base is a hero, whose rawcode starts upper case.
_FIRST_LETTER = {'w3t': 'I', 'w3b': 'B', 'w3d': 'D', 'w3a': 'A', 'w3h': 'B', 'w3q': 'R'}
_TRIGSTR = re.compile(r'TRIGSTR_\d+')


def _export(objects):
    """Object data with every type present: export_data() of a file of one
    type gives None for the others."""
    if hasattr(objects, 'export_data'):
        objects = objects.export_data()
    out = dict(objects)
    for ext in TYPES:
        tables = out.get(ext) or {}
        out[ext] = {'original': tables.get('original') or {},
                    'custom': tables.get('custom') or {}}
    return out


def _base_ids():
    from wc3files.w3o import BaseObjectData
    base = BaseObjectData.default()
    ids = set()
    for ext in TYPES:
        ids.update(base.ids(ObjectFileType(ext)))
    return ids


def _all_ids(data):
    ids = set()
    for ext in TYPES:
        tables = data.get(ext) or {}
        ids.update(tables.get('custom') or {})
        ids.update(tables.get('original') or {})
    return ids


def _free_id(first, taken):
    """The first rawcode `first` + three characters that is not in `taken`."""
    for a in _ALPHABET:
        for b in _ALPHABET:
            for c in _ALPHABET:
                candidate = first + a + b + c
                if candidate not in taken:
                    return candidate
    raise ValueError(f'no free rawcode starts with {first!r}')


def _new_letter(ext, base_id):
    if ext == 'w3u':
        return 'H' if base_id[:1].isupper() else 'h'
    return _FIRST_LETTER[ext]


def _map_strings(value, function):
    if isinstance(value, dict):
        return {key: function(item) if isinstance(item, str) else item
                for key, item in value.items()}
    return function(value) if isinstance(value, str) else value


def rewrite_ids(objects, table):
    """(objects, count): every string field of `objects` (one side's
    export_data() dict) with each comma-separated item equal to an old
    rawcode of `table` replaced by the new one. The `ID` of a custom object
    (its base) is left alone, since a base is always a default object."""
    if not table:
        return objects, 0
    count = 0

    def one(text):
        nonlocal count
        items = text.split(',')
        new = [table.get(item, item) for item in items]
        count += sum(1 for old, item in zip(items, new) if old != item)
        return ','.join(new)

    out = copy.deepcopy(objects)
    for ext in TYPES:
        for table_name in ('original', 'custom'):
            for fields in ((out.get(ext) or {}).get(table_name) or {}).values():
                for key, field in fields.items():
                    if key != 'ID' and isinstance(field, dict) and field.get('type') == STRING:
                        field['value'] = _map_strings(field['value'], one)
    return out, count


def _strings_of(objects):
    found = set()

    def collect(text):
        found.update(_TRIGSTR.findall(text))
        return text

    for ext in TYPES:
        for table_name in ('original', 'custom'):
            for fields in ((objects.get(ext) or {}).get(table_name) or {}).values():
                for key, field in fields.items():
                    if isinstance(field, dict) and field.get('type') == STRING:
                        _map_strings(field['value'], collect)
    return found


def merge_objects(a, b, on_conflict='remap', on_edit_conflict='remap', taken=()):
    """(merged, report): the objects of `b` put into `a`.

    `a` and `b` are ObjectFile objects or their export_data() dicts; neither
    is changed. `taken` adds rawcodes a remapped object must not take, such
    as the ids another file of the target map uses.

    The report gives:
      `table`: {b's old rawcode: new}, for remapped objects of both kinds;
      `remapped`, `kept_first`, `kept_second`: [(type, rawcode)] per conflict;
      `added`: how many of `b`'s objects went in with no conflict;
      `references_rewritten`: list items of `b`'s objects changed by `table`;
      `strings`: the TRIGSTR_ references of `b`'s objects, which point into
          `b`'s war3map.wts."""
    for name, policy in (('on_conflict', on_conflict), ('on_edit_conflict', on_edit_conflict)):
        if policy not in POLICIES:
            raise ValueError(f'{name} must be one of {", ".join(POLICIES)}, not {policy!r}')
    a = _export(a)
    b = _export(b)
    taken = set(taken) | _all_ids(a) | _all_ids(b) | _base_ids()

    # The table first, so `b`'s references can be rewritten before it goes in.
    table, plan = {}, []
    for ext in TYPES:
        mine = (a.get(ext) or {})
        theirs = (b.get(ext) or {})
        for object_id, fields in (theirs.get('custom') or {}).items():
            if object_id in (mine.get('custom') or {}):
                if on_conflict == 'remap':
                    new = _free_id(object_id[:1], taken)
                    taken.add(new)
                    table[object_id] = new
                    plan.append(('remapped', ext, 'custom', object_id, new))
                else:
                    plan.append(('kept_' + on_conflict, ext, 'custom', object_id, object_id))
            else:
                plan.append(('added', ext, 'custom', object_id, object_id))
        for object_id, fields in (theirs.get('original') or {}).items():
            if object_id in (mine.get('original') or {}):
                if on_edit_conflict == 'remap':
                    new = _free_id(_new_letter(ext, object_id), taken)
                    taken.add(new)
                    table[object_id] = new
                    plan.append(('remapped', ext, 'original', object_id, new))
                else:
                    plan.append(('kept_' + on_edit_conflict, ext, 'original', object_id,
                                 object_id))
            else:
                plan.append(('added', ext, 'original', object_id, object_id))

    theirs_all, count = rewrite_ids(b, table)
    merged = copy.deepcopy(a)
    report = {'table': table, 'remapped': [], 'kept_first': [], 'kept_second': [],
              'added': 0, 'references_rewritten': count,
              'strings': sorted(_strings_of(b), key=lambda s: int(s.split('_')[1]))}
    for outcome, ext, table_name, object_id, new in plan:
        target = merged.setdefault(ext, {}).setdefault(table_name, {})
        fields = copy.deepcopy(theirs_all[ext][table_name][object_id])
        if outcome == 'added':
            target[object_id] = fields
            report['added'] += 1
        elif outcome == 'kept_first':
            report['kept_first'].append((ext, object_id))
        elif outcome == 'kept_second':
            target[object_id] = fields
            report['kept_second'].append((ext, object_id))
        else:
            custom = merged[ext].setdefault('custom', {})
            if table_name == 'original':
                # The edit becomes a new object based on the default one.
                fields = dict({'ID': object_id}, **fields)
            custom[new] = fields
            report['remapped'].append((ext, object_id))
    for ext in TYPES:
        merged.setdefault(ext, {}).setdefault('original', {})
        merged[ext].setdefault('custom', {})
    return merged, report
