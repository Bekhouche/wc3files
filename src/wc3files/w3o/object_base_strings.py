import os

from .data import ObjectFileType
from .cfg_file import CfgFile

_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata', 'strings')

# Which per-race/category real Blizzard *strings.txt files back each object category, and
# which of their keys are real object-data fields (mapped 1:1 onto ObjectMetaData's own
# resolved field names for unam/uhot/utip/utub/upro/utpr/uawt - confirmed by checking
# UnitMetaData.slk directly, not assumed) versus keys that are qualified balance-profile
# variants (e.g. 'Ubertip:custom,V0') or otherwise not part of the base default view.
# The campaign and skin tables are listed last on purpose: later files win
# on a collision, and a skin table's whole job is to override a base name for
# the Reforged art. Campaign units (Hct1, Hjsm, ...) exist *only* in the
# campaign tables, so without them those objects cannot be named at all.
_FILES = {
    ObjectFileType.Units: ['humanunitstrings.txt', 'orcunitstrings.txt', 'nightelfunitstrings.txt',
                           'undeadunitstrings.txt', 'neutralunitstrings.txt',
                           'campaignunitstrings.txt', 'unitskinstrings.txt'],
    ObjectFileType.Abilities: ['humanabilitystrings.txt', 'orcabilitystrings.txt',
                               'nightelfabilitystrings.txt', 'undeadabilitystrings.txt',
                               'neutralabilitystrings.txt', 'commonabilitystrings.txt',
                               'itemabilitystrings.txt', 'campaignabilitystrings.txt',
                               'abilityskinstrings.txt'],
    ObjectFileType.Upgrades: ['humanupgradestrings.txt', 'orcupgradestrings.txt',
                              'nightelfupgradestrings.txt', 'undeadupgradestrings.txt',
                              'neutralupgradestrings.txt', 'campaignupgradestrings.txt',
                              'upgradeskinstrings.txt'],
    ObjectFileType.Items: ['itemstrings.txt', 'itemskinstrings.txt'],
    ObjectFileType.Destructables: ['destructableskinstrings.txt'],
}

_RECOGNIZED_KEYS = {'Name', 'Tip', 'Ubertip', 'Hotkey', 'Propernames', 'Revivetip', 'Awakentip'}


class BaseObjectStrings(object):
    """
    Blizzard's own default display strings (Name/Tip/Ubertip/Hotkey/...) for base-game
    units/abilities/upgrades/items - the companion BaseObjectData is missing. These fields
    (unam/uhot/utip/utub/upro/utpr/uawt in ObjectMetaData) are all tagged 'Profile' - meaning
    there's no real Blizzard base *value* table column for them at all (same category as
    icon/model paths, see TODO.md's override-table entry) - so BaseObjectData's SLK-column
    merge can never fill them in. They live in a completely different, separately-localized
    file family instead (`_locales/enus.w3mod/units/*strings.txt`, the `[Section]key=value`
    format CfgFile already parses generically), sourced from the same real Reforged install
    as everything else in this package's metadata/ directory.

    Same shape as BaseObjectData (get(object_type, obj_id) -> {column: value} or None), so
    the two can be combined with CombinedBaseData for object_table_to_slk()'s base_data
    parameter. Only the plain Name/Tip/Ubertip/Hotkey/Propernames/Revivetip/Awakentip keys are
    kept - a real file can also carry balance-profile-qualified variants like
    'Ubertip:custom,V0' (a different tooltip text under the "custom game" ruleset); those
    aren't part of one single "effective default" view and are dropped rather than guessed
    at which profile a given map wants.
    """

    def __init__(self):
        self._rows = {}

    def load(self, object_type, paths=None):
        object_type = ObjectFileType(object_type)
        default_names = _FILES.get(object_type)
        if not default_names:
            return
        paths = paths or [os.path.join(_METADATA_DIR, name) for name in default_names]

        merged = {}
        for path in paths:
            cfg = CfgFile()
            cfg.read(path)
            for row_id, row in cfg.export_data().items():
                filtered = {k: v for k, v in row.items() if k in _RECOGNIZED_KEYS}
                if filtered:
                    merged.setdefault(row_id, {}).update(filtered)
        self._rows[object_type] = merged

    def get(self, object_type, obj_id):
        """Returns the {column: value} row (Name/Tip/Ubertip/...) for this real Blizzard
        object id, or None if it's not a recognized base-game id for this category."""
        object_type = ObjectFileType(object_type)
        return self._rows.get(object_type, {}).get(obj_id)

    @classmethod
    def default(cls):
        if cls._default_instance is None:
            instance = cls()
            for object_type in _FILES:
                instance.load(object_type)
            cls._default_instance = instance
        return cls._default_instance

    _default_instance = None


class CombinedBaseData(object):
    """Merges several base_data-shaped sources (each providing get(object_type, obj_id) ->
    {column: value} or None) into one - e.g. BaseObjectData (stat defaults) and
    BaseObjectStrings (name/tooltip defaults), which load from entirely different real
    Blizzard file formats (SLK vs. [Section] cfg) and so can't share one loader. Drop-in for
    object_table_to_slk()'s/ObjectFile.to_slk()'s base_data parameter. Later sources win on
    key collisions (there shouldn't be any in practice - the two real sources this was built
    for cover disjoint field sets)."""

    def __init__(self, *sources):
        self._sources = sources

    def get(self, object_type, obj_id):
        merged = None
        for source in self._sources:
            row = source.get(object_type, obj_id)
            if row:
                merged = {**(merged or {}), **row}
        return merged
