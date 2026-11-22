"""Tests for wc3files.mpq.compression and wc3files.mpq.sectors.

The codecs are checked against fixed vectors and round trips:

- PKWare DCL against the worked example in Mark Adler's blast.c;
- Huffman by round trip through huffman_compress, a port of StormLib's
  encoder that shares the decoder's tree, for every weight table;
- ADPCM by round trip through a port of StormLib's CompressADPCM (here, in
  the test), which must give back the samples within the codec's error;
- the sector reader on a hand-built uncompressed multi-sector block, the
  layout whose misreading cut the start off a real map's war3map.j.
"""

import io
import math
import struct
import unittest
import zlib

import _helpers  # noqa: F401  (puts src/ on the path)

import importlib

compression = importlib.import_module('wc3files.mpq.compression')
sectors = importlib.import_module('wc3files.mpq.sectors')
from wc3files.mpq.data import MPQFileDataType  # noqa: E402


class TestExplode(unittest.TestCase):

     def test_blast_example(self):
          # blast.c: "00 04 82 24 25 8f 80 7f" decompresses to "AIAIAIAIAIAIA"
          data = bytes.fromhex('00048224258f807f')
          self.assertEqual(compression.explode(data), b'AIAIAIAIAIAIA')

     def test_method_byte_dispatch(self):
          data = bytes.fromhex('00048224258f807f')
          self.assertEqual(compression.decompress(b'\x08' + data), b'AIAIAIAIAIAIA')

     def test_bad_header(self):
          with self.assertRaises(compression.MpqCompressionError):
               compression.explode(b'\x02\x04\x00')


class TestHuffman(unittest.TestCase):

     SAMPLES = [
          b'',
          b'a',
          b'function main takes nothing returns nothing\r\n' * 40,
          bytes(range(256)) * 3,
          bytes((i * 37) & 0xFF for i in range(5000)),
          b'\x00' * 3000 + b'\xff' * 10,
     ]

     def test_round_trip_every_table(self):
          for data_type in range(9):
               for sample in self.SAMPLES:
                    with self.subTest(data_type=data_type, size=len(sample)):
                         packed = compression.huffman_compress(sample, data_type)
                         self.assertEqual(
                              compression.huffman_decompress(packed, len(sample) or 1),
                              sample)

     def test_text_compresses(self):
          text = b'call SetUnitX(u, GetUnitX(u) + 10.0)\r\n' * 100
          # Storm's tree only adapts on a new byte (or always, for type 0),
          # so the gain is modest: about a quarter on text
          self.assertLess(len(compression.huffman_compress(text, 2)), len(text) * 0.85)

     def test_stops_at_the_sector_size(self):
          packed = compression.huffman_compress(b'abcdef', 2)
          self.assertEqual(compression.huffman_decompress(packed, 4), b'abcd')


def _compress_adpcm(samples, channels, level=5):
     """StormLib's CompressADPCM, ported for the round trip test."""
     data = struct.pack('<%dh' % len(samples), *samples)
     out = bytearray((0, level - 1))
     shift = level - 1
     predicted = [0, 0]
     steps = [0x2C, 0x2C]
     position = 0
     for channel in range(channels):
          predicted[channel] = samples[channel]
          out += data[position:position + 2]
          position += 2
     channel = channels - 1
     for sample in samples[channels:]:
          encoded = 0
          channel = (channel + 1) % channels
          difference = sample - predicted[channel]
          if difference < 0:
               difference = -difference
               encoded |= 0x40
          step = compression._STEP_SIZE[steps[channel]]
          if difference < (step >> level):
               if steps[channel]:
                    steps[channel] -= 1
               out.append(0x80)
               continue
          while difference > (step << 1):
               if steps[channel] >= 0x58:
                    break
               steps[channel] = min(steps[channel] + 8, 0x58)
               step = compression._STEP_SIZE[steps[channel]]
               out.append(0x81)
          max_bit = min(1 << (shift - 1), 0x20)
          total = 0
          bit = 1
          while bit <= max_bit:
               if total + step <= difference:
                    total += step
                    encoded |= bit
               step >>= 1
               bit <<= 1
          base = compression._STEP_SIZE[steps[channel]] >> shift
          value = predicted[channel]
          value = max(value - (base + total), -32768) if encoded & 0x40 else \
               min(value + base + total, 32767)
          predicted[channel] = value
          out.append(encoded)
          steps[channel] = min(max(steps[channel] + compression._NEXT_STEP[encoded & 0x1F], 0), 88)
     return bytes(out)


class TestAdpcm(unittest.TestCase):

     def wave(self, count, channels):
          return [int(12000 * math.sin(i / (7.0 + c))) for i in range(count)
                  for c in range(channels)]

     def test_round_trip(self):
          for channels in (1, 2):
               with self.subTest(channels=channels):
                    samples = self.wave(2000, channels)
                    packed = _compress_adpcm(samples, channels)
                    self.assertLess(len(packed), len(samples) * 2)
                    raw = compression.adpcm_decompress(packed, len(samples) * 2, channels)
                    decoded = struct.unpack('<%dh' % (len(raw) // 2), raw)
                    self.assertEqual(len(decoded), len(samples))
                    worst = max(abs(a - b) for a, b in zip(samples, decoded))
                    self.assertLess(worst, 1500)

     def test_wave_sector_methods(self):
          # a sound sector is ADPCM, then Huffman over it: method byte 0x41
          samples = self.wave(500, 1)
          sector = b'\x41' + compression.huffman_compress(_compress_adpcm(samples, 1), 4)
          raw = compression.decompress(sector, len(samples) * 2)
          self.assertEqual(len(raw), len(samples) * 2)
          self.assertEqual(raw[:2], struct.pack('<h', samples[0]))


class _Reader:
     def __init__(self, data):
          self.stream = io.BytesIO(data)

     def seek(self, position):
          self.stream.seek(position)

     def read(self, _kind, num):
          return self.stream.read(num)


class TestReadBlock(unittest.TestCase):

     def no_decrypt(self, data, key):
          raise AssertionError('not encrypted')

     def test_uncompressed_sectors_have_no_offset_table(self):
          content = bytes(i & 0xFF for i in range(10000))
          block = content + b'NEXT FILE DATA'
          entry = {'Flags': MPQFileDataType.EXISTS.value, 'FileSize': len(content),
                   'BlockSize': len(content)}
          data = sectors.read_block(_Reader(block), entry, 0, 4096, self.no_decrypt, 0)
          self.assertEqual(data, content)

     def test_compressed_sectors(self):
          content = b'globals\r\nendglobals\r\n' * 500
          size = 4096
          pieces = [content[i:i + size] for i in range(0, len(content), size)]
          stored = [b'\x02' + zlib.compress(p) for p in pieces]
          table_size = 4 * (len(stored) + 1)
          offsets = [table_size]
          for piece in stored:
               offsets.append(offsets[-1] + len(piece))
          block = struct.pack('<%dI' % len(offsets), *offsets) + b''.join(stored)
          entry = {'Flags': MPQFileDataType.EXISTS.value | MPQFileDataType.COMPRESS.value,
                   'FileSize': len(content), 'BlockSize': len(block)}
          self.assertEqual(sectors.read_block(_Reader(block), entry, 0, size,
                                              self.no_decrypt, 0), content)

     def test_exact_multiple_of_the_sector_size(self):
          content = b'x' * 8192
          stored = [b'\x02' + zlib.compress(content[:4096]), b'\x02' + zlib.compress(content[4096:])]
          offsets = [12, 12 + len(stored[0]), 12 + len(stored[0]) + len(stored[1])]
          block = struct.pack('<3I', *offsets) + b''.join(stored)
          entry = {'Flags': MPQFileDataType.EXISTS.value | MPQFileDataType.COMPRESS.value,
                   'FileSize': 8192, 'BlockSize': len(block)}
          self.assertEqual(sectors.read_block(_Reader(block), entry, 0, 4096,
                                              self.no_decrypt, 0), content)


if __name__ == '__main__':
     unittest.main()
