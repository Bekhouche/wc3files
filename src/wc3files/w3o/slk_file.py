import pandas as pd
from io import StringIO
from loguru import logger
from .sylk_parser import SylkParser


class SlkFile(object):
     def __init__(self):
          
          self._data = {}

     def read(self, file_path):
          parser = SylkParser(file_path)
          fbuf = StringIO()
          parser.to_csv(fbuf)
          try:
               data = pd.read_csv(StringIO(fbuf.getvalue()))
          except:
               data = ''
               max_val = 0
               for val in fbuf.getvalue().split('\n'):
                    if max_val < len(val.split(',')):
                         max_val = len(val.split(','))
               
               for val in fbuf.getvalue().split('\n'):
                    if len(val.split(',')) < max_val and len(val.split(',')) > 1:
                         val_ = val.split(',') + ["1"] * (max_val-len(val.split(',')))
                         val = ','.join(val_)
                         data += val + '\n'
                    else:
                         data += val + '\n'
               data = pd.read_csv(StringIO(data))
          data.columns = [eval(name) for name in data.columns]
          self._data = {eval(row[data.columns[0]]): row.to_dict() for _, row in data.iterrows()}
          try:
               self._data = {key: {inner_key: inner_value if not isinstance(inner_value, str) else eval(inner_value) for inner_key, inner_value in value.items()} for key, value in self._data.items()}
          except:
               err = 0
     
     def import_data(self, data):
          self._data = data

     def export_data(self):
          return self._data

     @staticmethod
     def fix_strings(value):
          if isinstance(value, str) and len(value) >= 2:
               return value[1:-1]
          return value