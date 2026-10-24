"""BC1/BC2/BC3 (DXT1/DXT3/DXT5) block decompression, shared by dds_file.py."""
import struct


def _rgb565_to_rgb888(v):
    r5, g6, b5 = (v >> 11) & 0x1F, (v >> 5) & 0x3F, v & 0x1F
    return (r5 * 255 + 15) // 31, (g6 * 255 + 31) // 63, (b5 * 255 + 15) // 31


def _decode_color_block(data, offset, always_four_color):
    c0, c1 = struct.unpack_from('<HH', data, offset)
    idx = struct.unpack_from('<I', data, offset + 4)[0]
    r0, g0, b0 = _rgb565_to_rgb888(c0)
    r1, g1, b1 = _rgb565_to_rgb888(c1)
    colors = [(r0, g0, b0, 255), (r1, g1, b1, 255)]
    if always_four_color or c0 > c1:
        colors.append(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255))
        colors.append(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255))
    else:
        colors.append(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255))
        colors.append((0, 0, 0, 0))
    return [colors[(idx >> (2 * i)) & 0x3] for i in range(16)]


def decode_dxt1_block(data, offset):
    return _decode_color_block(data, offset, always_four_color=False)


def decode_dxt3_block(data, offset):
    alpha_bits = struct.unpack_from('<Q', data, offset)[0]
    alphas = [((alpha_bits >> (4 * i)) & 0xF) * 17 for i in range(16)]
    colors = _decode_color_block(data, offset + 8, always_four_color=True)
    return [(r, g, b, alphas[i]) for i, (r, g, b, _a) in enumerate(colors)]


def decode_dxt5_block(data, offset):
    a0, a1 = data[offset], data[offset + 1]
    alpha_idx = int.from_bytes(data[offset + 2:offset + 8], 'little')
    if a0 > a1:
        lut = [a0, a1,
               (6 * a0 + 1 * a1) // 7, (5 * a0 + 2 * a1) // 7, (4 * a0 + 3 * a1) // 7,
               (3 * a0 + 4 * a1) // 7, (2 * a0 + 5 * a1) // 7, (1 * a0 + 6 * a1) // 7]
    else:
        lut = [a0, a1,
               (4 * a0 + 1 * a1) // 5, (3 * a0 + 2 * a1) // 5, (2 * a0 + 3 * a1) // 5,
               (1 * a0 + 4 * a1) // 5, 0, 255]
    alphas = [lut[(alpha_idx >> (3 * i)) & 0x7] for i in range(16)]
    colors = _decode_color_block(data, offset + 8, always_four_color=True)
    return [(r, g, b, alphas[i]) for i, (r, g, b, _a) in enumerate(colors)]


_DECODERS = {b'DXT1': (8, decode_dxt1_block), b'DXT3': (16, decode_dxt3_block),
             b'DXT5': (16, decode_dxt5_block)}


def _rgb888_to_rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _encode_color_block(pixels, allow_punchthrough):
    """pixels: 16 (r,g,b,a) tuples. Range-fit encoder: picks the min/max-luminance
    pixels as the two endpoint colors, then assigns each pixel its nearest of the 4
    resulting palette colors - a standard, fast (not optimal) DXT color encoder."""
    lum = [0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in pixels]
    c_hi = pixels[lum.index(max(lum))][:3]
    c_lo = pixels[lum.index(min(lum))][:3]
    v0, v1 = _rgb888_to_rgb565(*c_hi), _rgb888_to_rgb565(*c_lo)

    has_transparent = allow_punchthrough and any(p[3] < 128 for p in pixels)
    if has_transparent:
        if v0 > v1:
            v0, v1 = v1, v0
    elif v0 <= v1:
        v0, v1 = v1, v0

    r0, g0, b0 = _rgb565_to_rgb888(v0)
    r1, g1, b1 = _rgb565_to_rgb888(v1)
    if has_transparent:
        palette = [(r0, g0, b0), (r1, g1, b1), ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2), None]
    else:
        palette = [(r0, g0, b0), (r1, g1, b1),
                   ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
                   ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3)]

    indices = 0
    for i, p in enumerate(pixels):
        if has_transparent and p[3] < 128:
            sel = 3
        else:
            sel, best_d = 0, None
            for k, pc in enumerate(palette):
                if pc is None:
                    continue
                d = (p[0] - pc[0]) ** 2 + (p[1] - pc[1]) ** 2 + (p[2] - pc[2]) ** 2
                if best_d is None or d < best_d:
                    best_d, sel = d, k
        indices |= sel << (2 * i)
    return struct.pack('<HHI', v0, v1, indices)


def _encode_dxt3_alpha_block(alphas):
    """DXT3's alpha is explicit, not interpolated: each pixel's 8-bit alpha is simply
    truncated to its top 4 bits (0-15), packed 4 bits per pixel."""
    bits = 0
    for i, a in enumerate(alphas):
        bits |= (a >> 4) << (4 * i)
    return bits.to_bytes(8, 'little')


def _encode_alpha_block(alphas):
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        lut = [a0] * 8
    else:
        lut = [a0, a1,
               (6 * a0 + 1 * a1) // 7, (5 * a0 + 2 * a1) // 7, (4 * a0 + 3 * a1) // 7,
               (3 * a0 + 4 * a1) // 7, (2 * a0 + 5 * a1) // 7, (1 * a0 + 6 * a1) // 7]
    idx = 0
    for i, a in enumerate(alphas):
        sel, best_d = 0, None
        for k, v in enumerate(lut):
            d = abs(a - v)
            if best_d is None or d < best_d:
                best_d, sel = d, k
        idx |= sel << (3 * i)
    return bytes((a0, a1)) + idx.to_bytes(6, 'little')


def encode_dxt_image(rgba, width, height, fourcc):
    """Encode RGBA8 pixel data (width*height*4 bytes) to DXT1/DXT3/DXT5 block data.
    DXT1 uses 1-bit punch-through alpha (a pixel is either opaque or fully
    transparent) if the image has any alpha < 128 anywhere, else fully opaque
    4-color mode; DXT3 encodes explicit 4-bit-per-pixel alpha (sharp transitions,
    no interpolation - best for hard cutouts); DXT5 encodes full 8-bit interpolated
    alpha (best for smooth gradients)."""
    if fourcc not in (b'DXT1', b'DXT3', b'DXT5'):
        raise ValueError(f'unsupported DXT fourCC for encoding: {fourcc!r}')
    blocks_x, blocks_y = -(-width // 4), -(-height // 4)
    out = bytearray()
    for by in range(blocks_y):
        for bx in range(blocks_x):
            pixels = []
            for py in range(4):
                y = min(by * 4 + py, height - 1)
                row = y * width
                for px in range(4):
                    x = min(bx * 4 + px, width - 1)
                    i = (row + x) * 4
                    pixels.append((rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]))
            if fourcc == b'DXT5':
                out += _encode_alpha_block([p[3] for p in pixels])
                out += _encode_color_block(pixels, allow_punchthrough=False)
            elif fourcc == b'DXT3':
                out += _encode_dxt3_alpha_block([p[3] for p in pixels])
                out += _encode_color_block(pixels, allow_punchthrough=False)
            else:
                out += _encode_color_block(pixels, allow_punchthrough=True)
    return bytes(out)


def decode_dxt_image(data, width, height, fourcc):
    if fourcc not in _DECODERS:
        raise ValueError(f'unsupported DXT fourCC: {fourcc!r}')
    block_size, decode_block = _DECODERS[fourcc]
    blocks_x, blocks_y = -(-width // 4), -(-height // 4)
    out = bytearray(width * height * 4)
    offset = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            pixels = decode_block(data, offset)
            offset += block_size
            for py in range(min(4, height - by * 4)):
                y = by * 4 + py
                for px in range(min(4, width - bx * 4)):
                    x = bx * 4 + px
                    r, g, b, a = pixels[py * 4 + px]
                    idx = (y * width + x) * 4
                    out[idx:idx + 4] = bytes((r, g, b, a))
    return bytes(out)
