from loguru import logger

class WesFile(object):
     def __init__(self):
          
          self._data = {}

     def read(self, file_path):
          self._data = {}
          with open(file_path, mode='r', encoding='utf-8') as file:
               strings = [string.split('=') for string in file.read().split('\n') if len(string.split('=')) > 1]
               for i in range(len(strings)):
                    self._data[strings[i][0]] = strings[i][1]
     
     def import_data(self, data):
          self._data = data

     def export_data(self):
          return self._data
