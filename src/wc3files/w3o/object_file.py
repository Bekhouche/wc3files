from wc3files.data import BinaryDataType, ObjectFileType
from wc3files.files import BinaryFile

class ObjectTable(object):
     def __init__(self, data_type=ObjectFileType.W3U, data_table='original'):
          
          self._data_type = data_type
          self._data_table = data_table
          self.clear()
     
     def clear(self):
          self._data = {}
          self._data_total = 0
     
     def load(self, data):
          self._data = data
          self._data_total = len(data)

     def read(self, reader: BinaryFile):
          self._data_total = reader.read(BinaryDataType.INT)
          for _ in range(self._data_total):
               # Get ID: Original & New [only for custom]
               ORG_ID = reader.read(BinaryDataType.BYTES).decode('utf-8')
               NEW_ID = reader.read(BinaryDataType.BYTES).decode('utf-8')
               
               IDX = ORG_ID
               if self._data_table == 'custom':
                    IDX = NEW_ID
                    self._data[IDX] = {'ID': ORG_ID}
               else:
                    self._data[IDX] = {}

               reader.read(BinaryDataType.INT, num=8) # 8 bytes [TODO]

               # Modifications
               modifications_num = reader.read(BinaryDataType.INT)
               for _ in range(modifications_num):
                    # Get Modifications ID & Type
                    MOD_ID = reader.read(BinaryDataType.BYTES).decode('utf-8')
                    MOD_TYPE = reader.read(BinaryDataType.INT)
                    if MOD_ID not in self._data[IDX]:
                         self._data[IDX][MOD_ID] = {'value': None, 'type': MOD_TYPE}

                    # Case of Doodads, Abilities, Upgrades [contains levels]
                    if (ObjectFileType(self._data_type) == ObjectFileType.W3D or ObjectFileType(self._data_type) == ObjectFileType.W3A or ObjectFileType(self._data_type) == ObjectFileType.W3Q):
                         MOD_LEVEL = reader.read(BinaryDataType.INT)
                         MOD_DATA_PTR = reader.read(BinaryDataType.INT)
                         MOD_VALUE = reader.read(BinaryDataType(MOD_TYPE))
                         
                         if self._data[IDX][MOD_ID]['value'] == None:
                              self._data[IDX][MOD_ID]['value'] = {}
                              self._data[IDX][MOD_ID]['ptr'] = {}
                         self._data[IDX][MOD_ID]['value'][MOD_LEVEL] = MOD_VALUE
                         self._data[IDX][MOD_ID]['ptr'][MOD_LEVEL] = MOD_DATA_PTR
                    else:
                         MOD_VALUE = reader.read(BinaryDataType(MOD_TYPE))
                         self._data[IDX][MOD_ID]['value'] = MOD_VALUE
           
                    # end
                    end = reader.read(BinaryDataType.BYTES).decode('utf-8')

     def write(self, writer: BinaryFile):
          writer.write(BinaryDataType.INT, self._data_total)
          for idx in self._data.keys():
               if 'ID' in self._data[idx]:
                    writer.write(BinaryDataType.BYTES, self._data[idx]['ID'].encode('utf-8'))
                    writer.write(BinaryDataType.BYTES, idx.encode('utf-8'))
               else:
                    writer.write(BinaryDataType.BYTES, idx.encode('utf-8'))
                    writer.write(BinaryDataType.BYTES, b'\x00\x00\x00\x00')
               
               writer.write(BinaryDataType.INT, 1)
               writer.write(BinaryDataType.INT, 0)

               # Case of Doodads, Abilities, Upgrades [contains levels]
               if (ObjectFileType(self._data_type) == ObjectFileType.W3D or ObjectFileType(self._data_type) == ObjectFileType.W3A or ObjectFileType(self._data_type) == ObjectFileType.W3Q):
                    mods, levels, ptrs, values, types = [], [], [], [], []
                    
                    for mod in sorted(self._data[idx].keys()):
                         if mod != 'ID':
                              if isinstance(self._data[idx][mod]['value'], dict):
                                   for level in self._data[idx][mod]['value'].keys():
                                        mods.append(mod)
                                        levels.append(level)
                                        ptrs.append(self._data[idx][mod]['ptr'][level])
                                        values.append(self._data[idx][mod]['value'][level])
                                        types.append(self._data[idx][mod]['type'])
                              else:
                                   mods.append(mod)
                                   levels.append(0)
                                   ptrs.append(0)
                                   values.append(self._data[idx][mod]['value'])
                                   types.append(self._data[idx][mod]['type'])
                    
                   
                    writer.write(BinaryDataType.INT, len(mods))
                    for mod in range(len(mods)):
                         
                         writer.write(BinaryDataType.BYTES, mods[mod].encode('utf-8')) # modification id
                         writer.write(BinaryDataType.INT, types[mod]) # modification type

                         writer.write(BinaryDataType.INT, levels[mod]) # level
                         writer.write(BinaryDataType.INT, ptrs[mod]) # ptr
                         writer.write(BinaryDataType(types[mod]), values[mod]) # value
                         writer.write(BinaryDataType.INT, 0) # end
               else:
                    if 'ID' in self._data[idx].keys():
                         writer.write(BinaryDataType.INT, len(self._data[idx])-1)
                    else:
                         writer.write(BinaryDataType.INT, len(self._data[idx]))

                    for mod in sorted(self._data[idx].keys()):
                         if mod != 'ID':
                              writer.write(BinaryDataType.BYTES, mod.encode('utf-8')) # modification id
                              writer.write(BinaryDataType.INT, self._data[idx][mod]['type']) # modification type
                              
                              writer.write(BinaryDataType(self._data[idx][mod]['type']), self._data[idx][mod]['value']) # value
                              writer.write(BinaryDataType.INT, 0) # end

     def filter(self, remove=[], keep=[]):
          if len(keep):
               self._data = {key: self._data[key] for key in keep if key in self._data}
               self._data_total = len(self._data)

          if len(remove):
               self._data = {key: self._data[key] for key in remove if key in self._data}
               self._data_total = len(self._data)

     def get(self):
          return self._data

     def __len__(self):
          return self._data_total
     

class ObjectFile(object):
     def __init__(self):
          
          self.clear()
     
     def clear(self):

          self._data = {'w3u': None, 'w3t': None, 'w3b': None, 'w3d': None,
                        'w3a': None, 'w3h': None, 'w3q': None}
          self._data_total = {'w3u': {'original': 0, 'custom':0}, 'w3t': {'original': 0, 'custom':0}, 'w3b': {'original': 0, 'custom':0}, 'w3d': {'original': 0, 'custom':0},
                        'w3a': {'original': 0, 'custom':0}, 'w3h': {'original': 0, 'custom':0}, 'w3q': {'original': 0, 'custom':0}}
          self._data_type = ObjectFileType.W3O

     def import_data(self, data):
          self._data = data

     def export_data(self):
          return self._data

     def read(self, file_path):
          object_type = file_path.split('.')[-1].lower()
          if object_type in [item.value for item in ObjectFileType]:   
               with BinaryFile(file_path, 'read') as reader:
                    if ObjectFileType(object_type) == ObjectFileType.W3O:
                         version_ = reader.read(BinaryDataType.INT)
                         for object_type_ in self._data.keys():
                              if reader.read(BinaryDataType.INT):
                                   version_ = reader.read(BinaryDataType.INT)
                                   original = ObjectTable(ObjectFileType(object_type_), 'original')
                                   original.read(reader)
                                   custom = ObjectTable(ObjectFileType(object_type_), 'custom')
                                   custom.read(reader)
                                   self._data[object_type_] = {'original': original.get(), 'custom': custom.get()}
                                   self._data_total[object_type_]['original'] = len(self._data[object_type_]['original'])
                                   self._data_total[object_type_]['custom'] = len(self._data[object_type_]['custom'])
                    else:
                         version = reader.read(BinaryDataType.INT)
                         original = ObjectTable(ObjectFileType(object_type), 'original')
                         original.read(reader)
                         custom = ObjectTable(ObjectFileType(object_type), 'custom')
                         custom.read(reader)
                         self._data[object_type] = {'original': original.get(), 'custom': custom.get()}
                         self._data_total[object_type]['original'] = len(self._data[object_type]['original'])
                         self._data_total[object_type]['custom'] = len(self._data[object_type]['custom'])
                         self._data_type = ObjectFileType(object_type)
          else:
               raise ValueError("Invalid file type")
          
     def write(self, file_path):
          object_type = file_path.split('.')[-1].lower()
          if object_type in [item.value for item in ObjectFileType]:
               with BinaryFile(file_path, 'write', ) as writer:
                    if ObjectFileType(object_type) == ObjectFileType.W3O:
                         writer.write(BinaryDataType.INT, 1)
                         for object_type_ in self._data.keys():
                              if self._data[object_type_] != None:
                                   writer.write(BinaryDataType.INT, 1)
                                   writer.write(BinaryDataType.INT, 3)
                         
                                   original = ObjectTable(ObjectFileType(object_type_), 'original')
                                   original.load(self._data[object_type_]['original'])
                                   original.write(writer)

                                   custom = ObjectTable(ObjectFileType(object_type_), 'custom')
                                   custom.load(self._data[object_type_]['custom'])
                                   custom.write(writer)
                              else:
                                   writer.write(BinaryDataType.INT, 0)

                    else:
                         writer.write(BinaryDataType.INT, 3)
                         
                         original = ObjectTable(ObjectFileType(object_type), 'original')
                         original.load(self._data[object_type]['original'])
                         original.write(writer)

                         custom = ObjectTable(ObjectFileType(object_type), 'custom')
                         custom.load(self._data[object_type]['custom'])
                         custom.write(writer)
          else:
               raise ValueError("Invalid file type")
          
     def filter(object_type=ObjectFileType.W3O, table=['original', 'custom'], keep=[], remove=[]):
          #print(object_type, table, keep, remove)
          x = 0
          
     def get_object_type(self):
          return self._data_type
     
     def set_object_type(self, object_type):
          self._data_type = object_type

     
