"""A damaged MPQ sector raises MpqCompressionError, whatever its method
(#143). zlib and bzip2 errors used to come through as zlib.error and
OSError."""
import bz2
import unittest
import zlib

import _helpers  # noqa: F401
from wc3files.mpq.compression import decompress, MpqCompressionError, ZLIB, BZIP2, HUFFMAN


class TestDamagedSectors(unittest.TestCase):

    DATA = b'hello world, ' * 40

    def assert_damaged(self, sector):
        with self.assertRaises(MpqCompressionError) as caught:
            decompress(sector, len(self.DATA))
        self.assertIsNotNone(caught.exception.__cause__)

    def test_zlib(self):
        packed = zlib.compress(self.DATA)
        self.assertEqual(decompress(bytes([ZLIB]) + packed, len(self.DATA)), self.DATA)
        self.assert_damaged(bytes([ZLIB]) + packed[:-6])                # cut short
        self.assert_damaged(bytes([ZLIB]) + packed[:-4] + b'\0\0\0\0')  # bad checksum
        self.assert_damaged(bytes([ZLIB]) + b'\xff' * 20)               # not zlib

    def test_bzip2(self):
        packed = bz2.compress(self.DATA)
        self.assertEqual(decompress(bytes([BZIP2]) + packed, len(self.DATA)), self.DATA)
        self.assert_damaged(bytes([BZIP2]) + packed[:20])
        self.assert_damaged(bytes([BZIP2]) + b'BZh9' + b'\0' * 30)

    def test_huffman_garbage_is_one_error(self):
        for first in range(0, 9):
            with self.subTest(table=first):
                try:
                    decompress(bytes([HUFFMAN, first]) + b'\xff' * 3, 1000)
                except MpqCompressionError:
                    pass


if __name__ == '__main__':
    unittest.main()
