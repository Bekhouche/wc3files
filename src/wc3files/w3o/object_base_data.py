import os

from .data import ObjectFileType
from ..table.slk_file import SlkFile

_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')

# Units are split across four base files (Blizzard's own split, not this package's choice) -
# UnitBalance.slk in particular is the one that actually carries combat stats (HP, armor,
# regen, bounty, ...); UnitData/UnitUI/UnitWeapons cover race/UI/weapon-only fields. Every
# other category has exactly one base file.
_FILES = {
    ObjectFileType.Units: ['UnitData.slk', 'UnitUI.slk', 'UnitWeapons.slk', 'UnitBalance.slk',
                           'UnitAbilities.slk'],
    ObjectFileType.Items: ['ItemData.slk'],
    ObjectFileType.Abilities: ['AbilityData.slk'],
    ObjectFileType.Buffs: ['AbilityBuffData.slk'],
    ObjectFileType.Upgrades: ['UpgradeData.slk'],
    ObjectFileType.Destructables: ['DestructableData.slk'],
    ObjectFileType.Doodads: ['Doodads.slk'],
}


class BaseObjectData(object):
    """
    Blizzard's own default *value* tables (as opposed to ObjectMetaData's field *schema*) -
    what a brand-new, un-customized object of a given id actually has. A war3map.w3u/w3t/...
    file only ever stores a map's DIFF from these defaults ('original'-table entries) or a
    definition built from one of these as a template ('custom'-table entries, via the
    template id kept under mods['ID']) - without this, there's no way to see an object's
    *full* effective stats, only what a map changed.

    Same provenance as everything else in this package's metadata/ directory: copied
    directly from the user's own real Reforged install, not from any third-party
    redistribution.

    Known limitation, not silently absorbed: a base table's own column names aren't
    guaranteed to align 1:1 with ObjectMetaData's resolved field names for every
    multi-slot/level-indexed field (e.g. a real ability's base data splits a "Data" stat
    into DataA1..DataJ1 sub-slots per level, while ObjectMetaData's naming produces a plain
    Data1/Data2/... per level) - object_table_to_slk() overlays the override on top of the
    base row as-is rather than trying to force-unify every naming scheme, so both the base
    table's native columns and the override's resolved names are preserved (nothing is lost
    or silently misassigned), even where they don't collapse into one clean key.
    """

    def __init__(self):
        self._rows = {}
        self._rows_by_file = {}

    def load(self, object_type, paths=None):
        object_type = ObjectFileType(object_type)
        default_names = _FILES.get(object_type)
        if not default_names:
            return
        paths = paths or [os.path.join(_METADATA_DIR, name) for name in default_names]

        merged = {}
        by_file = {}
        for path in paths:
            filename = os.path.basename(path)
            slk = SlkFile()
            slk.read(path)
            file_rows = {}
            for row_id, row in slk.export_data().items():
                # A real Blizzard base file can carry a genuinely blank/placeholder row -
                # confirmed directly in the bundled AbilityData.slk: an id of a single space,
                # every field blank (' ') or the SLK "unset" marker ('-') - not a real,
                # usable base id at all. Loading it as a legitimate id let
                # _find_closest_template() (object_override.py) pick it as the "best match"
                # template for any sparse custom object with a lot of its own blank/unset
                # fields (a common shape for under-specified custom abilities) purely because
                # blank == blank scores as a hit - poisoning that object's ID with a
                # nonsensical single-space value and desyncing ObjectTable.write()'s binary
                # layout (a 4-byte BYTES field written 1 byte short). Real base ids are always
                # non-blank, so this is safe to filter outright rather than special-case in
                # the scoring itself.
                if not row_id or not row_id.strip():
                    continue
                merged.setdefault(row_id, {}).update(row)
                file_rows[row_id] = row
            by_file[filename] = file_rows
        self._rows[object_type] = merged
        self._rows_by_file[object_type] = by_file

    def get(self, object_type, obj_id):
        """Returns the {column: value} row for this real Blizzard object id, merged across
        every base file this category spans (e.g. Units' 5 files combined into one row), or
        None if it's not a recognized base-game id for this category (e.g. a fully invented
        id with no real template, or a category this file has no base table for). This is
        the right view for showing a human/tool an object's full effective stats in one
        place (see object_table_to_slk()'s --with-base-data) - it is NOT the right view for
        deciding what belongs in one specific literal-path override file (see get_file_row)."""
        object_type = ObjectFileType(object_type)
        return self._rows.get(object_type, {}).get(obj_id)

    def get_file_row(self, object_type, filename, obj_id):
        """Returns the {column: value} row for this object id from just ONE specific base
        file (e.g. only 'UnitBalance.slk', not the other 4 Units files), or None. Unlike
        get() (deliberately merged across every file a category spans, for showing a
        complete effective-stats view), this is what a literal-path override table write
        must use - each real base file has its own fixed, narrow native column set, and
        writing another file's columns into it (e.g. UnitBalance's 'HP'/'STR' into
        UnitData.slk) produces a file real WC3 tooling was never built to expect, which is a
        very plausible crash cause, not just a cosmetic size bloat - confirmed directly: this
        package's own bundled UnitData.slk has exactly 31 native columns, matching a real
        reference map's own UnitData.slk exactly, while the merged get() view for the same
        category spans 100+ columns across all 5 Units files combined."""
        object_type = ObjectFileType(object_type)
        return self._rows_by_file.get(object_type, {}).get(filename, {}).get(obj_id)

    def ids(self, object_type):
        """Every real base-game id known for this category (dict-view, insertion order)."""
        object_type = ObjectFileType(object_type)
        return self._rows.get(object_type, {}).keys()

    @classmethod
    def default(cls):
        if cls._default_instance is None:
            instance = cls()
            for object_type in _FILES:
                instance.load(object_type)
            cls._default_instance = instance
        return cls._default_instance

    _default_instance = None
