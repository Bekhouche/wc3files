import struct
from wc3files.data import BinaryDataType, RegionFileType
from wc3files.files import BinaryFile

class RegionFile(object):
     def __init__(self):
          
          self.clear()
     
     def clear(self):

          self._data = {}
          self.version = 5
          self.number = 0

     def read(self, file_path):
          file_type = file_path.split('.')[-1].lower()
          if file_type in [item.value for item in RegionFileType]:
               with BinaryFile(file_path, 'read') as reader:
                    self.version = reader.read(BinaryDataType.INT)
                    self.number = reader.read(BinaryDataType.INT)
                    self._data = {}
                    
                    for idx in range(self.number):
                         left = reader.read(BinaryDataType.REAL)
                         right = reader.read(BinaryDataType.REAL)
                         bottom = reader.read(BinaryDataType.REAL)
                         top = reader.read(BinaryDataType.REAL)
                         name = reader.read(BinaryDataType.STRING)
                         index = reader.read(BinaryDataType.INT)
                         weather_effect = reader.read(BinaryDataType.BYTES)
                         ambient_sound = reader.read(BinaryDataType.STRING)
                         color = RegionFile.b2c(reader.read(BinaryDataType.BYTES))
                         self._data[idx] = {
                              'index': index,
                              'name': name,
                              'left': left,
                              'right': right,
                              'bottom': bottom,
                              'top': top,
                              'weather_effect': weather_effect,
                              'ambient_sound': ambient_sound,
                              'color': color
                         }                    
          else:
               raise ValueError("Invalid region file type")

     def write(self, file_path):
          file_type = file_path.split('.')[-1].lower()
          if file_type in [item.value for item in RegionFileType]:
               with BinaryFile(file_path, 'write', ) as writer:
                    writer.write(BinaryDataType.INT, self.version)
                    writer.write(BinaryDataType.INT, len(self._data))
                    for region in self._data.values():
                         writer.write(BinaryDataType.REAL, region['left'])
                         writer.write(BinaryDataType.REAL, region['right'])
                         writer.write(BinaryDataType.REAL, region['bottom'])
                         writer.write(BinaryDataType.REAL, region['top'])
                         writer.write(BinaryDataType.STRING, region['name'])
                         writer.write(BinaryDataType.INT, region['index'])
                         writer.write(BinaryDataType.BYTES, region['weather_effect'])
                         writer.write(BinaryDataType.STRING, region['ambient_sound'])
                         writer.write(BinaryDataType.BYTES, RegionFile.c2b(region['color']))
          else:
               raise ValueError("Invalid region file type")
     
     @staticmethod     
     def b2c(bytes):
          return struct.unpack('BBB', bytes[:-1])
     
     @staticmethod     
     def c2b(color):
          return bytes(color) + b'\xff'

