"""
A baseline JPEG encoder for BLP1's JPEG mipmaps (#261), the write-side
counterpart of jpeg_decoder.py.

BLP1 stores its JPEG data split in two, which this writes the same way as the
game's own files (checked on BTNMountainKing.blp):

- a header shared by every mip: SOI, one quantization table, the standard
  DC and AC Huffman tables of the JPEG specification (Annex K, luminance),
  and the start of the SOF0 segment up to its precision byte;
- each mip's chunk: the rest of SOF0 (height, width, and four components,
  ids 0 to 3, sampled 1x1, all on table 0), the scan header, the entropy
  coded data and EOI.

The four components are the image's blue, green, red and alpha planes as
they are: no colour transform and no subsampling, as jpeg_decoder.py reads
them. One quantization table, the specification's luminance table scaled by
`quality` the way the IJG library does, serves all four.
"""
import math
import struct

from .jpeg_decoder import ZIGZAG

# JPEG specification, Annex K, table K.1.
_LUMINANCE_QUANT = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99)

# Annex K.3: the luminance DC and AC tables, as (bits, values).
_DC_BITS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
_DC_VALUES = tuple(range(12))
_AC_BITS = (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D)
_AC_VALUES = (
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06, 0x13, 0x51,
    0x61, 0x07, 0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
    0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0A, 0x16, 0x17, 0x18,
    0x19, 0x1A, 0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3A, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55, 0x56, 0x57,
    0x58, 0x59, 0x5A, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
    0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x92,
    0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7,
    0xA8, 0xA9, 0xAA, 0xB2, 0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA, 0xC2, 0xC3,
    0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9, 0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
    0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2,
    0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA)


def _codes(bits, values):
    """{value: (code, length)} of a canonical Huffman table."""
    table, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[values[k]] = (code, length)
            code += 1
            k += 1
        code <<= 1
    return table


_DC_CODES = _codes(_DC_BITS, _DC_VALUES)
_AC_CODES = _codes(_AC_BITS, _AC_VALUES)

# The DCT as an 8x8 matrix: row u holds c(u) cos((2x+1) u pi / 16) / 2.
_DCT = [[(math.sqrt(0.5) if u == 0 else 1.0) * math.cos((2 * x + 1) * u * math.pi / 16) / 2
         for x in range(8)] for u in range(8)]


def quant_table(quality):
    """The luminance table scaled for `quality` (1 to 100) as the IJG
    library does, in natural (row-major) order."""
    quality = max(1, min(100, int(quality)))
    scale = 5000 / quality if quality < 50 else 200 - quality * 2
    return [max(1, min(255, (q * scale + 50) // 100)) for q in _LUMINANCE_QUANT]


def _segment(marker, payload):
    return bytes((0xFF, marker)) + struct.pack('>H', len(payload) + 2) + payload


def shared_header(quality):
    """The bytes BLP1 keeps once for all mips: up to SOF0's precision byte."""
    table = quant_table(quality)
    dqt = bytes([0]) + bytes(int(table[ZIGZAG[i]]) for i in range(64))
    dht_dc = bytes([0x00]) + bytes(_DC_BITS) + bytes(_DC_VALUES)
    dht_ac = bytes([0x10]) + bytes(_AC_BITS) + bytes(_AC_VALUES)
    # SOF0 is 8 + 3 * 4 bytes long; only its marker, length and precision
    # are in the shared part.
    return (b'\xFF\xD8' + _segment(0xDB, dqt) + _segment(0xC4, dht_dc)
            + _segment(0xC4, dht_ac) + b'\xFF\xC0' + struct.pack('>HB', 8 + 3 * 4, 8))


class _Bits:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.count = 0

    def write(self, code, length):
        self.acc = (self.acc << length) | code
        self.count += length
        while self.count >= 8:
            self.count -= 8
            byte = (self.acc >> self.count) & 0xFF
            self.out.append(byte)
            if byte == 0xFF:
                self.out.append(0)          # byte stuffing
        self.acc &= (1 << self.count) - 1

    def flush(self):
        if self.count:
            self.write((1 << (8 - self.count)) - 1, 8 - self.count)    # pad with ones
        return bytes(self.out)


def _magnitude(value):
    """(size category, bits) of a coefficient, as JPEG codes it."""
    if value == 0:
        return 0, 0
    size = abs(value).bit_length()
    return size, value if value > 0 else value + (1 << size) - 1


def _block(plane, width, height, bx, by, table):
    """The quantized coefficients of the 8x8 block at (bx, by), edges
    repeated past the image, in zigzag order."""
    rows = []
    for y in range(8):
        row_start = min(by + y, height - 1) * width
        rows.append([plane[row_start + min(bx + x, width - 1)] - 128 for x in range(8)])
    # Rows, then columns.
    tmp = [[sum(c * v for c, v in zip(_DCT[u], row)) for u in range(8)] for row in rows]
    coeffs = [0.0] * 64
    for u in range(8):
        column = [tmp[y][u] for y in range(8)]
        for v in range(8):
            coeffs[v * 8 + u] = sum(c * x for c, x in zip(_DCT[v], column))
    return [int(round(coeffs[ZIGZAG[i]] / table[ZIGZAG[i]])) for i in range(64)]


def encode_chunk(width, height, planes, quality):
    """One mip's chunk: the rest of SOF0, the scan and EOI, for `planes`
    (four planes of width * height bytes: blue, green, red, alpha)."""
    if len(planes) != 4 or any(len(p) != width * height for p in planes):
        raise ValueError(f'expected four planes of {width * height} bytes')
    table = quant_table(quality)
    sof_rest = struct.pack('>HHB', height, width, 4) + b''.join(
        bytes((c, 0x11, 0)) for c in range(4))
    sos = _segment(0xDA, bytes([4]) + b''.join(bytes((c, 0x00)) for c in range(4))
                   + bytes((0, 63, 0)))
    bits = _Bits()
    previous = [0, 0, 0, 0]
    for by in range(0, height, 8):
        for bx in range(0, width, 8):
            for c in range(4):
                block = _block(planes[c], width, height, bx, by, table)
                size, value = _magnitude(block[0] - previous[c])
                previous[c] = block[0]
                code, length = _DC_CODES[size]
                bits.write(code, length)
                if size:
                    bits.write(value, size)
                run = 0
                for k in range(1, 64):
                    coefficient = block[k]
                    if coefficient == 0:
                        run += 1
                        continue
                    while run > 15:
                        code, length = _AC_CODES[0xF0]          # sixteen zeros
                        bits.write(code, length)
                        run -= 16
                    size, value = _magnitude(coefficient)
                    code, length = _AC_CODES[(run << 4) | size]
                    bits.write(code, length)
                    bits.write(value, size)
                    run = 0
                if run:
                    code, length = _AC_CODES[0x00]              # end of block
                    bits.write(code, length)
    return sof_rest + sos + bits.flush() + b'\xFF\xD9'
