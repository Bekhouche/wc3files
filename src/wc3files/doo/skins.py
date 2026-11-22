"""Whether the records of a war3map.doo or war3mapUnits.doo carry a skin id.

Since 1.32 every record of both files has a 4-byte skin id after its scale,
but the file version was not changed (Blizzard forum, "war3mapUnits.doo
changed, format version not changed"; HiveWE's wiki: `if (game_version >=
1.32) char[4] skin_id`). So the field is in every record of a file or in
none, and the game and the World Editor read every record the same way.

The readers detect it per record, by peeking at the byte after the scale. The
writers used to decide per record too, so a record added by code decided on
its own, and the two files did it in opposite ways: a doodad without
`has_skin` got a skin, a unit without one did not. Either way the file then
mixed two layouts, which the game reads out of step from that record on.

Now presence is a property of the file, `data['skins']`, set on read and
used for every record on write.
"""


def file_skins(records):
    """`skins` for a file just read: True or False when all its records
    agree, None when there are none or they disagree (no corpus file does;
    such a file is then written back record by record, as it was read)."""
    seen = {bool(record.get('has_skin')) for record in records}
    return seen.pop() if len(seen) == 1 else None


def writer_skins(declared, records):
    """True or False to write every record with or without a skin, or None
    to follow each record's own `has_skin` (a file read with mixed records).

    `declared` is the file's `skins`, when it has one. Data built in code,
    without it, follows the records that do say: a file read before `skins`
    existed has `has_skin` on every record. With nothing to go on, a skin is
    written when any record's skin differs from its id."""
    if declared is not None:
        return bool(declared)
    known = {bool(record['has_skin']) for record in records if 'has_skin' in record}
    if len(known) > 1:
        return None
    if known:
        return known.pop()
    return any(record.get('skin', record['id']) != record['id'] for record in records)


def record_skin(record, skins):
    """Whether to write `record` with a skin, given writer_skins()'s answer."""
    if skins is not None:
        return skins
    return bool(record.get('has_skin', record.get('skin', record['id']) != record['id']))
