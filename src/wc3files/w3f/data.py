from enum import Enum

class W3fFileType(Enum):
     W3F = 'w3f'

class CampaignRace(Enum):
     HUMAN = 0
     ORC = 1
     UNDEAD = 2
     NIGHT_ELF = 3

class CampaignBackgroundVersion(Enum):
     DEFAULT = 0
     CLASSIC = 1
     REFORGED = 2

class FogStyle(Enum):
     UNSPECIFIED = -1
     LINEAR = 0
     EXPONENTIAL_1 = 1
     EXPONENTIAL_2 = 2
