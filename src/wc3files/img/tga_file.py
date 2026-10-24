"""TGA (Truevision) reader/writer - the common uncompressed interchange format used by
WC3 icon/texture workflows (e.g. the classic Icon_Border.TGA overlay)."""
import struct


class TgaError(ValueError):
    pass

_HEADER = struct.Struct('<BBBHHBHHHHBB')


class TgaFile:
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

        if len(data) < 18:
            raise TgaError('not a TGA file (too short for a header)')
        (id_len, color_map_type, image_type, _cm_first, _cm_len, _cm_entry_size,
         _x_origin, _y_origin, width, height, pixel_depth, image_descriptor) = \
            _HEADER.unpack_from(data, 0)

        if color_map_type != 0:
            raise TgaError('color-mapped TGA files are not supported')
        if image_type not in (2, 10):
            raise TgaError(f'unsupported TGA image type: {image_type} '
                            '(only 2/uncompressed-truecolor and 10/RLE-truecolor are supported)')
        if pixel_depth not in (24, 32):
            raise TgaError(f'unsupported TGA pixel depth: {pixel_depth} (only 24/32 are supported)')

        off = 18 + id_len
        bpp = pixel_depth // 8
        npix = width * height
        pixels = bytearray(npix * bpp)

        if image_type == 2:
            chunk = data[off:off + npix * bpp]
            if len(chunk) < npix * bpp:
                raise TgaError('truncated TGA file: pixel data is short')
            pixels[:] = chunk
        else:  # RLE (image type 10)
            pos = off
            out_pos = 0
            while out_pos < len(pixels):
                if pos >= len(data):
                    raise TgaError('truncated TGA file: RLE stream ends early')
                packet = data[pos]
                pos += 1
                count = (packet & 0x7F) + 1
                if packet & 0x80:  # run-length packet: one pixel repeated `count` times
                    px = data[pos:pos + bpp]
                    pos += bpp
                    for _ in range(count):
                        pixels[out_pos:out_pos + bpp] = px
                        out_pos += bpp
                else:  # raw packet: `count` distinct pixels
                    n = count * bpp
                    pixels[out_pos:out_pos + n] = data[pos:pos + n]
                    pos += n
                    out_pos += n

        rgba = bytearray(npix * 4)
        for i in range(npix):
            j = i * bpp
            k = i * 4
            rgba[k] = pixels[j + 2]      # R
            rgba[k + 1] = pixels[j + 1]  # G
            rgba[k + 2] = pixels[j]      # B
            rgba[k + 3] = pixels[j + 3] if bpp == 4 else 255

        top_to_bottom = bool(image_descriptor & 0x20)
        if not top_to_bottom:
            flipped = bytearray(npix * 4)
            stride = width * 4
            for row in range(height):
                src = (height - 1 - row) * stride
                dst = row * stride
                flipped[dst:dst + stride] = rgba[src:src + stride]
            rgba = flipped

        self.width, self.height = width, height
        self._rgba = bytes(rgba)

    def get_rgba(self):
        return self._rgba

    def to_bytes(self):
        """Encode as an uncompressed 32-bit top-to-bottom TGA."""
        header = _HEADER.pack(0, 0, 2, 0, 0, 0, 0, 0, self.width, self.height, 32, 0x20)
        npix = self.width * self.height
        pixels = bytearray(npix * 4)
        for i in range(npix):
            j = i * 4
            pixels[j] = self._rgba[j + 2]      # B
            pixels[j + 1] = self._rgba[j + 1]  # G
            pixels[j + 2] = self._rgba[j]      # R
            pixels[j + 3] = self._rgba[j + 3]  # A
        return header + bytes(pixels)

    def write(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    @classmethod
    def from_rgba(cls, width, height, rgba):
        if len(rgba) != width * height * 4:
            raise TgaError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')
        tga = cls()
        tga.width, tga.height = width, height
        tga._rgba = bytes(rgba)
        return tga

    def to_png_bytes(self):
        from .png_writer import write_png
        return write_png(self.width, self.height, self._rgba)

    def save_png(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_png_bytes())

    def __repr__(self):
        return f'<TgaFile {self.width}x{self.height}>'
