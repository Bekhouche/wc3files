import struct
import zlib
from ..data import BinaryDataType
from .data import ReplayFileType
from wc3files.files import BinaryFile
from .header import ReplayHeader
from .compressed_data import ReplayCompressedData

DEFAULT_BLOCK_SIZE = 8192

class ReplayFile(object):
     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}
          self.header = ReplayHeader()
          self.compressed_data = ReplayCompressedData()

     def read(self, file_path):
          file_type = file_path.split('.')[-1].lower()
          if file_type not in [item.value for item in ReplayFileType]:
               raise ValueError("Invalid replay file type")

          with BinaryFile(file_path, 'read') as reader:
               self.header.read(reader)
               self.compressed_data.set_header(self.header.get_data())
               self.compressed_data.read(reader)

          self._data = {
               'header': self.header.get_data(),
               'compressed_data': self.compressed_data.get_data(),
               'reforged_player_metadata': self.compressed_data.reforged_player_metadata,
               # The blocks exactly as stored. Without them a dump -> build
               # has to recompress, and the game does not use zlib level 9 -
               # the rebuilt file is then valid but 5-7% smaller, so the CLI
               # would not match what read()/write() produces directly.
               'raw_blocks': list(self.compressed_data._raw_blocks),
               'source_decompressed': self.compressed_data._source_decompressed,
          }

     def write(self, file_path, block_size=DEFAULT_BLOCK_SIZE):
          """
          Write a replay file back out, rebuilding the header and the parsed preamble
          (players, game settings, map/creator name, ...) from self.compressed_data's data -
          so editing those fields (e.g. a player's name) and calling write() reflects the
          edit - while everything after the preamble (the action-block/command stream) is
          reattached verbatim from what read() captured, unedited.

          Note: per-block reserved fields in the compressed-data section (4 bytes, plus 2
          more for Reforged) aren't independently verified - if they're a checksum WC3 itself
          validates on load, this writer's output may not pass that check even though this
          package's own read() parses it back identically. Not knowing what they are, they
          are written as zero.
          """
          header_data = dict(self.header.get_data())
          header_data['sub'] = dict(header_data.get('sub', {}))
          is_reforged = header_data.get('reforged', False)

          decompressed = self.compressed_data.build()

          blocks = bytearray()
          block_count = 0

          # Nothing changed, so put the original blocks back exactly as they
          # were found. The game does not compress at zlib level 9, so
          # recompressing identical bytes yields a valid but different file -
          # across 25 real replays it came out 5-7% smaller every time, and
          # the header then had to describe the new size. Reusing the stored
          # blocks makes an unedited read()/write() byte-for-byte.
          original_blocks = getattr(self.compressed_data, '_raw_blocks', None)
          source = getattr(self.compressed_data, '_source_decompressed', None)
          if original_blocks and source is not None and decompressed == source:
               for block in original_blocks:
                    blocks += struct.pack('<H', len(block['compressed']))
                    if is_reforged:
                         blocks += b'\x00\x00'
                    blocks += struct.pack('<H', block['uncompressed_size'])
                    blocks += block['reserved']
                    blocks += block['compressed']
                    block_count += 1
          else:
               for offset in range(0, len(decompressed), block_size):
                    chunk = decompressed[offset:offset + block_size]
                    compressed = zlib.compress(chunk, 9)
                    blocks += struct.pack('<H', len(compressed))
                    if is_reforged:
                         blocks += b'\x00\x00'
                    blocks += struct.pack('<H', len(chunk))
                    blocks += b'\x00\x00\x00\x00'
                    if is_reforged:
                         blocks += b'\x00\x00'
                    blocks += compressed
                    block_count += 1

          header_data['size_decompressed'] = len(decompressed)
          header_data['number_compressed'] = block_count
          header_data['size_compressed'] = header_data['fileoffset'] + len(blocks)

          with BinaryFile(file_path, 'write') as writer:
               self.header.write(writer, header_data)
               writer.write(BinaryDataType.BYTES, bytes(blocks))

          self.header._data = header_data

     def import_data(self, data):
          """
          Load a replay from the same dict shape get_data() returns, so a dumped
          replay can be edited and built back into a real file.

          Without this, write() only ever rebuilt from whatever state a prior read()
          had left on self.header/self.compressed_data - so building from dumped data
          silently produced either nothing usable or the unedited original, which is
          why the CLI declared this format unbuildable rather than misleading anyone.

          The raw spans read() doesn't model (_leading_bytes, _raw_settings,
          _settings_tail, _raw_tail, each player's unparsed trailing data) must be
          present and are passed through verbatim, exactly as they are on the read
          path - they are what makes the action-block stream survive at all. The CLI
          carries them through JSON base64-encoded (see _BytesEncoder in cli/main.py).
          """
          if not isinstance(data, dict):
               raise ValueError("Replay data must be a dictionary.")
          for key in ('header', 'compressed_data'):
               if key not in data:
                    raise ValueError(f"Replay data is missing the {key!r} section.")

          self.header = ReplayHeader()
          self.header._data = dict(data['header'])

          self.compressed_data = ReplayCompressedData()
          self.compressed_data.set_header(self.header._data)
          self.compressed_data._data = dict(data['compressed_data'])
          self.compressed_data.reforged_player_metadata = list(
               data.get('reforged_player_metadata') or [])
          self.compressed_data._raw_blocks = [dict(b) for b in
                                              (data.get('raw_blocks') or [])]
          self.compressed_data._source_decompressed = (
               data.get('source_decompressed') or b'')

          self._data = {
               'header': self.header.get_data(),
               'compressed_data': self.compressed_data.get_data(),
               'reforged_player_metadata': self.compressed_data.reforged_player_metadata,
               'raw_blocks': list(self.compressed_data._raw_blocks),
               'source_decompressed': self.compressed_data._source_decompressed,
          }

     def get_data(self):
          return self._data
