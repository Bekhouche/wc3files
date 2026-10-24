from .data import ObjectFileType
from .object_metadata import ObjectMetaData, field_display_name


def _column_name(existing_row, base_name, mod_id):
    # Two different mod-id rawcodes can legitimately share the same metadata field name
    # (e.g. a repeated per-level tooltip slot) - fall back to naming the column after the
    # raw mod-id itself rather than silently overwriting one column's value with another's.
    if base_name not in existing_row:
        return base_name
    return f'{base_name} ({mod_id})'


def object_table_to_slk(object_type, table_data, metadata=None, base_data=None):
    """
    Flatten one object category's parsed modification data (one entry of
    ObjectFile.export_data(), e.g. export_data()['w3u']) into flat {row_id: {column: value}}
    tables ready for SlkFile.write() - one for the 'original' table (fields a map overrode on
    a base-game object) and one for 'custom' (map-defined objects built from a template).

    Without base_data, this reflects exactly what the map itself customizes (what's actually
    stored in e.g. war3map.w3u), not Blizzard's own default stat tables. Pass a
    wc3files.w3o.BaseObjectData (or BaseObjectData.default()) to merge each row onto its real
    base-game object's (or, for 'custom' entries, its template's) full default stats first -
    see BaseObjectData's own docstring for the one honest limitation of that merge (base
    table column names aren't guaranteed to line up 1:1 with the override's resolved field
    names for every multi-slot field; both are kept rather than guessed at).

    Column names come from the real Blizzard *MetaData.slk field definitions (ObjectMetaData)
    when the mod-id is recognized, falling back to the raw 4-character mod-id rawcode for
    anything wc3files' bundled metadata doesn't know - deliberately never dropping a field just
    because it's unrecognized (unrecognized/non-standard fields are exactly what a map using
    non-standard object data, protected or otherwise, would have).
    """
    if table_data is None:
        return {'original': {}, 'custom': {}}

    metadata = metadata or ObjectMetaData.default()
    fields = metadata.fields(ObjectFileType(object_type))

    result = {}
    for table_name in ('original', 'custom'):
        rows = {}
        for obj_id, mods in (table_data.get(table_name) or {}).items():
            # Collision-avoidance in _column_name is only about two different override
            # mod-ids landing on the same field name within THIS row - it must not trigger
            # against a base_data-seeded value, which an override field is always meant to
            # replace. So the override is built in its own dict first, using the exact same
            # logic as the no-base_data case, and only merged onto the base row afterward.
            override = {}
            for mod_id, mod in mods.items():
                if mod_id == 'ID':
                    override['template_id'] = mod
                    continue
                field_def = fields.get(mod_id)
                base_name = field_display_name(field_def) if field_def else mod_id
                value = mod.get('value')
                if isinstance(value, dict):
                    for level in sorted(value.keys()):
                        col = _column_name(override, f'{base_name}{level + 1}', mod_id)
                        override[col] = value[level]
                else:
                    col = _column_name(override, base_name, mod_id)
                    override[col] = value

            base_id = mods.get('ID', obj_id)
            row = dict(base_data.get(object_type, base_id) or {}) if base_data else {}
            row.update(override)
            rows[obj_id] = row
        result[table_name] = rows
    return result
