import json
import math
import os

from ..data import BinaryDataType
from .data import ObjectFileType

from .object_metadata import ObjectMetaData, field_display_name
from .object_base_data import BaseObjectData

# ObjectMetaData's semantic 'type' string does NOT reliably predict the binary MOD_TYPE code
# - checked against real object files' own ground-truth mod['type'] rather than assumed, and
# even aggregating by type *string* (e.g. every field metadata calls 'int') isn't reliable:
# among 909 distinct mod-ids checked across every real map this session had access to, even
# 'int'/'real'/'unreal' themselves showed cross-type conflicts for a handful of specific
# mod-ids. What *is* reliable is checking by the exact mod-id itself: 886 of those 909 had one
# single, unambiguous binary type in every real file they appeared in. That per-mod-id table -
# not a type-string heuristic - is ConfirmedModBinaryTypes.json, bundled here and used as the
# primary source; the 23 mod-ids that were genuinely inconsistent across different real files
# (plausibly different WE-version encoding choices for numerically-equivalent REAL/UNREAL
# forms) and anything never observed at all fall back to a type-string-based best guess,
# flagged as unconfirmed in rebuild_object_table's report rather than silently trusted.
_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_CONFIRMED_MOD_ID_TYPES_FILE = 'ConfirmedModBinaryTypes.json'
_confirmed_mod_id_types = None

_TYPE_STRING_FALLBACK = {
    'int': BinaryDataType.INT.value,
    'real': BinaryDataType.REAL.value,
    'unreal': BinaryDataType.UNREAL.value,
    'bool': BinaryDataType.INT.value,
}


def _load_confirmed_mod_id_types():
    global _confirmed_mod_id_types
    if _confirmed_mod_id_types is None:
        path = os.path.join(_METADATA_DIR, _CONFIRMED_MOD_ID_TYPES_FILE)
        with open(path, 'r', encoding='utf-8') as handle:
            _confirmed_mod_id_types = json.load(handle)
    return _confirmed_mod_id_types


def _binary_type_for(object_type, mod_id, meta_type, value):
    confirmed = _load_confirmed_mod_id_types()
    key = f'{object_type.value}:{mod_id}'
    if key in confirmed:
        confirmed_type = confirmed[key]
        # A handful of mod-id codes (confirmed directly: Abilities' generic per-level
        # "Data" slot family in particular) turn out to be reused as a literal 4-char slot
        # across genuinely different, unrelated abilities/effects rather than always
        # meaning the same field - so the empirically "confirmed" type for one real
        # occurrence of this exact mod-id doesn't guarantee every other occurrence agrees.
        # A value that has an actual fractional part is unambiguous proof it's not a
        # genuine INT here regardless of what the table says for this mod-id elsewhere -
        # trust that direct, hard evidence over the table for this one occurrence, rather
        # than truncating (corrupting) a real fractional value to match a label that's
        # demonstrably wrong for it.
        if confirmed_type == BinaryDataType.INT.value and isinstance(value, float) and value != int(value):
            return BinaryDataType.REAL.value, False
        return confirmed_type, True
    # A directly-observed int/float value is more trustworthy than the metadata type
    # *string* at this point - confirmed against real data that the string alone isn't
    # reliable even for 'int'/'real'/'unreal' themselves (a real map's own 'rangeN1'
    # value of 900.0, i.e. this mod-id genuinely UNREAL here, was getting forced back to
    # INT because its metadata says 'int' and this exact mod-id isn't in the confirmed
    # table - corrupting a value that was already unambiguous from its own Python type).
    # A string value is deliberately excluded from this - it's ambiguous between "really a
    # string field" and "a numeric field reread as a string" (the known sparse-SLK-column
    # quirk), so the metadata type string is the better signal specifically for strings.
    if isinstance(value, bool) or isinstance(value, int):
        return BinaryDataType.INT.value, False
    if isinstance(value, float):
        # Python has no separate float "flavor" for REAL vs UNREAL - both decode to a plain
        # float, so observing "it's a float" only rules out INT, it can't tell these two
        # apart the way it can distinguish INT from non-INT above. The metadata type string
        # is the only signal available for that distinction specifically, and - checked
        # directly against w3x2lni's own metadata loader (script/prebuilt/metadata.lua's
        # get_typedefine/typedefine table) - the community's dominant optimizer/deprotector
        # trusts this exact string unconditionally, with no per-mod-id override table of its
        # own at all, and has no known reports of corrupting numeric values from doing so.
        # Deliberately narrower than trusting meta_type everywhere (see the INT-string
        # distrust above, which stays - that one is about metadata wrongly calling a
        # genuinely fractional value 'int', not about REAL vs UNREAL).
        if meta_type == 'unreal':
            return BinaryDataType.UNREAL.value, False
        return BinaryDataType.REAL.value, False
    if isinstance(value, str):
        stripped = value.strip()
        if stripped not in ('', '-', '_'):
            try:
                float(value)
            except ValueError:
                # A string that isn't a known SLK "unset" marker and doesn't even parse as a
                # number (e.g. a genuine order-string value like 'cyclone'/'metamorphosis') is
                # unambiguous evidence of a real string field, regardless of what a resolved
                # mod-id's metadata type claims - confirmed against a real map where a
                # wrongly-guessed template (see _find_closest_template's docstring) resolved
                # such a value to an unrelated numeric-typed sibling mod-id, and trusting that
                # mod-id's 'unreal'/'int' metadata type over the value's own clear evidence
                # made the numeric coercion below fail and silently drop it entirely. This is
                # the same "trust hard evidence in the value itself over a label that can be
                # wrong for this specific occurrence" principle already applied to floats
                # above - writing it as STRING here, even under an uncertain mod-id, preserves
                # the real data instead of losing it outright.
                return BinaryDataType.STRING.value, False
    if meta_type in _TYPE_STRING_FALLBACK:
        return _TYPE_STRING_FALLBACK[meta_type], False
    return BinaryDataType.STRING.value, False


def literal_table_paths(object_type):
    """The literal, in-game relative paths (e.g. 'Units\\UnitData.slk') build_override_tables()
    can produce for this category - useful for a caller (like the CLI) that needs to know
    what to look for on disk without reaching into this module's private tag/filename maps."""
    object_type = ObjectFileType(object_type)
    prefix = _CATEGORY_PATH_PREFIX.get(object_type, '')
    return sorted(f'{prefix}{name}' for name in _SLK_TAG_TO_FILENAME.get(object_type, {}).values())

# ObjectMetaData's own 'slk' column names which real Blizzard base file a field's default
# value lives in - but the tag name doesn't always match the real bundled filename exactly
# (DoodadData -> Doodads.slk), so this is an explicit map, not a guessed f'{tag}.slk'.
_SLK_TAG_TO_FILENAME = {
    ObjectFileType.Units: {
        'UnitData': 'UnitData.slk',
        'UnitUI': 'UnitUI.slk',
        'UnitWeapons': 'UnitWeapons.slk',
        'UnitBalance': 'UnitBalance.slk',
        'UnitAbilities': 'UnitAbilities.slk',
    },
    ObjectFileType.Items: {
        'ItemData': 'ItemData.slk',
    },
    ObjectFileType.Abilities: {
        'AbilityData': 'AbilityData.slk',
    },
    ObjectFileType.Upgrades: {
        'UpgradeData': 'UpgradeData.slk',
    },
    ObjectFileType.Destructables: {
        'DestructableData': 'DestructableData.slk',
    },
    ObjectFileType.Doodads: {
        'DoodadData': 'Doodads.slk',
    },
    ObjectFileType.Buffs: {},  # every AbilityBuffMetaData.slk field is 'Profile'-tagged
}

# The literal in-game path prefix these files are loaded from (case-insensitive in practice,
# written matching Blizzard's own casing). Confirmed against a real map using this technique.
_CATEGORY_PATH_PREFIX = {
    ObjectFileType.Units: 'Units\\',
    ObjectFileType.Items: 'Units\\',
    ObjectFileType.Abilities: 'Units\\',
    ObjectFileType.Buffs: 'Units\\',
    ObjectFileType.Upgrades: 'Units\\',
    ObjectFileType.Destructables: 'Units\\',
    ObjectFileType.Doodads: 'Doodads\\',
}


# Object types where a mod's value is stored per-level in the binary format (see
# ObjectTable._has_level_data in object_file.py) - only these get a level-suffixed column
# name (e.g. 'Cool1'/'Cool2') on the way out, so only these need suffix-stripping on the way
# back in. For every other type, a trailing digit is just part of the field's own literal
# name (e.g. Units' 'atkType1'/'atkType2' are two distinct, separately-coded fields, not one
# field repeated per level).
_LEVEL_INDEXED_TYPES = (ObjectFileType.Doodads, ObjectFileType.Abilities, ObjectFileType.Upgrades)


def _parse_specific_list(raw):
    text = str(raw).strip() if raw is not None else ''
    if not text:
        return None
    return {code.strip() for code in text.split(',') if code.strip()}


def _resolve_mod_id(candidates, obj_id, fields, code=None):
    """
    Several object types reuse the same display field name across many distinct mod-ids
    (e.g. Abilities' generic 'Data' name is shared by ~670 mod-ids, one family per specific
    ability). ObjectMetaData's own useSpecific/notSpecific columns are the real,
    metadata-declared discriminator for this - the same one World Editor and w2l's own Lua
    backend use - not a guess: useSpecific is a comma-list of the *only* object ids a mod-id
    applies to; notSpecific is the inverse (applies to everything *except* these).

    `code` is Abilities' own AbilityData.slk 'code' column (the underlying engine
    implementation id an alias/clone shares with other aliases of the same base ability,
    e.g. 'Asdg' "Self Destruct (Clockwerk Goblins)" has code 'Asds', the original "Self
    Destruct") - confirmed directly against real metadata that useSpecific/notSpecific lists
    are written against this *code*, not against every derived alias id, so a clone with no
    useSpecific entry under its own id can still resolve correctly by also checking its code.
    Absent for every other category (no base table outside Abilities has a 'code' column at
    all) - harmless to pass None there.

    Returns (mod_id, was_ambiguous) - was_ambiguous is True whenever more than one candidate
    remained after filtering, so callers can flag it rather than silently trust a guess.
    """
    if len(candidates) == 1:
        return candidates[0], False

    identities = {obj_id}
    if code:
        identities.add(code)

    narrowed = []
    for mod_id in candidates:
        row = fields[mod_id]
        specific = _parse_specific_list(row.get('useSpecific'))
        not_specific = _parse_specific_list(row.get('notSpecific'))
        if specific is not None and identities.isdisjoint(specific):
            continue
        if not_specific is not None and identities & not_specific:
            continue
        narrowed.append(mod_id)

    if len(narrowed) == 1:
        return narrowed[0], False
    pool = narrowed or candidates
    return sorted(pool)[0], True


_UNSET_MARKERS = ('', '-', '_', ' ')


def _match_weight(value):
    """
    A shared value is far stronger evidence of "same real template" when it's a distinctive
    string (e.g. an ability's real order-string value like 'cyclone'/'devour'/'metamorphosis'
    - a handful of real base abilities share any given one of these) than when it's a common
    numeric default or blank/unset marker most ids share by sheer sparseness. Weighting the
    former much more heavily fixes a real, observed failure mode: a custom object cloned
    from an ability whose distinguishing feature is a rare orderString-typed "Data" field was
    getting matched to an unrelated candidate that merely shared more common blank/numeric
    fields, diluting the one field that actually identified it - confirmed against a real map
    where this caused several orderString values ('cyclone', 'devour', 'metamorphosis',
    'dismount', 'cripple') to be dropped entirely (resolved to the wrong, numeric-typed
    sibling mod-id once the wrong template was picked).
    """
    if isinstance(value, str) and value.strip() not in _UNSET_MARKERS:
        return 5
    return 1


def _find_closest_template(row, object_type, base_data):
    """
    Best-effort template guess for a 'custom'-table id with no remainder hint at all (see
    rebuild_object_table's 'guessed_templates' report entry): picks the known base id whose
    own default row shares the most (column, value) pairs with the merged row in hand,
    weighted by how distinctive each matching value is (see _match_weight) rather than an
    arbitrary pick - cheap (well under a second per id even across every real Units base id)
    and meaningfully reduces how many *other* fields end up silently inheriting an unrelated
    template's defaults (e.g. picking a same-race unit instead of an arbitrary one). Still a
    guess, not a recovery of the true original template - reported as such regardless of how
    it was chosen.
    """
    best_id, best_score = None, -1
    for candidate_id in base_data.ids(object_type):
        candidate_row = base_data.get(object_type, candidate_id) or {}
        score = sum(_match_weight(value) for key, value in row.items()
                    if candidate_row.get(key) == value)
        if score > best_score:
            best_score, best_id = score, candidate_id
    return best_id


def _build_reverse_index(fields):
    index = {}
    for mod_id, row in fields.items():
        index.setdefault(field_display_name(row), []).append(mod_id)
    return index


def _strip_level_suffix(text, reverse_index):
    """
    Try progressively more trailing digits as the level suffix - starting from just the
    last one, the common case - until the remainder matches a known field name, rather than
    greedily stripping every trailing digit at once. Needed because a field's own name can
    already end in a digit (Upgrades' 'base1'/'base2'/'mod1'/'effect1', etc.) - greedily
    stripping both digits from a level-suffixed 'base11' (field 'base1', level 0) would
    wrongly look for a field named 'base', which doesn't exist. Returns (base_name, digits)
    - both None if no prefix of the trailing-digit run matches anything known.
    """
    i = len(text)
    while i > 0 and text[i - 1].isdigit():
        i -= 1
        candidate = text[:i]
        if candidate in reverse_index:
            return candidate, text[i:]
    return None, None


def _parse_column(column_name, reverse_index, obj_id, fields, level_indexed, code=None):
    """Returns (mod_id, level_or_None, was_ambiguous), or (None, None, False) if this
    column name doesn't match any known field at all (kept verbatim by the caller so
    nothing is silently dropped). `code` - see _resolve_mod_id - is Abilities-only."""
    if column_name.endswith(')') and ' (' in column_name:
        base_part, _, rest = column_name.rpartition(' (')
        mod_id = rest[:-1]
        if level_indexed:
            _, digits = _strip_level_suffix(base_part, reverse_index)
            if digits:
                return mod_id, int(digits) - 1, False
        return mod_id, None, False

    candidates = reverse_index.get(column_name)
    if candidates is not None:
        mod_id, ambiguous = _resolve_mod_id(candidates, obj_id, fields, code)
        return mod_id, None, ambiguous

    if level_indexed:
        base_name, digits = _strip_level_suffix(column_name, reverse_index)
        if digits:
            candidates = reverse_index.get(base_name)
            mod_id, ambiguous = _resolve_mod_id(candidates, obj_id, fields, code)
            return mod_id, int(digits) - 1, ambiguous

    return None, None, False


def build_override_tables(object_type, table_data, metadata=None, base_data=None):
    """
    Split a map's object-data overrides into (a) literal, game-loadable default-table
    overrides (Units\\UnitData.slk, Units\\UnitBalance.slk, ...) and (b) whatever genuinely
    can't move there - so the map's object files can end up as close to "just the base id /
    custom id" as the data actually allows, without silently dropping anything.

    This is the real protection/optimization technique some existing maps use: replacing
    war3map.w3u/w3t/w3h/w3q's per-object diffs with literal-path override tables the engine
    merges against its own built-in defaults, confirmed against a real reference map (see
    TODO.md) whose archive has no war3map.w3u/w3t/w3h/w3q at all - only Units\\UnitData.slk
    and friends, containing full merged rows including plainly non-Blizzard custom ids.

    Not every override field can move, though: ObjectMetaData's own 'slk' column marks a
    real chunk of fields (icons, models, tooltips, animation/attachment props, and a few
    genuinely computed ones like a building's 'Builds' list) as 'Profile' - meaning there is
    no real Blizzard base *value* table column for them at all, only a World-Editor-UI-side
    concept. Concretely: `umdl`/`file` (a unit's model) and `uico`/`iico` (icon art) are both
    'Profile'-tagged - dropping them, which is what a naive full SLK conversion does, is a
    very plausible explanation for maps converted this way coming out with corrupted/missing
    icons and models. This function keeps those fields instead of dropping them, returning
    them separately so they can still be written to a (much smaller) binary object file.

    Returns (tables, remainder):
      tables: {literal_path: {id: {column: value}}} - one entry per real base file this
        category actually needed, ready for SlkFile.write() at that literal path.
      remainder: {'original': {...}, 'custom': {...}} - the same shape ObjectFile.import_data()
        expects, containing only the fields that had to stay (Profile-tagged, or a mod-id with
        no metadata entry at all - unrecognized fields have no known target file either, so
        they stay rather than being silently discarded).

    A base-game ('original'-table) id only gets a row in `tables` for a given file if the map
    actually overrides something there - the engine's own built-in defaults cover everything
    else already. A brand-new ('custom'-table) id gets a full merged row in *every* file this
    category maps to, even where the map didn't override anything for that specific file -
    a wholly new id has no built-in default of its own to fall back to, so every relevant
    table needs at least the template's values carried over.
    """
    if table_data is None:
        return {}, {'original': {}, 'custom': {}}

    object_type = ObjectFileType(object_type)
    metadata = metadata or ObjectMetaData.default()
    base_data = base_data or BaseObjectData.default()
    fields = metadata.fields(object_type)
    tag_to_filename = _SLK_TAG_TO_FILENAME.get(object_type, {})
    path_prefix = _CATEGORY_PATH_PREFIX.get(object_type, '')
    all_target_paths = {f'{path_prefix}{name}' for name in tag_to_filename.values()}
    filename_by_target_path = {f'{path_prefix}{name}': name for name in tag_to_filename.values()}

    tables = {}
    remainder = {'original': {}, 'custom': {}}

    for table_name in ('original', 'custom'):
        is_custom = table_name == 'custom'
        for obj_id, mods in (table_data.get(table_name) or {}).items():
            base_id = mods.get('ID', obj_id)

            remainder_mods = {}
            # Two genuinely distinct real mod-ids can land on the exact same (target_path,
            # column) in ONE object's own row - not just a metadata-schema quirk, but a real,
            # observed case: a heavily-edited custom ability can carry real binary mods left
            # over from more than one template it was ever cloned from (e.g. a "leftover"
            # slot-A mod from its declared ID's family alongside the slot-A mod that's
            # actually driving its current behavior, copied in from a different, unrelated
            # ability entirely). Deciding which of them gets the bare column name and which
            # gets the "(mod_id)" suffix can't be done mod-by-mod as each is visited (that's
            # an accident of dict-iteration order, not a real "this one is primary" signal) -
            # it has to be known before writing any of them, so a first pass collects every
            # movable mod's plain (pre-disambiguation) column first.
            entries = []
            for mod_id, mod in mods.items():
                if mod_id == 'ID':
                    continue
                field_def = fields.get(mod_id)
                filename = tag_to_filename.get(field_def.get('slk')) if field_def else None
                if filename is None:
                    remainder_mods[mod_id] = mod
                    continue
                target_path = f'{path_prefix}{filename}'
                base_name = field_display_name(field_def)
                value = mod.get('value')
                if isinstance(value, dict):
                    for level in sorted(value.keys()):
                        entries.append((mod_id, target_path, f'{base_name}{level + 1}', value[level]))
                else:
                    entries.append((mod_id, target_path, base_name, value))

            collision_counts = {}
            for _, target_path, plain_col, _ in entries:
                key = (target_path, plain_col)
                collision_counts[key] = collision_counts.get(key, 0) + 1

            movable_by_target = {}
            for mod_id, target_path, plain_col, value in entries:
                bucket = movable_by_target.setdefault(target_path, {})
                # A genuine collision (>1 real mod-id landing on this exact column) is always
                # suffixed on EVERY one of its owners, not just the second-onward - so the
                # reverse side (rebuild_object_table) never has to guess which of several real
                # candidates a bare column name belongs to; it can parse every one of them
                # directly by mod-id instead.
                col = plain_col if collision_counts[(target_path, plain_col)] == 1 else \
                    f'{plain_col} ({mod_id})'
                bucket[col] = value

            target_paths = all_target_paths if is_custom else set(movable_by_target.keys())
            for target_path in target_paths:
                # Each literal file has its own fixed, narrow native column set (e.g.
                # UnitData.slk's own 31 columns) - use ONLY that file's own base row here,
                # never BaseObjectData.get()'s cross-file-merged view (which spans every base
                # file a category has, e.g. all 5 Units files combined). Writing another
                # file's columns into this one (UnitBalance's 'HP'/'STR' into UnitData.slk,
                # say) produces a file real WC3 tooling has never seen the shape of - a very
                # plausible crash cause in a real client, not just a cosmetic size bloat
                # (confirmed: this inflated a real map's Units\UnitData.slk output to 184
                # columns and ~25x a real reference map's own file size for the same table).
                file_base_row = base_data.get_file_row(object_type, filename_by_target_path[target_path], base_id) or {}
                row = dict(file_base_row)
                row.update(movable_by_target.get(target_path, {}))
                tables.setdefault(target_path, {})[obj_id] = row

            if remainder_mods:
                if is_custom:
                    remainder_mods['ID'] = base_id
                remainder[table_name][obj_id] = remainder_mods

    return tables, remainder


def _values_equal(a, b):
    if isinstance(a, float) and math.isnan(a):
        a = None
    if isinstance(b, float) and math.isnan(b):
        b = None
    if a is None or b is None:
        return a == b
    # A sparse SLK column can reread a real number as its string form (e.g. '1.0') - a known,
    # accepted SlkFile round-trip quirk (see the SLK-export TODO.md entry), not a real
    # difference in value.
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def rebuild_object_table(object_type, tables, remainder=None, metadata=None, base_data=None):
    """
    Reverse of build_override_tables(): given the literal-path override tables (and
    whatever remainder had to stay in a binary object file), reconstruct a normal
    ObjectFile-shaped {'original': {...}, 'custom': {...}} table, ready for
    ObjectFile.import_data()/write() - i.e. undoes the technique, so a map converted this way
    can be inspected/edited normally again.

    Returns (table_data, report). report has four lists, none of them silent:
      'ambiguous_fields': [(obj_id, mod_id), ...] - a shared display name (e.g. Abilities'
        generic 'Data', reused by ~670 mod-ids) couldn't be narrowed to exactly one mod-id
        via useSpecific/notSpecific; resolved to a deterministic choice, not a certainty.
      'guessed_templates': [obj_id, ...] - a 'custom'-table id had no remainder hint for its
        real template (the map never needed to keep any of its Profile-tagged fields -
        icon/model/tooltip - in binary form, so there was nothing left pointing back to it)
        and no reliable way exists to recover which base id it was really built from; an
        arbitrary known id was used as a stand-in, so this id's icon/model/tooltip may not
        match the true original.
      'uncertain_binary_types': [(path, column), ...] - a field's binary storage type
        (int/real/unreal/string) wasn't one of the ones this package has confirmed against
        real data (see _CONFIRMED_BINARY_TYPES); defaulted to string, which is right most of
        the time (the large majority of checked types are string-stored) but not certain.
      'unrecognized_columns': [(path, column), ...] - a column name matched no known field at
        all; left out of the reconstructed object data entirely rather than guessed at.
      'unparseable_values': [(path, column, value), ...] - a numeric-typed field's cell
        wasn't a real SLK "unset" marker ('-'/'_') but also wasn't parseable as a number
        (e.g. a bare spreadsheet error literal like '#VALUE!', seen in a real map's own
        bundled data) - dropped rather than guessed at, same reasoning as
        'unrecognized_columns'.
    """
    object_type = ObjectFileType(object_type)
    metadata = metadata or ObjectMetaData.default()
    base_data = base_data or BaseObjectData.default()
    fields = metadata.fields(object_type)
    reverse_index = _build_reverse_index(fields)
    level_indexed = object_type in _LEVEL_INDEXED_TYPES
    remainder = remainder or {'original': {}, 'custom': {}}

    result = {'original': {}, 'custom': {}}
    report = {'ambiguous_fields': [], 'guessed_templates': [], 'uncertain_binary_types': [],
              'unrecognized_columns': [], 'unparseable_values': []}

    all_ids = set()
    for rows in tables.values():
        all_ids.update(rows.keys())
    all_ids.update(remainder.get('original', {}).keys())
    all_ids.update(remainder.get('custom', {}).keys())

    for obj_id in all_ids:
        merged_row = {}
        for rows in tables.values():
            merged_row.update(rows.get(obj_id) or {})

        if obj_id in remainder.get('original', {}):
            table_name, base_id = 'original', obj_id
        elif obj_id in remainder.get('custom', {}):
            table_name = 'custom'
            base_id = remainder['custom'][obj_id].get('ID') or \
                _find_closest_template(merged_row, object_type, base_data)
        elif base_data.get(object_type, obj_id) is not None:
            table_name, base_id = 'original', obj_id
        else:
            table_name = 'custom'
            base_id = _find_closest_template(merged_row, object_type, base_data)
            report['guessed_templates'].append(obj_id)

        base_row = base_data.get(object_type, base_id) or {}
        # Abilities-only: AbilityData.slk's 'code' column names the underlying engine
        # implementation a clone/alias id shares with other aliases of the same base ability
        # (see _resolve_mod_id) - useSpecific/notSpecific lists are written against this code,
        # not against every derived alias, so it's passed through as a second identity to
        # check field ownership against.
        code = base_row.get('code')
        mods = {}

        for path, rows in tables.items():
            row = rows.get(obj_id)
            if row is None:
                continue
            for column, value in row.items():
                if column == 'id':
                    continue
                # Administrative/structural base-table columns (a native id column like
                # 'unitBalanceID', or authoring aids like 'sort'/'comment(s)') ride along in
                # every row via the base_row merge but were never a real overridable field -
                # ObjectMetaData has no mapping for them at all. Always skip these outright
                # (never even worth reporting as "unrecognized") rather than let a
                # guessed-template mismatch (see 'guessed_templates' below) make them look
                # like a spurious override with nothing to map it to.
                # A plain `column not in reverse_index` check is not enough here for a
                # level-indexed category: reverse_index only holds the *unsuffixed* field
                # name ('targs'), while a level-suffixed column ('targs2') is exactly what
                # a real column looks like both in override tables *and*, just as often, as
                # a genuinely native column in Blizzard's own base table (its own per-level
                # columns use the identical naming convention) - so a real, resolvable
                # override field was being misclassified as unrecoverable baggage and
                # silently dropped. Recognize it via level-suffix-stripping too before
                # concluding there's really no field behind this column at all.
                recognized = column in reverse_index or (
                    level_indexed and _strip_level_suffix(column, reverse_index)[0] is not None)
                if column in base_row and not recognized:
                    continue
                # A genuinely blank SLK cell (pandas NaN, or a plain missing key reread as
                # None - both real, observed shapes for an always-empty column in a real
                # map's own bundled table - as opposed to the '-'/'_' string markers handled
                # below) means "this table doesn't specify a value here" - not "override
                # with NaN/None" - regardless of what the base row itself has for this
                # column (a real map's own bundled override table can legitimately leave a
                # field blank for one specific object while this package's *own* bundled
                # base defaults have a real value there; the blank always means "no
                # override", it's never something to diff against or declare).
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue
                # A column with no known field mapping whose value is simply this object's
                # own id (e.g. a differently-named id column like 'unitWeapID' vs this
                # package's own bundled 'unitWeaponID' - real map files aren't guaranteed to
                # use the exact same column name for this across every SLK-tool version) is
                # the same kind of harmless administrative baggage as the base_row check
                # above, just not catchable by name - catchable by value instead.
                if column not in reverse_index and value == obj_id:
                    continue
                # A disambiguated "name (mod_id)" column never exists as a literal key in the
                # real base table (that suffix is only ever introduced by _column_name on the
                # way out) - there's no reliable base value to diff against, so always keep it
                # rather than risk wrongly treating it as unchanged.
                is_disambiguated = column.endswith(')') and ' (' in column
                if not is_disambiguated and _values_equal(value, base_row.get(column)):
                    continue

                mod_id, level, ambiguous = _parse_column(column, reverse_index, base_id, fields, level_indexed, code)
                if mod_id is None:
                    report['unrecognized_columns'].append((path, column))
                    continue
                if ambiguous:
                    report['ambiguous_fields'].append((obj_id, mod_id))

                field_def = fields[mod_id]
                # The binary format writes MOD_TYPE once per mod-id, not once per level, so
                # every level must agree - but which type is *right* can only be known by
                # looking at every level's value, and they arrive one at a time here. Always
                # recompute fresh per level (so a level whose value has a real fractional
                # part is never missed), then reconcile against whatever an earlier level
                # for this same mod-id already decided - widening INT to REAL/UNREAL when a
                # later level proves INT wrong (an int-valued earlier level stays perfectly
                # valid written as REAL/UNREAL too, since BinaryFile.write() already
                # coerces those via float()), and otherwise keeping the earlier decision
                # rather than letting a later, less-certain guess override a settled one.
                fresh_type, confirmed = _binary_type_for(object_type, mod_id, field_def.get('type'), value)
                if mod_id in mods:
                    existing_type = mods[mod_id]['type']
                    if existing_type == BinaryDataType.INT.value and fresh_type in (
                            BinaryDataType.REAL.value, BinaryDataType.UNREAL.value):
                        mod_type = fresh_type
                        mods[mod_id]['type'] = mod_type
                    else:
                        mod_type = existing_type
                else:
                    mod_type = fresh_type
                    if not confirmed:
                        report['uncertain_binary_types'].append((path, column))
                # A confirmed-numeric type can still arrive as a string (a sparse SLK
                # column rereading a real number as e.g. '1.0' - the same accepted SlkFile
                # round-trip quirk noted elsewhere) - coerce so BinaryFile.write() (which
                # only auto-coerces REAL/UNREAL via float(), not INT or STRING) never gets a
                # value of the wrong Python type for the type code being written.
                if mod_type in (BinaryDataType.INT.value, BinaryDataType.REAL.value,
                                BinaryDataType.UNREAL.value) and isinstance(value, str):
                    # A blank/whitespace-only cell, or '-'/'_', are real SLK conventions for
                    # "unset" on an otherwise-numeric column (a bare space is literally
                    # SYLK's own blank-cell padding character; '-'/'_' seen directly in real
                    # AbilityData.slk data) - nothing to override with, so drop quietly, not
                    # worth reporting as a concern.
                    if value.strip() in ('', '-', '_'):
                        continue
                    # Anything else that still won't parse (e.g. a bare spreadsheet error
                    # literal like '#VALUE!', seen once in a real protected map's own
                    # bundled UnitAbilities.slk) is different: not a known "unset" idiom,
                    # so worth flagging - there's no way to know what a genuinely garbage
                    # numeric cell "means" as an override, so it's dropped, not guessed at.
                    try:
                        value = int(float(value)) if mod_type == BinaryDataType.INT.value else float(value)
                    except ValueError:
                        report['unparseable_values'].append((path, column, value))
                        continue
                elif mod_type == BinaryDataType.INT.value and isinstance(value, float):
                    # A whole-number-valued float (e.g. 250.0) reaching here as a genuine
                    # Python float, not a string, is the same pandas dtype-inference
                    # artifact as the string case above wears a different hat: a sparse or
                    # NaN-mixed SLK column gets float64 dtype even for otherwise-integer
                    # data. Safe to truncate - there's no fractional part by construction
                    # (a real fractional value would have already forced mod_type to
                    # REAL/UNREAL above, before this branch is ever reached).
                    value = int(value)
                elif mod_type == BinaryDataType.STRING.value and not isinstance(value, str):
                    value = str(value)

                entry = mods.setdefault(mod_id, {'value': None, 'type': mod_type})
                if level is None:
                    entry['value'] = value
                else:
                    if not isinstance(entry['value'], dict):
                        entry['value'] = {}
                        entry['ptr'] = {}
                    entry['value'][level] = value
                    # MOD_DATA_PTR - a per-level field ObjectTable.write() requires for
                    # every level-indexed mod (indexes directly into self._data[idx][mod]
                    # ['ptr'][level] with no fallback of its own) but whose purpose is
                    # undocumented (see e.g. w3g's own reserved-field entries for the same
                    # "undocumented, so write zero rather than guess" convention already
                    # used elsewhere in this package) - there is nothing in a literal-path
                    # override table to recover a real value from, so it's supplied here.
                    entry['ptr'][level] = 0

        for mod_id, mod in remainder.get(table_name, {}).get(obj_id, {}).items():
            if mod_id != 'ID':
                mods[mod_id] = mod

        if table_name == 'custom':
            mods['ID'] = base_id

        if mods:
            result[table_name][obj_id] = mods

    return result, report
