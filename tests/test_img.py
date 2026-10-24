"""Real tests for wc3files.img: every bundled image format sample decodes back
to the exact RGBA buffer it was built from (DDS/DXT5 is lossy block
compression, so it's checked for shape/plausibility rather than exact bytes;
every other format here is lossless)."""
import unittest

import _helpers
from wc3files.img import (
    BlpFile, DdsFile, TgaFile, read_png,
    resize_nearest, resize_box, composite_over, make_disabled_variant,
)
from wc3files.img.bmp_file import BmpFile
from wc3files.img.tiff_file import TiffFile

W, H = 8, 8
EXPECTED_RGBA = bytes(
    byte
    for y in range(H)
    for x in range(W)
    for byte in (x * 32 % 256, y * 32 % 256, (x + y) * 16 % 256, 255)
)


class TestLosslessFormats(unittest.TestCase):
    def test_blp(self):
        blp = BlpFile()
        blp.read(_helpers.sample_path('sample.blp'))
        self.assertEqual((blp.width, blp.height), (W, H))
        self.assertEqual(blp.get_rgba(0), EXPECTED_RGBA)

    def test_bmp(self):
        bmp = BmpFile()
        bmp.read(_helpers.sample_path('sample.bmp'))
        self.assertEqual(bmp.get_rgba(), EXPECTED_RGBA)

    def test_tiff(self):
        tif = TiffFile()
        tif.read(_helpers.sample_path('sample.tif'))
        self.assertEqual(tif.get_rgba(), EXPECTED_RGBA)

    def test_tga(self):
        tga = TgaFile()
        tga.read(_helpers.sample_path('sample.tga'))
        self.assertEqual((tga.width, tga.height), (W, H))
        self.assertEqual(tga.get_rgba(), EXPECTED_RGBA)

    def test_png(self):
        width, height, rgba = read_png(_helpers.sample_path('sample.png'))
        self.assertEqual((width, height), (W, H))
        self.assertEqual(rgba, EXPECTED_RGBA)


class TestLossyFormats(unittest.TestCase):
    def test_dds_dxt5_decodes_to_plausible_rgba(self):
        dds = DdsFile()
        dds.read(_helpers.sample_path('sample.dds'))
        self.assertEqual((dds.width, dds.height), (W, H))
        decoded = dds.get_rgba(0)
        self.assertEqual(len(decoded), len(EXPECTED_RGBA))
        # DXT5 block-averages 4x4 pixel groups, so a steep per-pixel gradient
        # (this fixture changes every pixel) legitimately drifts more than a
        # tight tolerance would allow - this checks "still a plausible decode",
        # not codec precision.
        for i in range(0, len(decoded), 4):
            for c in range(4):
                self.assertLessEqual(
                    abs(decoded[i + c] - EXPECTED_RGBA[i + c]), 80,
                    f'pixel byte {i + c} drifted too far under DXT5 compression'
                )


class TestRealIconFixture(unittest.TestCase):
    """A real Warcraft III command-card icon (BTNMountainKing.blp - 64x64,
    JPEG-compressed BLP1, 7 mip levels) exercising the full decode +
    icon-authoring pipeline (transform.py, palette.py) against real,
    photographic-ish art with JPEG-compression artifacts - the other tests
    above only ever exercise a tiny synthetic 8x8 gradient, which can't
    stand in for real variety (thousands of unique colors, an uneven color
    distribution) the way this fixture can."""

    @classmethod
    def setUpClass(cls):
        cls.blp = BlpFile()
        cls.blp.read(_helpers.sample_path('BTNMountainKing.blp'))
        cls.rgba = cls.blp.get_rgba(0)

    def test_decodes_as_a_real_jpeg_compressed_command_card_icon(self):
        self.assertEqual((self.blp.width, self.blp.height), (64, 64))
        self.assertEqual(self.blp.compression, 0)  # the JPEG sub-variant, not paletted
        self.assertEqual(self.blp.mip_count(), 7)
        self.assertEqual(len(self.rgba), 64 * 64 * 4)
        # Real command-card art, not a flat color or a tiny synthetic
        # gradient - real visual variety is exactly what the other fixtures
        # above can't exercise.
        unique_colors = {self.rgba[i:i + 4] for i in range(0, len(self.rgba), 4)}
        self.assertGreater(len(unique_colors), 500)

    def test_every_mip_level_decodes_and_halves_correctly(self):
        for mip in range(self.blp.mip_count()):
            width, height = self.blp.mip_size(mip)
            self.assertEqual((width, height), (max(1, 64 >> mip), max(1, 64 >> mip)))
            decoded = self.blp.get_rgba(mip)
            self.assertEqual(len(decoded), width * height * 4)

    def test_reports_the_mip_levels_that_only_alias_another_levels_chunk(self):
        # Blizzard's own writer stops re-encoding below a size threshold and
        # repeats the previous chunk's offset instead: here level 1's 32x32
        # JPEG chunk is reused verbatim by levels 2-6, so only 2 of the 7
        # declared levels carry data of their own.
        offsets = self.blp._mip_offsets[:self.blp.mip_count()]
        self.assertEqual(len(set(offsets)), 2)
        self.assertEqual(self.blp.aliased_mip_count(), 5)
        # Aliased or not, every level still decodes at its own declared size.
        self.assertEqual(self.blp.mip_size(6), (1, 1))
        self.assertEqual(len(self.blp.get_rgba(6)), 1 * 1 * 4)

    def test_round_trips_through_this_packages_own_png_writer_and_reader(self):
        png_bytes = self.blp.to_png_bytes(0)
        width, height, rgba = read_png(png_bytes)
        self.assertEqual((width, height), (64, 64))
        self.assertEqual(rgba, self.rgba)

    def test_resize_box_to_1x1_produces_the_images_true_average_color(self):
        # The box filter is an area-average resize - shrinking all the way to
        # a single pixel should land on (within +/-1 rounding) the image's
        # real per-channel average, a well-defined, checkable invariant a
        # tiny synthetic gradient can't meaningfully exercise.
        tiny = resize_box(self.rgba, 64, 64, 1, 1)
        self.assertEqual(len(tiny), 4)
        npix = 64 * 64
        for channel in range(4):
            expected = sum(self.rgba[channel::4]) // npix
            self.assertAlmostEqual(tiny[channel], expected, delta=1)

    def test_resize_nearest_upscale_preserves_dimensions_and_source_pixels(self):
        upscaled = resize_nearest(self.rgba, 64, 64, 128, 128)
        self.assertEqual(len(upscaled), 128 * 128 * 4)
        self.assertEqual(upscaled[0:4], self.rgba[0:4])  # top-left corner preserved

    def test_make_disabled_variant_darkens_and_desaturates_like_a_real_disbtn(self):
        disabled = make_disabled_variant(self.rgba, 64, 64)
        self.assertEqual(len(disabled), len(self.rgba))

        def avg_luma_and_saturation(rgba):
            npix = len(rgba) // 4
            luma_total = sat_total = 0
            for i in range(npix):
                r, g, b = rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2]
                luma_total += 0.299 * r + 0.587 * g + 0.114 * b
                sat_total += max(r, g, b) - min(r, g, b)
            return luma_total / npix, sat_total / npix

        orig_luma, orig_sat = avg_luma_and_saturation(self.rgba)
        disabled_luma, disabled_sat = avg_luma_and_saturation(disabled)
        self.assertLess(disabled_luma, orig_luma)
        self.assertLess(disabled_sat, orig_sat)
        # DISBTN convention: alpha is untouched, only color changes.
        self.assertEqual(disabled[3::4], self.rgba[3::4])

    def test_composite_over_blends_a_border_overlay_realistically(self):
        # A synthetic opaque-red-border/fully-transparent-center overlay - a
        # stand-in for a real icon border image (the make-icon CLI's
        # --border applies one exactly this way).
        overlay = bytearray(64 * 64 * 4)
        for y in range(64):
            for x in range(64):
                i = (y * 64 + x) * 4
                if x < 4 or x >= 60 or y < 4 or y >= 60:
                    overlay[i:i + 4] = b'\xff\x00\x00\xff'
                else:
                    overlay[i:i + 4] = b'\x00\x00\x00\x00'

        composited = composite_over(self.rgba, 64, 64, bytes(overlay), 64, 64)
        self.assertEqual(len(composited), len(self.rgba))

        border_i = 0  # (0, 0) is on the border
        self.assertEqual(composited[border_i:border_i + 4], b'\xff\x00\x00\xff')
        center_i = (32 * 64 + 32) * 4  # deep in the fully-transparent overlay region
        self.assertEqual(composited[center_i:center_i + 4], self.rgba[center_i:center_i + 4])

    def test_requantizing_through_blpfile_from_rgba_stays_plausible(self):
        # This real icon has thousands of unique colors (JPEG artifacts) -
        # far more than a paletted BLP1's 256-color budget, so re-encoding it
        # through BlpFile.from_rgba() (median-cut quantization, the only BLP1
        # *write* path this package has - there's no JPEG encoder, see
        # blp_file.py's own docstring) is meaningfully lossy. Checked for
        # "plausible", not exact - same bar as the DXT5 test above, and a
        # real stress test for quantize_median_cut that a small synthetic
        # image (already <=256 colors) could never exercise.
        requantized = BlpFile.from_rgba(64, 64, self.rgba)
        self.assertEqual(requantized.compression, 1)  # paletted, not JPEG
        roundtripped = requantized.get_rgba(0)
        self.assertEqual(len(roundtripped), len(self.rgba))

        avg_diff_per_channel = sum(abs(a - b) for a, b in zip(roundtripped, self.rgba)) / len(self.rgba)
        self.assertLess(avg_diff_per_channel, 20,
                         'median-cut requantization drifted implausibly far from the real icon')

    def test_requantized_blp_round_trips_exactly_once_written(self):
        # The lossiness above is entirely in the quantization step - the
        # paletted BLP1 *container* itself is lossless, so once quantized,
        # writing it out and reading it back must reproduce it exactly.
        requantized = BlpFile.from_rgba(64, 64, self.rgba)
        written = requantized.to_bytes()

        reread = BlpFile()
        reread.read(written)
        self.assertEqual((reread.width, reread.height), (64, 64))
        self.assertEqual(reread.get_rgba(0), requantized.get_rgba(0))


if __name__ == '__main__':
    unittest.main()
