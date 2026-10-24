import os
from .cfg_file import CfgFile

_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata')
_DEFAULT_FILES = ('WorldEditStrings.txt', 'WorldEditGameStrings.txt')


class WorldEditStrings(object):
     """
     Resolves 'WESTRING_XXX' text keys - the kind of thing that shows up as a field's
     displayName in ObjectMetaData, or as a category/display name in TriggerData.txt - to the
     actual UI text the World Editor shows for them.

     Sourced from the same real Reforged install as wc3o/metadata/*.slk: WorldEditStrings.txt
     (UI text) and WorldEditGameStrings.txt (the subset also needed at runtime by the game
     itself, kept in a separate file upstream to save memory - both use the same
     '[WorldEditStrings]' key namespace, so they're merged here into one lookup).
     """

     def __init__(self):
          self._data = {}

     def read(self, *paths):
          self._data = {}
          for path in paths:
               cfg = CfgFile()
               cfg.read(path)
               self._data.update(cfg.export_data().get('WorldEditStrings', {}))

     def resolve(self, key):
          # Passes through anything that isn't a known key (including non-strings), so it's
          # safe to call on any field value regardless of whether it's really a WESTRING key.
          if not isinstance(key, str):
               return key
          return self._data.get(key, key)

     def __len__(self):
          return len(self._data)

     @classmethod
     def default(cls):
          if cls._default_instance is None:
               instance = cls()
               instance.read(*(os.path.join(_METADATA_DIR, name) for name in _DEFAULT_FILES))
               cls._default_instance = instance
          return cls._default_instance

     _default_instance = None
