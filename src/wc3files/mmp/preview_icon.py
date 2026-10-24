from ..data import BinaryDataType


class PreviewIcon:
     """
     One minimap preview-icon overlay (war3map.mmp) - the gold-mine/neutral-building/
     player-start markers drawn on the map preview image in the game lobby and map
     selection screen. Not per-match ping data (there is no such file in a map
     archive) - confirmed against War3Net.Build.Core's MapPreviewIcons/PreviewIcon
     and cross-checked against real bundled war3map.mmp fixtures: player-start icons
     (type 2) carry real WC3 player colors (e.g. red/blue/teal), gold-mine/neutral
     icons (types 0/1) are always opaque white.

     Colors are stored on disk as BGRA (confirmed against real fixtures, matches
     War3Net's ReadColorBgra()) - exposed here as an (R, G, B, A) tuple to match
     this package's other color fields (e.g. Region.color).
     """

     def __init__(self, icon_type=0, x=0, y=0, color=(255, 255, 255, 255)):
          self.icon_type = icon_type
          self.x = x
          self.y = y
          if isinstance(color, (bytes, bytearray)):
               self.color = self._bytes_to_color(color)
          else:
               self.color = color

     def get_data(self):
          return {
               'icon_type': (BinaryDataType.INT, self.icon_type),
               'x': (BinaryDataType.INT, self.x),
               'y': (BinaryDataType.INT, self.y),
               'color': (BinaryDataType.BYTES, self._color_to_bytes(self.color)),
          }

     @staticmethod
     def _bytes_to_color(bytes_val):
          if len(bytes_val) != 4:
               raise ValueError("Expected 4 bytes for color conversion.")
          b, g, r, a = bytes_val
          return (r, g, b, a)

     @staticmethod
     def _color_to_bytes(color):
          if len(color) != 4 or not all(0 <= v <= 255 for v in color):
               raise ValueError("Color must be a tuple of four integers between 0 and 255.")
          r, g, b, a = color
          return bytes((b, g, r, a))

     @staticmethod
     def structure():
          return {
               'icon_type': BinaryDataType.INT,
               'x': BinaryDataType.INT,
               'y': BinaryDataType.INT,
               'color': BinaryDataType.BYTES,
          }

     def __repr__(self):
          return f"<PreviewIcon type={self.icon_type} pos=({self.x}, {self.y}) color=RGBA{self.color}>"
