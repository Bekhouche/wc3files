import json
from loguru import logger
from .object_file import ObjectFileType

class JsonFile(object):
     def __init__(self):

          self._data = {'w3u': None, 'w3t': None, 'w3b': None, 'w3d': None,
                        'w3a': None, 'w3h': None, 'w3q': None}
          self._data_total = {'w3u': {'original': 0, 'custom':0}, 'w3t': {'original': 0, 'custom':0}, 'w3b': {'original': 0, 'custom':0}, 'w3d': {'original': 0, 'custom':0},
                        'w3a': {'original': 0, 'custom':0}, 'w3h': {'original': 0, 'custom':0}, 'w3q': {'original': 0, 'custom':0}}
          
          self._data_type = ObjectFileType.W3O
          self._data = {}

          self._slk = {'w3u': None, 'w3t': None, 'w3b': None, 'w3d': None,
                        'w3a': None, 'w3h': None, 'w3q': None}
          self._wes = {}

     def clear(self):
          self._data_type = ObjectFileType.W3O
          self._data = {}

     def load_wes(self, data):
          self._wes = data

     def load_slk(self, data, object_type):
          self._slk[object_type] = data
     
     def import_data(self, data):
          for object_type in data.keys():
               if object_type not in self._data:
                    self._data[object_type] = {}
               if self._slk[object_type] and self._wes:
                    for table in data[object_type].keys():
                         if table not in self._data[object_type]:
                              self._data[object_type][table] = {}

                         for idx in data[object_type][table].keys():
                              if idx not in self._data[object_type][table]:
                                   self._data[object_type][table][idx] = {}

                              for mod in data[object_type][table][idx]:
                                   self._data[object_type][table][idx][mod] = data[object_type][table][idx][mod]
                                   if mod in self._slk[object_type].keys():
                                        self._data[object_type][table][idx][mod]['description'] = self._wes[eval(self._slk[object_type][mod]['displayName'])]
                                        
                         #print(self.slk[object_type].keys())
               else:
                    self._data[object_type] = data[object_type]

     def export_data(self):
          return self._data
     
     def write(self, file_path):
          with open(file_path, 'w', encoding='utf-8') as json_file:
               json.dump(self._data, json_file, indent=4)
