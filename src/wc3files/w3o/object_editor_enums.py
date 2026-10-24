import os

from .cfg_file import CfgFile

_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_DEFAULT_FILE = 'UnitEditorData.txt'


class ObjectEditorEnums(object):
    """
    Valid-value lists for the ~35 fixed enum-like field types ObjectMetaData's own 'type'
    column names (unitRace, armorType, attackType, defenseType, ...) - sourced from
    UnitEditorData.txt, which despite its name covers every object category's editor
    dropdowns, not just units. Its own header comment: "Each section corresponds to a field
    type in the Object Editor. The section name should match the 'type' from
    <Object>MetaData.slk" - the exact mechanism object_validator.py needs to check an enum
    field's value against its real, Blizzard-defined set of legal codes instead of skipping
    it entirely (as this package previously had to, having no source for these value lists).

    Same provenance as the *MetaData.slk files: copied directly from the user's own Reforged
    install (war3.w3mod/ui/uniteditordata.txt), not from any third-party redistribution.
    """

    def __init__(self):
        self._sections = {}

    def read(self, path):
        cfg = CfgFile()
        cfg.read(path)
        self._sections = {}
        for section, entries in cfg.export_data().items():
            codes = set()
            for key, raw in entries.items():
                if key.lower() == 'numvalues' or key.lower() == 'sort':
                    continue
                code = raw.split(',', 1)[0]
                codes.add(code)
            self._sections[section] = codes

    def valid_codes(self, type_name):
        """Returns the set of legal codes for this metadata 'type' name, or None if this
        type isn't one of the fixed enums this file defines (e.g. it's a free-form rawcode
        reference, file path, or list type - nothing to check it against)."""
        return self._sections.get(type_name)

    def __len__(self):
        return len(self._sections)

    @classmethod
    def default(cls):
        if cls._default_instance is None:
            instance = cls()
            instance.read(os.path.join(_METADATA_DIR, _DEFAULT_FILE))
            cls._default_instance = instance
        return cls._default_instance

    _default_instance = None
