"""BMP (Windows Bitmap) reader/writer - uncompressed 8/24/32-bit only (BI_RGB)."""
import struct

_FILE_HEADER = struct.Struct('<2sIHHI')  # bfType, bfSize, reserved1, reserved2, bfOffBits
_INFO_HEADER = struct.Struct('<IiiHHIIiiII')  # BITMAPINFOHEADER (40 bytes)


class BmpError(ValueError):
    pass


class BmpFile:
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

        if len(data) < 14 or data[0:2] != b'BM':
            raise BmpError('not a BMP file (bad signature)')
        _sig, _size, _r1, _r2, off_bits = _FILE_HEADER.unpack_from(data, 0)

        (bi_size, width, height, _planes, bit_count, compression, _size_image,
         _xppm, _yppm, clr_used, _clr_important) = _INFO_HEADER.unpack_from(data, 14)
        if bi_size != 40:
            raise BmpError(f'unsupported BMP info-header size: {bi_size} (only the 40-byte '
                            'BITMAPINFOHEADER is supported)')
        if compression != 0:
            raise BmpError(f'unsupported BMP compression: {compression} (only BI_RGB/'
                            'uncompressed is supported)')
        if bit_count not in (8, 24, 32):
            raise BmpError(f'unsupported BMP bit depth: {bit_count} (only 8/24/32 are supported)')

        top_down = height < 0
        height = abs(height)

        palette = None
        pixel_start = 14 + bi_size
        if bit_count == 8:
            n_colors = clr_used or 256
            palette = data[pixel_start:pixel_start + n_colors * 4]
            pixel_start += n_colors * 4
        pixel_start = off_bits if off_bits else pixel_start

        bpp = bit_count // 8 if bit_count != 8 else 1
        row_size = ((bit_count * width + 31) // 32) * 4
        npix = width * height
        rgba = bytearray(npix * 4)

        for row in range(height):
            src_y = row if top_down else (height - 1 - row)
            row_off = pixel_start + src_y * row_size
            row_data = data[row_off:row_off + row_size]
            dst_row = row * width * 4
            if bit_count == 32:
                for x in range(width):
                    si, di = x * 4, dst_row + x * 4
                    rgba[di] = row_data[si + 2]
                    rgba[di + 1] = row_data[si + 1]
                    rgba[di + 2] = row_data[si]
                    rgba[di + 3] = row_data[si + 3]
            elif bit_count == 24:
                for x in range(width):
                    si, di = x * 3, dst_row + x * 4
                    rgba[di] = row_data[si + 2]
                    rgba[di + 1] = row_data[si + 1]
                    rgba[di + 2] = row_data[si]
                    rgba[di + 3] = 255
            else:  # 8-bit palette
                for x in range(width):
                    idx = row_data[x] * 4
                    di = dst_row + x * 4
                    rgba[di] = palette[idx + 2]
                    rgba[di + 1] = palette[idx + 1]
                    rgba[di + 2] = palette[idx]
                    rgba[di + 3] = 255

        self.width, self.height = width, height
        self._rgba = bytes(rgba)

    def get_rgba(self):
        return self._rgba

    def to_bytes(self):
        """Encode as an uncompressed 32-bit bottom-up BGRA BMP."""
        width, height = self.width, self.height
        row_size = width * 4
        pixel_data = bytearray(row_size * height)
        for row in range(height):
            src_row = (height - 1 - row) * row_size
            dst_row = row * row_size
            for x in range(width):
                si, di = src_row + x * 4, dst_row + x * 4
                pixel_data[di] = self._rgba[si + 2]
                pixel_data[di + 1] = self._rgba[si + 1]
                pixel_data[di + 2] = self._rgba[si]
                pixel_data[di + 3] = self._rgba[si + 3]

        off_bits = 14 + 40
        file_size = off_bits + len(pixel_data)
        file_header = _FILE_HEADER.pack(b'BM', file_size, 0, 0, off_bits)
        info_header = _INFO_HEADER.pack(40, width, height, 1, 32, 0, len(pixel_data), 0, 0, 0, 0)
        return file_header + info_header + bytes(pixel_data)

    def write(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_bytes())

    @classmethod
    def from_rgba(cls, width, height, rgba):
        if len(rgba) != width * height * 4:
            raise BmpError(f'expected {width * height * 4} bytes of RGBA data, got {len(rgba)}')
        bmp = cls()
        bmp.width, bmp.height = width, height
        bmp._rgba = bytes(rgba)
        return bmp

    def to_png_bytes(self):
        from .png_writer import write_png
        return write_png(self.width, self.height, self._rgba)

    def save_png(self, path):
        with open(path, 'wb') as fh:
            fh.write(self.to_png_bytes())

    def __repr__(self):
        return f'<BmpFile {self.width}x{self.height}>'
