# Game data for other versions

The package bundles the tables of one game version, Reforged: the SLKs and
strings behind object data, `common.j` and `Blizzard.j` behind the script
checkers and the runtime, `TriggerData.txt` behind GUI triggers. A map made
for an older version is best read against that version's tables, since its
object fields, natives and GUI functions are those of that version.

A *profile* is a folder with the same tables, copied from a real install.

```bash
wc3files gamedata-extract "C:/Games/Warcraft III 1.27" profiles/enUS-1.27.1
wc3files --game-data profiles/enUS-1.27.1 obj-to-slk war3map.w3u out/
wc3files --game-data profiles/enUS-1.27.1 jass war3map.j --check
```

```python
from wc3files import gamedata

gamedata.extract('C:/Games/Warcraft III 1.27', 'profiles/enUS-1.27.1')
gamedata.use('profiles/enUS-1.27.1')    # every table is read from the profile
gamedata.use(None)                      # back to the bundled ones
```

## 1. Which installs

- **Classic installs** (1.31 and older): the tables are read from
  `war3.mpq`, `war3local.mpq`, `war3x.mpq`, `war3xlocal.mpq` and
  `war3patch.mpq`, a later archive overriding an earlier one, as the game
  loads them. The language is that of the install's locale archives.
- **CASC installs** (1.32 and later, Reforged): the tables are read from the
  game's storage; `--locale` picks the strings (only `enUS` is readable from
  a normal install, see [CASC](formats.md)).

## 2. What a profile holds

Each table is parsed with this package's own reader before it is written; a
table that does not parse is refused and reported, not copied. The file
`profile.json` records the install, its version, the archive each table came
from, and the tables the version does not have. Those (the skin tables are
Reforged-only) are read from the bundled copies.

## 3. Why the data is not bundled

wc3files is MIT-licensed. The versions w3x2lni bundles (zhCN 1.24.4, enUS
1.27.1, zhCN 1.32.8) are in a GPL-3 project, and their files are the game's,
so a profile is made from an install its user owns, not copied from either.
