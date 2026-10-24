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
