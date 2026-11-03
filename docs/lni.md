# Lni mode: a map as text

`wc3files lni` turns a map into a folder of text files that can be read,
diffed, reviewed and kept in git, and turns such a folder back into a map.
It follows the layout of [w3x2lni](https://github.com/sumneko/w3x2lni)'s Lni
mode, written from its file format and behaviour (wc3files contains none of
its code).

```bash
wc3files lni MyMap.w3x MyMap/          # map -> folder
wc3files lni MyMap/ MyMap-built.w3x    # folder -> map
```

```python
from wc3files.lni import map_to_lni, lni_to_map

map_to_lni('MyMap.w3x', 'MyMap')
lni_to_map('MyMap', 'MyMap-built.w3x')
```

## 1. The folder

```
MyMap/
    wc3files-lni.json     what each file of the map became
    table/                object data as Lni (unit.ini, ability.ini, item.ini,
                          buff.ini, doodad.ini, destructable.ini, upgrade.ini,
                          skin_*.ini for the Reforged skin files), and map
                          info, imports, strings, regions, cameras, sounds,
                          placed units and doodads as JSON
    trigger/              war3map.wtg and war3map.wct as JSON
    scripts/              war3map.j, war3map.lua and other scripts
    resource/             models and textures
    sound/                sounds and music
    map/                  everything else, as it is (terrain, pathing, ...)
```

The terrain, pathing and shadow maps stay binary under `map/`: their text
form would be a large grid that no one reviews.

## 2. Object data

One section per object, only what the map changes, in w3x2lni's syntax:

```ini
[h000]
_parent = 'hfoo'
-- unam: Name
Name = 'Footman Captain'
-- umvs: spd
spd = 300

[A000]
_parent = 'AHbz'
-- Hbz1: DataA
Hbz1 = {10.0, 20.0, 30.0}
```

- A custom object has `_parent`, the object it was made from; a changed
  standard object has none.
- A field is named by its SLK column (`Name`, `spd`, `Buttonpos_1`) when that
  name means a single field for the object; otherwise by its four-letter id.
  Ability data is the usual case: `DataA` is a different field for each
  ability, so it is written as `Hbz1`. The comment line above each field
  gives both.
- A value per level is a table: `{1, 2, 3}` for levels 1 to n, `{[2] = 5}`
  when only some levels are set.
- Strings are in single quotes, or in `[[ ]]` for text with line breaks.
  A carriage return is always written as `\r`, so git and text editors
  cannot change it.
- What the binary file holds beyond that is written only when it is not the
  usual value: a field's stored type, data pointer or end marker, as
  `Key__type`, `Key__ptr`, `Key__end`.

## 3. Nothing is lost

Each conversion is checked as it is made. A file is written as JSON only if
building it back from that JSON gives its exact bytes, and as Lni only if
reading the Lni back gives the same objects; otherwise it is stored as it is
under `map/`. So a folder always rebuilds the map's files:

- every file other than object data comes back byte for byte;
- object files come back with the same objects, fields, values and file
  version, though not necessarily byte for byte: the order in which the
  editor wrote the levels of a field is not kept (w3x2lni does not keep it
  either).

On the 3,306 object files of the 361-map benchmark corpus, 3,304 convert to
Lni and back with the same data; the other 2 are files the object reader
refuses (two entries share one object id). On sample maps of the corpus,
every file came back.

## 4. Differences from w3x2lni

- Files other than object data are JSON (the same JSON `wc3files dump`
  writes), not w3x2lni's own `.ini`/`.lml` layouts; triggers are therefore
  JSON rather than `.lml` files.
- Field keys are the SLK column names of the bundled Reforged metadata, and
  the comment lines are not translated display names.
- A folder written by w3x2lni cannot be read by wc3files, nor the other way
  round, unless it only holds object data in the syntax above.

## 5. Slk and Obj modes

w3x2lni's other two formats, for a whole map:

```bash
wc3files slk MyMap.w3x MyMap-release.w3x    # object data into the game's tables
wc3files obj MyMap-release.w3x MyMap.w3x    # and back into object files
```

**Slk mode** moves every object file's data into the tables the game reads
(`Units\UnitData.slk`, `Units\AbilityData.slk`, `Doodads\Doodads.slk`, ...).
Only what no table has a column for stays in the object files: models,
icons, tooltips (the metadata's `Profile` fields), and each custom object's
parent. It is a format for releasing a map, not for editing it in the World
Editor.

Each object file is checked before it is converted: the tables and the
smaller object file are read back, and every field that would not come back
exactly (one equal to the game's default, a value a table rounds) is kept in
the object file, which the game applies over the tables. An object file
that still would not come back is left as it is, and so is a category whose
tables the map already ships. On sample maps of the benchmark corpus, all
116 object files converted, and Slk then Obj gave back the same objects,
fields and values for every one.

**Obj mode** is the reverse: the tables become ordinary object files again
and are removed. A custom object's parent comes from the object file Slk
mode left; for a map converted by another tool, without that, the report
lists the objects whose parent had to be guessed.

These tables were checked against the files of a real map that ships its
data this way; the files this mode writes have not yet been loaded in a
Reforged client, so test a converted map in the game before releasing it.
