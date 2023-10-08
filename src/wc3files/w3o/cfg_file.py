from loguru import logger
from configparser import RawConfigParser
from .object_file import ObjectFileType

class CfgFile(object):
     def __init__(self):

          self._data = {'w3u': None, 'w3t': None, 'w3b': None, 'w3d': None,
                        'w3a': None, 'w3h': None, 'w3q': None}
          self._data_total = {'w3u': {'original': 0, 'custom':0}, 'w3t': {'original': 0, 'custom':0}, 'w3b': {'original': 0, 'custom':0}, 'w3d': {'original': 0, 'custom':0},
                        'w3a': {'original': 0, 'custom':0}, 'w3h': {'original': 0, 'custom':0}, 'w3q': {'original': 0, 'custom':0}}
          
          self._data_type = ObjectFileType.W3O
          self._cfg = RawConfigParser()
          self._data = {}

     def clear(self):
          self._data_type = ObjectFileType.W3O
          self._cfg = RawConfigParser()
          self._data = {}

     def write(self, file_path):
          with open(file_path, 'w', encoding='utf-8') as configfile:
               self._cfg.write(configfile)

     def import_data(self, data):
          self._data = data
          self.data2cfg()

     def export_data(self):
          return self._data
     
     def data2cfg(self):
          for object_type in self._data.keys():
               if self._data[object_type] != None:
                    for table in self._data[object_type]:
                         for idx in self._data[object_type][table]:
                              section = object_type + '.' + table + '.' + idx
                              self._cfg.add_section(section)
                              self._cfg.set(section, ';comment', 'Name')
                              for modification in self._data[object_type][table][idx].keys():
                                   if modification != 'ID':
                                        self._cfg.set(section, modification + '.name', modification)
                                        self._cfg.set(section, modification + '.value', self._encode_value(self._data[object_type][table][idx][modification]['value']))
                                        self._cfg.set(section, modification + '.type', self._encode_value(self._data[object_type][table][idx][modification]['type']))
                                        

     @staticmethod
     def _encode_value(value):
          if isinstance(value, str):
               if "'" in value:
                    value = f'"{value}"'
          return value
     
     @staticmethod
     def _decode_value(value):
          value_type = None
          if value.startswith("{") and value.endswith("}"):
               value_type = 'dict'
          
          if "." in value and value_type:
               try:
                    num = float(value)
                    if str(num).startswith(value.split('.')[0]):
                         value_type = 'float'
                         value = num           
               except ValueError:
                    value = value
          
          else:
               try:
                    num = int(value)
                    if str(num) == value:
                         value_type = 'int'
                         value = num 
               except ValueError:
                    value = value
          return value
     

     


     
