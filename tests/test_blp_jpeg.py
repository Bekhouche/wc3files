import io
import os
import random
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import _helpers
from wc3files.cli.main import main
from wc3files.img import BlpError, BlpFile, write_png
from wc3files.img.jpeg_decoder import ZIGZAG, decode_planes
from wc3files.img.jpeg_encoder import encode_chunk, quant_table, shared_header


def _icon():
    blp = BlpFile()
    blp.read(_helpers.sample_path('BTNMountainKing.blp'))
    return blp


def _noise(width, height, seed=1):
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(width * height * 4))


def _max_error(a, b, channels=4):
    return max(abs(x - y) for i, (x, y) in enumerate(zip(a, b)) if i % 4 < channels)


class TestJpegEncoder(unittest.TestCase):
    def test_quality_68_is_the_game_icons_table(self):
        header = _icon()._jpeg_shared_header
        stored = header[7:7 + 64]       # SOI, DQT marker and length, table id
        natural = [0] * 64
        for i in range(64):
            natural[ZIGZAG[i]] = stored[i]
        self.assertEqual(quant_table(68), natural)

    def test_split_like_the_game(self):
        header = shared_header(68)
        game = _icon()._jpeg_shared_header
        # Same markers in the same places, and the same tables at quality 68.
        self.assertEqual(header, game)
        chunk = encode_chunk(64, 64, [bytes(64 * 64)] * 4, 68)
        raw = _icon()._raw
        first = raw[_icon()._mip_offsets[0]:][:12]
        self.assertEqual(chunk[:12], first)       # height, width, 4 components, ids 0-3
        self.assertTrue(chunk.endswith(b'\xff\xd9'))

    def test_decodes_back_at_any_size(self):
        for width, height in ((64, 64), (37, 21), (8, 1), (1, 1)):
            planes = [bytes((x * 7 + y * 13 + c * 50) % 256 for y in range(height)
                            for x in range(width)) for c in range(4)]
            jw, jh, back = decode_planes(shared_header(100) + encode_chunk(width, height,
                                                                          planes, 100))
            self.assertEqual((jw, jh), (width, height))
            for c in range(4):
                self.assertLessEqual(max(abs(a - b) for a, b in zip(planes[c], back[c])), 2)

    @staticmethod
    def _mean_error(width, height, rgba, quality):
        blp = BlpFile.from_rgba(width, height, rgba, compression='jpeg', quality=quality)
        back = BlpFile()
        back.read(blp.to_bytes())
        return sum(abs(a - b) for a, b in zip(back.get_rgba(0), rgba)) / len(rgba)

    def test_error_falls_with_quality(self):
        # A smooth picture that was never a JPEG.
        rgba = bytes(v for y in range(48) for x in range(48)
                     for v in ((x * 5) % 256, (y * 5) % 256, (x * y) % 256, 255))
        errors = [self._mean_error(48, 48, rgba, q) for q in (30, 68, 95)]
        self.assertEqual(errors, sorted(errors, reverse=True))
        self.assertLess(errors[-1], 1.0)

    def test_the_game_icon_at_its_own_quality(self):
        # The icon was written at quality 68: encoding it again at 68 falls on
        # the same grid of values and changes it less than quality 95 does,
        # which needs the same transform and rounding as the game's encoder.
        rgba = _icon().get_rgba(0)
        self.assertLess(self._mean_error(64, 64, rgba, 68),
                        self._mean_error(64, 64, rgba, 95) / 2)

    def test_reference_decoder(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Pillow is not installed')
        blp = BlpFile.from_rgba(37, 21, _noise(37, 21), compression='jpeg', quality=75,
                                alpha=True)
        for chunk in blp._encoded_mips[:3]:
            stream = blp._jpeg_shared_header + chunk
            _, _, ours = decode_planes(stream)
            image = Image.open(io.BytesIO(stream))
            image.load()
            self.assertEqual(image.mode, 'CMYK')
            # Pillow takes a four-component JPEG as inverted CMYK, as it
            # does the game's own BLP streams.
            for c, band in enumerate(image.split()):
                theirs = bytes(255 - v for v in band.tobytes())
                self.assertLessEqual(max(abs(a - b) for a, b in zip(ours[c], theirs)), 1)


class TestBlpJpeg(unittest.TestCase):
    def test_file_layout_and_mips(self):
        rgba = _icon().get_rgba(0)
        blp = BlpFile.from_rgba(64, 64, rgba, compression='jpeg', quality=68)
        data = blp.to_bytes()
        magic, compression, alpha_bits, width, height, kind, mips = struct.unpack_from(
            '<4s6I', data)
        self.assertEqual((magic, compression, alpha_bits, width, height, kind, mips),
                         (b'BLP1', 0, 0, 64, 64, 5, 1))
        back = BlpFile()
        back.read(data)
        self.assertEqual(back.mip_count(), 7)
        self.assertEqual([back.mip_size(m) for m in range(7)],
                         [(64 >> m, 64 >> m) for m in range(7)])
        self.assertEqual(back.get_rgba(6), blp.get_rgba(6))
        single = BlpFile.from_rgba(64, 64, rgba, compression='jpeg', mipmaps=False)
        self.assertEqual(single.mip_count(), 1)

    def test_alpha(self):
        rgba = bytearray(_noise(16, 16))
        opaque = bytes(v if i % 4 != 3 else 255 for i, v in enumerate(rgba))
        self.assertEqual(BlpFile.from_rgba(16, 16, opaque, compression='jpeg').alpha_bits, 0)
        blp = BlpFile.from_rgba(16, 16, bytes(rgba), compression='jpeg', quality=100)
        self.assertEqual(blp.alpha_bits, 8)
        back = BlpFile()
        back.read(blp.to_bytes())
        self.assertLessEqual(_max_error(back.get_rgba(0), rgba), 2)

    def test_cache_is_what_was_written(self):
        rgba = _noise(24, 24)
        blp = BlpFile.from_rgba(24, 24, rgba, compression='jpeg', quality=40)
        back = BlpFile()
        back.read(blp.to_bytes())
        self.assertEqual(blp.get_rgba(0), back.get_rgba(0))
        self.assertNotEqual(blp.get_rgba(0), rgba)

    def test_errors(self):
        with self.assertRaisesRegex(BlpError, 'quality'):
            BlpFile.from_rgba(1, 1, b'\0\0\0\xff', compression='jpeg', quality=0)
        with self.assertRaisesRegex(BlpError, "'palette' or 'jpeg'"):
            BlpFile.from_rgba(1, 1, b'\0\0\0\xff', compression='dxt')

    def test_cli(self):
        rgba = _icon().get_rgba(0)
        with tempfile.TemporaryDirectory() as tmp:
            png = os.path.join(tmp, 'in.png')
            with open(png, 'wb') as handle:
                handle.write(write_png(64, 64, rgba))
            out = os.path.join(tmp, 'out.blp')
            self.assertEqual(main(['img-convert', png, out, '--blp-jpeg', '68']), 0)
            blp = BlpFile()
            blp.read(out)
            self.assertEqual((blp.compression, blp.mip_count()), (0, 7))
            self.assertEqual(main(['img-convert', png, out]), 0)
            blp.read(out)
            self.assertEqual(blp.compression, 1)


if __name__ == '__main__':
    unittest.main()
