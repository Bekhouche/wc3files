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


class _BigEndianStruct:
    """Stands in for `struct` on a big-endian machine: a format with no byte
    order prefix resolves to big-endian, as native order would there."""
    @staticmethod
    def _fmt(fmt):
        return fmt if fmt[:1] in '<>!=@' else '>' + fmt

    def pack(self, fmt, *values):
        return struct.pack(self._fmt(fmt), *values)

    def unpack(self, fmt, data):
        return struct.unpack(self._fmt(fmt), data)


class TestFloatByteOrder(unittest.TestCase):
    """Warcraft III files are little-endian on every machine, so a float must
    not follow the host's byte order."""
    ONE = b'\x00\x00\x80\x3f'  # 1.0 as a little-endian float

    def setUp(self):
        import importlib
        from unittest import mock
        # import_module: the modules themselves, whatever the package
        # attributes of the same name hold.
        modules = [importlib.import_module('wc3files.files.binary_file'),
                   importlib.import_module('wc3files.utils.converter')]
        for module in modules:
            patcher = mock.patch.object(module, 'struct', _BigEndianStruct())
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_binary_file_writes_and_reads_little_endian_floats(self):
        from wc3files.files.binary_file import BinaryFile
        for data_type in (BinaryDataType.REAL, BinaryDataType.UNREAL):
            with self.subTest(data_type=data_type):
                with BinaryFile(b'', 'write') as writer:
                    writer.write(data_type, 1.0)
                    written = writer.file.getvalue()
                self.assertEqual(written, self.ONE)
                with BinaryFile(self.ONE) as reader:
                    self.assertEqual(reader.read(data_type), 1.0)

    def test_convert_bytes_to_reads_little_endian_floats(self):
        self.assertEqual(convert_bytes_to(self.ONE, BinaryDataType.REAL), 1.0)
        self.assertEqual(convert_bytes_to(self.ONE, BinaryDataType.UNREAL), 1.0)


if __name__ == '__main__':
    unittest.main()
