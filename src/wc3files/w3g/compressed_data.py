import struct
import zlib
from dataclasses import dataclass
from typing import ByteString, Dict, Any, List, Optional
# It is best to import only what is needed rather than using wildcard imports.
from ..data import BinaryDataType
from .data import ReplayRace
from wc3files.utils import nulltermstr, fixedlengthstr, b2i, blizdecomp, blizcomp
from . import blocks as _blocks

# Constants for magic numbers found in the binary format.
PLAYER_RECORD_MARKER = 0x16
METADATA_RECORD_MARKER = 0x19
MAX_METADATA_ATTEMPTS = 24


@dataclass(frozen=True)
class Player:
    """
    Represents a player with parsed information from raw binary data.

    Attributes:
        player_id (int): The player's unique identifier.
        name (str): The player's name.
        race (str): The player's race.
        is_host (bool): Whether the player is the host.
        runtime (int): The runtime value (e.g. in seconds) if applicable.
        raw (bytes): The raw binary data for this player.
        size (int): The number of bytes consumed from the raw data.
    """
    player_id: int = -1
    name: str = ''
    race: str = 'none'
    is_host: bool = False
    runtime: int = -1
    raw: bytes = b''
    size: int = 0

    @classmethod
    def from_raw(cls, data: ByteString) -> 'Player':
        """
        Construct a Player instance by parsing raw binary data.

        The expected binary format is:
          - Byte 0: A flag (0 indicates host).
          - Byte 1: The player’s ID.
          - Bytes 2 onward: A null-terminated string for the name.
          - Next byte: Mode flag (0x08 for ladder mode; otherwise custom).
          - Ladder mode: Next 4 bytes for runtime, following 4 bytes for race flag.
          - Custom mode: Skip <mode_flag> bytes; runtime is set to 0 and race to 'none'.

        Args:
            data (ByteString): The raw binary data.

        Returns:
            Player: A new Player instance with the parsed fields.
        """
        offset = 0
        raw_data = bytearray()

        # Host flag: player is host if the first byte equals 0.
        is_host = b2i(data[offset]) == 0
        raw_data.extend(data[offset:offset+1])
        offset += 1

        # Parse player ID (assumed to be one byte).
        player_id = b2i(data[offset])
        raw_data.extend(data[offset:offset+1])
        offset += 1

        # Parse the player's null-terminated name.
        name, name_length = nulltermstr(data[offset:])
        name_bytes = data[offset:offset + name_length + 1]
        raw_data.extend(name_bytes)
        offset += name_length + 1  # +1 for the null terminator

        # The next byte indicates the mode (ladder or custom).
        mode_flag = b2i(data[offset])
        raw_data.extend(data[offset:offset+1])
        offset += 1

        if mode_flag == 0x08:
            # Ladder mode: extract runtime and race flag.
            runtime = b2i(data[offset:offset + 4])
            raw_data.extend(data[offset:offset+4])
            offset += 4
            race_flag = b2i(data[offset:offset + 4])
            raw_data.extend(data[offset:offset+4])
            offset += 4
            race = ReplayRace[race_flag].name
        else:
            # Custom mode: skip custom data.
            skip_bytes = mode_flag
            raw_data.extend(data[offset:offset+skip_bytes]) # capture skipped bytes in raw data
            offset += skip_bytes
            runtime = 0
            race = 'none'

        total_size = offset
        final_raw_data = bytes(raw_data)

        return cls(
            player_id=player_id,
            name=name,
            race=race,
            is_host=is_host,
            runtime=runtime,
            raw=final_raw_data,
            size=total_size
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a dictionary representation of the player."""
        return {
            "player_id": self.player_id,
            "name": self.name,
            "race": self.race,
            "is_host": self.is_host,
            "runtime": self.runtime,
            "raw": self.raw.hex(), # Represent raw bytes as hex string for JSON compatibility
            "size": self.size,
        }

    @staticmethod
    def dict_to_bytes(data: Dict[str, Any]) -> bytes:
        """
        Serialize a Player.to_dict() dict back to its raw record bytes.

        Renders 'name' into the name slot (so a caller can read a replay, edit a player's
        name, and write it back), and takes everything else - the host flag/player id
        bytes, and the ladder/custom "additional data" whose exact semantics from_raw()
        doesn't otherwise model - verbatim from 'raw'.
        """
        raw = bytes.fromhex(data['raw'])
        prefix = raw[:2]
        name_bytes = data['name'].encode('utf-8') + b'\x00'
        original_name_end = raw.index(b'\x00', 2) + 1
        rest = raw[original_name_end:]
        return prefix + name_bytes + rest


@dataclass(frozen=True)
class ReforgedPlayerMetadata:
    """
    Represents reforged player metadata extracted from binary data.

    Attributes:
        player_id (int): The player's ID.
        name (str): The player's name.
        clan (str): The player's clan.
        raw (bytes): The raw binary data for this metadata.
        size (int): The number of bytes consumed from the raw data.
    """
    player_id: int = -1
    name: str = ''
    clan: str = ''
    raw: bytes = b''
    size: int = 0

    @classmethod
    def from_raw(cls, data: ByteString) -> 'ReforgedPlayerMetadata':
        """
        Parse raw binary data to extract reforged player metadata.

        The expected format is:
          - First 2 bytes: The total size of the metadata block.
          - Next 2 bytes: The player ID.
          - Next byte: Length of the player name.
          - Next <name_length> bytes: The player name (fixed-length string).
          - Next byte: Null terminator for the name.
          - Next byte: Length of the clan name.
          - Next <clan_length> bytes: The clan name (fixed-length string).
          - Next byte: Null terminator for the clan name.
          - Next byte: Extra length (currently read but not used).

        Args:
            data (ByteString): The raw binary data.

        Returns:
            ReforgedPlayerMetadata: A new instance with extracted metadata.
        """
        offset = 0
        raw_data = bytearray()
        total_size = b2i(data[offset:offset + 2])
        raw_data.extend(data[offset:offset+2])
        offset += 2

        player_id = b2i(data[offset:offset + 2])
        raw_data.extend(data[offset:offset+2])
        offset += 2

        name_length = b2i(data[offset])
        raw_data.extend(data[offset:offset+1])
        offset += 1
        name = fixedlengthstr(data[offset:], name_length)
        name_bytes = data[offset:offset + name_length + 1]
        raw_data.extend(name_bytes)
        offset += name_length + 1  # Skip the null terminator

        clan_length = b2i(data[offset])
        raw_data.extend(data[offset:offset+1])
        offset += 1
        clan = fixedlengthstr(data[offset:], clan_length)
        clan_bytes = data[offset:offset + clan_length + 1]
        raw_data.extend(clan_bytes)
        offset += clan_length + 1  # Skip the null terminator

        # Read extra length (if needed in the future).
        _extra_length = b2i(data[offset])
        raw_data.extend(data[offset:offset+1])
        offset += 1

        final_raw_data = bytes(raw_data)
        return cls(
            player_id=player_id,
            name=name,
            clan=clan,
            raw=final_raw_data,
            size=total_size
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a dictionary representation of the reforged player metadata."""
        return {
            "player_id": self.player_id,
            "name": self.name,
            "clan": self.clan,
            "raw": self.raw.hex(), # Represent raw bytes as hex string for JSON compatibility
            "size": self.size,
        }

    @staticmethod
    def dict_to_bytes(data: Dict[str, Any]) -> bytes:
        """
        Serialize a ReforgedPlayerMetadata.to_dict() dict back to its raw record bytes,
        rebuilding the length-prefixed name/clan fields from 'name'/'clan' (editable) and
        recomputing the total-size header field to match. Unlike Player, every field here
        is fully modeled by from_raw() (no unparsed "additional data"), so this rebuilds
        from scratch rather than splicing into the original 'raw'.
        """
        name_bytes = data['name'].encode('utf-8')
        clan_bytes = data['clan'].encode('utf-8')
        body = (
            bytes([len(name_bytes)]) + name_bytes + b'\x00'
            + bytes([len(clan_bytes)]) + clan_bytes + b'\x00'
            + b'\x00'  # extra length byte - read but unused by from_raw(), preserved as 0
        )
        total_size = 2 + 2 + len(body)  # total_size field itself + player_id field + body
        return struct.pack('<HH', total_size, data['player_id']) + body


class ReplayCompressedData:
    """
    Handles the compressed data section of a Warcraft III replay file.

    This class reads the compressed data blocks (per the W3G format) and extracts
    various fields such as players, game settings, map details, etc.
    """

    def __init__(self) -> None:
        self.clear()
        self.reforged_player_metadata: List[Dict[str, Any]] = []  # Explicit type hint

    def clear(self) -> None:
        """Clear any stored replay data and header information."""
        self._data: Dict[str, Any] = {}
        self._header: Dict[str, Any] = {}
        # Each block exactly as it was stored, so an unedited replay can be
        # written back byte-for-byte instead of recompressed.
        self._raw_blocks: List[Dict[str, Any]] = []
        self._source_decompressed: bytes = b''

    def set_header(self, header: Dict[str, Any]) -> None:
        """
        Set the header information needed for parsing the replay.

        The header should contain keys such as:
          - 'fileoffset': The file offset where compressed data begins.
          - 'number_compressed': The number of compressed blocks.
          - 'reforged': A boolean flag indicating if this is a reforged replay.

        Args:
            header (dict): The header information.
        """
        self._header = header

    def _read_compressed_block(self, reader, is_reforged: bool) -> bytes:
        """
        Read and decompress one block of compressed data from the reader.

        Args:
            reader: A file-like object with methods (seek, tell, read, convert).
            is_reforged (bool): Whether the replay is from a reforged version.

        Returns:
            bytes: The decompressed data for the block.

        Raises:
            ValueError: If the decompressed size does not match the expected size.
        """
        # Read the compressed block size (2 bytes)
        compressed_size = reader.read(BinaryDataType.INT, 2)

        # For reforged replays, skip an extra 2 bytes.
        if is_reforged:
            reader.seek(reader.tell() + 2)

        # Read the expected uncompressed size (2 bytes)
        uncompressed_size = reader.read(BinaryDataType.INT, 2)

        # The reserved bytes (4, plus 2 more for Reforged) are of unknown
        # meaning, so they are carried through verbatim rather than rewritten
        # as zero - if the game checks them, a rebuilt replay keeps whatever
        # it had.
        reserved = reader.read(BinaryDataType.BYTES, 6 if is_reforged else 4)

        # Read the raw compressed block data.
        compressed_data = reader.read(BinaryDataType.BYTES, compressed_size)

        # Keep the block exactly as it was stored. The game's own compressor
        # is not zlib level 9, so recompressing the same bytes produces a
        # valid but different - and measurably smaller - file: across 25 real
        # replays a rebuild came out 5-7% smaller every time. Holding the
        # original records lets write() reproduce an unedited replay exactly.
        self._raw_blocks.append({
            'compressed': bytes(compressed_data),
            'uncompressed_size': uncompressed_size,
            'reserved': reserved,
        })

        # Decompress using zlib.
        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(compressed_data, uncompressed_size)

        if len(decompressed) != uncompressed_size:
            raise ValueError(
                f"Decompressed block size ({len(decompressed)}) does not match expected size ({uncompressed_size})."
            )

        return decompressed

    def read(self, reader) -> None:
        """
        Parse the compressed replay data from the provided reader.

        This method decompresses all data blocks, concatenates the decompressed data,
        and then extracts various fields such as players, game settings, map names, etc.

        Args:
            reader: A file-like object with necessary methods.

        Raises:
            ValueError: If any decompression block's size is incorrect.
        """
        file_offset = self._header.get('fileoffset', 0)
        num_blocks = self._header.get('number_compressed', 0)
        is_reforged = self._header.get('reforged', False)

        # Jump to the start of the compressed data.
        reader.seek(file_offset)
        uncompressed_data = bytearray()
        self._raw_blocks = []

        # Process each compressed block.
        for _ in range(num_blocks):
            block = self._read_compressed_block(reader, is_reforged)
            uncompressed_data.extend(block)

        # Each compressed block decompresses to a full, fixed-size chunk (matching the size
        # it was originally compressed from) even when its real content is shorter than that
        # - the true content length is size_decompressed from the header, and anything past
        # it is trailing pad bytes, not replay data.
        size_decompressed = self._header.get('size_decompressed')
        if size_decompressed is not None:
            uncompressed_data = uncompressed_data[:size_decompressed]
        self._source_decompressed = bytes(uncompressed_data)

        data = bytes(uncompressed_data)
        self._data = {}
        # Seen as the constant b'\x10\x01\x00\x00' across every real replay checked -
        # meaning/semantics undetermined, kept verbatim rather than assumed to be padding.
        self._data['_leading_bytes'] = data[:4]
        offset = 4
        self._data['Players'] = [] # Initialize Players as a list

        # --- Parse the first player ---
        first_player = Player.from_raw(data[offset:])
        self._data['Players'].append(first_player.to_dict())
        offset += first_player.size

        # --- Parse the null-terminated game name ---
        game_name, name_length = nulltermstr(data[offset:])
        self._data['GameName'] = game_name
        offset += name_length + 1

        # --- Parse the null-terminated game password (usually empty) ---
        # What looked like one stray reserved byte here in most replays is actually this
        # field: empty (just its own null terminator) unless the host set a real password -
        # confirmed against a real replay with a real password ("hunter2") set.
        password, password_length = nulltermstr(data[offset:])
        self._data['GamePassword'] = password
        offset += password_length + 1

        # --- Parse additional game settings from a decompressed block ---
        decompressed_block, block_offset = blizdecomp(data[offset:])
        # Keep both sides of the encoding: blizcomp() is not a byte-exact
        # inverse of blizdecomp() for every input the game produces, so
        # re-encoding an unedited settings block changes it. Holding the
        # original encoded bytes alongside what they decode to lets build()
        # put them back untouched when nothing in them changed.
        self._data['_raw_settings_encoded'] = bytes(data[offset:offset + block_offset + 1])
        self._data['_raw_settings_decoded'] = bytes(decompressed_block)
        offset += block_offset + 1

        # The first 13 bytes of the decompressed block hold game settings. Bytes 4-8 are
        # unused/undocumented - not decoded into game_settings below, but kept verbatim in
        # _raw_settings so write() can reproduce them unchanged instead of zeroing them out.
        raw_settings = decompressed_block[:13]
        self._data['_raw_settings'] = raw_settings
        game_settings = {
            "GameSpeed": raw_settings[0] & 0b00000011,
            "Visibility": {
                "hide_terrain": bool(raw_settings[1] & (1 << 0)),
                "map_explored": bool(raw_settings[1] & (1 << 1)),
                "always_visible": bool(raw_settings[1] & (1 << 2)),
                "default": bool(raw_settings[1] & (1 << 3)),
            },
            "Observer": (raw_settings[1] >> 4) & 0b11,
            "TeamsTogether": bool(raw_settings[1] & (1 << 6)),
            "FixedTeams": (raw_settings[2] >> 1) & 0b11,
            "FullSharedUnitControl": bool(raw_settings[3] & (1 << 0)),
            "RandomHero": bool(raw_settings[3] & (1 << 1)),
            "RandomRaces": bool(raw_settings[3] & (1 << 2)),
            "ObserverReferees": bool(raw_settings[3] & (1 << 6)),
            "MapChecksum": reader.convert(
                raw_settings[9:13],
                BinaryDataType.BYTES,
                BinaryDataType.INT
            )
        }
        self._data['GameSettings'] = game_settings

        # --- Parse map and creator names ---
        map_name, map_name_length = nulltermstr(decompressed_block[13:])
        self._data['MapName'] = map_name
        creator_name, creator_name_length = nulltermstr(decompressed_block[13 + map_name_length + 1:])
        self._data['CreatorName'] = creator_name

        # The blizdecomp'd block has further bytes after creator_name (seen in practice -
        # looks like a random seed/hash, undecoded) before its own terminator. Keep them so
        # write() can reproduce the block exactly instead of truncating it.
        settings_consumed = 13 + map_name_length + 1 + creator_name_length + 1
        self._data['_settings_tail'] = decompressed_block[settings_consumed:]

        # --- Parse player count ---
        player_count = b2i(data[offset:offset + 4])
        self._data['PlayerCount'] = player_count
        offset += 4

        # --- Parse game type information ---
        game_type_value = b2i(data[offset])
        self._data['GameType'] = {"GameType": game_type_value}
        offset += 1

        privacy = b2i(data[offset])
        # isPublic/isPrivate model only 0x00/0x08; real replays have been seen with other
        # privacy byte values (neither flag set) - keep the raw byte too so build() can
        # reproduce it exactly instead of collapsing an unrecognized value to "public".
        self._data['GameType']['privacy'] = privacy
        self._data['GameType']['isPublic'] = (privacy == 0x00)
        self._data['GameType']['isPrivate'] = (privacy == 0x08)
        offset += 1

        # The byte between the privacy byte and the language id is not always
        # zero - 20 of 25 real replays here carry 0x49 - so it is kept rather
        # than written back as 0, which is what made those replays fail to
        # reproduce.
        self._data['_reserved_after_privacy'] = data[offset:offset + 1]
        offset += 1

        # --- Parse language ID ---
        self._data['LanguageID'] = data[offset:offset + 4]
        offset += 4

        # One reserved/unknown byte sits between LanguageID and the additional
        # player records - skipping it is exactly what's needed to land on the
        # 0x16 PLAYER_RECORD_MARKER of the next joined player instead of
        # stopping the loop below before it even starts. Kept, not assumed.
        self._data['_reserved_before_players'] = data[offset:offset + 1]
        offset += 1

        # --- Parse additional player records (if present) ---
        while offset < len(data) and b2i(data[offset]) == PLAYER_RECORD_MARKER: # Add offset boundary check
            player = Player.from_raw(data[offset:])
            self._data['Players'].append(player.to_dict())
            # Skip the player record bytes plus 4 unknown padding bytes.
            offset += player.size + 4

        # --- Parse reforged player metadata if available ---
        self.reforged_player_metadata = [] # Initialize reforged_player_metadata here to ensure it's reset on each read
        # This leading marker byte is consumed whenever present, even if zero metadata
        # entries turn out to follow it (a real, if unusual, replay observed in practice) -
        # build() needs to know that byte existed to re-emit it, since an empty
        # reforged_player_metadata list alone can't distinguish "no marker" from "marker,
        # no entries".
        self._data['_has_reforged_metadata_marker'] = offset < len(data) and b2i(data[offset]) == METADATA_RECORD_MARKER
        if self._data['_has_reforged_metadata_marker']: # Check offset boundary before accessing data[offset]
            offset += 1 # Skip METADATA_RECORD_MARKER
            metadata_attempts = 0
            while offset < len(data) and b2i(data[offset]) != PLAYER_RECORD_MARKER and metadata_attempts < MAX_METADATA_ATTEMPTS and b2i(data[offset]) == METADATA_RECORD_MARKER: # Added offset check and marker check in while condition
                metadata = ReforgedPlayerMetadata.from_raw(data[offset:])
                self.reforged_player_metadata.append(metadata.to_dict())
                offset += metadata.size

                # Check for record marker again after metadata and skip if present and increment offset.
                if offset < len(data) and b2i(data[offset]) == METADATA_RECORD_MARKER: # Check if there is another metadata marker immediately after and skip it.
                    offset += 1 # Skip METADATA_RECORD_MARKER after each metadata entry

                metadata_attempts += 1
        # Everything from here on (chat messages, timeslots/actions, leave-game records, ...)
        # isn't parsed - kept verbatim so write() can reproduce a full, valid replay by
        # rebuilding only the fields actually parsed above and reattaching this untouched.
        tail = data[offset:]
        # The tail is not opaque: it is the Reforged player records, the
        # game-start record and then the block stream (chat, time slots,
        # leave-game). Parse it, and fall back to carrying it verbatim if
        # anything in there is not recognized, so an unusual replay still
        # round-trips rather than failing to read at all.
        try:
            reforged_players, game_start, parsed = _blocks.parse_stream(
                tail,
                marker_already_consumed=self._data['_has_reforged_metadata_marker'],
            )
        except _blocks.ReplayBlockError:
            self._data['_raw_tail'] = tail
        else:
            self._data['reforged_players'] = reforged_players
            self._data['game_start'] = game_start
            self._data['blocks'] = parsed

    def get_data(self) -> Dict[str, Any]:
        """
        Return the parsed replay data as a dictionary.

        Returns:
            dict: The extracted replay data.
        """
        return self._data

    def build(self) -> bytes:
        """
        Rebuild the full decompressed replay-data stream from self._data - the inverse of
        read(). Mirrors read() field for field: structured fields (players, game settings,
        map/creator name, ...) are re-encoded from self._data, so editing e.g. a player's
        name or the map name and calling build() reflects that; everything read() doesn't
        model is reattached verbatim from what it captured (_leading_bytes, each player's
        unparsed "additional data", _raw_settings, _settings_tail, reforged player metadata,
        and _raw_tail for the action-block stream and everything after).
        """
        data = self._data
        players = data.get('Players', [])
        if not players:
            raise ValueError("Cannot build replay data with no players.")

        out = bytearray()
        out.extend(data.get('_leading_bytes', b'\x10\x01\x00\x00'))
        out.extend(Player.dict_to_bytes(players[0]))
        out.extend(data.get('GameName', '').encode('utf-8'))
        out.append(0)  # name terminator
        out.extend(data.get('GamePassword', '').encode('utf-8'))
        out.append(0)  # password terminator

        raw_settings = bytearray(data.get('_raw_settings', bytes(13)))
        settings = data.get('GameSettings', {})
        if settings:
            raw_settings[0] = (raw_settings[0] & ~0b11) | (settings.get('GameSpeed', 0) & 0b11)
            visibility = settings.get('Visibility', {})
            byte1 = raw_settings[1]
            for bit, key in ((0, 'hide_terrain'), (1, 'map_explored'), (2, 'always_visible'), (3, 'default')):
                mask = 1 << bit
                byte1 = (byte1 | mask) if visibility.get(key) else (byte1 & ~mask)
            byte1 = (byte1 & ~0b1110000) | ((settings.get('Observer', 0) & 0b11) << 4)
            byte1 = (byte1 | (1 << 6)) if settings.get('TeamsTogether') else (byte1 & ~(1 << 6))
            raw_settings[1] = byte1
            raw_settings[2] = (raw_settings[2] & ~0b110) | ((settings.get('FixedTeams', 0) & 0b11) << 1)
            byte3 = raw_settings[3]
            for bit, key in ((0, 'FullSharedUnitControl'), (1, 'RandomHero'), (2, 'RandomRaces'), (6, 'ObserverReferees')):
                mask = 1 << bit
                byte3 = (byte3 | mask) if settings.get(key) else (byte3 & ~mask)
            raw_settings[3] = byte3
            raw_settings[9:13] = struct.pack('<I', settings.get('MapChecksum', 0))

        settings_block = bytes(raw_settings)
        settings_block += data.get('MapName', '').encode('utf-8') + b'\x00'
        settings_block += data.get('CreatorName', '').encode('utf-8') + b'\x00'
        settings_block += data.get('_settings_tail', b'')
        if settings_block == data.get('_raw_settings_decoded'):
            # Unchanged, so put the original encoding back rather than a
            # freshly computed one that decodes the same but differs byte for
            # byte.
            out.extend(data['_raw_settings_encoded'])
        else:
            out.extend(blizcomp(settings_block))

        out.extend(struct.pack('<I', data.get('PlayerCount', len(players))))

        game_type = data.get('GameType', {})
        out.append(game_type.get('GameType', 0) & 0xFF)
        if 'privacy' in game_type:
            out.append(game_type['privacy'] & 0xFF)
        else:
            out.append(0x08 if game_type.get('isPrivate') else 0x00)
        out.extend(data.get('_reserved_after_privacy', b'\x00')[:1] or b'\x00')
        out.extend(data.get('LanguageID', b'\x00\x00\x00\x00'))
        out.extend(data.get('_reserved_before_players', b'\x00')[:1] or b'\x00')

        for player in players[1:]:
            out.extend(Player.dict_to_bytes(player))
            out.extend(b'\x00\x00\x00\x00')

        # Verified against real replays only for the zero-entries case (a leading marker
        # with nothing after it - see the comment on _has_reforged_metadata_marker in
        # read()). No real fixture has actual metadata entries to confirm this against, so
        # this reproduces read()'s own marker-consumption pattern (one marker, then each
        # entry) rather than guessing further.
        if data.get('_has_reforged_metadata_marker') or self.reforged_player_metadata:
            out.append(METADATA_RECORD_MARKER)
            for metadata in self.reforged_player_metadata:
                out.extend(ReforgedPlayerMetadata.dict_to_bytes(metadata))

        if 'blocks' in data:
            out.extend(_blocks.build_stream(data.get('reforged_players'),
                                            data.get('game_start'),
                                            data['blocks']))
        else:
            out.extend(data.get('_raw_tail', b''))
        return bytes(out)