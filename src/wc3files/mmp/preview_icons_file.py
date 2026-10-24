from .data import PreviewIconFileType
from .preview_icon import PreviewIcon
from ..data import BinaryDataType
from wc3files.files import BinaryFile
from typing import Dict


class PreviewIconsFile:
     """
     Reader/writer for war3map.mmp (minimap preview-icon overlays).

     No magic number - starts directly with version(int32, always 0 in every real
     fixture checked) then count(int32), then `count` fixed 16-byte records. Verified
     against every real war3map.mmp fixture available locally: header's declared
     count always matches (file_size - 8) / 16 exactly, and player-start icons (type
     2) carry real, varied WC3 player colors while gold-mine/neutral-building icons
     are always opaque white - matching PreviewIcon's own docstring.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data: Dict[int, PreviewIcon] = {}
          self.version: int = 0
          self.number: int = 0

     def read(self, file_path: str):
          file_type = file_path.split('.')[-1].lower()
          if file_type not in [item.value for item in PreviewIconFileType]:
               raise ValueError(f"Invalid preview icon file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               self.version = reader.read(BinaryDataType.INT)
               self.number = reader.read(BinaryDataType.INT)
               self._data.clear()

               for idx in range(self.number):
                    fields = reader.read_batch(PreviewIcon.structure())
                    self._data[idx] = PreviewIcon(**fields)

     def write(self, file_path: str):
          file_type = file_path.split('.')[-1].lower()
          if file_type not in [item.value for item in PreviewIconFileType]:
               raise ValueError(f"Invalid preview icon file type: {file_type}")

          with BinaryFile(file_path, 'write') as writer:
               writer.write(BinaryDataType.INT, self.version)
               writer.write(BinaryDataType.INT, len(self._data))

               for icon in self._data.values():
                    writer.write_batch(icon.get_data())

     def get_icons(self) -> Dict[int, PreviewIcon]:
          return self._data

     def add_icon(self, index: int, icon: PreviewIcon):
          self._data[index] = icon
          self.number = len(self._data)

     def remove_icon(self, index: int):
          if index in self._data:
               del self._data[index]
               self.number = len(self._data)
          else:
               raise KeyError(f"No preview icon found at index {index}")

     def __repr__(self) -> str:
          return (f"<PreviewIconsFile version={self.version}, "
                  f"number_of_icons={self.number}, icons={list(self._data.keys())}>")
