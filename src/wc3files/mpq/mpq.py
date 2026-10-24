import os
import struct
from tqdm import tqdm
from io import BytesIO
from .data import *
from wc3files.files import BinaryFile
from .cipher import MpqCipher
from .optional import MpqOptionalData
from .userdata import MpqUserData
from .header import MpqHeader
from .hash_table import MpqHashTable
from .block_table import MpqBlockTable
from .files import MpqFiles
from .writer import MpqWriter

class MpqFile:
     """Class to read and handle MPQ archives, supporting encryption and decryption of data."""
     ATTRIBUTES = "(attributes)"
     LISTFILE = "(listfile)"

     def __init__(self):
          """Initialize the MPQ file handler with cryptographic table."""
          self.clear()
          self.current_path = None
          self.current_name = None
          self.mpq_cipher = MpqCipher()

     def clear(self):
          """Clear any previously stored MPQ data."""
          self._data = {'OPTIONAL': {}, 'USER_DATA': {}, 'HEADER': {}, 'HASH_TABLE': [], 'BLOCK_TABLE': [], 'FILES': []}
          self.mpq_optional_data = MpqOptionalData()
          self.mpq_user_data = MpqUserData()
          self.mpq_header = MpqHeader()
          self.mpq_hash_table = MpqHashTable()
          self.mpq_block_table = MpqBlockTable()
          self.mpq_files = MpqFiles()

     def read(self, file_path):
          """Read and process an MPQ archive given its file path."""
          self.current_path = file_path
          self.current_name = os.path.basename(file_path).replace('.' + file_path.split('.')[-1], '')
          file_type = file_path.split('.')[-1].lower()
          if file_type in [item.value for item in MpqFileType]:

               self.reader = BinaryFile(file_path, 'read')
               with BinaryFile(file_path, 'read') as reader:
                    ### MPQ DATA OPTIONAL ###
                    self.mpq_optional_data.read(reader)
                    self._data['OPTIONAL'] = self.mpq_optional_data.get_data()
                    reader.seek(self._data['OPTIONAL']['ARCHIVE_OFFSET'])
                    
                    ### MPQ USER DATA OPTIONAL ###
                    self.mpq_user_data.read(reader)
                    self._data['USER_DATA'] = self.mpq_user_data.get_data()
                    reader.seek(self._data['USER_DATA']['ARCHIVE_OFFSET'])

                    ### MPQ HEADER ###
                    self.mpq_header.read(reader)
                    self._data['HEADER'] = self.mpq_header.get_data()

                    ### MPQ HASH TABLE ###
                    self.mpq_hash_table.set_archive_header(self.mpq_header)
                    self.mpq_hash_table.read(reader)
                    self._data['HASH_TABLE'] = self.mpq_hash_table.get_data()

                    ### MPQ BLOCK TABLE ###
                    self.mpq_block_table.set_archive_header(self.mpq_header)
                    self.mpq_block_table.set_hash_table(self.mpq_hash_table)
                    self.mpq_block_table.read(reader)
                    self._data['BLOCK_TABLE'] = self.mpq_block_table.get_data()
          else:
               raise ValueError(f'{file_type} is not supported as an archive file format!')
          

     def read_files(self):
          ### MPQ FILES ###
          with BinaryFile(self.current_path, 'read') as reader:
               self.mpq_files.set_archive_header(self.mpq_header)
               self.mpq_files.set_hash_table(self.mpq_hash_table)
               self.mpq_files.set_block_table(self.mpq_block_table)
               self.mpq_files.read(reader)
               self.files = self.mpq_files.get_data()
     
     def export(self, path):
          if not os.path.exists(os.path.join(path, self.current_name)):
               os.mkdir(os.path.join(path, self.current_name))

          for file in self.files.keys():
               if len(file.split('\\')) > 1:
                    if not os.path.exists(os.path.basename(os.path.join(path, self.current_name, file))):
                         os.makedirs(os.path.dirname(os.path.join(path, self.current_name, file)), exist_ok=True)
               
               f = open(os.path.join(path, self.current_name, file), 'wb')
               f.write(self.files[file] or b'')
               f.close()

     def build(self, files, dst_path, sector_size_shift=3,
               protection=MpqProtectionLevel.NONE, attributes=False):
          """Build a new MPQ archive from a {filename: bytes} table and write it to dst_path.

          `protection` (see MpqProtectionLevel / mpq/protection.py) opts into MPQ-container
          "protection" techniques real map protectors use - NONE (default) keeps the plain,
          standard archive layout. `attributes` (bool) additionally writes a real (attributes)
          file (mpq/attributes.py)."""
          MpqWriter(sector_size_shift=sector_size_shift, protection=protection,
                    attributes=attributes).write(files, dst_path)