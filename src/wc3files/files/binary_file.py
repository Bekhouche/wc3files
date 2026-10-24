import os
import io
import struct
from loguru import logger
from wc3files.data import BinaryDataType

class BinaryFile:
    def __init__(self, file_or_data, mode='read'):
        """
        Initialize BinaryFile. file_or_data can be either a file path or a bytes object.
        """
        if mode not in ('read', 'write'):
            raise ValueError("Invalid mode. Use 'read' or 'write'.")

        self.mode = mode
        self.file = None

        # Deliberately does NOT call logger.remove()/logger.add() here: this used to wipe and
        # reconfigure loguru's *global* sink on every single BinaryFile construction (i.e. on
        # nearly every read/write in this whole package), silently overriding any logging
        # setup the calling application had already put in place (e.g. wc3files.cli's
        # quiet-by-default config) the moment the first file got opened. Sink configuration is
        # an application-level decision, not something a low-level binary-I/O class should
        # seize for the whole process - loguru already provides a sane default sink on its own.
        self.logger = logger.bind(name=self.__class__.__name__)

        # Determine if input is a file path or binary data
        if isinstance(file_or_data, str):
            self.file_path = file_or_data
            self.is_binary_data = False
        elif isinstance(file_or_data, (bytes, bytearray)):
            self.binary_data = file_or_data
            self.is_binary_data = True
        else:
            raise TypeError("file_or_data must be a file path (str) or binary data (bytes or bytearray).")

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def size(self):
        pos = self.tell()
        self.file.seek(0, os.SEEK_END)
        total_size = self.tell()
        self.seek(pos)
        return total_size

    def open(self):
        if self.mode == 'read':
            if not self.is_binary_data:
                if os.path.exists(self.file_path):
                    self.file = io.open(self.file_path, 'rb')
                else:
                    raise FileNotFoundError(f"File not found: {self.file_path}")
            else:
                # Open an in-memory stream for binary data
                self.file = io.BytesIO(self.binary_data)
        elif self.mode == 'write':
            if not self.is_binary_data:
                self.file = io.open(self.file_path, 'wb')
            else:
                # Prepare an in-memory stream for writing binary data
                self.file = io.BytesIO()

    def close(self):
        if self.file is not None and not self.file.closed:
            self.file.close()

    def tell(self):
        return self.file.tell()

    def seek(self, offset):
        self.file.seek(offset)

    def convert(self, value, src_type, dst_type):
        if src_type == BinaryDataType.BYTES and dst_type == BinaryDataType.STRING:
            return value.decode('utf-8')
        elif src_type == BinaryDataType.BYTES and dst_type == BinaryDataType.INT:
            return int.from_bytes(value, 'little')

    def read(self, data_type=BinaryDataType.INT, num=4, byteorder='little', signed=False):
        if self.mode != 'read':
            raise ValueError("File is not open in read mode.")

        if data_type == BinaryDataType.INT:
            return int.from_bytes(self.file.read(num), byteorder, signed=signed)
        elif data_type == BinaryDataType.REAL:
            return struct.unpack('f', self.file.read(num))[0]
        elif data_type == BinaryDataType.UNREAL:
            # Returned exactly as stored. Rounding to seven decimals here
            # looked harmless but changed the value: a real object field
            # holding 0.58999997 came back as 0.59, which packs to a
            # different four bytes, so the file could never be written back
            # unchanged. 19 of the 23 object files that still failed to
            # round-trip differed only in float bytes like this one.
            return struct.unpack('f', self.file.read(num))[0]
        elif data_type == BinaryDataType.STRING:
            # Read in chunks and scan for the null terminator instead of one
            # byte-at-a-time file.read(1) call per character (was the single
            # biggest cost when parsing object files with long tooltip/name
            # strings - millions of Python-level read() calls per file).
            chunk_size = 64
            parts = []
            while True:
                chunk = self.file.read(chunk_size)
                if not chunk:
                    break
                idx = chunk.find(b'\x00')
                if idx == -1:
                    parts.append(chunk)
                    continue
                parts.append(chunk[:idx])
                overshoot = len(chunk) - idx - 1
                if overshoot:
                    self.file.seek(-overshoot, 1)
                break
            # surrogateescape rather than plain utf-8: real maps carry strings
            # that are not valid UTF-8 at all - region and sound names written
            # in a legacy codepage, where a byte like 0x80 or 0xE8 made the
            # decode raise and took the whole file with it. This handler maps
            # any such byte to a lone surrogate, and the matching encode on
            # write turns it back into exactly the same byte, so a file
            # round-trips whatever its strings happen to contain. Well-formed
            # UTF-8 decodes exactly as before.
            return b''.join(parts).decode('utf-8', errors='surrogateescape')
        elif data_type == BinaryDataType.BYTES:
            return self.file.read(num)
        else:
            return None

    def read_batch(self, data):
        new_data = {}
        for key, val in data.items():
            new_data[key] = self.read(val)
        return new_data

    def write(self, data_type, value):
        if self.mode != 'write':
            raise ValueError("File is not open in write mode.")

        if data_type == BinaryDataType.INT:
            # read() takes INT as unsigned by default, so a field with the top
            # bit set comes back above 2^31 and signed packing refused it -
            # real region files hit this and could be read but not written.
            # Masking to 32 bits accepts both conventions and produces exactly
            # the same four bytes for every value either one could hold.
            self.file.write(struct.pack('<I', int(value) & 0xFFFFFFFF))
        elif data_type == BinaryDataType.REAL:
            self.file.write(struct.pack('f', float(value)))
        elif data_type == BinaryDataType.UNREAL:
            self.file.write(struct.pack('f', float(value)))
        elif data_type == BinaryDataType.BYTES:
            self.file.write(value)
        elif data_type == BinaryDataType.STRING:
            # Matches the surrogateescape decode in read(): a byte that was
            # not valid UTF-8 going in comes back out as the same byte.
            self.file.write(value.encode('utf-8', errors='surrogateescape'))
            self.file.write(b'\x00')

    def write_batch(self, data):
        for key, (data_type, value) in data.items():
            self.write(data_type, value)

    def write_data(self, data_type, value):
        self.write(BinaryDataType.INT, data_type.value)
        self.write(data_type, value)
        self.write(BinaryDataType.INT, 0)

    def write_id(self, value):
        self.write_data(BinaryDataType.STRING, value)

    def write_integer(self, value):
        self.write_data(BinaryDataType.INT, value)

    def write_real(self, value):
        self.write_data(BinaryDataType.REAL, value)

    def write_unreal(self, value):
        self.write_data(BinaryDataType.UNREAL, value)

    def write_bytes(self, value):
        self.write_data(BinaryDataType.BYTES, value)

    def write_string(self, value):
        self.write_data(BinaryDataType.STRING, value)
