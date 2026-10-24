import bz2
import struct
import zlib
from io import BytesIO
from .data import *

class MpqCipher:
     """
     Class for Cipher
     """
     def __init__(self):
          self.initialize_crypt_table()
     
     def initialize_crypt_table(self):
          self.crypt_table = {}
          seed = 0x00100001

          for index1 in range(0x100):
               for index2 in range(index1, index1 + 0x500, 0x100):
                    seed = (seed * 125 + 3) % 0x2AAAAB
                    temp1 = (seed & 0xFFFF) << 0x10

                    seed = (seed * 125 + 3) % 0x2AAAAB
                    temp2 = (seed & 0xFFFF)

                    self.crypt_table[index2] = (temp1 | temp2)

     def encrypt(self, buffer, key):
          buffer = bytearray(buffer)
          buffer_length = len(buffer)
          seed = 0xEEEEEEEE
          for i in range(0, buffer_length - buffer_length % 4, 4):
               seed = (seed + self.crypt_table[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
               value = struct.unpack_from('<I', buffer, i)[0]
               ch = (value ^ (key + seed)) & 0xFFFFFFFF
               key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
               seed = (value + seed + (seed << 5) + 3) & 0xFFFFFFFF

               struct.pack_into('<I', buffer, i, ch)

          return bytes(buffer)

     def decrypt(self, buffer, key):
          seed1 = key
          seed2 = 0xEEEEEEEE
          result = BytesIO()

          for i in range(len(buffer) // 4):
               seed2 += self.crypt_table[0x400 + (seed1 & 0xFF)]
               seed2 &= 0xFFFFFFFF
               value = struct.unpack("<I", buffer[i*4:i*4+4])[0]
               value = (value ^ (seed1 + seed2)) & 0xFFFFFFFF

               seed1 = ((~seed1 << 0x15) + 0x11111111) | (seed1 >> 0x0B)
               seed1 &= 0xFFFFFFFF
               seed2 = value + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

               result.write(struct.pack("<I", value))

          rem = len(buffer) % 4
          if rem:
               result.write(buffer[-rem:])

          return result.getvalue()
     
     def hash(self, lpszString, hash_type):
          seed1 = 0x7FED7FED
          seed2 = 0xEEEEEEEE
          
          for ch in lpszString.upper():
               if not isinstance(ch, int): ch = ord(ch)
               value = self.crypt_table[(hash_type << 8) + ch]
               seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
               seed2 = ch + seed1 + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF

          return seed1
     
     @staticmethod
     def decompress(data):
          compression_mask = ord(data[0:1])

          if compression_mask == 0:
               return data[1:]
          
          decompressed_data = data[1:]

          if compression_mask & 0x01:  # Huffman encoded
               print('Huffman encoded')
               decompressed_data = decompressed_data
          if compression_mask & 0x02:  # Deflated (ZLib)
               decompressed_data = zlib.decompress(decompressed_data)
          if compression_mask & 0x08:  # Imploded (PKWare)
               import dclimplode  # optional native codec, rarely hit - keep it out of the module's hard dependencies
               decompressed_data = dclimplode.decompressobj().decompress(decompressed_data)
          if compression_mask & 0x10:  # BZip2 compressed
               decompressed_data = bz2.decompress(decompressed_data)
          
          return decompressed_data