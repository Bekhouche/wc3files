"""Minimal RGBA -> PNG encoder (stdlib zlib only, no third-party imaging dependency)."""
import struct
import zlib


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(width: int, height: int, rgba: bytes) -> bytes:
    """Encode raw RGBA8 pixel data (width*height*4 bytes, row-major, no padding) as a PNG."""
    if len(rgba) != width * height * 4:
        raise ValueError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')

    stride = width * 4
    raw = bytearray((stride + 1) * height)
    for y in range(height):
        row_start = y * (stride + 1)
        raw[row_start] = 0  # filter type 0 (none)
        raw[row_start + 1:row_start + 1 + stride] = rgba[y * stride:(y + 1) * stride]

    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)  # 8-bit/channel RGBA, no interlace
    idat = zlib.compress(bytes(raw), 9)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', ihdr) + _chunk(b'IDAT', idat) + _chunk(b'IEND', b'')


def write_png_gray(width: int, height: int, values, bit_depth: int = 16) -> bytes:
    """Encode `values` (width*height samples, row-major from the top row) as
    a grayscale PNG of 8 or 16 bits per pixel. A 16-bit PNG keeps any value
    from 0 to 65535 exactly, such as terrain heights."""
    if bit_depth not in (8, 16):
        raise ValueError(f'bit depth must be 8 or 16, not {bit_depth}')
    if len(values) != width * height:
        raise ValueError(f'expected {width * height} values, got {len(values)}')
    top = (1 << bit_depth) - 1
    if values and (min(values) < 0 or max(values) > top):
        raise ValueError(f'a {bit_depth}-bit PNG holds values from 0 to {top}')
    code = '>%dH' % width if bit_depth == 16 else '%dB' % width
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (none)
        raw += struct.pack(code, *values[y * width:(y + 1) * width])
    ihdr = struct.pack('>IIBBBBB', width, height, bit_depth, 0, 0, 0, 0)
    idat = zlib.compress(bytes(raw), 9)
    return b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', ihdr) + _chunk(b'IDAT', idat) + _chunk(b'IEND', b'')
