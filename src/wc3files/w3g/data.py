from enum import Enum

class ReplayFileType(Enum):
     W3G = 'w3g'

class ReplayRace(Enum):
    HUMAN           = 0x01
    ORC             = 0x02
    NIGHTELF        = 0x04
    UNDEAD          = 0x08 
    DAEMON          = 0x10
    RANDOM          = 0x20
    SELECTABLE_FIXED= 0x40