"""DDS reader - the classic DXT1-DXT5 (BC1-BC3) family plus uncompressed-RGB(A).

Classic (DX9-header) DDS only - a DX10 extended header (indicated by fourCC 'DX10',
used for the modern BC6H/BC7 successors, or texture arrays) is rejected explicitly
rather than mis-read: no real WC3 fixture in this repo uses one, BC6H is an HDR-only
format WC3 has no use for, and BC7 - while a real, higher-quality-than-DXT5 LDR format
- would need its own from-scratch block codec (a different, more elaborate bitstream
than any BC1-3 block) that no real WC3 asset in this repo's fixture set exercises.

DXT2/DXT4 (the premultiplied-alpha siblings of DXT3/DXT5, "virtually obsolete" per
Microsoft's own docs) share DXT3's/DXT5's exact block bitstream - only the alpha
semantics differ - so they're read here by decoding as DXT3/DXT5 and then
un-premultiplying; no real fixture uses them either, so this is unverified against
real data like the rest of this module's DXT2/4 handling, but it's cheap and correct
by construction rather than a silent gap. Writing DXT2/4 is not offered - no known
real reason to produce new premultiplied-alpha DDS files today. DXT1/DXT3/DXT5 are
all writable (see from_rgba()) - DXT3 for sharp cutout alpha, DXT5 for smooth alpha
gradients, DXT1 for no/binary alpha at the smallest size.
"""
import struct

from .dxt import decode_dxt_image, encode_dxt_image
from .png_writer import write_png
from .transform import resize_box

_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40

_SUPPORTED_FOURCC = (b'DXT1', b'DXT2', b'DXT3', b'DXT4', b'DXT5')
_PREMULTIPLIED_FOURCC = (b'DXT2', b'DXT4')
_BLOCK_LAYOUT = {b'DXT2': b'DXT3', b'DXT4': b'DXT5'}  # same bitstream as their un-premultiplied sibling


def _unpremultiply(rgba):
    out = bytearray(rgba)
    for i in range(0, len(rgba), 4):
        a = out[i + 3]
        if a in (0, 255):
            continue  # a==0: color is indeterminate anyway; a==255: no-op
        out[i] = min(255, (out[i] * 255) // a)
        out[i + 1] = min(255, (out[i + 1] * 255) // a)
        out[i + 2] = min(255, (out[i + 2] * 255) // a)
    return bytes(out)


class DdsError(ValueError):
    pass


class DdsFile:
    def __init__(self):
        self.clear()

    def clear(self):
        self.width = 0
        self.height = 0
        self.fourcc = None
        self._rgb_mode = None
        self._data = b''
        self._mip_info = []  # (offset, size, width, height)
        self._mip_data = None  # set only on a from_rgba()-built instance, for write()
        self._rgba_cache = {}

    def read(self, file_or_data):
        if isinstance(file_or_data, (bytes, bytearray)):
            data = bytes(file_or_data)
        else:
            with open(file_or_data, 'rb') as fh:
                data = fh.read()
        self.clear()

        if data[0:4] != b'DDS ':
            raise DdsError('not a DDS file (bad magic)')
        header_size = struct.unpack_from('<I', data, 4)[0]
        if header_size != 124:
            raise DdsError(f'unexpected DDS header size: {header_size} (expected 124)')

        _flags, height, width, _pitch, _depth, mip_count = struct.unpack_from('<6I', data, 8)
        pf_off = 4 + 72
        _pf_size, pf_flags, fourcc, rgb_bit_count, r_mask, g_mask, b_mask, a_mask = \
            struct.unpack_from('<II4sIIIII', data, pf_off)

        self.width, self.height = width, height
        mip_count = max(1, mip_count)

        if pf_flags & _DDPF_FOURCC:
            if fourcc == b'DX10':
                raise DdsError('DX10 extended-header DDS files are not supported')
            if fourcc not in _SUPPORTED_FOURCC:
                raise DdsError(f'unsupported DDS FourCC: {fourcc!r} (only DXT1/DXT2/DXT3/'
                                'DXT4/DXT5 and uncompressed RGB are supported)')
            self.fourcc = fourcc
        elif pf_flags & _DDPF_RGB:
            self._rgb_mode = {
                'bit_count': rgb_bit_count, 'r_mask': r_mask, 'g_mask': g_mask,
                'b_mask': b_mask, 'a_mask': a_mask if (pf_flags & _DDPF_ALPHAPIXELS) else 0,
            }
        else:
            raise DdsError('unsupported DDS pixel format (neither FourCC-compressed nor '
                            'uncompressed RGB)')

        self._data = data
        off = 4 + 124
        w, h = width, height
        for _level in range(mip_count):
            if self.fourcc:
                block_size = 8 if self.fourcc == b'DXT1' else 16
                size = -(-w // 4) * -(-h // 4) * block_size
            else:
                size = w * h * (self._rgb_mode['bit_count'] // 8)
            self._mip_info.append((off, size, w, h))
            off += size
            w, h = max(1, w // 2), max(1, h // 2)

    def mip_count(self):
        return len(self._mip_info)

    def get_rgba(self, mip=0):
        if mip in self._rgba_cache:
            return self._rgba_cache[mip]
        if not (0 <= mip < len(self._mip_info)):
            raise DdsError(f'mip level {mip} not present in this file')
        offset, size, w, h = self._mip_info[mip]
        chunk = self._data[offset:offset + size]
        if len(chunk) < size:
            raise DdsError(f'truncated DDS file: mip level {mip} is short')
        if self.fourcc:
            rgba = decode_dxt_image(chunk, w, h, _BLOCK_LAYOUT.get(self.fourcc, self.fourcc))
            if self.fourcc in _PREMULTIPLIED_FOURCC:
                rgba = _unpremultiply(rgba)
        else:
            rgba = self._decode_rgb(chunk, w, h)
        self._rgba_cache[mip] = rgba
        return rgba

    @staticmethod
    def _mask_shift_width(mask):
        if mask == 0:
            return 0, 0
        shift = 0
        m = mask
        while not (m & 1):
            m >>= 1
            shift += 1
        return shift, bin(m).count('1')

    def _decode_rgb(self, chunk, w, h):
        mode = self._rgb_mode
        bpp = mode['bit_count'] // 8
        rs, rn = self._mask_shift_width(mode['r_mask'])
        gs, gn = self._mask_shift_width(mode['g_mask'])
        bs, bn = self._mask_shift_width(mode['b_mask'])
        as_, an = self._mask_shift_width(mode['a_mask'])

        def scale(v, n):
            if n == 0:
                return 255
            maxv = (1 << n) - 1
            return (v * 255) // maxv

        out = bytearray(w * h * 4)
        for i in range(w * h):
            px = int.from_bytes(chunk[i * bpp:(i + 1) * bpp], 'little')
            j = i * 4
            out[j] = scale((px & mode['r_mask']) >> rs, rn)
            out[j + 1] = scale((px & mode['g_mask']) >> gs, gn)
            out[j + 2] = scale((px & mode['b_mask']) >> bs, bn)
            out[j + 3] = scale((px & mode['a_mask']) >> as_, an) if mode['a_mask'] else 255
        return bytes(out)

    def to_png_bytes(self, mip=0):
        _, _, w, h = self._mip_info[mip]
        return write_png(w, h, self.get_rgba(mip))

    def save_png(self, path, mip=0):
        with open(path, 'wb') as fh:
            fh.write(self.to_png_bytes(mip))

    @classmethod
    def from_rgba(cls, width, height, rgba, fourcc=b'DXT5', mipmaps=True):
        """Build a new DDS from raw RGBA8 pixel data. `fourcc` is b'DXT1' (no alpha,
        or 1-bit punch-through alpha - smaller), b'DXT3' (explicit 4-bit-per-pixel
        alpha - sharp cutout transitions, no interpolation banding), b'DXT5' (full
        8-bit interpolated alpha, the default - matches what most real WC3 texture
        fixtures use, best for smooth alpha gradients), or None for uncompressed
        32-bit A8R8G8B8 (largest, lossless, no block artifacts).

        mipmaps=True (default) generates a full mip chain via box-filter downsampling,
        matching what a real in-game 3D-used texture needs for correct LOD; pass False
        for a single-level DDS.
        """
        if len(rgba) != width * height * 4:
            raise DdsError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')
        dds = cls()
        dds.width, dds.height = width, height
        dds.fourcc = fourcc
        if fourcc is None:
            dds._rgb_mode = {'bit_count': 32, 'r_mask': 0x00FF0000, 'g_mask': 0x0000FF00,
                              'b_mask': 0x000000FF, 'a_mask': 0xFF000000}

        def encode_level(level_rgba, w, h):
            if fourcc is not None:
                return encode_dxt_image(level_rgba, w, h, fourcc)
            out = bytearray(w * h * 4)  # A8R8G8B8 -> stored little-endian as B,G,R,A per pixel
            for i in range(w * h):
                j = i * 4
                out[j] = level_rgba[j + 2]
                out[j + 1] = level_rgba[j + 1]
                out[j + 2] = level_rgba[j]
                out[j + 3] = level_rgba[j + 3]
            return bytes(out)

        mip_data = [encode_level(rgba, width, height)]
        dds._mip_info = [(-1, len(mip_data[0]), width, height)]
        dds._rgba_cache = {0: bytes(rgba)}
        if mipmaps:
            w, h, cur, level = width, height, rgba, 1
            while w > 1 or h > 1:
                nw, nh = max(1, w // 2), max(1, h // 2)
                cur = resize_box(cur, w, h, nw, nh)
                mip_data.append(encode_level(cur, nw, nh))
                dds._mip_info.append((-1, len(mip_data[-1]), nw, nh))
                dds._rgba_cache[level] = cur
                w, h, level = nw, nh, level + 1
        dds._mip_data = mip_data
        return dds

    def to_bytes(self):
        if self._mip_data is None:
            raise DdsError('writing is only supported for a DDS built with DdsFile.from_rgba()')
        mip_count = len(self._mip_data)
        is_compressed = self.fourcc is not None
        flags = 0x1 | 0x2 | 0x4 | 0x1000  # CAPS|HEIGHT|WIDTH|PIXELFORMAT
        flags |= 0x80000 if is_compressed else 0x8  # LINEARSIZE or PITCH
        caps = 0x1000  # DDSCAPS_TEXTURE
        if mip_count > 1:
            flags |= 0x20000  # DDSD_MIPMAPCOUNT
            caps |= 0x8 | 0x400000  # DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

        pitch_or_linear_size = len(self._mip_data[0]) if is_compressed else self.width * 4
        header = struct.pack('<7I', 124, flags, self.height, self.width,
                              pitch_or_linear_size, 0, mip_count)
        header += b'\x00' * 44  # dwReserved1
        if is_compressed:
            pixel_format = struct.pack('<II4sIIIII', 32, 0x4, self.fourcc, 0, 0, 0, 0, 0)
        else:
            m = self._rgb_mode
            pixel_format = struct.pack('<II4sIIIII', 32, _DDPF_RGB | _DDPF_ALPHAPIXELS, b'\x00\x00\x00\x00',
                                        m['bit_count'], m['r_mask'], m['g_mask'], m['b_mask'], m['a_mask'])
        caps_block = struct.pack('<5I', caps, 0, 0, 0, 0)
        return b'DDS ' + header + pixel_format + caps_block + b''.join(self._mip_data)

    def write(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    def __repr__(self):
        fmt = self.fourcc.decode('ascii') if self.fourcc else f"RGB{self._rgb_mode['bit_count']}"
        return f'<DdsFile {self.width}x{self.height} format={fmt} mips={self.mip_count()}>'
