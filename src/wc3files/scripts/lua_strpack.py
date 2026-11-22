"""
Lua 5.3's string.pack, string.unpack and string.packsize.

Follows lstrlib.c of Lua 5.3: the same options, alignment rule, overflow
checks and error messages. Native sizes are those of the 64-bit Windows
build Reforged runs: short 2, int 4, long 4, lua_Integer 8, size_t 8, float
4, double and lua_Number 8, little endian, maximum alignment 8.

Strings in the interpreter are Python str holding one byte per character.
"""

import struct

from .lua_vm import LuaError

_NATIVE = {'b': 1, 'h': 2, 'l': 4, 'j': 8, 'T': 8, 'i': 4, 's': 8}
_MAXINTSIZE = 16
_MAXALIGN = 8
_SZINT = 8

INT, UINT, FLOAT, CHAR, STRING, ZSTR, PADDING, PADDALIGN, NOP = range(9)


def _bad(argument, fname, message):
     return LuaError(f"bad argument #{argument} to '{fname}' ({message})")


class _Header:
     __slots__ = ('little', 'maxalign', 'fname')

     def __init__(self, fname):
          self.little = True
          self.maxalign = 1
          self.fname = fname


class _Format:
     __slots__ = ('text', 'pos')

     def __init__(self, text):
          self.text = text
          self.pos = 0

     def more(self):
          return self.pos < len(self.text)

     def number(self, default):
          start = self.pos
          while self.pos < len(self.text) and self.text[self.pos].isdigit() and \
                    self.pos - start < 10:
               self.pos += 1
          if self.pos == start:
               return default
          return int(self.text[start:self.pos])

     def limit(self, default):
          size = self.number(default)
          if size > _MAXINTSIZE or size <= 0:
               raise LuaError(f'integral size ({size}) out of limits [1,{_MAXINTSIZE}]')
          return size


def _option(h, fmt):
     opt = fmt.text[fmt.pos]
     fmt.pos += 1
     if opt == 'b':
          return INT, 1
     if opt == 'B':
          return UINT, 1
     if opt == 'h':
          return INT, 2
     if opt == 'H':
          return UINT, 2
     if opt == 'l':
          return INT, _NATIVE['l']
     if opt == 'L':
          return UINT, _NATIVE['l']
     if opt == 'j':
          return INT, 8
     if opt == 'J':
          return UINT, 8
     if opt == 'T':
          return UINT, 8
     if opt == 'f':
          return FLOAT, 4
     if opt in 'dn':
          return FLOAT, 8
     if opt == 'i':
          return INT, fmt.limit(4)
     if opt == 'I':
          return UINT, fmt.limit(4)
     if opt == 's':
          return STRING, fmt.limit(8)
     if opt == 'c':
          size = fmt.number(-1)
          if size == -1:
               raise LuaError("missing size for format option 'c'")
          return CHAR, size
     if opt == 'z':
          return ZSTR, 0
     if opt == 'x':
          return PADDING, 1
     if opt == 'X':
          return PADDALIGN, 0
     if opt == ' ':
          return NOP, 0
     if opt == '<':
          h.little = True
          return NOP, 0
     if opt == '>':
          h.little = False
          return NOP, 0
     if opt == '=':
          h.little = True
          return NOP, 0
     if opt == '!':
          h.maxalign = fmt.limit(_MAXALIGN)
          return NOP, 0
     raise LuaError(f"invalid format option '{opt}'")


def _details(h, total, fmt):
     opt, size = _option(h, fmt)
     align = size
     if opt == PADDALIGN:
          if not fmt.more():
               raise _bad(1, h.fname, "invalid next option for option 'X'")
          next_opt, align = _option(h, fmt)
          if next_opt == CHAR or align == 0:
               raise _bad(1, h.fname, "invalid next option for option 'X'")
     if align <= 1 or opt == CHAR:
          return opt, size, 0
     if align > h.maxalign:
          align = h.maxalign
     if align & (align - 1):
          raise _bad(1, h.fname, 'format asks for alignment not power of 2')
     return opt, size, (align - (total & (align - 1))) & (align - 1)


def _pack_int(value, little, size, negative):
     value &= (1 << 64) - 1
     data = bytearray(size)
     for i in range(min(size, _SZINT)):
          data[i] = (value >> (8 * i)) & 0xFF
     if size > _SZINT and negative:
          for i in range(_SZINT, size):
               data[i] = 0xFF
     if not little:
          data.reverse()
     return bytes(data)


def _unpack_int(data, little, size, signed):
     raw = data if little else data[::-1]
     limit = min(size, _SZINT)
     result = 0
     for i in range(limit - 1, -1, -1):
          result = (result << 8) | raw[i]
     if size < _SZINT:
          if signed:
               mask = 1 << (size * 8 - 1)
               result = (result ^ mask) - mask
     else:
          if result >= 1 << 63:
               result -= 1 << 64
          if size > _SZINT:
               fill = 0xFF if signed and result < 0 else 0
               if any(raw[i] != fill for i in range(limit, size)):
                    raise LuaError(f'{size}-byte integer does not fit into Lua Integer')
     return result


def pack(lib, args):
     fname = 'pack'
     h = _Header(fname)
     fmt = _Format(lib.check_str(args, 0, fname))
     out = []
     total = 0
     arg = 0
     while fmt.more():
          opt, size, padding = _details(h, total, fmt)
          total += padding + size
          out.append('\0' * padding)
          arg += 1
          if opt == INT:
               n = lib.check_int(args, arg, fname)
               if size < _SZINT:
                    lim = 1 << (size * 8 - 1)
                    if not -lim <= n < lim:
                         raise _bad(arg + 1, fname, 'integer overflow')
               out.append(_pack_int(n, h.little, size, n < 0).decode('latin-1'))
          elif opt == UINT:
               n = lib.check_int(args, arg, fname)
               if size < _SZINT and (n & ((1 << 64) - 1)) >= 1 << (size * 8):
                    raise _bad(arg + 1, fname, 'unsigned overflow')
               out.append(_pack_int(n, h.little, size, False).decode('latin-1'))
          elif opt == FLOAT:
               n = float(lib.check_num(args, arg, fname))
               code = ('<' if h.little else '>') + ('f' if size == 4 else 'd')
               try:
                    packed = struct.pack(code, n)
               except OverflowError:
                    packed = struct.pack(code, float('inf') if n > 0 else float('-inf'))
               out.append(packed.decode('latin-1'))
          elif opt == CHAR:
               s = lib.check_str(args, arg, fname)
               if len(s) > size:
                    raise _bad(arg + 1, fname, 'string longer than given size')
               out.append(s + '\0' * (size - len(s)))
          elif opt == STRING:
               s = lib.check_str(args, arg, fname)
               if size < 8 and len(s) >= 1 << (size * 8):
                    raise _bad(arg + 1, fname, 'string length does not fit in given size')
               out.append(_pack_int(len(s), h.little, size, False).decode('latin-1'))
               out.append(s)
               total += len(s)
          elif opt == ZSTR:
               s = lib.check_str(args, arg, fname)
               if '\0' in s:
                    raise _bad(arg + 1, fname, 'string contains zeros')
               out.append(s + '\0')
               total += len(s) + 1
          else:
               if opt == PADDING:
                    out.append('\0')
               arg -= 1
     return [''.join(out)]


def packsize(lib, args):
     fname = 'packsize'
     h = _Header(fname)
     fmt = _Format(lib.check_str(args, 0, fname))
     total = 0
     while fmt.more():
          opt, size, padding = _details(h, total, fmt)
          if opt in (STRING, ZSTR):
               raise _bad(1, fname, 'variable-length format')
          size += padding
          if total > 0x7FFFFFFF - size:
               raise _bad(1, fname, 'format result too large')
          total += size
     return [total]


def unpack(lib, args):
     fname = 'unpack'
     h = _Header(fname)
     fmt = _Format(lib.check_str(args, 0, fname))
     data = lib.check_str(args, 1, fname)
     length = len(data)
     init = lib.opt_int(args, 2, fname, 1)
     if init < 0:
          init = 0 if -init > length else length + init + 1
     pos = init - 1
     if pos < 0 or pos > length:
          raise _bad(3, fname, 'initial position out of string')
     results = []
     while fmt.more():
          opt, size, padding = _details(h, pos, fmt)
          if pos + padding + size > length:
               raise _bad(2, fname, 'data string too short')
          pos += padding
          if opt in (INT, UINT):
               chunk = data[pos:pos + size].encode('latin-1')
               results.append(_unpack_int(chunk, h.little, size, opt == INT))
          elif opt == FLOAT:
               chunk = data[pos:pos + size].encode('latin-1')
               code = ('<' if h.little else '>') + ('f' if size == 4 else 'd')
               results.append(struct.unpack(code, chunk)[0])
          elif opt == CHAR:
               results.append(data[pos:pos + size])
          elif opt == STRING:
               chunk = data[pos:pos + size].encode('latin-1')
               count = _unpack_int(chunk, h.little, size, False)
               if pos + count + size > length:
                    raise _bad(2, fname, 'data string too short')
               results.append(data[pos + size:pos + size + count])
               pos += count
          elif opt == ZSTR:
               end = data.find('\0', pos)
               if end < 0:
                    raise _bad(2, fname, "unfinished string for format 'z'")
               results.append(data[pos:end])
               pos = end + 1
          pos += size
     results.append(pos + 1)
     return results
