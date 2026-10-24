"""Minimal TIFF reader/writer - uncompressed, 8-bit-per-sample, chunky (interleaved)
grayscale/RGB/RGBA only. TIFF is a very general container format (arbitrary byte order,
tiled or strip-based layout, dozens of compression schemes); this module intentionally
covers only the common simple case real icon/texture source art actually uses, and
raises a clear error for anything else rather than guessing.
"""
import struct

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


class TiffError(ValueError):
    pass


def _read_ifd(data, offset, endian):
    count = struct.unpack_from(endian + 'H', data, offset)[0]
    entries = {}
    pos = offset + 2
    for _ in range(count):
        tag, typ, cnt = struct.unpack_from(endian + 'HHI', data, pos)
        val_off = pos + 8
        size = _TYPE_SIZE.get(typ, 1) * cnt
        raw = data[val_off:val_off + 4] if size <= 4 else data[
            struct.unpack_from(endian + 'I', data, val_off)[0]:
            struct.unpack_from(endian + 'I', data, val_off)[0] + size]
        fmt = {1: 'B', 3: 'H', 4: 'I'}.get(typ)
        if fmt:
            entries[tag] = list(struct.unpack_from(endian + fmt * cnt, raw, 0))
        else:
            entries[tag] = raw
        pos += 12
    next_ifd = struct.unpack_from(endian + 'I', data, pos)[0]
    return entries, next_ifd


class TiffFile:
    def __init__(self):
        self.clear()

    def clear(self):
        self.width = 0
        self.height = 0
        self._rgba = b''

    def read(self, file_or_data):
        if isinstance(file_or_data, (bytes, bytearray)):
            data = bytes(file_or_data)
        else:
            with open(file_or_data, 'rb') as fh:
                data = fh.read()
        self.clear()

        if len(data) < 8 or data[0:2] not in (b'II', b'MM'):
            raise TiffError('not a TIFF file (bad byte-order marker)')
        endian = '<' if data[0:2] == b'II' else '>'
        magic = struct.unpack_from(endian + 'H', data, 2)[0]
        if magic != 42:
            raise TiffError(f'not a TIFF file (bad magic number: {magic})')
        first_ifd = struct.unpack_from(endian + 'I', data, 4)[0]

        ifd, _next = _read_ifd(data, first_ifd, endian)

        def tag(t, default=None):
            v = ifd.get(t)
            return v[0] if v else default

        width = tag(256)
        height = tag(257)
        if width is None or height is None:
            raise TiffError('missing ImageWidth/ImageLength tag')
        compression = tag(259, 1)
        if compression != 1:
            raise TiffError(f'unsupported TIFF compression: {compression} (only 1/none is supported)')
        photometric = tag(262, 1)
        if photometric not in (0, 1, 2):
            raise TiffError(f'unsupported PhotometricInterpretation: {photometric} '
                             '(only grayscale/RGB are supported)')
        bits_per_sample = ifd.get(258, [8])
        if any(b != 8 for b in bits_per_sample):
            raise TiffError(f'unsupported BitsPerSample: {bits_per_sample} (only 8 is supported)')
        samples_per_pixel = tag(277, 1)
        planar_config = tag(284, 1)
        if planar_config != 1:
            raise TiffError('planar (non-chunky) TIFF layout is not supported')
        strip_offsets = ifd.get(273)
        strip_byte_counts = ifd.get(279)
        rows_per_strip = tag(278, height)
        if strip_offsets is None or strip_byte_counts is None:
            raise TiffError('tiled TIFF files are not supported (no StripOffsets tag)')

        pixels = bytearray()
        for off, size in zip(strip_offsets, strip_byte_counts):
            pixels += data[off:off + size]

        npix = width * height
        rgba = bytearray(npix * 4)
        if samples_per_pixel >= 4:
            for i in range(npix):
                s, d = i * samples_per_pixel, i * 4
                rgba[d:d + 4] = pixels[s:s + 4]
        elif samples_per_pixel == 3:
            for i in range(npix):
                s, d = i * 3, i * 4
                rgba[d:d + 3] = pixels[s:s + 3]
                rgba[d + 3] = 255
        else:  # grayscale
            inverted = photometric == 0
            for i in range(npix):
                g = pixels[i]
                if inverted:
                    g = 255 - g
                d = i * 4
                rgba[d] = rgba[d + 1] = rgba[d + 2] = g
                rgba[d + 3] = 255

        self.width, self.height = width, height
        self._rgba = bytes(rgba)

    def get_rgba(self):
        return self._rgba

    def to_bytes(self):
        """Encode as a single-strip, uncompressed, chunky RGBA8 little-endian TIFF."""
        width, height = self.width, self.height
        pixel_data = self._rgba

        entries = [
            (256, 4, 1, width),
            (257, 4, 1, height),
            (258, 3, 4, None),   # BitsPerSample x4 - written out-of-line below
            (259, 3, 1, 1),      # Compression = none
            (262, 3, 1, 2),      # PhotometricInterpretation = RGB
            (277, 3, 1, 4),      # SamplesPerPixel
            (278, 4, 1, height),  # RowsPerStrip
            (279, 4, 1, len(pixel_data)),  # StripByteCounts
            (284, 3, 1, 1),      # PlanarConfiguration = chunky
            (338, 3, 1, 2),      # ExtraSamples = unassociated alpha
        ]
        strip_offsets_index = len(entries)
        entries.append((273, 4, 1, None))  # StripOffsets - patched in below

        header_size = 8
        ifd_offset = header_size
        ifd_size = 2 + len(entries) * 12 + 4
        out_of_line_offset = ifd_offset + ifd_size
        bits_per_sample_offset = out_of_line_offset
        pixel_offset = bits_per_sample_offset + 8

        out = bytearray()
        out += struct.pack('<2sHI', b'II', 42, ifd_offset)
        out += struct.pack('<H', len(entries))
        for tag_id, typ, count, value in entries:
            out += struct.pack('<HHI', tag_id, typ, count)
            if tag_id == 258:
                out += struct.pack('<I', bits_per_sample_offset)
            elif tag_id == 273:
                out += struct.pack('<I', pixel_offset)
            elif typ == 3:  # SHORT - occupies the first 2 bytes of the 4-byte value field
                out += struct.pack('<HH', value, 0)
            else:  # LONG - occupies the full 4-byte value field
                out += struct.pack('<I', value)
        out += struct.pack('<I', 0)  # no next IFD
        out += struct.pack('<4H', 8, 8, 8, 8)  # BitsPerSample values
        out += pixel_data
        return bytes(out)

    def write(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    @classmethod
    def from_rgba(cls, width, height, rgba):
        if len(rgba) != width * height * 4:
            raise TiffError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')
        tiff = cls()
        tiff.width, tiff.height = width, height
        tiff._rgba = bytes(rgba)
        return tiff

    def to_png_bytes(self):
        from .png_writer import write_png
        return write_png(self.width, self.height, self._rgba)

    def save_png(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_png_bytes())

    def __repr__(self):
        return f'<TiffFile {self.width}x{self.height}>'
