"""Minimal PNG decoder (stdlib zlib only) - the read-side counterpart to png_writer.py.

Supports the common case for icon/texture source art: non-interlaced, 8-bit-per-channel
grayscale/RGB/RGBA/palette+alpha. 16-bit-per-channel and Adam7-interlaced PNGs are
rejected with a clear error rather than silently mis-decoded - neither is a realistic
shape for hand-authored icon source art, and supporting them would roughly double this
module's size for no real benefit here.
"""
import struct
import zlib

_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}  # color type -> samples per pixel


class PngError(ValueError):
    pass


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw, width, height, bpp, stride):
    out = bytearray(height * stride)
    prev_row = bytes(stride)
    pos = 0
    for y in range(height):
        filter_type = raw[pos]
        pos += 1
        row = bytearray(raw[pos:pos + stride])
        pos += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + (a + prev_row[i]) // 2) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise PngError(f'unknown PNG filter type: {filter_type}')
        out[y * stride:(y + 1) * stride] = row
        prev_row = row
    return bytes(out)


def read_png(file_or_data):
    """Decode a PNG into (width, height, rgba_bytes)."""
    if isinstance(file_or_data, (bytes, bytearray)):
        data = bytes(file_or_data)
    else:
        with open(file_or_data, 'rb') as fh:
            data = fh.read()

    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise PngError('not a PNG file (bad signature)')

    pos = 8
    width = height = bit_depth = color_type = interlace = None
    palette = None
    trns = None
    idat_parts = []

    while pos < len(data):
        length = struct.unpack_from('>I', data, pos)[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # length(4) + tag(4) + payload + crc(4)

        if tag == b'IHDR':
            width, height, bit_depth, color_type, _comp, _filt, interlace = \
                struct.unpack('>IIBBBBB', payload)
        elif tag == b'PLTE':
            palette = payload
        elif tag == b'tRNS':
            trns = payload
        elif tag == b'IDAT':
            idat_parts.append(payload)
        elif tag == b'IEND':
            break

    if width is None:
        raise PngError('missing IHDR chunk')
    if bit_depth != 8:
        raise PngError(f'unsupported PNG bit depth: {bit_depth} (only 8 is supported)')
    if interlace != 0:
        raise PngError('interlaced (Adam7) PNGs are not supported')
    if color_type not in _COLOR_CHANNELS:
        raise PngError(f'unsupported PNG color type: {color_type}')

    channels = _COLOR_CHANNELS[color_type]
    stride = width * channels
    raw = zlib.decompress(b''.join(idat_parts))
    pixels = _unfilter(raw, width, height, channels, stride)

    npix = width * height
    rgba = bytearray(npix * 4)
    if color_type == 6:  # RGBA
        rgba[:] = pixels
    elif color_type == 2:  # RGB
        for i in range(npix):
            s, d = i * 3, i * 4
            rgba[d:d + 3] = pixels[s:s + 3]
            rgba[d + 3] = 255
    elif color_type == 0:  # grayscale
        for i in range(npix):
            g = pixels[i]
            d = i * 4
            rgba[d] = rgba[d + 1] = rgba[d + 2] = g
            rgba[d + 3] = 255
    elif color_type == 4:  # grayscale + alpha
        for i in range(npix):
            g, a = pixels[i * 2], pixels[i * 2 + 1]
            d = i * 4
            rgba[d] = rgba[d + 1] = rgba[d + 2] = g
            rgba[d + 3] = a
    elif color_type == 3:  # palette (+ optional tRNS alpha)
        if palette is None:
            raise PngError('palette color type with no PLTE chunk')
        for i in range(npix):
            idx = pixels[i]
            p = idx * 3
            d = i * 4
            rgba[d:d + 3] = palette[p:p + 3]
            rgba[d + 3] = trns[idx] if trns is not None and idx < len(trns) else 255

    return width, height, bytes(rgba)
