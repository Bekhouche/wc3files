from enum import Enum

class WtgFileType(Enum):
     WTG = 'wtg'

class WctFileType(Enum):
     WCT = 'wct'

class TriggerFunctionType(Enum):
     EVENT = 0
     CONDITION = 1
     ACTION = 2
     CALL = 3

class TriggerFunctionParameterType(Enum):
     PRESET = 0
     VARIABLE = 1
     FUNCTION = 2
     STRING = 3
     UNDEFINED = -1

class TriggerItemType(Enum):
     ROOT_CATEGORY = 1 << 0
     UNK1 = 1 << 1
     CATEGORY = 1 << 2
     GUI = 1 << 3
     COMMENT = 1 << 4
     SCRIPT = 1 << 5
     VARIABLE = 1 << 6
     UNK7 = 1 << 7
