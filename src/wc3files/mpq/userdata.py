import struct
from ..data import BinaryDataType
from .data import *

class MpqUserData:
     """
     Class for MPQ User Data (optional)
     """

     def __init__(self):
          """Initialize by clearing any stored MPQ data."""
          self.clear()

     def clear(self):
          """Clear any previously stored MPQ data."""
          self._data = {}

     def read(self, reader):
          """Read and parse an MPQ user-data header, if the archive has one.

          An `MPQ` archive doesn't start with the MPQ header itself - it starts
          with a 16-byte user-data header whose HEADER_OFFSET field says where the
          real MPQ header lives, relative to the start of the user-data header.
          Everything downstream (hash/block tables, file sectors) is already
          expressed relative to the header's own recorded position, which is why an
          HM3W map's archive-at-0x200 layout works - so all this has to get right is
          leaving ARCHIVE_OFFSET pointing at the real header.

          Until now this was unfinished scaffolding: it recognized the magic and then
          did nothing, leaving ARCHIVE_OFFSET at the user-data header, so MpqHeader
          rejected the file outright ("Invalid MPQ file header. Got: b'MPQ'").
          That was a clean refusal rather than a misparse, but it did mean these
          archives couldn't be opened at all.

          No real Warcraft III file exercises this: of 168 real local archives, 158
          are plain `MPQ` and 9 are `HM3W`, and none carry a user-data header
          (it's mostly a StarCraft II / WoW-era layout). So unlike the rest of this
          package's MPQ handling, the field layout here comes from the public format
          documentation rather than from real Warcraft III bytes. What it IS verified
          against is a real archive wrapped in a user-data header: the inner archive
          is genuine, and the parsed result must match parsing that same archive
          unwrapped, file for file (see tests/test_mpq.py).
          """
          current_offset = reader.tell()
          magic = reader.read(BinaryDataType.BYTES)
          reader.seek(current_offset)
          self._data['ARCHIVE_OFFSET'] = current_offset

          if magic == MpqMagicType.MPQ_ARCHIVE_V2.value:
               self._data[MPQUserDataFields.MAGIC.value] = reader.read(BinaryDataType.BYTES, num=4)
               self._data[MPQUserDataFields.USER_DATA_SIZE.value] = reader.read(BinaryDataType.INT)
               self._data[MPQUserDataFields.HEADER_OFFSET.value] = reader.read(BinaryDataType.INT)
               self._data[MPQUserDataFields.USER_DATA_HEADER.value] = reader.read(BinaryDataType.INT)

               header_offset = self._data[MPQUserDataFields.HEADER_OFFSET.value]
               if header_offset < 16:
                    # The real header would have to sit inside the 16-byte user-data
                    # header itself. Raise rather than seek somewhere nonsensical.
                    raise ValueError(
                         f'Invalid MPQ user data header: HEADER_OFFSET is {header_offset}, '
                         f'which points inside the 16-byte user data header itself.'
                    )

               # Keep the user-data span itself, so it isn't silently lost on
               # inspection (nothing in this package writes these archives yet).
               self._data['USER_DATA'] = reader.read(BinaryDataType.BYTES,
                                                     num=header_offset - 16)
               self._data['ARCHIVE_OFFSET'] = current_offset + header_offset

          reader.seek(self._data['ARCHIVE_OFFSET'])

     def get_data(self):
          """Return the parsed MPQ data."""
          return self._data
