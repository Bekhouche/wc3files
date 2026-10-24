from enum import Enum


class TerrainFileType(Enum):
     W3E = 'w3e'


# 'A'..'Z' main-tileset codes, per the World Editor's own tileset list (confirmed
# against real bundled/fixture tilesets: 'Y' Lordaeron Summer/City, 'X' Lordaeron
# Fall, 'C' city-cliff-adjacent codes appear as cliff-tileset prefixes).
MAIN_TILESETS = {
     'A': 'Ashenvale', 'B': 'Barrens', 'C': 'Felwood', 'D': 'Dungeon',
     'F': 'Lordaeron Fall', 'G': 'Underground', 'I': 'Icecrown',
     'J': 'Dalaran Ruins', 'K': 'Black Citadel', 'L': 'Lordaeron Summer',
     'N': 'Northrend', 'O': 'Outland', 'Q': 'Village Fall', 'V': 'Village',
     'W': 'Lordaeron Winter', 'X': 'Dalaran', 'Y': 'Cityscape', 'Z': 'Sunken Ruins',
}
