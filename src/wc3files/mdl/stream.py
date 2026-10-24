import io
import struct


class FixedString(str):
    """Text from a fixed-length field, remembering the bytes it came from.

    Behaves as an ordinary string everywhere else; `raw` exists so the
    field can be written back exactly, padding and all.
    """
    __slots__ = ('raw',)

    def __new__(cls, text, raw):
        self = super().__new__(cls, text)
        self.raw = raw
        return self

    def __reduce__(self):
        # str's own reduce rebuilds the value with a single argument, which
        # this constructor will not accept, so copying or pickling a model
        # would fail. export_data() deep-copies, so that meant every caller.
        return (FixedString, (str(self), self.raw))


class BinaryStream:
    """Little-endian binary reader/writer used by MDX chunk codecs."""

    def __init__(self, data=None, mode='read'):
        if mode not in ('read', 'write'):
            raise ValueError("Invalid mode. Use 'read' or 'write'.")
        self.mode = mode
        if mode == 'read':
            if data is None:
                raise ValueError('Binary data is required in read mode.')
            self._buf = io.BytesIO(data if isinstance(data, (bytes, bytearray)) else bytes(data))
        else:
            self._buf = io.BytesIO()

    def tell(self):
        return self._buf.tell()

    def seek(self, offset):
        self._buf.seek(offset)

    def remaining(self):
        pos = self.tell()
        self._buf.seek(0, io.SEEK_END)
        end = self.tell()
        self.seek(pos)
        return end - pos

    def getvalue(self):
        return self._buf.getvalue()

    def skip(self, n):
        self._buf.seek(n, io.SEEK_CUR)

    def peek(self, n=4):
        pos = self.tell()
        value = self._buf.read(n)
        self.seek(pos)
        return value

    def read_bytes(self, n):
        value = self._buf.read(n)
        if len(value) != n:
            raise EOFError(f'Expected {n} bytes, got {len(value)}')
        return value

    def write_bytes(self, value):
        self._buf.write(value)

    def read_u8(self):
        return self.read_bytes(1)[0]

    def write_u8(self, value):
        self.write_bytes(bytes((value & 0xFF,)))

    def read_u16(self):
        return struct.unpack('<H', self.read_bytes(2))[0]

    def write_u16(self, value):
        self.write_bytes(struct.pack('<H', value))

    def read_i32(self):
        return struct.unpack('<i', self.read_bytes(4))[0]

    def write_i32(self, value):
        self.write_bytes(struct.pack('<i', value))

    def read_u32(self):
        return struct.unpack('<I', self.read_bytes(4))[0]

    def write_u32(self, value):
        self.write_bytes(struct.pack('<I', value))

    def read_f32(self):
        return struct.unpack('<f', self.read_bytes(4))[0]

    def write_f32(self, value):
        self.write_bytes(struct.pack('<f', float(value)))

    def read_tag(self):
        return self.read_bytes(4).decode('latin-1')

    def write_tag(self, tag):
        raw = tag.encode('latin-1')
        if len(raw) != 4:
            raise ValueError(f'Tag must be 4 bytes, got {tag!r}')
        self.write_bytes(raw)

    def read_fixed_string(self, length):
        raw = self.read_bytes(length)
        text = raw.split(b'\x00', 1)[0].decode('utf-8', errors='replace')
        return FixedString(text, raw)

    def write_fixed_string(self, value, length):
        # A fixed-length field is rarely all zeros after its terminator: real
        # models leave fragments of whatever the field held before. Writing
        # clean padding changes those bytes without changing the size, which
        # is why models read correctly and still would not write back
        # unchanged. The original bytes go back when the text has not been
        # edited; an edited value is re-encoded cleanly, since the old padding
        # would then belong to a different string.
        raw = getattr(value, 'raw', None)
        if (raw is not None and len(raw) == length
                and raw.split(b'\x00', 1)[0].decode('utf-8', errors='replace')
                == str(value)):
            self.write_bytes(raw)
            return
        encoded = (value or '').encode('utf-8', errors='replace')[:length]
        self.write_bytes(encoded + b'\x00' * (length - len(encoded)))

    def read_float_array(self, count):
        if count <= 0:
            return []
        return list(struct.unpack(f'<{count}f', self.read_bytes(4 * count)))

    def write_float_array(self, values):
        if not values:
            return
        self.write_bytes(struct.pack(f'<{len(values)}f', *values))

    def read_u32_array(self, count):
        if count <= 0:
            return []
        return list(struct.unpack(f'<{count}I', self.read_bytes(4 * count)))

    def write_u32_array(self, values):
        if not values:
            return
        self.write_bytes(struct.pack(f'<{len(values)}I', *values))

    def read_u16_array(self, count):
        if count <= 0:
            return []
        return list(struct.unpack(f'<{count}H', self.read_bytes(2 * count)))

    def write_u16_array(self, values):
        if not values:
            return
        self.write_bytes(struct.pack(f'<{len(values)}H', *values))

    def read_u8_array(self, count):
        if count <= 0:
            return []
        return list(self.read_bytes(count))

    def write_u8_array(self, values):
        if not values:
            return
        self.write_bytes(bytes(values))
