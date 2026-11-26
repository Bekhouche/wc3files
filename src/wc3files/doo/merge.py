"""
Merging war3map.doo files (#219).

`merge_doodads` puts the doodads and destructables of several war3map.doo
files into one, as Warcraft 3 Doodad Merger does. This places objects of
several maps, or of several copies of one map, in the same terrain, and gets
past the World Editor's limits on what one edit can place.

The World Editor names a placed destructable that the script or the GUI
triggers use `gg_dest_<rawcode>_<editor id>`, so a destructable that gets a
new editor id loses its variable. The report lists those.
"""
import collections
import copy
import re

from .map_doodads_file import MapDoodadsFile

# gg_dest_LTlt_0012: the rawcode, then the editor id with at least 4 digits.
_DESTRUCTABLE_VARIABLE = re.compile(rb'gg_dest_([A-Za-z0-9]{4})_(\d{4,})')


def referenced_destructables(*texts):
    """{(rawcode, editor id)} of the `gg_dest_` variables named in `texts`
    (the script, war3map.wtg and war3map.wct, as bytes or str)."""
    found = set()
    for text in texts:
        if not text:
            continue
        if isinstance(text, str):
            text = text.encode('utf-8', errors='replace')
        for rawcode, number in _DESTRUCTABLE_VARIABLE.findall(text):
            found.add((rawcode.decode('latin-1'), int(number)))
    return found


def _read(raw):
    handler = MapDoodadsFile()
    handler.read(bytes(raw))
    return handler.get_data()


def merge_doodads(sources, references=None, renumber_all=False):
    """(MapDoodadsFile, report): every record of `sources`, in order.

    `sources` are MapDoodadsFile objects, their get_data() dicts, or the
    bytes of war3map.doo files. The result takes the highest format version
    among them, so no record loses a field. Editor ids: the first source
    keeps its own, so its script still finds its destructables, and the
    records of the others are numbered on from the highest id; with
    `renumber_all`, every record is numbered from 0 in order.

    `references` is a list, one entry per source, of the `gg_dest_` pairs
    its script and triggers use (see referenced_destructables), or None.

    The report gives:
      `renumbered`: per source, {old editor id: new} for the ids that changed;
      `broken_references`: [{'source', 'id', 'old', 'new'}], a renumbered
          destructable that its source's script or triggers name;
      `item_tables`: [{'source', 'index', 'id', 'pointer'}], a record of a
          source after the first whose item_table_pointer is set: it points
          into its own map's item tables, which the merged map may not have;
      `specials_dropped`: special doodads left out as exact duplicates;
      `duplicate_ids`: editor ids the first source itself repeats, kept as
          they are without `renumber_all` (16 of 341 corpus maps have one);
      `version`: the (version, subversion) written."""
    datas = [_read(s) if isinstance(s, (bytes, bytearray))
             else s.get_data() if isinstance(s, MapDoodadsFile) else s for s in sources]
    if not datas:
        raise ValueError('give at least one war3map.doo')
    if references is not None and len(references) != len(datas):
        raise ValueError('give one set of references per source, or none')
    newest = max(datas, key=lambda d: (d.get('version', 8), d.get('subversion', 11)))
    version, subversion = newest.get('version', 8), newest.get('subversion', 11)

    first_ids = collections.Counter(d.get('editor_id', 0) for d in datas[0].get('doodads', []))
    report = {'renumbered': [], 'broken_references': [], 'item_tables': [],
              'specials_dropped': 0, 'version': (version, subversion),
              'duplicate_ids': [] if renumber_all else sorted(
                  i for i, n in first_ids.items() if n > 1)}
    doodads = []
    if renumber_all:
        next_id = 0
    else:
        next_id = max((d.get('editor_id', 0) for d in datas[0].get('doodads', [])),
                      default=-1) + 1
    for number, data in enumerate(datas):
        changed = {}
        for index, record in enumerate(data.get('doodads', [])):
            record = copy.deepcopy(record)
            old = record.get('editor_id', 0)
            if renumber_all or number > 0:
                record['editor_id'] = next_id
                next_id += 1
                if record['editor_id'] != old:
                    changed[old] = record['editor_id']
            if number > 0 and record.get('item_table_pointer', -1) != -1:
                report['item_tables'].append({'source': number, 'index': index,
                                              'id': record['id'],
                                              'pointer': record['item_table_pointer']})
            doodads.append(record)
        report['renumbered'].append(changed)
        if references is not None:
            for rawcode, old in sorted(references[number] or ()):
                if old in changed:
                    report['broken_references'].append(
                        {'source': number, 'id': rawcode, 'old': old, 'new': changed[old]})

    specials, seen = [], set()
    for data in datas:
        for special in data.get('specials', []):
            key = repr(sorted(special.items())) if isinstance(special, dict) else repr(special)
            if key in seen:
                report['specials_dropped'] += 1
                continue
            seen.add(key)
            specials.append(copy.deepcopy(special))

    merged = {'version': version, 'subversion': subversion,
              'skins': any(d.get('skins') for d in datas),
              'doodads': doodads,
              'special_version': newest.get('special_version', 0),
              'specials': specials}
    return MapDoodadsFile().import_data(merged), report
