"""
BLP1 (Warcraft III's native icon/texture format) reader/writer, plus read-only BLP2
(the later WoW-era format) support.

Every real .blp fixture available in this repo (2708 files, pulled from real maps'
unpacked ReplaceableTextures/Textures/icon assets) is 'BLP1'. Two sub-variants exist
within BLP1 itself, both verified against that real fixture set, not assumed from a
spec alone:

- compression=1 (paletted): a 256-entry BGRA palette followed by one byte-per-pixel index
  plane per mip, plus an optional separate 8-bit alpha plane (alpha_bits/`flags`==8) - only
  0 and 8 were observed in the wild; 1/4-bit alpha (documented elsewhere for this format)
  is not exercised by any real fixture and is rejected rather than guessed at.
- compression=0 (JPEG): ~79% of real fixtures use this, including most command-card
  icons - see jpeg_decoder.py for the embedded bitstream's own quirks (4 raw BGRA planes,
  no chroma subsampling, no colorspace transform). A real multi-mip command-card icon
  (BTNMountainKing.blp, 64x64/7 mips, tests/samples/img/) revealed a genuine encoder
  quirk previously unexercised (the only other bundled BLP1 sample is a single-mip,
  paletted 8x8 fixture): below a size threshold (32x32 here), every smaller mip's
  stored chunk is byte-identical to the one before it - Blizzard's own writer reuses
  the smallest "reasonable" JPEG-encoded mip rather than re-encoding a genuinely tiny
  (2x2, 1x1) JPEG, leaving the mip-size table as the only place the true, smaller
  dimensions are recorded. `_decode_jpeg_mip` downsamples (area-average, via
  transform.py's `resize_box` - already this package's documented mipmap-downscale
  tool) whenever the decoded JPEG is larger than the declared mip size, rather than
  raising - a decoded size *smaller* than declared remains an error (never observed).

BLP2 support is a different case: no real WC3 map/fixture available anywhere in this
project uses it (it's a WoW-era format; Warcraft III always writes BLP1), so unlike
everything else in this package it is implemented from the public community-documented
spec alone and only self-tested against synthetically constructed BLP2 data, not a real
game asset. Treat it as best-effort. Its 1-bit/4-bit packed palette alpha (alpha_bits in
(1, 4)) is deliberately NOT supported - different sources disagree on the bit-packing
order and there's no real file available to settle it, so it raises rather than guesses.
"""
import struct

from .dxt import decode_dxt_image
from .jpeg_decoder import decode_planes, JpegError
from .palette import quantize_median_cut
from .png_writer import write_png
from .transform import resize_box

_HEADER_FIXED = struct.Struct('<4s6I')  # magic, compression, flags, width, height, extra, has_mipmaps
_MIP_OFFSETS_SIZES = struct.Struct('<16I')
_BLP2_HEADER_FIXED = struct.Struct('<4sIBBBBII')  # magic, type, compression, alpha_depth, alpha_type, has_mips, width, height


class BlpError(ValueError):
    pass


class BlpFile:
    def __init__(self):
        self.clear()

    def clear(self):
        self.version = None
        self.compression = None
        self.alpha_bits = None
        self.width = 0
        self.height = 0
        self.picture_type = None
        self.has_mipmaps = False
        self._blp2_alpha_type = None
        self._raw = b''
        self._mip_offsets = []
        self._mip_sizes = []
        self._palette = None
        self._jpeg_shared_header = b''
        self._rgba_cache = {}

    def read(self, file_or_data):
        if isinstance(file_or_data, (bytes, bytearray)):
            data = bytes(file_or_data)
        else:
            with open(file_or_data, 'rb') as fh:
                data = fh.read()
        self.clear()
        self._raw = data

        magic = data[0:4]
        if magic == b'BLP2':
            self._read_blp2(data)
            return
        if magic not in (b'BLP0', b'BLP1'):
            raise BlpError(f'not a BLP1/BLP2 file (bad magic {magic!r})')

        magic, compression, flags, width, height, picture_type, has_mipmaps = \
            _HEADER_FIXED.unpack_from(data, 0)
        self.version = magic.decode('ascii')
        self.compression = compression
        self.alpha_bits = flags
        self.width = width
        self.height = height
        self.picture_type = picture_type
        self.has_mipmaps = bool(has_mipmaps)

        off = _HEADER_FIXED.size
        self._mip_offsets = list(_MIP_OFFSETS_SIZES.unpack_from(data, off))
        off += _MIP_OFFSETS_SIZES.size
        self._mip_sizes = list(_MIP_OFFSETS_SIZES.unpack_from(data, off))
        off += _MIP_OFFSETS_SIZES.size

        if compression == 0:
            jpeg_header_size = struct.unpack_from('<I', data, off)[0]
            off += 4
            self._jpeg_shared_header = data[off:off + jpeg_header_size]
        elif compression == 1:
            self._palette = data[off:off + 1024]
            if len(self._palette) != 1024:
                raise BlpError('truncated BLP1 file: palette is short')
        else:
            raise BlpError(f'unrecognized BLP1 compression type: {compression}')

    def _read_blp2(self, data):
        (_magic, _type, compression, alpha_depth, alpha_type, has_mips, width, height) = \
            _BLP2_HEADER_FIXED.unpack_from(data, 0)
        self.version = 'BLP2'
        self.compression = compression
        self.alpha_bits = alpha_depth
        self._blp2_alpha_type = alpha_type
        self.width, self.height = width, height
        self.has_mipmaps = bool(has_mips)

        off = _BLP2_HEADER_FIXED.size
        self._mip_offsets = list(_MIP_OFFSETS_SIZES.unpack_from(data, off))
        off += _MIP_OFFSETS_SIZES.size
        self._mip_sizes = list(_MIP_OFFSETS_SIZES.unpack_from(data, off))
        off += _MIP_OFFSETS_SIZES.size

        if compression == 1:
            self._palette = data[off:off + 1024]
            if len(self._palette) != 1024:
                raise BlpError('truncated BLP2 file: palette is short')
            if alpha_depth not in (0, 8):
                raise BlpError(f'unsupported BLP2 palette alpha depth: {alpha_depth} bits - '
                                'only 0 and 8 are supported (see this module\'s docstring: '
                                '1/4-bit packed alpha is deliberately not guessed at)')
        elif compression not in (2, 3):
            raise BlpError(f'unrecognized BLP2 compression type: {compression}')

    def mip_count(self):
        return sum(1 for s in self._mip_sizes if s > 0)

    def aliased_mip_count(self):
        """How many of this file's declared mip levels merely point at another level's
        stored chunk instead of carrying data of their own.

        Blizzard's own BLP1 writer stops re-encoding below a size threshold and simply
        repeats the last chunk's offset for every remaining level, so the mip-size table
        is the only record of those levels' true dimensions (see this module's docstring;
        `_decode_jpeg_mip` downsamples accordingly). The bundled real command-card icon
        tests/samples/img/BTNMountainKing.blp is a concrete example: 7 declared levels,
        where level 1's 32x32 chunk is reused verbatim by levels 2-6, so 5 count as
        aliases (the level that first stores a chunk is its owner, not an alias). Purely
        diagnostic - decoding already
        accounts for this, and a level counted here still decodes at its declared size.
        """
        seen = {}
        aliased = 0
        for level, (offset, size) in enumerate(zip(self._mip_offsets, self._mip_sizes)):
            if size == 0:
                continue
            if offset in seen:
                aliased += 1
            else:
                seen[offset] = level
        return aliased

    def mip_size(self, mip=0):
        if not (0 <= mip < 16) or self._mip_sizes[mip] == 0:
            raise BlpError(f'mip level {mip} not present in this file')
        return max(1, self.width >> mip), max(1, self.height >> mip)

    def get_rgba(self, mip=0):
        """Decoded RGBA8 pixel data for one mip level: width*height*4 bytes, row-major."""
        if mip in self._rgba_cache:
            return self._rgba_cache[mip]
        w, h = self.mip_size(mip)
        chunk = self._raw[self._mip_offsets[mip]:self._mip_offsets[mip] + self._mip_sizes[mip]]
        if self.version == 'BLP2':
            rgba = self._decode_blp2_mip(chunk, w, h)
        elif self.compression == 0:
            rgba = self._decode_jpeg_mip(chunk, w, h)
        else:
            rgba = self._decode_paletted_mip(chunk, w, h)
        self._rgba_cache[mip] = rgba
        return rgba

    def _decode_blp2_mip(self, chunk, w, h):
        if self.compression == 1:
            return self._decode_paletted_mip(chunk, w, h)
        if self.compression == 3:
            n = w * h
            if len(chunk) < n * 4:
                raise BlpError('truncated BLP2 file: uncompressed mip data is short')
            out = bytearray(n * 4)
            for i in range(n):
                j = i * 4
                out[j], out[j + 1], out[j + 2], out[j + 3] = chunk[j + 2], chunk[j + 1], chunk[j], chunk[j + 3]
            return bytes(out)
        # compression == 2: DXTC
        if self.alpha_bits == 0:
            fourcc = b'DXT1'
        elif self._blp2_alpha_type == 0:
            fourcc = b'DXT1'
        elif self._blp2_alpha_type == 1:
            fourcc = b'DXT3'
        else:
            fourcc = b'DXT5'
        return decode_dxt_image(chunk, w, h, fourcc)

    def _decode_jpeg_mip(self, chunk, w, h):
        try:
            jw, jh, planes = decode_planes(self._jpeg_shared_header + chunk)
        except JpegError as exc:
            raise BlpError(f'failed to decode embedded JPEG mip data: {exc}') from exc
        if len(planes) < 3:
            raise BlpError(f'expected at least 3 planes (B,G,R) in BLP1 JPEG data, got {len(planes)}')
        b_plane, g_plane, r_plane = planes[0], planes[1], planes[2]
        a_plane = planes[3] if len(planes) > 3 else None
        n = jw * jh
        out = bytearray(n * 4)
        for i in range(n):
            j = i * 4
            out[j] = r_plane[i]
            out[j + 1] = g_plane[i]
            out[j + 2] = b_plane[i]
            out[j + 3] = a_plane[i] if a_plane is not None else 255

        if (jw, jh) != (w, h):
            # A real command-card icon (BTNMountainKing.blp, 64x64/7 mips)
            # revealed this: below some size threshold (32x32 here), every
            # smaller mip's stored chunk is byte-identical to the previous
            # one - Blizzard's own BLP1 writer apparently just reuses the
            # smallest "reasonable" JPEG-encoded mip rather than re-encoding
            # a genuinely tiny JPEG (a 2x2 or 1x1 JPEG is a degenerate case
            # anyway), leaving the *declared* mip_size table as the only
            # place the true, smaller dimensions are recorded. The engine is
            # expected to downsample the shared decode to whatever size that
            # mip actually needs - resize_box (area-average, already this
            # package's documented mipmap-downscale tool, see transform.py)
            # is exactly that operation. A decoded size *smaller* than the
            # declared mip is a different, genuine error (never observed in
            # any real fixture) - not silently guessed at.
            if jw < w or jh < h:
                raise BlpError(f'decoded JPEG size {jw}x{jh} is smaller than the declared '
                                f'mip size {w}x{h}')
            out = bytearray(resize_box(bytes(out), jw, jh, w, h))

        return bytes(out)

    def _decode_paletted_mip(self, chunk, w, h):
        n = w * h
        if len(chunk) < n:
            raise BlpError('truncated BLP1 file: paletted mip data is short')
        indices = chunk[:n]
        if self.alpha_bits == 8:
            if len(chunk) < 2 * n:
                raise BlpError('truncated BLP1 file: alpha plane is short')
            alpha = chunk[n:2 * n]
        elif self.alpha_bits == 0:
            alpha = None
        else:
            raise BlpError(f'unsupported palette alpha depth: {self.alpha_bits} bits '
                            '(only 0 and 8 were observed in real fixtures)')
        palette = self._palette
        out = bytearray(n * 4)
        for i in range(n):
            p = indices[i] * 4
            j = i * 4
            # palette entries are stored BGRA (Blizzard's usual DIB-style byte order)
            out[j] = palette[p + 2]
            out[j + 1] = palette[p + 1]
            out[j + 2] = palette[p]
            out[j + 3] = alpha[i] if alpha is not None else 255
        return bytes(out)

    def to_png_bytes(self, mip=0):
        w, h = self.mip_size(mip)
        return write_png(w, h, self.get_rgba(mip))

    def save_png(self, path, mip=0):
        with open(path, 'wb') as fh:
            fh.write(self.to_png_bytes(mip))

    @classmethod
    def from_rgba(cls, width, height, rgba, alpha='auto'):
        """Build a new single-mip, paletted (compression=1) BLP1 from raw RGBA8 pixel
        data. There is no JPEG *encoder* in this package (only a decoder) - every BLP1
        this writes uses the paletted sub-variant, which is lossless for the <=256
        distinct colors real command-card icons typically use and degrades gracefully
        (median-cut quantization) for anything with more.

        alpha: 'auto' (default) includes an 8-bit alpha plane only if the image
        actually has a non-255 alpha byte anywhere; True/False force it on/off.
        """
        if len(rgba) != width * height * 4:
            raise BlpError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')
        npix = width * height
        if alpha == 'auto':
            has_alpha = any(rgba[i * 4 + 3] != 255 for i in range(npix))
        else:
            has_alpha = bool(alpha)

        palette, indices = quantize_median_cut(rgba, width, height)

        blp = cls()
        blp.version = 'BLP1'
        blp.compression = 1
        blp.alpha_bits = 8 if has_alpha else 0
        blp.width, blp.height = width, height
        blp.picture_type = 5
        blp.has_mipmaps = False
        blp._palette = b''.join(bytes((b, g, r, 0)) for r, g, b in palette)
        alpha_plane = bytes(rgba[i * 4 + 3] for i in range(npix)) if has_alpha else b''
        blp._encoded_mip0 = indices + alpha_plane
        blp._mip_offsets = [0] * 16
        blp._mip_sizes = [len(blp._encoded_mip0)] + [0] * 15
        # Cache the *actual* quantized result (decoded from the same palette+
        # indices bytes to_bytes()/write() will serialize), not the original
        # `rgba` argument - caching the input verbatim here was a real,
        # misleading inconsistency: get_rgba() on a freshly-built object
        # would report a losslessly-perfect image even when quantize_median_cut
        # had already discarded color detail (any real image with more than
        # 256 distinct colors - true of most real command-card icons, not
        # just synthetic test data), so a caller checking get_rgba() before
        # writing could easily be fooled into thinking the write would be
        # lossless when it wouldn't be. Found via a real fixture
        # (BTNMountainKing.blp) whose from_rgba()-then-written-then-reread
        # result silently disagreed with the pre-write object's own
        # get_rgba() - see tests/test_img.py.
        blp._rgba_cache = {0: blp._decode_paletted_mip(blp._encoded_mip0, width, height)}
        return blp

    def to_bytes(self):
        if self.compression != 1:
            raise BlpError('writing is only supported for paletted (compression=1) BLP1 '
                            'data - build one with BlpFile.from_rgba(), not by mutating a '
                            'file that was read as JPEG-compressed')
        data_start = _HEADER_FIXED.size + 2 * _MIP_OFFSETS_SIZES.size + 1024
        mip_offsets = [data_start] + [0] * 15
        mip_sizes = [len(self._encoded_mip0)] + [0] * 15
        header = _HEADER_FIXED.pack(b'BLP1', 1, self.alpha_bits, self.width, self.height,
                                     self.picture_type, int(self.has_mipmaps))
        return (header + _MIP_OFFSETS_SIZES.pack(*mip_offsets)
                + _MIP_OFFSETS_SIZES.pack(*mip_sizes) + self._palette + self._encoded_mip0)

    def write(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    def __repr__(self):
        return (f'<BlpFile {self.version} {self.width}x{self.height} '
                f'compression={self.compression} mips={self.mip_count()}>')
