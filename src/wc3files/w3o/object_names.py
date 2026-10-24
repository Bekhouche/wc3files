"""Resolving an object rawcode to the name a player would recognize.

A rawcode like `hfoo` or `u00T` is all a replay, a doodad placement or an
object-data table ever records. Turning it into "Footman" or a map's own
"Cannon Tower" needs two different sources depending on where the object
came from:

* **Base game objects** (`hfoo`, `AOsf`, `wneg`) - the bundled strings
  tables, which carry real display names for units, items, abilities and
  upgrades.
* **A map's own objects** (`n000`, `u00T`) - the map's object data, where a
  custom object's name field usually holds a `TRIGSTR_nnn` reference that
  only that map's `war3map.wts` can resolve. An SLK map is different again:
  it ships replacement `Units/*.slk` tables with a plain name column and no
  binary object file at all.

`ObjectNames` merges whichever of these are available, newest-wins, so a map
that renames a base-game unit shadows the stock name while everything it
does not touch still resolves.

One real detail worth stating, found while building this: on a Blizzard
campaign map the custom units sit in `war3map.w3u` but their *names* are in
`war3mapSkin.w3u`, so reading only the former yields objects with no names
at all. Both are merged.
"""
import os
import re
import tempfile

from .data import ObjectFileType

# The field each category uses for a display name.
_NAME_FIELDS = {
    ObjectFileType.Units: 'unam',
    ObjectFileType.Items: 'unam',
    ObjectFileType.Abilities: 'anam',
    ObjectFileType.Upgrades: 'gnam',
    ObjectFileType.Destructables: 'bnam',
    ObjectFileType.Doodads: 'dnam',
    ObjectFileType.Buffs: 'fnam',
}

# Object files a map may carry, and the category each holds. The `Skin`
# variants are the Reforged HD overrides and frequently hold the names.
_MAP_OBJECT_FILES = (
    ('war3map.w3u', ObjectFileType.Units),
    ('war3mapskin.w3u', ObjectFileType.Units),
    ('war3map.w3t', ObjectFileType.Items),
    ('war3mapskin.w3t', ObjectFileType.Items),
    ('war3map.w3a', ObjectFileType.Abilities),
    ('war3mapskin.w3a', ObjectFileType.Abilities),
    ('war3map.w3q', ObjectFileType.Upgrades),
    ('war3mapskin.w3q', ObjectFileType.Upgrades),
    ('war3map.w3b', ObjectFileType.Destructables),
    ('war3mapskin.w3b', ObjectFileType.Destructables),
    ('war3map.w3d', ObjectFileType.Doodads),
    ('war3mapskin.w3d', ObjectFileType.Doodads),
    ('war3map.w3h', ObjectFileType.Buffs),
    ('war3mapskin.w3h', ObjectFileType.Buffs),
)

# SLK tables an SLK map ships, and the column holding a name.
_MAP_SLK_FILES = (
    ('units/unitui.slk', ObjectFileType.Units, 'name'),
    ('units/unitdata.slk', ObjectFileType.Units, 'name'),
    ('units/itemdata.slk', ObjectFileType.Items, 'name'),
    ('units/abilitydata.slk', ObjectFileType.Abilities, 'name'),
    ('units/upgradedata.slk', ObjectFileType.Upgrades, 'name'),
)

_TRIGSTR = re.compile(r'^TRIGSTR_(\d+)$')


class ObjectNames(object):
    """Rawcode -> display name, from the base game and optionally a map."""

    def __init__(self):
        self._names = {}          # rawcode -> name
        self._categories = {}     # rawcode -> ObjectFileType
        self._templates = {}      # custom rawcode -> the base object it derives from
        self._strings = None
        self._base_data = None
        self._world_edit = None

    # -- lookup -----------------------------------------------------------

    def get(self, rawcode, default=None):
        """The display name for a rawcode, or `default`.

        Map-specific names take precedence over base-game ones, because a
        map that renames a stock unit means the new name.
        """
        if rawcode in self._names:
            return self._names[rawcode]

        # A custom object that does not override its name keeps the name of
        # the base object it was derived from, so follow that link before
        # giving up. Guarded against a chain that loops back on itself.
        template = self._templates.get(rawcode)
        seen = {rawcode}
        while template and template not in seen:
            if template in self._names:
                return self._names[template]
            seen.add(template)
            template = self._templates.get(template)

        if self._strings is not None:
            for candidate in (rawcode, self._templates.get(rawcode)):
                if not candidate:
                    continue
                for object_type in (ObjectFileType.Units, ObjectFileType.Items,
                                    ObjectFileType.Abilities, ObjectFileType.Upgrades,
                                    ObjectFileType.Destructables,
                                    ObjectFileType.Doodads, ObjectFileType.Buffs):
                    row = self._strings.get(object_type, candidate)
                    if row and row.get('Name'):
                        # Multi-level entries are comma-separated; the first
                        # is the object's own name.
                        return row['Name'].split(',')[0]

            # Categories with no strings table of their own keep their name
            # in the data tables instead.
            for candidate in (rawcode, self._templates.get(rawcode)):
                if not candidate:
                    continue
                found = self._base_data_name(candidate)
                if found:
                    return found
        return default

    def category(self, rawcode):
        return self._categories.get(rawcode)

    def __len__(self):
        return len(self._names)

    def __contains__(self, rawcode):
        return self.get(rawcode) is not None

    # -- loading ----------------------------------------------------------

    def load_base_game(self):
        """Use the bundled strings tables for base-game objects."""
        from .object_base_strings import BaseObjectStrings

        self._strings = BaseObjectStrings.default()
        return self

    def _base_data_name(self, rawcode):
        """A name from the base *data* tables rather than the strings tables.

        Destructables and doodads have no strings table of their own; their
        name sits in the SLK as a `WESTRING_*` key that the World Editor
        strings resolve (`Dogv` -> `WESTRING_DEST_DOGV` -> "Graveyard").
        """
        from .object_base_data import BaseObjectData
        from .world_edit_strings import WorldEditStrings

        if self._base_data is None:
            self._base_data = BaseObjectData.default()
            self._world_edit = WorldEditStrings.default()

        for object_type in (ObjectFileType.Destructables, ObjectFileType.Doodads,
                            ObjectFileType.Units, ObjectFileType.Items):
            row = self._base_data.get(object_type, rawcode)
            if not row:
                continue
            value = row.get('Name') or row.get('name')
            if not value or not str(value).strip() or str(value).strip() == '-':
                continue
            value = str(value).strip()
            resolved = self._world_edit.resolve(value)
            if resolved and resolved != value:
                return resolved
            # Not a WESTRING key - the SLK holds the plain name.
            if not value.startswith('WESTRING_'):
                return value
        return None

    def load_map(self, path):
        """Read a `.w3x`/`.w3m` and take the names of everything it defines."""
        from ..w3x.w3x_file import W3xFile

        w3x = W3xFile()
        w3x.read(path)
        return self.load_map_files(w3x.files)

    def load_map_files(self, files):
        """Same as `load_map`, for an already-open `{filename: bytes}` table."""
        lowered = {}
        for name, data in (files or {}).items():
            if data:
                lowered[name.lower().replace('/', '\\')] = data

        strings = self._read_wts(lowered.get('war3map.wts'))

        for filename, object_type in _MAP_OBJECT_FILES:
            data = lowered.get(filename)
            if data:
                self._read_object_file(data, filename, object_type, strings)

        for filename, object_type, column in _MAP_SLK_FILES:
            data = lowered.get(filename)
            if data:
                self._read_slk(data, filename, object_type, column)

        return self

    # -- internals --------------------------------------------------------

    @staticmethod
    def _read_wts(data):
        if not data:
            return {}
        from ..table.wts_file import WtsFile

        reader = WtsFile()
        reader.read(data)  # WtsFile.read accepts raw bytes
        return {str(key): value for key, value in (reader.export_data() or {}).items()}

    def _read_object_file(self, data, filename, object_type, strings):
        from .object_file import ObjectFile

        try:
            obj = ObjectFile()
            obj.read(data, object_type.value)  # ObjectFile.read accepts bytes
            exported = obj.export_data()
        except Exception:
            # A map can carry an object file this package cannot read; that
            # should cost those names, not the whole lookup.
            return

        table = exported.get(object_type.value) or {}
        field = _NAME_FIELDS.get(object_type)
        for section in ('original', 'custom'):
            for obj_id, mods in (table.get(section) or {}).items():
                self._categories.setdefault(obj_id, object_type)
                # 'ID' holds the base object a custom one was derived from.
                template = mods.get('ID')
                if isinstance(template, str) and template and template != obj_id:
                    self._templates.setdefault(obj_id, template)
                entry = mods.get(field) if field else None
                if not entry:
                    continue
                value = entry.get('value')
                if isinstance(value, dict):   # per-level values
                    value = value.get(min(value)) if value else None
                name = self._resolve_string(value, strings)
                if name:
                    self._names[obj_id] = name

    def _read_slk(self, data, filename, object_type, column):
        from ..table.slk_file import SlkFile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, os.path.basename(filename))
            with open(path, 'wb') as handle:
                handle.write(data)
            try:
                slk = SlkFile()
                slk.read(path)
                rows = slk.export_data()
            except Exception:
                return

        for obj_id, row in (rows or {}).items():
            if not obj_id or not str(obj_id).strip():
                continue
            self._categories.setdefault(obj_id, object_type)
            value = row.get(column)
            if value and str(value).strip() not in ('-', ''):
                self._names.setdefault(obj_id, str(value))

    @staticmethod
    def _resolve_string(value, strings):
        if not isinstance(value, str) or not value.strip():
            return None
        value = value.strip()
        match = _TRIGSTR.match(value)
        if not match:
            return value
        # The reference is zero-padded ('TRIGSTR_007') while the strings file
        # keys on the plain number, so normalize before looking up.
        resolved = strings.get(str(int(match.group(1))))
        # When it cannot be resolved - a map shipping object data without its
        # strings file - keep the placeholder. Falling through to the
        # template's name here would quietly report the wrong name instead of
        # an obviously unresolved one.
        return resolved if resolved is not None else value

    # -- construction -----------------------------------------------------

    @classmethod
    def for_map(cls, path, base_game=True):
        """Names for a map's own objects, falling back to the base game."""
        instance = cls()
        if base_game:
            instance.load_base_game()
        instance.load_map(path)
        return instance

    @classmethod
    def base_game(cls):
        return cls().load_base_game()
