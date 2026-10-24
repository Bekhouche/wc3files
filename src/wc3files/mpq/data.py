from enum import Enum
from collections import namedtuple

class MpqMagicType(Enum):
    MPQ_ARCHIVE_V1 = b'MPQ\x1a'  # Standard MPQ archive header
    MPQ_ARCHIVE_V2 = b'MPQ\x1b'  # MPQ archive with user data
    HM3W_ARCHIVE = b'HM3W'       # Blizzard's other formats, for example War3 map headers
    NGIS_ARCHIVE = b'NGIS'       # Indicates the presence of a digital signature. Must be "NGIS" ("SIGN" backwards).

class MapFileType(Enum):
    W3X = 'w3x'
    W3M = 'w3m'
    W3N = 'w3n'

class MpqFileType(Enum):
    MPQ = 'mpq'
    W3M = 'w3m'
    W3N = 'w3n'
    W3X = 'w3x'

class MpqUnpack(Enum):
    HEADER = '<4s2I2H4I'
    HEADER_v2 = 'q2h'
    HASH_TABLE = '<2IHHI'
    BLOCK_ENTRY = '<4I'

class MpqMapType(Enum):
    NotChecked = 0
    NotRecognized = 1
    Starcraft = 2
    Warcraft3 = 3
    Starcraft2 = 4
    AviFile = 5

class MpqOpenFlags(Enum):
    NoHeaderSearch = 0x00100
    ForceMPQv1 = 0x00200
    CheckSectorCRC = 0x00400
    NoListFile = 0x10000
    NoAttributes = 0x20000
    ForceListFile = 0x40000

class MpqFlags(Enum):
    ReadOnly = 0x001
    CheckSectorCRC = 0x002
    Patch = 0x004
    Changed = 0x008
    SavingTables = 0x010
    SignatureNew = 0x020
    ListFileNew = 0x040
    ListFileForce = 0x080
    AttributesNew = 0x100
    Malformed = 0x200
    StarcraftBeta = 0x400
    Warcraft3Map = 0x800
    StarcraftMap = 0x1000

class MPQFileHeaderFields(Enum):
    MAGIC = 'Magic'
    HEADER_SIZE = 'HeaderSize'
    ARCHIVE_SIZE = 'ArchiveSize'
    FORMAT_VERSION = 'FormatVersion'
    SECTOR_SIZE_SHIFT = 'SectorSizeShift'
    HASH_TABLE_OFFSET = 'HashTableOffset'
    BLOCK_TABLE_OFFSET = 'BlockTableOffset'
    HASH_TABLE_ENTRIES = 'HashTableEntries'
    BLOCK_TABLE_ENTRIES = 'BlockTableEntries'

class MPQFileHeaderExtendedFieldsFormat(Enum):
    HASH_TABLE_OFFSET_HIGH = 'HASH_TABLE_OFFSET_HIGH'
    BLOCK_TABLE_OFFSET_HIGH = 'BLOCK_TABLE_OFFSET_HIGH'

class MPQFileHeaderExtendedFieldsFormatV2(Enum):
    EXTENDED_BLOCK_TABLE_OFFSET = 'EXTENDED_BLOCK_TABLE_OFFSET'
    HASH_TABLE_OFFSET_HIGH = 'HASH_TABLE_OFFSET_HIGH'
    BLOCK_TABLE_OFFSET_HIGH = 'BLOCK_TABLE_OFFSET_HIGH'

class MPQFileHeaderExtendedFieldsFormatV3(Enum):
    ARCHIVE_SIZE_64 = 'ARCHIVE_SIZE_64'
    BET_TABLE_POS_64 = 'BET_TABLE_POS_64'
    HET_TABLE_POS_64 = 'HET_TABLE_POS_64'

class MPQFileHeaderExtendedFieldsFormatV4(Enum):
    HASH_TABLE_SIZE_64 = 'HASH_TABLE_SIZE_64'
    BLOCK_TABLE_SIZE_64 = 'BLOCK_TABLE_SIZE_64'
    HI_BLOCK_TABLE_SIZE_64 = 'HI_BLOCK_TABLE_SIZE_64'
    HET_TABLE_SIZE_64 = 'HET_TABLE_SIZE_64'
    BET_TABLE_SIZE_64 = 'BET_TABLE_SIZE_64'

class MPQFileHeaderExtendedFieldsFormatV4(Enum):
    EXTENDED_BLOCK_TABLE_OFFSET = 'EXTENDED_BLOCK_TABLE_OFFSET'
    HASH_TABLE_OFFSET_HIGH = 'HASH_TABLE_OFFSET_HIGH'
    BLOCK_TABLE_OFFSET_HIGH = 'BLOCK_TABLE_OFFSET_HIGH'

class MPQUserDataFields(Enum):
    MAGIC = 'MAGIC'
    USER_DATA_SIZE = 'USER_DATA_SIZE'
    HEADER_OFFSET = 'HEADER_OFFSET'
    USER_DATA_HEADER = 'USER_DATA_HEADER'

class MPQHashTableEntry(Enum):
    FILE_PATH_HASH_A = 'FilePathHashA'
    FILE_PATH_HASH_B = 'FilePathHashB'
    LANGUAGE = 'Language'
    PLATFORM = 'Platform'
    FILE_BLOCK_INDEX = 'FileBlockIndex'

class MPQBlockTableEntry(Enum):
    BLOCK_OFFSET = 'BlockOffset'
    BLOCK_SIZE  = 'BlockSize'
    FILE_SIZE = 'FileSize'
    FLAGS = 'Flags'

class MPQHashType(Enum):
    MPQ_HASH_TABLE_OFFSET = 0
    MPQ_HASH_NAME_A = 1
    MPQ_HASH_NAME_B = 2
    MPQ_HASH_FILE_KEY = 3

class MPQKeyType(Enum):
    MPQ_BLOCK_TABLE = '(block table)'
    MPQ_HASH_TABLE = '(hash table)'
    
class MPQFileDataType(Enum):
    IMPLODE = 0x00000100
    COMPRESS = 0x00000200
    ENCRYPTED = 0x00010000
    FIX_KEY = 0x00020000
    SINGLE_UNIT = 0x01000000
    DELETE_MARKER = 0x02000000
    SECTOR_CRC = 0x04000000
    EXISTS = 0x80000000

class MPQAttributesFlags(Enum):
    """(attributes) file bitmask - which per-block-table-entry metadata arrays
    are present. See mpq/attributes.py."""
    CRC32 = 0x00000001
    FILETIME = 0x00000002
    MD5 = 0x00000004
    PATCH_BIT = 0x00000008

class MpqProtectionLevel(Enum):
    """Opt-in MPQ-container "protection" tiers - see mpq/protection.py and
    TODO.md for what each tier does and its verification status."""
    NONE = 'none'
    SAFE = 'safe'
    EXPERIMENTAL = 'experimental'
    AGGRESSIVE = 'aggressive'