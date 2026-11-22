"""
MPQ sector decompression, in pure Python.

A sector of a file with the COMPRESS flag starts with one byte naming the
methods applied, then the data. The methods are undone in the order StormLib
uses (SCompDecompress): bzip2, PKWare DCL, zlib, Huffman, ADPCM stereo,
ADPCM mono. Warcraft III maps use zlib for most files, Huffman with ADPCM for
sounds, and PKWare DCL in old maps and in files with the IMPLODE flag.

- PKWare DCL ("implode") is decoded as Mark Adler's blast.c describes the
  format: literal and distance codes from fixed Huffman tables, bits read
  least significant first and inverted.
- Huffman is Storm's adaptive Huffman (the FGK variant), ported from
  StormLib's huff.cpp (Ladislav Zezula, MIT licence): the tree starts from one
  of nine fixed weight tables, named by the first byte of the data.
- ADPCM is Storm's IMA ADPCM variant, ported from StormLib's adpcm.cpp.

Sparse and LZMA compression only appear in later Blizzard games and are not
supported.
"""

import bz2
import zlib

HUFFMAN = 0x01
ZLIB = 0x02
PKWARE = 0x08
BZIP2 = 0x10
SPARSE = 0x20
ADPCM_MONO = 0x40
ADPCM_STEREO = 0x80

_KNOWN = HUFFMAN | ZLIB | PKWARE | BZIP2 | ADPCM_MONO | ADPCM_STEREO


class MpqCompressionError(ValueError):
     pass


def decompress(sector, size=None):
     """Decompress one sector that starts with its method byte. `size` is the
     sector's real size; the ADPCM and Huffman stages stop there."""
     methods = sector[0]
     data = bytes(sector[1:])
     if methods == 0:
          return data
     if methods == 0x12:
          raise MpqCompressionError('LZMA-compressed MPQ sectors are not supported')
     if methods & ~_KNOWN:
          raise MpqCompressionError(f'unsupported MPQ compression methods 0x{methods:02x}')
     if size is None:
          size = 1 << 30
     if methods & BZIP2:
          data = bz2.decompress(data)
     if methods & PKWARE:
          data = explode(data)
     if methods & ZLIB:
          data = zlib.decompress(data)
     if methods & HUFFMAN:
          data = huffman_decompress(data, size)
     if methods & ADPCM_STEREO:
          data = adpcm_decompress(data, size, 2)
     if methods & ADPCM_MONO:
          data = adpcm_decompress(data, size, 1)
     return data


# --- PKWare DCL ------------------------------------------------------------

# Code lengths in blast.c's compact form: the low 4 bits are a length, the
# high 4 bits one less than how many symbols in a row have it.
_LITLEN = (
     11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8, 9, 7, 6, 7, 8, 7, 6,
     55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5, 7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7,
     9, 8, 25, 11, 8, 11, 9, 12, 8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24,
     10, 27, 44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45, 44, 173)
_LENLEN = (2, 35, 36, 53, 38, 23)
_DISTLEN = (2, 20, 53, 230, 247, 151, 248)
_LEN_BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
_LEN_EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)
_MAXBITS = 13


def _dcl_table(compact):
     lengths = []
     for value in compact:
          lengths += [value & 15] * ((value >> 4) + 1)
     count = [0] * (_MAXBITS + 1)
     for length in lengths:
          count[length] += 1
     offsets = [0] * (_MAXBITS + 1)
     for length in range(1, _MAXBITS):
          offsets[length + 1] = offsets[length] + count[length]
     symbols = [0] * len(lengths)
     for symbol, length in enumerate(lengths):
          if length:
               symbols[offsets[length]] = symbol
               offsets[length] += 1
     return count, symbols


_DCL_TABLES = None


class _DclBits:
     __slots__ = ('data', 'pos', 'buffer', 'count')

     def __init__(self, data):
          self.data = data
          self.pos = 0
          self.buffer = 0
          self.count = 0

     def bits(self, need):
          value = self.buffer
          while self.count < need:
               if self.pos >= len(self.data):
                    raise MpqCompressionError('PKWare DCL data ends early')
               value |= self.data[self.pos] << self.count
               self.pos += 1
               self.count += 8
          self.buffer = value >> need
          self.count -= need
          return value & ((1 << need) - 1)

     def decode(self, table):
          count, symbols = table
          code = first = index = 0
          for length in range(1, _MAXBITS + 1):
               code |= self.bits(1) ^ 1          # the codes are stored inverted
               number = count[length]
               if code - first < number:
                    return symbols[index + code - first]
               index += number
               first = (first + number) << 1
               code <<= 1
          raise MpqCompressionError('invalid PKWare DCL code')


def explode(data):
     """Decompress PKWare DCL ("implode") data."""
     global _DCL_TABLES
     if _DCL_TABLES is None:
          _DCL_TABLES = (_dcl_table(_LITLEN), _dcl_table(_LENLEN), _dcl_table(_DISTLEN))
     litcode, lencode, distcode = _DCL_TABLES
     stream = _DclBits(bytes(data))
     coded_literals = stream.bits(8)
     if coded_literals > 1:
          raise MpqCompressionError('invalid PKWare DCL literal mode')
     dictionary = stream.bits(8)
     if not 4 <= dictionary <= 6:
          raise MpqCompressionError('invalid PKWare DCL dictionary size')
     out = bytearray()
     while True:
          if stream.bits(1):
               symbol = stream.decode(lencode)
               length = _LEN_BASE[symbol] + stream.bits(_LEN_EXTRA[symbol])
               if length == 519:                 # end of stream
                    break
               shift = 2 if length == 2 else dictionary
               distance = (stream.decode(distcode) << shift) + stream.bits(shift) + 1
               if distance > len(out):
                    raise MpqCompressionError('PKWare DCL distance too far back')
               start = len(out) - distance
               if distance >= length:
                    out += out[start:start + length]
               else:
                    for i in range(length):
                         out.append(out[start + i])
          else:
               out.append(stream.decode(litcode) if coded_literals else stream.bits(8))
     return bytes(out)


# --- Huffman -----------------------------------------------------------------

def _sparse_weights():
     table = [0] * 258
     table[0] = 0x0A
     table[255] = 0x02
     return table


def _rows(*runs):
     """Weight tables, written as (value, ...) runs with zero padding."""
     table = []
     for run in runs:
          table += list(run)
     return table + [0] * (258 - len(table))


_HUFFMAN_WEIGHTS = [
     _sparse_weights(),
     _rows((
          0x54, 0x16, 0x16, 0x0D, 0x0C, 0x08, 0x06, 0x05, 0x06, 0x05, 0x06, 0x03, 0x04, 0x04, 0x03, 0x05,
          0x0E, 0x0B, 0x14, 0x13, 0x13, 0x09, 0x0B, 0x06, 0x05, 0x04, 0x03, 0x02, 0x03, 0x02, 0x02, 0x02,
          0x0D, 0x07, 0x09, 0x06, 0x06, 0x04, 0x03, 0x02, 0x04, 0x03, 0x03, 0x03, 0x03, 0x03, 0x02, 0x02,
          0x09, 0x06, 0x04, 0x04, 0x04, 0x04, 0x03, 0x02, 0x03, 0x02, 0x02, 0x02, 0x02, 0x03, 0x02, 0x04,
          0x08, 0x03, 0x04, 0x07, 0x09, 0x05, 0x03, 0x03, 0x03, 0x03, 0x02, 0x02, 0x02, 0x03, 0x02, 0x02,
          0x03, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x02, 0x01, 0x01, 0x01, 0x02, 0x01, 0x02, 0x02,
          0x06, 0x0A, 0x08, 0x08, 0x06, 0x07, 0x04, 0x03, 0x04, 0x04, 0x02, 0x02, 0x04, 0x02, 0x03, 0x03,
          0x04, 0x03, 0x07, 0x07, 0x09, 0x06, 0x04, 0x03, 0x03, 0x02, 0x01, 0x02, 0x02, 0x02, 0x02, 0x02,
          0x0A, 0x02, 0x02, 0x03, 0x02, 0x02, 0x01, 0x01, 0x02, 0x02, 0x02, 0x06, 0x03, 0x05, 0x02, 0x03,
          0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x03, 0x01, 0x01, 0x01,
          0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x04, 0x04, 0x04, 0x07, 0x09, 0x08, 0x0C, 0x02,
          0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x03,
          0x04, 0x01, 0x02, 0x04, 0x05, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01,
          0x04, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x03, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x02, 0x01, 0x01, 0x02, 0x02, 0x02, 0x06, 0x4B,
     ),),
     _rows((
          0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x27, 0x00, 0x00, 0x23, 0x00, 0x00,
          0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
          0xFF, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x02, 0x01, 0x01, 0x06, 0x0E, 0x10, 0x04,
          0x06, 0x08, 0x05, 0x04, 0x04, 0x03, 0x03, 0x02, 0x02, 0x03, 0x03, 0x01, 0x01, 0x02, 0x01, 0x01,
          0x01, 0x04, 0x02, 0x04, 0x02, 0x02, 0x02, 0x01, 0x01, 0x04, 0x01, 0x01, 0x02, 0x03, 0x03, 0x02,
          0x03, 0x01, 0x03, 0x06, 0x04, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x02, 0x01, 0x01,
          0x01, 0x29, 0x07, 0x16, 0x12, 0x40, 0x0A, 0x0A, 0x11, 0x25, 0x01, 0x03, 0x17, 0x10, 0x26, 0x2A,
          0x10, 0x01, 0x23, 0x23, 0x2F, 0x10, 0x06, 0x07, 0x02, 0x09, 0x01, 0x01, 0x01, 0x01, 0x01, 0x00,
     ),),
     _rows((
          0xFF, 0x0B, 0x07, 0x05, 0x0B, 0x02, 0x02, 0x02, 0x06, 0x02, 0x02, 0x01, 0x04, 0x02, 0x01, 0x03,
          0x09, 0x01, 0x01, 0x01, 0x03, 0x04, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01,
          0x05, 0x01, 0x01, 0x01, 0x0D, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x02, 0x01, 0x01, 0x03, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x01,
          0x0A, 0x04, 0x02, 0x01, 0x06, 0x03, 0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x03, 0x01, 0x01, 0x01,
          0x05, 0x02, 0x03, 0x04, 0x03, 0x03, 0x03, 0x02, 0x01, 0x01, 0x01, 0x02, 0x01, 0x02, 0x03, 0x03,
          0x01, 0x03, 0x01, 0x01, 0x02, 0x05, 0x01, 0x01, 0x04, 0x03, 0x05, 0x01, 0x03, 0x01, 0x03, 0x03,
          0x02, 0x01, 0x04, 0x03, 0x0A, 0x06, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x02, 0x02, 0x01, 0x0A, 0x02, 0x05, 0x01, 0x01, 0x02, 0x07, 0x02, 0x17, 0x01, 0x05, 0x01, 0x01,
          0x0E, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x06, 0x02, 0x01, 0x04, 0x05, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01,
          0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01,
          0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x07, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x01,
          0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x02, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x11,
     ),),
     _rows((0xFF, 0xFB, 0x98, 0x9A, 0x84, 0x85, 0x63, 0x64, 0x3E, 0x3E, 0x22, 0x22, 0x13, 0x13,
            0x18, 0x17),),
     _rows((
          0xFF, 0xF1, 0x9D, 0x9E, 0x9A, 0x9B, 0x9A, 0x97, 0x93, 0x93, 0x8C, 0x8E, 0x86, 0x88, 0x80, 0x82,
          0x7C, 0x7C, 0x72, 0x73, 0x69, 0x6B, 0x5F, 0x60, 0x55, 0x56, 0x4A, 0x4B, 0x40, 0x41, 0x37, 0x37,
          0x2F, 0x2F, 0x27, 0x27, 0x21, 0x21, 0x1B, 0x1C, 0x17, 0x17, 0x13, 0x13, 0x10, 0x10, 0x0D, 0x0D,
          0x0B, 0x0B, 0x09, 0x09, 0x08, 0x08, 0x07, 0x07, 0x06, 0x05, 0x05, 0x04, 0x04, 0x04, 0x19, 0x18,
     ),),
     _rows((0xC3, 0xCB, 0xF5, 0x41, 0xFF, 0x7B, 0xF7, 0x21), (0,) * 56,
           (0xBF, 0xCC, 0xF2, 0x40, 0xFD, 0x7C, 0xF7, 0x22), (0,) * 56,
           (0x7A, 0x46)),
     _rows((0xC3, 0xD9, 0xEF, 0x3D, 0xF9, 0x7C, 0xE9, 0x1E, 0xFD, 0xAB, 0xF1, 0x2C, 0xFC, 0x5B,
            0xFE, 0x17), (0,) * 48,
           (0xBD, 0xD9, 0xEC, 0x3D, 0xF5, 0x7D, 0xE8, 0x1D, 0xFB, 0xAE, 0xF0, 0x2C, 0xFB, 0x5C,
            0xFF, 0x18), (0,) * 48,
           (0x70, 0x6C)),
     _rows((0xBA, 0xC5, 0xDA, 0x33, 0xE3, 0x6D, 0xD8, 0x18, 0xE5, 0x94, 0xDA, 0x23, 0xDF, 0x4A,
            0xD1, 0x10, 0xEE, 0xAF, 0xE4, 0x2C, 0xEA, 0x5A, 0xDE, 0x15, 0xF4, 0x87, 0xE9, 0x21,
            0xF6, 0x43, 0xFC, 0x12), (0,) * 32,
           (0xB0, 0xC7, 0xD8, 0x33, 0xE3, 0x6B, 0xD6, 0x18, 0xE7, 0x95, 0xD8, 0x23, 0xDB, 0x49,
            0xD0, 0x11, 0xE9, 0xB2, 0xE2, 0x2B, 0xE8, 0x5C, 0xDD, 0x15, 0xF1, 0x87, 0xE7, 0x20,
            0xF7, 0x44, 0xFF, 0x13), (0,) * 32,
           (0x5F, 0x9E)),
]

_HUFF_ITEM_COUNT = 515


class _Item:
     __slots__ = ('next', 'prev', 'value', 'weight', 'parent', 'child_lo')

     def __init__(self):
          self.next = self.prev = None
          self.value = 0
          self.weight = 0
          self.parent = self.child_lo = None

     def remove(self):
          if self.next is not None:
               self.prev.next = self.next
               self.next.prev = self.prev
               self.next = self.prev = None


class _HuffmanTree:
     """StormLib's THuffmannTree, decompression side. The list runs from the
     highest weight (head.next) to the lowest (head.prev)."""

     def __init__(self):
          self.head = _Item()
          self.head.next = self.head.prev = self.head
          self.used = 0
          self.by_byte = [None] * 258

     @staticmethod
     def link(item1, item2):                   # put item2 right after item1
          item2.next = item1.next
          item2.prev = item1.next.prev
          item1.next.prev = item2
          item1.next = item2

     def create(self, value, weight, before):
          if self.used >= _HUFF_ITEM_COUNT:
               return None
          self.used += 1
          item = _Item()
          item.remove()
          self.link(self.head.prev if before else self.head, item)
          item.value = value
          item.weight = weight
          return item

     def find_higher_or_equal(self, item, weight):
          if item is not None:
               while item is not self.head:
                    if item.weight >= weight:
                         return item
                    item = item.prev
          return self.head

     def fixup(self, item, max_weight):
          if item.weight < max_weight:
               higher = self.find_higher_or_equal(self.head.prev, item.weight)
               item.remove()
               self.link(higher, item)
               return max_weight
          return item.weight

     def build(self, data_type):
          data_type &= 0x0F
          if data_type >= len(_HUFFMAN_WEIGHTS):
               raise MpqCompressionError(f'unknown Huffman data type {data_type}')
          weights = _HUFFMAN_WEIGHTS[data_type]
          max_weight = 0
          for byte in range(0x100):
               if weights[byte]:
                    item = self.by_byte[byte] = self.create(byte, weights[byte], False)
                    max_weight = self.fixup(item, max_weight)
          self.by_byte[0x100] = self.create(0x100, 1, True)
          self.by_byte[0x101] = self.create(0x101, 1, True)
          child_lo = self.head.prev
          while child_lo is not self.head:
               child_hi = child_lo.prev
               if child_hi is self.head:
                    break
               parent = self.create(0, child_hi.weight + child_lo.weight, False)
               if parent is None:
                    raise MpqCompressionError('Huffman tree overflow')
               child_lo.parent = parent
               child_hi.parent = parent
               parent.child_lo = child_lo
               max_weight = self.fixup(parent, max_weight)
               child_lo = child_hi.prev

     def increment(self, item):
          while item is not None:
               item.weight += 1
               higher = self.find_higher_or_equal(item.prev, item.weight)
               child_hi = higher.next
               if child_hi is not item:
                    child_hi.remove()
                    self.link(item, child_hi)
                    item.remove()
                    self.link(higher, item)
                    child_lo = child_hi.parent.child_lo
                    parent = item.parent
                    if parent.child_lo is item:
                         parent.child_lo = child_hi
                    if child_lo is child_hi:
                         child_hi.parent.child_lo = item
                    parent = item.parent
                    item.parent = child_hi.parent
                    child_hi.parent = parent
               item = item.parent

     def insert_branch(self, value1, value2):
          last = self.head.prev
          child_hi = self.create(value1, last.weight, True)
          if child_hi is None:
               raise MpqCompressionError('Huffman tree overflow')
          child_hi.parent = last
          self.by_byte[value1] = child_hi
          child_lo = self.create(value2, 0, True)
          if child_lo is None:
               raise MpqCompressionError('Huffman tree overflow')
          child_lo.parent = last
          last.child_lo = child_lo
          self.by_byte[value2] = child_lo
          self.increment(child_lo)


def huffman_decompress(data, size):
     """Storm's adaptive Huffman, up to `size` bytes of output."""
     data = bytes(data)
     position = 0
     buffer = 0
     count = 0
     out = bytearray()

     def bit():
          nonlocal position, buffer, count
          if count == 0:
               if position >= len(data):
                    raise MpqCompressionError('Huffman data ends early')
               buffer = data[position]
               position += 1
               count = 8
          value = buffer & 1
          buffer >>= 1
          count -= 1
          return value

     def byte():
          nonlocal position, buffer, count
          if count < 8:
               if position >= len(data):
                    raise MpqCompressionError('Huffman data ends early')
               buffer |= data[position] << count
               position += 1
               count += 8
          value = buffer & 0xFF
          buffer >>= 8
          count -= 8
          return value

     if size <= 0:
          return b''
     data_type = byte()
     sparse = data_type == 0
     tree = _HuffmanTree()
     tree.build(data_type)
     head = tree.head
     while True:
          if head.next is head:
               raise MpqCompressionError('empty Huffman tree')
          item = head.next
          while item.child_lo is not None:
               item = item.child_lo.prev if bit() else item.child_lo
          value = item.value
          if value == 0x100:
               break
          if value == 0x101:
               value = byte()
               tree.insert_branch(head.prev.value, value)
               if not sparse:
                    tree.increment(tree.by_byte[value])
          if len(out) >= size:
               break
          out.append(value)
          if sparse:
               tree.increment(tree.by_byte[value])
     return bytes(out)


def huffman_compress(data, data_type=2):
     """Storm's adaptive Huffman encoder (StormLib's THuffmannTree::Compress),
     the inverse of huffman_decompress. `data_type` picks the weight table
     (0 sparse, 1 binary, 2 text, 3 general, 4-8 audio)."""
     tree = _HuffmanTree()
     tree.build(data_type)
     sparse = data_type == 0
     out = bytearray()
     buffer = 0
     count = 0

     def put(value, bits):
          nonlocal buffer, count
          buffer |= value << count
          count += bits
          while count >= 8:
               out.append(buffer & 0xFF)
               buffer >>= 8
               count -= 8

     def encode(item):
          value = bits = 0
          parent = item.parent
          while parent is not None:
               value = (value << 1) | (1 if parent.child_lo is not item else 0)
               bits += 1
               item = parent
               parent = parent.parent
          put(value, bits)

     put(data_type, 8)
     for byte in bytes(data):
          if tree.by_byte[byte] is None:
               encode(tree.by_byte[0x101])
               put(byte, 8)
               tree.insert_branch(tree.head.prev.value, byte)
               tree.increment(tree.by_byte[byte])
               continue
          encode(tree.by_byte[byte])
          if sparse:
               tree.increment(tree.by_byte[byte])
     encode(tree.by_byte[0x100])
     if count:
          out.append(buffer & 0xFF)
     return bytes(out)


# --- ADPCM -------------------------------------------------------------------

_NEXT_STEP = (-1, 0, -1, 4, -1, 2, -1, 6, -1, 1, -1, 5, -1, 3, -1, 7,
              -1, 1, -1, 5, -1, 3, -1, 7, -1, 2, -1, 4, -1, 6, -1, 8)
_STEP_SIZE = (
     7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
     34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
     157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
     724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024,
     3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
     13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767)
_INITIAL_STEP_INDEX = 0x2C


def _int16(value):
     value &= 0xFFFF
     return value - 0x10000 if value & 0x8000 else value


def adpcm_decompress(data, size, channels):
     """Storm's IMA ADPCM, `channels` 1 or 2, up to `size` bytes of output."""
     data = bytes(data)
     out = bytearray()
     limit = size - size % 2

     def write(sample):
          if len(out) + 2 > limit:
               return False
          out.extend((sample & 0xFF, (sample >> 8) & 0xFF))
          return True

     if len(data) < 2:
          return b''
     shift = data[1]
     position = 2
     predicted = [0, 0]
     steps = [_INITIAL_STEP_INDEX, _INITIAL_STEP_INDEX]
     for channel in range(channels):
          if position + 2 > len(data):
               return bytes(out)
          predicted[channel] = _int16(data[position] | (data[position + 1] << 8))
          position += 2
          if not write(predicted[channel]):
               return bytes(out)
     channel = channels - 1
     while position < len(data):
          encoded = data[position]
          position += 1
          channel = (channel + 1) % channels
          if encoded == 0x80:
               if steps[channel]:
                    steps[channel] -= 1
               if not write(predicted[channel]):
                    break
          elif encoded == 0x81:
               steps[channel] = min(steps[channel] + 8, 0x58)
               channel = (channel + 1) % channels
          else:
               step_index = steps[channel]
               step = _STEP_SIZE[step_index]
               difference = step >> shift
               for bit in range(6):
                    if encoded & (1 << bit):
                         difference += step >> bit
               sample = predicted[channel]
               if encoded & 0x40:
                    sample = max(sample - difference, -32768)
               else:
                    sample = min(sample + difference, 32767)
               predicted[channel] = _int16(sample)
               if not write(predicted[channel]):
                    break
               steps[channel] = min(max(step_index + _NEXT_STEP[encoded & 0x1F], 0), 88)
     return bytes(out)
