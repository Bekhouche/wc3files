import os
from .data import ObjectFileType
from ..table.slk_file import SlkFile

_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')

# Which bundled *MetaData.slk file backs each object category, and (for the one file two
# categories share) how to tell its rows apart.
_FILES = {
     ObjectFileType.Units: 'UnitMetaData.slk',
     ObjectFileType.Items: 'UnitMetaData.slk',
     ObjectFileType.Abilities: 'AbilityMetaData.slk',
     ObjectFileType.Buffs: 'AbilityBuffMetaData.slk',
     ObjectFileType.Upgrades: 'UpgradeMetaData.slk',
     ObjectFileType.Destructables: 'DestructableMetaData.slk',
     ObjectFileType.Doodads: 'DoodadMetaData.slk',
     'misc': 'MiscMetaData.slk',
     # Bundled but deliberately NOT wired into object_table_to_slk()/validate_object_table():
     # 'skin' is the schema for war3mapSkin.txt's icon/UI-skin fields, a different format
     # from the war3mapSkin.w3u-style binary skin overrides the other categories already
     # cover - no real fixture with its '[CustomSkin]'-style section was available this
     # session to verify field-name resolution against, so it's exposed for direct querying
     # only. 'upgrade_effect' isn't a per-field schema in the same sense as the others at all
     # - its rows describe what an upgrade-effect *type* (e.g. 'ratd') means (a Base/Mod
     # value pair), referenced BY VALUE from an UpgradeMetaData.slk field like 'effect1', not
     # keyed by a mod-id that appears directly in object data.
     'skin': 'SkinMetaData.slk',
     'upgrade_effect': 'UpgradeEffectMetaData.slk',
}

# AbilityMetaData.slk's own 'data' column (1-10, 0 = not applicable) is a slot index used to
# tell apart several mod-ids that legitimately share the exact same display name ('field') AND
# the exact same useSpecific/notSpecific object set - e.g. Abilities' generic 'Data' name is
# shared by 674 mod-ids, and useSpecific/notSpecific alone only narrow that down to "which
# ability", not "which of that ability's several same-named Data slots" (one real ability can
# use more than one). This is exactly how w3x2lni's own metadata loader
# (script/prebuilt/metadata.lua's parse_id: `key = key .. characters[num]` where
# `num = meta.data`) resolves the same ambiguity - confirmed against that reference
# implementation, not guessed. No other bundled *MetaData.slk file has a 'data' column at all
# (Units/Upgrades/Destructables/Doodads/Buffs all read back None here), so this only ever
# changes Abilities' field names.
_DATA_SLOT_LETTERS = 'ABCDEFGHIJ'


def field_display_name(field_def):
     """
     The effective column/display name for a metadata field row - `field_def['field']`, with
     an AbilityMetaData.slk 'data' slot letter (A-J) appended when present and nonzero (see
     `_DATA_SLOT_LETTERS` above). Use this instead of reading `field_def['field']` directly
     anywhere a field name is used as a lookup/column key (object_slk.py, object_override.py)
     so mod-ids that share both a display name and a useSpecific/notSpecific set still resolve
     to distinct, stable keys.
     """
     name = field_def['field']
     try:
          slot = int(field_def.get('data') or 0)
     except (TypeError, ValueError):
          slot = 0
     if slot:
          name = f'{name}{_DATA_SLOT_LETTERS[slot - 1]}'
     return name


class ObjectMetaData(object):
     """
     Field-definition lookup for object data (war3map.w3u/.w3t/.w3a/.w3h/.w3q/.w3b/.w3d and
     the equivalent war3campaign.* files): for a given field code (e.g. 'uabi', 'iarm'),
     what it's called in the base *Data.slk table, its value type, min/max/default-related
     bounds, and which object subtypes it applies to (useUnit/useHero/useBuilding/useItem
     for the shared unit/item table).

     Sourced from Blizzard's own MetaData.slk files (bundled in wc3o/metadata/, taken
     directly from a real Warcraft III: Reforged install's war3.w3mod data, not from any
     third-party redistribution) - the same tables the World Editor itself uses to build its
     object editor UI and to validate field values, rather than the hand-transcribed,
     units-only field table this package previously carried.

     ObjectFile's own binary read/write is unaffected by this - it already determines each
     field's on-disk type from the file itself and does not depend on this lookup.
     """

     def __init__(self):
          self._categories = {}

     def load(self, object_type, path=None):
          filename = _FILES[object_type]
          path = path or os.path.join(_METADATA_DIR, filename)

          slk = SlkFile()
          slk.read(path)
          rows = slk.export_data()

          if object_type == ObjectFileType.Items:
               rows = {code: row for code, row in rows.items() if row.get('slk') == 'ItemData'}
          elif object_type == ObjectFileType.Units:
               rows = {code: row for code, row in rows.items() if row.get('slk') != 'ItemData'}

          self._categories[object_type] = rows

     def field(self, object_type, field_code):
          try:
               return self._categories[object_type][field_code]
          except KeyError:
               raise KeyError(f"Unknown {self._label(object_type)} field: {field_code!r}")

     def fields(self, object_type):
          return self._categories.get(object_type, {})

     def unknown_fields(self, object_type, field_codes):
          known = self.fields(object_type)
          return {code for code in field_codes if code not in known}

     @staticmethod
     def _label(object_type):
          return object_type.name if isinstance(object_type, ObjectFileType) else str(object_type)

     @classmethod
     def default(cls):
          """The metadata bundled with this package, covering every category in _FILES,
          lazily loaded and cached on first use."""
          if cls._default_instance is None:
               instance = cls()
               for object_type in _FILES:
                    instance.load(object_type)
               cls._default_instance = instance
          return cls._default_instance

     _default_instance = None
