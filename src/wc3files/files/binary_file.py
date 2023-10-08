import os
import sys
import io
import struct
from loguru import logger
from wc3files.data import BinaryDataType

class BinaryFile:
     def __init__(self, file_path, mode='read', logger_level='DEBUG'):
          if mode not in ('read', 'write'):
               raise ValueError("Invalid mode. Use 'read' or 'write'.")
          
          self.mode = mode
          self.file_path = file_path
          self.file = None

          logger.remove()
          logger.add(
               sink=sys.stdout,
               level=logger_level,
               format='<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>'
          )
          self.logger = logger.bind(name=self.__class__.__name__)

     def __enter__(self):
          self.open()
          return self

     def __exit__(self, exc_type, exc_value, traceback):
          self.close()

     def open(self):
          if self.mode == 'read':
               if os.path.exists(self.file_path):
                    self.file = io.open(self.file_path, 'rb')
               else:
                    raise FileNotFoundError(f"File not found: {self.file_path}")
          elif self.mode == 'write':
               self.file = io.open(self.file_path, 'wb')

     def close(self):
          if self.file is not None and not self.file.closed:
               self.file.close()

     def read(self, data_type=BinaryDataType.INT, num=4, byteorder='little'):
          if self.mode != 'read':
               raise ValueError("File is not open in read mode.")
          
          if data_type == BinaryDataType.INT:
               value = int.from_bytes(self.file.read(num), byteorder)
               self.logger.trace(f"Read INT value: {value}")
               return value
          elif data_type == BinaryDataType.REAL:
               value = struct.unpack('f', self.file.read(num))[0]
               self.logger.trace(f"Read REAL value: {value}")
               return value
          elif data_type == BinaryDataType.UNREAL:
               value = struct.unpack('f', self.file.read(num))[0]
               value = float(format(value, '.7f'))
               self.logger.trace(f"Read UNREAL value: {value}")
               return value
          elif data_type == BinaryDataType.STRING:
               value = []
               ch = self.file.read(1)
               while ch != b'\x00':
                    value.append(ch)
                    ch = self.file.read(1)
               value = b''.join(value).decode('utf-8')
               return value
          elif data_type == BinaryDataType.BYTES:
               value = self.file.read(num)
               self.logger.trace(f"Read BYTES value: {value}")
               return value
          else:
               return None

     def write(self, data_type, value):
          if self.mode != 'write':
               raise ValueError("File is not open in write mode.")
          
          if data_type == BinaryDataType.INT:
               self.file.write(struct.pack('<i', value))
               self.logger.trace(f"Wrote INT value: {value}")
          elif data_type == BinaryDataType.REAL:
               self.file.write(struct.pack('f', float(value)))
               self.logger.trace(f"Wrote REAL value: {value}")
          elif data_type == BinaryDataType.UNREAL:
               self.file.write(struct.pack('f', float(value)))
               self.logger.trace(f"Wrote UNREAL value: {value}")
          elif data_type == BinaryDataType.BYTES:
               self.file.write(value)
               self.logger.trace(f"Wrote BYTES value: {value}")
          elif data_type == BinaryDataType.STRING:
               self.file.write(value.encode('utf-8'))
               self.file.write(b'\x00')
               self.logger.trace(f"Wrote STRING value: {value}")

     def write_data(self, data_type, value):
          self.write(BinaryDataType.INT, data_type.value)
          self.write(data_type, value)
          self.write(BinaryDataType.INT, 0)

     def write_id(self, value):
          self.write_data(BinaryDataType.STRING, value)

     def write_integer(self, value):
          self.write_data(BinaryDataType.INT, value)

     def write_real(self, value):
          self.write_data(BinaryDataType.REAL, value)

     def write_unreal(self, value):
          self.write_data(BinaryDataType.UNREAL, value)

     def write_bytes(self, value):
          self.write_data(BinaryDataType.BYTES, value)

     def write_string(self, value):
          self.write_data(BinaryDataType.STRING, value)