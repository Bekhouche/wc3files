import sys


b2i = lambda b: b if isinstance(b, int) else int.from_bytes(b, 'little')

def fixedlengthstr(b, i):
    """Returns a string of length i from bytes - falls back to cp1252 for
    non-UTF-8 bytes, same fallback pattern used by CfgFile/WtsFile."""
    raw = b[:i]
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')

def nulltermstr(b, encoding='utf-8'):
     i = b.find(b'\0')
     if i == -1:
          raise ValueError('nulltermstr: no null terminator found in buffer')
     s = b[:i].decode(encoding)
     return s, i

def blizdecomp(b):
    """Performs wacky blizard 'decompression' and returns bytes and len in
    original string.
    """
    if isinstance(b, str):
        b = list(map(b2i, b))
    d = []
    pos = 0
    mask = None
    while b[pos] != 0:
        if pos%8 == 0:
            mask = b[pos]
        elif ((mask & (0x1 << (pos%8))) == 0):
            d.append(b[pos] - 1)
        else:
            d.append(b[pos])
        pos += 1
    if bytes == str:
        d = b''.join(map(chr, d))
    else:
        d = bytes(d)
    return d, pos

def blizdecode(b):
    d, l = blizdecomp(b)
    return d.decode(), l

def blizcomp(data):
    """Inverse of blizdecomp(): encodes bytes into Blizzard's masked format (7 data bytes
    plus 1 mask byte per group), choosing the encoding so no data byte is ever literally
    0x00 (that value is reserved as the terminator blizdecomp() scans for). A decoded byte
    of 0 must be raised to 1 with its mask bit cleared; every other byte is written
    unmodified with its mask bit set - either choice round-trips correctly through
    blizdecomp(), this just matches its bit-position convention exactly.
    """
    if isinstance(data, str):
        data = data.encode()

    out = bytearray()
    for i in range(0, len(data), 7):
        chunk = data[i:i + 7]
        # blizdecomp() never actually tests bit 0 of the mask (only bits 1-7, one per data
        # byte) - force it to 1 so an all-zero-valued chunk can't produce a mask byte that's
        # itself literally 0x00, which blizdecomp()'s "while b[pos] != 0" loop would
        # mistake for the terminator before ever reaching the real one.
        mask = 1
        encoded = bytearray(len(chunk))
        for j, byte in enumerate(chunk):
            if byte == 0:
                encoded[j] = 1
            else:
                encoded[j] = byte
                mask |= 1 << (j + 1)
        out.append(mask)
        out.extend(encoded)
    out.append(0)
    return bytes(out)