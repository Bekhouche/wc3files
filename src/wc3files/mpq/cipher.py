import struct

def name_bytes(name):
     """The bytes an archive member name is hashed from.

     MPQ hashes the bytes of a name, not its characters. Names are kept as
     UTF-8 decoded with surrogateescape throughout this package (listfile,
     object data, scripts), so encoding them back the same way gives exactly
     the bytes the archive and the game use - UTF-8 for a Reforged map, the
     original code-page bytes for an older one. A bytes name is used as it
     is."""
     if isinstance(name, (bytes, bytearray)):
          return bytes(name)
     return name.encode('utf-8', errors='surrogateescape')


def hash_name(crypt_table, name, hash_type):
     """The MPQ hash of `name` for `hash_type` (an int 0-4).

     Only ASCII letters are upper-cased, as the game does: bytes.upper()
     leaves every byte above 0x7F alone, where str.upper() turned 'e' with
     an accent into another character and a CJK character into an index
     past the 256-entry row of the crypt table (a KeyError)."""
     seed1 = 0x7FED7FED
     seed2 = 0xEEEEEEEE
     row = hash_type << 8
     for ch in name_bytes(name).upper():
          value = crypt_table[row + ch]
          seed1 = (value ^ (seed1 + seed2)) & 0xFFFFFFFF
          seed2 = ch + seed1 + seed2 + (seed2 << 5) + 3 & 0xFFFFFFFF
     return seed1


def plain_name(filename):
     """The part of an archive member name after its last folder separator,
     which is what an encrypted file's key is hashed from.

     Archive names use a backslash on every platform; a forward slash is
     treated the same, as Windows' os.path.basename (which this used to be)
     did. os.path.basename only splits on a backslash on Windows, so on Linux
     and macOS the whole name was hashed and every encrypted file inside a
     folder decrypted to garbage."""
     return filename.replace('/', '\\').rsplit('\\', 1)[-1]


def _build_crypt_table():
     table = {}
     seed = 0x00100001
     for index1 in range(0x100):
          for index2 in range(index1, index1 + 0x500, 0x100):
               seed = (seed * 125 + 3) % 0x2AAAAB
               temp1 = (seed & 0xFFFF) << 0x10

               seed = (seed * 125 + 3) % 0x2AAAAB
               temp2 = (seed & 0xFFFF)

               table[index2] = (temp1 | temp2)
     return table


# The table never changes, so it is built once per process and shared by
# every reader and writer (W3xFile, MpqFiles, MpqWriter, ...), instead of
# once per object.
CRYPT_TABLE = _build_crypt_table()
# The row block encryption uses (0x400..0x4FF), as a list for fast indexing.
_KEY_ROW = [CRYPT_TABLE[0x400 + i] for i in range(0x100)]


class MpqCipher:
     """The MPQ cipher: the crypt table, name hashing, and the block
     encryption of the hash table, the block table and encrypted files.

     This is the only copy in the package. W3xFile kept a second one, whose
     encrypt() had never worked (see TestOneCipher).
     """
     def __init__(self):
          self.initialize_crypt_table()

     def initialize_crypt_table(self):
          self.crypt_table = CRYPT_TABLE

     # Both directions work on the whole buffer at once: one unpack into a
     # tuple of ints, a tight loop on locals, one pack. Unpacking, packing and
     # writing one word at a time ran at about 4 MB/s, and was a fifth of the
     # time of reading a large map.
     #
     # The key is a DWORD in StormLib and the game, so it is masked to 32
     # bits first: `key - 1` for a sector table whose file key is 0 is
     # 0xFFFFFFFF, and `key + i` for sector i wraps around. Without the mask,
     # the sign or the 33rd bit of a Python int was shifted into the next key.

     def encrypt(self, buffer, key):
          count = len(buffer) // 4
          words = struct.unpack_from('<%dI' % count, buffer)
          table = _KEY_ROW
          key &= 0xFFFFFFFF
          seed = 0xEEEEEEEE
          out = [0] * count
          for i, value in enumerate(words):
               seed = (seed + table[key & 0xFF]) & 0xFFFFFFFF
               out[i] = value ^ ((key + seed) & 0xFFFFFFFF)
               key = (((key ^ 0xFFFFFFFF) << 0x15) + 0x11111111 | key >> 0x0B) & 0xFFFFFFFF
               seed = (value + seed * 33 + 3) & 0xFFFFFFFF
          return struct.pack('<%dI' % count, *out) + bytes(buffer[count * 4:])

     def decrypt(self, buffer, key):
          count = len(buffer) // 4
          words = struct.unpack_from('<%dI' % count, buffer)
          table = _KEY_ROW
          seed1 = key & 0xFFFFFFFF
          seed2 = 0xEEEEEEEE
          out = [0] * count
          for i, value in enumerate(words):
               seed2 = (seed2 + table[seed1 & 0xFF]) & 0xFFFFFFFF
               value ^= (seed1 + seed2) & 0xFFFFFFFF
               out[i] = value
               # ~x on a 32-bit value is x ^ 0xFFFFFFFF once masked, and
               # s + (s << 5) is s * 33: the same arithmetic, fewer steps.
               seed1 = (((seed1 ^ 0xFFFFFFFF) << 0x15) + 0x11111111 | seed1 >> 0x0B) & 0xFFFFFFFF
               seed2 = (value + seed2 * 33 + 3) & 0xFFFFFFFF
          return struct.pack('<%dI' % count, *out) + bytes(buffer[count * 4:])
     
     def hash(self, lpszString, hash_type):
          return hash_name(self.crypt_table, lpszString, hash_type)
     
     @staticmethod
     def decompress(data, size=None):
          """Decompress one MPQ sector (its method byte, then the data)."""
          from .compression import decompress
          return decompress(data, size)