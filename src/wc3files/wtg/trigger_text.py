"""Rendering GUI triggers the way the World Editor shows them.

A `war3map.wtg` stores a trigger as a function name plus a flat list of
parameters. The World Editor turns that into a sentence - "Add 1000
experience to (Triggering unit), Hide level-up graphics" - using a layout
that ships with the game.

TODO.md long listed this as "TriggerStrings.txt" and deferred it. That name
turns out to be wrong, which is worth stating because it changes where to
look: in a current install `TriggerStrings.txt` holds only *hints* (the
tooltip text the editor shows beneath a function). The layouts live in
`TriggerData.txt`:

    AddHeroXPSwapped=0,integer,unit,showhideoption
    _AddHeroXPSwapped_DisplayName="Add Experience"
    _AddHeroXPSwapped_Parameters="Add ",~Quantity," experience to ",~Hero,", ",~Show/Hide," level-up graphics"
    _AddHeroXPSwapped_Category=TC_HERO

A layout is a comma-separated list where a quoted value is literal text and a
`~`-prefixed value is a placeholder, filled from the function's parameters in
order. Commas inside display text are why the values are quoted.

Parameters nest: a parameter can itself be a function call, so rendering
recurses, and a preset or variable resolves through its own tables. What
cannot be resolved is rendered as the raw value rather than dropped, so the
output is always a faithful - if sometimes terse - account of the trigger.
"""
import os
import re

from .trigger_data import ACTION, CALL, CONDITION, EVENT

_GAMEDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_TRIGGER_DATA_PATH = os.path.join(_GAMEDATA, 'TriggerData.txt')
_TRIGGER_STRINGS_PATH = os.path.join(_GAMEDATA, 'TriggerStrings.txt')

# Parameter kinds as stored in war3map.wtg.
PRESET = 0
VARIABLE = 1
FUNCTION = 2
STRING = 3

# Splits a layout on commas that are not inside double quotes.
_LAYOUT_SPLIT = re.compile(r',(?=(?:[^"]*"[^"]*")*[^"]*$)')


def _unquote(value):
    value = value.strip()
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


class TriggerText(object):
    """Layouts, display names and hints for GUI trigger functions."""

    def __init__(self):
        self.layouts = {}        # function name -> list of layout pieces
        self.display_names = {}  # function name -> short name
        self.categories = {}     # function name -> category key
        self.hints = {}          # function name -> tooltip text
        self._presets = {}       # preset key -> display text
        self.preset_code = {}    # preset key -> the JASS value it compiles to
        self.preset_type = {}    # preset key -> the variable type it belongs to
        self._world_edit = None

    # -- loading ----------------------------------------------------------

    def read_trigger_data(self, path_or_text=None):
        text = self._text_of(path_or_text, _TRIGGER_DATA_PATH)
        section = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('//'):
                continue
            if stripped.startswith('[') and stripped.endswith(']'):
                section = stripped[1:-1]
                continue
            if '=' not in stripped:
                continue
            key, _, rest = stripped.partition('=')

            if section == 'TriggerParams':
                # key = version, type, code-text, display-text. The display is
                # the FOURTH field; the third is the script code, so reading
                # index 2 rendered 'ShowHideHide' as "false" - the JASS value -
                # instead of "Hide". The display is usually a WESTRING key.
                values = _LAYOUT_SPLIT.split(rest)
                if len(values) >= 3:
                    # Index 2 is the script value. Kept so the mapping can be
                    # read backwards, turning JASS back into a preset.
                    self.preset_code[key] = _unquote(values[2])
                    self.preset_type[key] = _unquote(values[1])
                if len(values) >= 4:
                    self._presets[key] = _unquote(values[3])
                elif len(values) >= 3:
                    self._presets[key] = _unquote(values[2])
                continue

            if not key.startswith('_'):
                continue
            for suffix, target in (('_Parameters', self.layouts),
                                   ('_DisplayName', self.display_names),
                                   ('_Category', self.categories)):
                if key.endswith(suffix):
                    name = key[1:-len(suffix)]
                    if suffix == '_Parameters':
                        target[name] = [_unquote(piece)
                                        for piece in _LAYOUT_SPLIT.split(rest)]
                    else:
                        target[name] = _unquote(rest)
                    break
        return self

    def read_trigger_strings(self, path_or_text=None):
        """Load the tooltip hints. Optional - rendering does not need them."""
        text = self._text_of(path_or_text, _TRIGGER_STRINGS_PATH)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('//') or '=' not in stripped:
                continue
            key, _, rest = stripped.partition('=')
            if key.endswith('Hint'):
                value = _unquote(rest)
                if value:
                    self.hints[key[:-len('Hint')]] = value
        return self

    @staticmethod
    def _text_of(path_or_text, default_path):
        if path_or_text is None:
            path_or_text = default_path
        if isinstance(path_or_text, (bytes, bytearray)):
            return bytes(path_or_text).decode('utf-8-sig', errors='replace')
        if '\n' not in path_or_text and os.path.exists(path_or_text):
            with open(path_or_text, encoding='utf-8-sig', errors='replace') as handle:
                return handle.read()
        return path_or_text

    # -- rendering --------------------------------------------------------

    def describe(self, function, depth=0):
        """Render one trigger function as the editor would show it."""
        if function is None:
            return ''
        name = function.get('name') or ''
        layout = self.layouts.get(name)
        parameters = function.get('parameters') or []

        if not layout:
            # No layout for this function: show the call plainly rather than
            # inventing a sentence for it.
            rendered = ', '.join(self.describe_parameter(p, depth + 1)
                                 for p in parameters)
            return f'{name}({rendered})' if parameters else name

        out = []
        index = 0
        for piece in layout:
            if piece.startswith('~'):
                if index < len(parameters):
                    out.append(self.describe_parameter(parameters[index], depth + 1))
                else:
                    # The layout expects a parameter the trigger does not
                    # carry; show the placeholder rather than silently
                    # producing a sentence with a hole in it.
                    out.append(piece)
                index += 1
            else:
                out.append(piece)

        # Any parameters the layout did not consume still belong in the output.
        for leftover in parameters[index:]:
            out.append(' ' + self.describe_parameter(leftover, depth + 1))
        return ''.join(out)

    def describe_parameter(self, parameter, depth=0):
        if parameter is None or depth > 12:
            return ''
        kind = parameter.get('type')
        value = parameter.get('value', '')

        if kind == FUNCTION or parameter.get('function'):
            inner = parameter.get('function')
            if inner:
                return '(' + self.describe(inner, depth + 1) + ')'
        if kind == PRESET:
            return self._resolve(self._presets.get(value, value))
        if kind == VARIABLE:
            text = str(value)
            indexer = parameter.get('array_indexer')
            if indexer:
                return f'{text}[{self.describe_parameter(indexer, depth + 1)}]'
            return text
        return str(value)

    def display_name(self, function_name, default=None):
        return self._resolve(
            self.display_names.get(function_name, default or function_name))

    def _resolve(self, value):
        """Turn a WESTRING key into the text the editor shows.

        Display text in TriggerData.txt is mostly WESTRING keys, which the
        World Editor strings resolve. A key with no entry is returned as-is
        rather than blanked, so nothing silently disappears.
        """
        if not isinstance(value, str) or not value.startswith('WESTRING_'):
            return value
        if self._world_edit is None:
            from ..w3o.world_edit_strings import WorldEditStrings

            self._world_edit = WorldEditStrings.default()
        resolved = self._world_edit.resolve(value)
        return resolved if resolved else value

    def hint(self, function_name, default=None):
        return self.hints.get(function_name, default)

    # -- construction -----------------------------------------------------

    @classmethod
    def default(cls):
        if cls._default_instance is None:
            instance = cls()
            instance.read_trigger_data()
            if os.path.exists(_TRIGGER_STRINGS_PATH):
                instance.read_trigger_strings()
            cls._default_instance = instance
        return cls._default_instance

    _default_instance = None


_TYPE_LABEL = {EVENT: 'Events', CONDITION: 'Conditions',
               ACTION: 'Actions', CALL: 'Calls'}


def describe_trigger(trigger, text=None, indent='    '):
    """Render a whole trigger - its events, conditions and actions - as lines.

    `trigger` is one entry from `MapTriggersFile.get_data()['triggers']`.
    """
    text = text or TriggerText.default()
    lines = []
    name = trigger.get('name') or '(unnamed trigger)'
    lines.append(name)
    if trigger.get('description'):
        lines.append(f'{indent}// {trigger["description"]}')

    grouped = {EVENT: [], CONDITION: [], ACTION: []}
    for function in trigger.get('functions') or ():
        grouped.setdefault(function.get('type'), []).append(function)

    for function_type in (EVENT, CONDITION, ACTION):
        functions = grouped.get(function_type) or []
        if not functions:
            continue
        lines.append(f'{indent}{_TYPE_LABEL[function_type]}:')
        for function in functions:
            lines.append(f'{indent * 2}{text.describe(function)}')
    return lines
