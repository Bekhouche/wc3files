from enum import Enum

class DooFileType(Enum):
     DOO = 'doo'

class RandomUnitDataMode(Enum):
     NONE = -1
     ANY = 0
     GLOBAL_TABLE = 1
     CUSTOM_TABLE = 2
