import math

from .data import ObjectFileType
from .object_metadata import ObjectMetaData
from .object_editor_enums import ObjectEditorEnums

# Metadata's own 'type' column names ~90 distinct semantic types (armorType, unitRace,
# attackType, ...) beyond plain numbers/strings/bools. Most of those are free-form
# references (a unit/ability/item rawcode, a file path, a list) with no fixed value set to
# check against - but ~35 of them (see UnitEditorData.txt / ObjectEditorEnums) are genuine
# fixed enums with a real, Blizzard-defined list of legal codes, checked below. Only these
# three are reliably "this is a plain number" for the numeric checks.
_NUMERIC_TYPES = {'int', 'real', 'unreal'}


def _parse_num(raw):
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == '':
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def validate_field_value(field_def, value, enums=None):
    """
    Check one (metadata field definition, actual value) pair against the constraints
    ObjectMetaData and UnitEditorData.txt reliably carry: numeric range (minVal/maxVal),
    sign (forceNonNeg), emptiness (canBeEmpty), a plain-numeric-vs-string mismatch for
    metadata's own 'int'/'real'/'unreal'/'bool' types, and - for the ~35 types that are a
    genuine fixed enum (unitRace, armorType, attackType, ...) - membership in that enum's
    real value list.

    Deliberately does NOT validate the ~55 other semantic types this metadata declares
    beyond that (free-form rawcode references, file paths, list types) - there's no fixed
    value set to check those against.

    Returns a list of {'severity': 'issue'|'note', 'message': str} dicts (empty if nothing
    to flag). 'note' is used for the one well-known case where the metadata's own declared
    constraint is routinely, intentionally violated by real maps and usually has no real
    effect: a value below a *declared minVal of exactly 0* - a common way to build
    deliberate below-baseline effects (e.g. negative HP regen for a curse, negative armor
    for "always takes bonus damage") by editing the object data directly rather than through
    the World Editor's own UI (which enforces minVal as a slider floor). Every other
    violation - including enum-value mismatches, which usually do have a real effect (WC3
    silently falls back to a default/undefined behavior for a code it doesn't recognize) -
    is 'issue'.
    """
    issues = []
    ftype = field_def.get('type') or ''

    if _is_blank(value):
        # canBeEmpty is about leaving a World Editor UI field blank (e.g. a tooltip) - for
        # any *List type (unitList, abilityList, techList, ...) an empty string is simply
        # how "no items" is represented, a completely normal and common customization (e.g.
        # clearing a unit's build/ability list), not a sign of missing/corrupt data. Checked
        # empirically against a real map: skipping *List fields here cut false positives from
        # 164/178 flagged fields down to the ones actually worth a second look.
        if not field_def.get('canBeEmpty') and 'list' not in ftype.lower():
            issues.append({'severity': 'issue', 'message': 'value is empty but canBeEmpty is false'})
        return issues

    if (ftype in _NUMERIC_TYPES or ftype == 'bool') and isinstance(value, str):
        issues.append({'severity': 'issue',
                        'message': f"metadata declares {ftype!r} but value is a string: {value!r}"})
        return issues

    if ftype in _NUMERIC_TYPES and isinstance(value, (int, float)) and not isinstance(value, bool):
        minv = _parse_num(field_def.get('minVal'))
        maxv = _parse_num(field_def.get('maxVal'))
        if minv is not None and value < minv:
            severity = 'note' if minv == 0 else 'issue'
            issues.append({'severity': severity, 'message': f'{value} is below minVal {minv}'})
        if maxv is not None and value > maxv:
            issues.append({'severity': 'issue', 'message': f'{value} is above maxVal {maxv}'})
        if field_def.get('forceNonNeg') and value < 0:
            issues.append({'severity': 'note', 'message': f'{value} is negative but forceNonNeg is set'})

    elif isinstance(value, str):
        enums = enums or ObjectEditorEnums.default()
        valid_codes = enums.valid_codes(ftype)
        if valid_codes is not None:
            case_sensitive = bool(field_def.get('caseSens'))
            check_codes = valid_codes if case_sensitive else {c.lower() for c in valid_codes}

            # Several of these enum types (targetList, unitClass, ...) are comma-joined
            # multi-select flag sets (e.g. 'ground,structure,air' or 'mechanical,townhall'),
            # not one single value - and "contains 'List' in its type name" turned out not
            # to reliably predict which ones (unitClass doesn't, but is multi-select too).
            # Splitting on ',' unconditionally is safe either way: a real enum code is never
            # itself a comma-containing string, so a single-valued field just becomes a
            # one-token list and checks identically to before.
            tokens = value.split(',')
            bad_tokens = [
                token for token in tokens
                if (token if case_sensitive else token.lower()) not in check_codes
            ]
            if bad_tokens:
                issues.append({
                    'severity': 'issue',
                    'message': f"{bad_tokens!r} not recognized {ftype} value(s) in {value!r} "
                                f"(expected one of {sorted(valid_codes)})",
                })

    return issues


def validate_object_table(object_type, table_data, metadata=None, enums=None):
    """
    Validate one object category's parsed data (one entry of ObjectFile.export_data(), e.g.
    export_data()['w3u']) against the bundled Blizzard metadata schema and enum value lists.

    Report mode only - never raises. Returns a flat list of {'table', 'id', 'mod_id', 'field',
    'level', 'value', 'issues'} dicts, one per (entry, field[, level]) pair that has at least
    one issue - 'issues' is itself a list of {'severity', 'message'} dicts (see
    validate_field_value for what 'note' vs 'issue' means). Fields with no matching metadata
    entry (unrecognized/non-standard - exactly what a protected or hand-edited map's data
    would have) are skipped, not flagged - there's nothing to validate them against.
    """
    if table_data is None:
        return []

    metadata = metadata or ObjectMetaData.default()
    enums = enums or ObjectEditorEnums.default()
    fields = metadata.fields(ObjectFileType(object_type))

    reports = []
    for table_name in ('original', 'custom'):
        for obj_id, mods in (table_data.get(table_name) or {}).items():
            for mod_id, mod in mods.items():
                if mod_id == 'ID':
                    continue
                field_def = fields.get(mod_id)
                if field_def is None:
                    continue

                value = mod.get('value')
                if isinstance(value, dict):
                    for level, level_value in value.items():
                        issues = validate_field_value(field_def, level_value, enums=enums)
                        if issues:
                            reports.append({
                                'table': table_name, 'id': obj_id, 'mod_id': mod_id,
                                'field': field_def.get('field', mod_id), 'level': level,
                                'value': level_value, 'issues': issues,
                            })
                else:
                    issues = validate_field_value(field_def, value, enums=enums)
                    if issues:
                        reports.append({
                            'table': table_name, 'id': obj_id, 'mod_id': mod_id,
                            'field': field_def.get('field', mod_id), 'level': None,
                            'value': value, 'issues': issues,
                        })
    return reports
