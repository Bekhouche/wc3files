import os

# Function-type identifiers as stored in war3map.wtg (TriggerFunctionType in War3Net).
EVENT = 0
CONDITION = 1
ACTION = 2
CALL = 3

_SECTION_BY_FUNCTION_TYPE = {
     EVENT: 'TriggerEvents',
     CONDITION: 'TriggerConditions',
     ACTION: 'TriggerActions',
     CALL: 'TriggerCalls',
}

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata', 'TriggerData.txt')


class TriggerData(object):
     """
     Parses a WC3 TriggerData.txt just enough to recover, for every GUI trigger
     event/condition/action/script-call, how many parameters it takes - the one piece of
     information war3map.wtg's binary format itself omits (a function's parameter count is
     looked up by name, not stored inline; see MapTriggersFile's docstring).

     Only the four function sections are parsed - TriggerCategories/TriggerTypes/TriggerParams/
     etc. exist purely for the World Editor's own UI (category icons, dropdown display names)
     and aren't needed to read or write the binary format.
     """

     SECTIONS = ('TriggerEvents', 'TriggerConditions', 'TriggerActions', 'TriggerCalls')

     def __init__(self):
          self.clear()

     def clear(self):
          self._arg_counts = {section: {} for section in self.SECTIONS}
          # The declared type of each argument, kept because a preset's script
          # value is not unique on its own: 'false' is the code for
          # ShowHideHide, DoorCloseClose and others, and only the parameter's
          # type says which one a call means.
          self._arg_types = {section: {} for section in self.SECTIONS}
          # What each TriggerCalls function returns. The World Editor needs
          # this to decide which calls may fill a given parameter slot; it is
          # kept here because it is also what lets a compiled comparison be
          # named again - see comparison_for_type().
          self._return_types = {}
          # Built on demand from _arg_types; reset here so a second read()
          # cannot serve a table derived from the previous file.
          self._comparison_by_type = {}
          # GUI name -> the name the World Editor actually emits, for the
          # functions where the two differ (`_X_ScriptName=Y` in the file).
          self._script_names = {}
          self._gui_names = {}

     def read(self, path_or_text):
          if isinstance(path_or_text, str) and '\n' not in path_or_text and os.path.exists(path_or_text):
               with open(path_or_text, encoding='utf-8-sig') as f:
                    text = f.read()
          else:
               text = path_or_text

          self.clear()
          section = None
          for line in text.splitlines():
               stripped = line.strip()
               if not stripped or stripped.startswith('//'):
                    continue
               if stripped.startswith('[') and stripped.endswith(']'):
                    section = stripped[1:-1]
                    continue
               if section not in self._arg_counts or '=' not in line:
                    continue

               key, _, rest = line.partition('=')
               if key.startswith('_'):
                    # Mostly display names and defaults for the editor's own
                    # UI, which are not needed here - but `_X_ScriptName=Y`
                    # is, because it is the only record of the cases where
                    # the name the editor emits into the script differs from
                    # the name it stores in the .wtg. A compiled
                    # `SetPlayerTechMaxAllowedSwap` call is the GUI's
                    # `SetPlayerUnitMaxAllowed`, and without this there is
                    # nothing to connect the two.
                    if key.endswith('_ScriptName'):
                         gui_name = key[1:-len('_ScriptName')]
                         script_name = rest.strip()
                         if gui_name and script_name:
                              self._script_names[gui_name] = script_name
                              # Several GUI functions can share one script
                              # name (ForGroup and ForGroupMultiple are both
                              # ForGroupBJ), so the reverse is a list and the
                              # caller decides, rather than one silently
                              # winning.
                              self._gui_names.setdefault(
                                   script_name, []).append(gui_name)
                    continue

               values = rest.split(',')
               if section == 'TriggerCalls':
                    arg_types = values[3:]
                    if len(values) > 2:
                         self._return_types[key] = values[2].strip()
               else:
                    arg_types = values[1:]
               if arg_types == ['nothing']:
                    self._arg_counts[section][key] = 0
                    self._arg_types[section][key] = []
               else:
                    self._arg_counts[section][key] = len(arg_types)
                    self._arg_types[section][key] = [t.strip() for t in arg_types]

     def arg_types(self, function_type, name):
          """The declared type of each argument, or [] if the function is unknown."""
          section = _SECTION_BY_FUNCTION_TYPE[function_type]
          return self._arg_types.get(section, {}).get(name, [])

     def script_name(self, gui_name):
          """The name the editor emits for a GUI function (itself if unchanged)."""
          return self._script_names.get(gui_name, gui_name)

     def gui_names(self, script_name):
          """Every GUI function the editor compiles to `script_name`.

          A list, not a single name: `ForGroupBJ` is what both `ForGroup` and
          `ForGroupMultiple` compile to, and which one it was depends on the
          call, not on the table.
          """
          return list(self._gui_names.get(script_name, ()))

     def return_type(self, name):
          """The GUI type a TriggerCalls function returns, or '' if unknown."""
          return self._return_types.get(name, '')

     def comparison_for_type(self, gui_type):
          """The `OperatorCompare*` condition that compares `gui_type`.

          Derived from the file rather than hardcoded: every comparison is
          declared as `OperatorCompareX=_,<type>,<operator>,<type>`, so the
          type its first argument takes is exactly the type it compares.
          `GetSpellAbilityId` returns `abilcode` and `OperatorCompareAbilityId`
          compares `abilcode`, which is what lets a compiled comparison be
          named again instead of being written off as raw script.
          """
          if not getattr(self, '_comparison_by_type', None):
               table = {}
               for name, types in self._arg_types.get('TriggerConditions', {}).items():
                    if name.startswith('OperatorCompare') and types:
                         table.setdefault(types[0].strip(), name)
               self._comparison_by_type = table
          return self._comparison_by_type.get(gui_type, '')

     def arg_count(self, function_type, name):
          section = _SECTION_BY_FUNCTION_TYPE[function_type]
          try:
               return self._arg_counts[section][name]
          except KeyError:
               # How many arguments a GUI function takes is only knowable from
               # a TriggerData.txt, so an unknown one cannot be skipped - the
               # parameters that follow it would be read as something else and
               # the rest of the trigger would come out wrong. Refusing is
               # right; a bare KeyError saying nothing useful was not.
               #
               # This is usually not a stale bundled file. Maps built with an
               # extended editor (WEX and the like) expose natives that stock
               # Warcraft III does not: BlzLoadTOCFile, GetHandleId and
               # BlzFrameSetText are all absent from the game's own
               # UI/TriggerData.txt, checked directly against an installed
               # copy. Such a map needs the TriggerData.txt its editor used.
               raise KeyError(
                    f"Unknown {section[7:].lower()} function: {name!r}. The "
                    f"bundled TriggerData.txt does not declare it, and nor "
                    f"does stock Warcraft III's - a map using an extended "
                    f"editor's functions needs that editor's TriggerData.txt, "
                    f"passed to read()/TriggerData, because the argument count "
                    f"cannot be guessed."
               )

     @classmethod
     def default(cls):
          """The TriggerData.txt bundled with this package (sourced from War3Net.Build.Core,
          github.com/Drake53/War3Net, MIT licensed - itself derived from Blizzard's own
          UI/TriggerData.txt). Covers a broad, modern function set; pass a game-version-specific
          TriggerData.txt to read() instead if a map uses functions this default doesn't have."""
          if cls._default_instance is None:
               instance = cls()
               instance.read(_DEFAULT_PATH)
               cls._default_instance = instance
          return cls._default_instance

     _default_instance = None
