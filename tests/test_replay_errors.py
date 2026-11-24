"""A damaged replay raises a ValueError (#166). A data block whose zlib stream
is damaged raised zlib.error, and a block cut short a plain EOFError."""
import os
import struct
import tempfile
import unittest
import zlib

import _helpers  # noqa: F401
from test_w3g import _replay_data
from wc3files import ReplayFile
from wc3files.files.binary_file import TruncatedDataError
from wc3files.w3g.blocks import ReplayBlockError


def _replay_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'r.w3g')
        writer = ReplayFile()
        writer.import_data(_replay_data())
        writer.write(path)
        with open(path, 'rb') as handle:
            data = handle.read()
        reader = ReplayFile()
        reader.read(path)
        header = reader.get_data()['header']
    return data, header['fileoffset'], header.get('reforged', False)


def _read(data):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'damaged.w3g')
        with open(path, 'wb') as handle:
            handle.write(data)
        ReplayFile().read(path)


class TestDamagedReplay(unittest.TestCase):

    def test_a_damaged_zlib_block_raises_replay_block_error(self):
        data, offset, reforged = _replay_bytes()
        size = struct.unpack_from('<H', data, offset)[0]
        start = offset + (12 if reforged else 8)
        damaged = data[:start + 2] + b'\xff' * (size - 2) + data[start + size:]
        with self.assertRaises(ReplayBlockError) as caught:
            _read(damaged)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIsInstance(caught.exception.__cause__, zlib.error)

    def test_a_block_cut_short_raises_truncated_data_error(self):
        data, offset, reforged = _replay_bytes()
        start = offset + (12 if reforged else 8)
        with self.assertRaises(TruncatedDataError):
            _read(data[:start + 3])


if __name__ == '__main__':
    unittest.main()
