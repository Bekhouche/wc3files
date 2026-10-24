import os

from .cfg_file import CfgFile
from ..table.slk_file import SlkFile


class UnitsFile(object):
     """
     Load a map's Units\\ override folder.

     Footies-style maps often store custom unit/item/ability definitions here
     (SLK tables + Func/Strings profile .txt) instead of war3map.w3u/w3t.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self.slk = {}
          self.profile = {}
          self.path = ''

     def read(self, path):
          """Read a Units directory (or a parent map folder containing Units/)."""
          self.clear()
          units_dir = path
          if os.path.isdir(os.path.join(path, 'Units')):
               units_dir = os.path.join(path, 'Units')
          if not os.path.isdir(units_dir):
               raise ValueError(f'Units directory not found: {path}')

          self.path = units_dir
          for name in sorted(os.listdir(units_dir)):
               file_path = os.path.join(units_dir, name)
               if not os.path.isfile(file_path):
                    continue
               lowered = name.lower()
               if lowered.endswith('.slk'):
                    slk = SlkFile()
                    slk.read(file_path)
                    self.slk[name] = slk.export_data()
               elif lowered.endswith('.txt'):
                    cfg = CfgFile()
                    cfg.read(file_path)
                    # Later files override earlier keys for the same object id.
                    for obj_id, fields in cfg.export_data().items():
                         self.profile.setdefault(obj_id, {}).update(fields)

          return self

     def export_data(self):
          return {'slk': self.slk, 'profile': self.profile}

     def asset_paths(self):
          """Collect model/icon/sound paths referenced by profile fields and SLK cells."""
          from wc3files.w3x.w3x_file import W3xFile

          paths = set()
          for fields in self.profile.values():
               for value in fields.values():
                    if isinstance(value, str):
                         paths.update(W3xFile._extract_manifest_asset_paths(value))
          for table in self.slk.values():
               for row in table.values():
                    for value in row.values():
                         if isinstance(value, str):
                              paths.update(W3xFile._extract_manifest_asset_paths(value))
          return paths
