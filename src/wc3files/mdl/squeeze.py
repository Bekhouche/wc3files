"""
Make an MDX model compress better, at the cost of precision: w3x2lni's
`mdx_squf`.

`squeeze_mdx(data)` zeroes the low 16 bits of the 32-bit floats a model
holds the most of: vertex positions, normals, texture coordinates, geoset
extents, and the keyframes of bones and helpers (translation, rotation,
scaling, with their tangents). What is left of each float is its sign,
exponent and top 7 mantissa bits, so a value moves by at most about 0.8%
of itself: the file keeps its size, but the long runs of zero bytes
compress far better inside a map's MPQ.

It is lossy and meant for a released map. Only version 800 models (the
classic MDX layout) are changed; any other version, or a file whose chunks
do not add up, is returned as it is, never half-rewritten.
"""

import struct


class _Abort(Exception):
     pass


class _Walker:
     def __init__(self, data):
          self.buf = bytearray(data)
          self.size = len(data)

     def u32(self, pos):
          if pos + 4 > self.size:
               raise _Abort()
          return struct.unpack_from('<I', self.buf, pos)[0]

     def zero_floats(self, pos, count):
          end = pos + 4 * count
          if count < 0 or end > self.size:
               raise _Abort()
          for p in range(pos, end, 4):
               self.buf[p] = 0
               self.buf[p + 1] = 0
          return end

     def counted_floats(self, pos, per_item, tag=None):
          """[tag] count, count * per_item floats -> new position."""
          if tag is not None:
               if bytes(self.buf[pos:pos + 4]) != tag:
                    raise _Abort()
               pos += 4
          count = self.u32(pos)
          return self.zero_floats(pos + 4, count * per_item)

     def skip_counted(self, pos, tag, item_size):
          if bytes(self.buf[pos:pos + 4]) != tag:
               raise _Abort()
          count = self.u32(pos + 4)
          end = pos + 8 + count * item_size
          if end > self.size:
               raise _Abort()
          return end

     def geosets(self, pos, end):
          while pos < end:
               size = self.u32(pos)
               geoset_end = pos + size
               if size < 4 or geoset_end > end:
                    raise _Abort()
               p = pos + 4
               p = self.counted_floats(p, 3, b'VRTX')
               p = self.counted_floats(p, 3, b'NRMS')
               p = self.skip_counted(p, b'PTYP', 4)
               p = self.skip_counted(p, b'PCNT', 4)
               p = self.skip_counted(p, b'PVTX', 2)
               p = self.skip_counted(p, b'GNDX', 1)
               p = self.skip_counted(p, b'MTGC', 4)
               p = self.skip_counted(p, b'MATS', 4)
               p += 40                   # material, group, flags, radius, min, max
               p = self.counted_floats(p, 7)          # sequence extents
               if bytes(self.buf[p:p + 4]) != b'UVAS':
                    raise _Abort()
               sets = self.u32(p + 4)
               p += 8
               for _ in range(sets):
                    p = self.counted_floats(p, 2, b'UVBS')
               if p != geoset_end:
                    raise _Abort()
               pos = geoset_end

     def tracks(self, pos, end):
          while pos < end:
               tag = bytes(self.buf[pos:pos + 4])
               if tag in (b'KGTR', b'KGSC'):
                    dims = 3
               elif tag == b'KGRT':
                    dims = 4
               else:
                    raise _Abort()
               count = self.u32(pos + 4)
               interpolation = self.u32(pos + 8)
               per_key = dims * (3 if interpolation > 1 else 1)
               p = pos + 16
               for _ in range(count):
                    p = self.zero_floats(p + 4, per_key)
               if p > end:
                    raise _Abort()
               pos = p
          if pos != end:
               raise _Abort()

     def nodes(self, pos, end, extra):
          """BONE (8 trailing bytes per bone) or HELP (none)."""
          while pos < end:
               size = self.u32(pos)
               node_end = pos + size
               if size < 96 or node_end + extra > end:
                    raise _Abort()
               self.tracks(pos + 96, node_end)
               pos = node_end + extra
          if pos != end:
               raise _Abort()


def squeeze_mdx(data):
     """The squeezed model, or `data` itself when it cannot be (not a
     version 800 MDX, or chunks that do not add up)."""
     if data[:4] != b'MDLX' or data[4:8] != b'VERS' or \
               struct.unpack_from('<II', data, 8) != (4, 800):
          return data
     walker = _Walker(data)
     pos = 16
     try:
          while pos < len(data):
               tag = bytes(walker.buf[pos:pos + 4])
               size = walker.u32(pos + 4)
               body = pos + 8
               end = body + size
               if end > len(data):
                    return data
               if tag == b'GEOS':
                    walker.geosets(body, end)
               elif tag == b'BONE':
                    walker.nodes(body, end, 8)
               elif tag == b'HELP':
                    walker.nodes(body, end, 0)
               pos = end
     except _Abort:
          return data
     out = bytes(walker.buf)
     return data if out == data else out
