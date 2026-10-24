"""Real tests for wc3files.utils - regression coverage for bugs found during
a capability audit (see TODO.md): convert_bytes_to's dead INT branch,
nulltermstr's silent mishandling of missing terminators, and
fixedlengthstr's UTF-8-only decode."""
import struct
import unittest

import _helpers
from wc3files.utils.converter import convert_bytes_to
from wc3files.utils.misc import nulltermstr, fixedlengthstr
from wc3files.data import BinaryDataType


class TestConvertBytesTo(unittest.TestCase):
    def test_int(self):
        self.assertEqual(convert_bytes_to(struct.pack('<i', 12345), BinaryDataType.INT), 12345)

    def test_real(self):
        self.assertAlmostEqual(convert_bytes_to(struct.pack('f', 1.5), BinaryDataType.REAL), 1.5)

    def test_unreal(self):
        value = convert_bytes_to(struct.pack('f', 1.23456789), BinaryDataType.UNREAL)
        self.assertEqual(value, float(format(struct.unpack('f', struct.pack('f', 1.23456789))[0], '.7f')))

    def test_string(self):
        self.assertEqual(convert_bytes_to(b'hello', BinaryDataType.STRING), 'hello')

    def test_bytes(self):
        self.assertEqual(convert_bytes_to(b'\x01\x02', BinaryDataType.BYTES), b'\x01\x02')


class TestNullTermStr(unittest.TestCase):
    def test_normal_case(self):
        self.assertEqual(nulltermstr(b'hello\x00world'), ('hello', 5))

    def test_raises_on_missing_terminator(self):
        with self.assertRaises(ValueError):
            nulltermstr(b'no terminator here')


class TestFixedLengthStr(unittest.TestCase):
    def test_utf8(self):
        self.assertEqual(fixedlengthstr(b'hello', 5), 'hello')

    def test_falls_back_to_cp1252_on_bad_utf8(self):
        # Doesn't raise UnicodeDecodeError - falls back like CfgFile/WtsFile do.
        result = fixedlengthstr(b'\xe9\xe8\xe7', 3)  # valid cp1252, invalid utf-8
        self.assertEqual(result, '\xe9\xe8\xe7')


if __name__ == '__main__':
    unittest.main()
