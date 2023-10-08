from enum import Enum

class BinaryDataType(Enum):
     INT = 0
     REAL = 1
     UNREAL = 2
     STRING = 3
     BYTES = 4

class ObjectFileType(Enum):
     W3O = 'w3o'
     W3U = 'w3u'
     W3T = 'w3t'
     W3B = 'w3b'
     W3D = 'w3d'
     W3A = 'w3a'
     W3H = 'w3h'
     W3Q = 'w3q'

class RegionFileType(Enum):
     W3R = 'w3r'

class ObjectDataType(Enum):
     int = BinaryDataType.INT
     real = BinaryDataType.REAL
     unreal = BinaryDataType.UNREAL
     string = BinaryDataType.STRING
     bool = BinaryDataType.INT
     char = BinaryDataType.STRING
     unitList = BinaryDataType.STRING
     itemList = BinaryDataType.STRING
     regenType = BinaryDataType.STRING
     attackType = BinaryDataType.STRING
     weaponType = BinaryDataType.STRING
     #targetType = BinaryDataType.STRING
     moveType = BinaryDataType.STRING
     defenseType = BinaryDataType.STRING
     pathingTexture = BinaryDataType.STRING
     upgradeList = BinaryDataType.STRING
     stringList = BinaryDataType.STRING
     abilityList = BinaryDataType.STRING
     heroAbilityList = BinaryDataType.STRING
     #missileArt = BinaryDataType.STRING
     attributeType = BinaryDataType.STRING
     attackBits = BinaryDataType.INT

     abilitySkinList = BinaryDataType.STRING
     modelList = BinaryDataType.STRING
     targetList = BinaryDataType.STRING
     techList = BinaryDataType.STRING
     tilesetList = BinaryDataType.STRING
     pathingListRequire = BinaryDataType.STRING
     pathingListPrevent = BinaryDataType.STRING
     intList = BinaryDataType.INT

     abilCode = BinaryDataType.STRING
     aiBuffer = BinaryDataType.STRING
     armorType = BinaryDataType.STRING
     combatSound = BinaryDataType.STRING
     icon = BinaryDataType.STRING
     model = BinaryDataType.STRING
     shadowTexture = BinaryDataType.STRING
     shadowImage = BinaryDataType.STRING
     soundLabel = BinaryDataType.STRING
     uberSplat = BinaryDataType.STRING
     unitClass = BinaryDataType.STRING
     unitSound = BinaryDataType.STRING
     unitRace = BinaryDataType.STRING
     
     deathType = BinaryDataType.INT
     teamColor = BinaryDataType.INT
     versionFlags = BinaryDataType.INT
