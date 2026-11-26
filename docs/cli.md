# Command line

Installing the package puts a `wc3files` command on your path.

```bash
wc3files --help
wc3files <command> --help     # every command documents its own options
```

When that command is not on your path (common on Windows), `python -m
wc3files` runs the same thing: `python -m wc3files unpack MyMap.w3x out/`.

28 commands, grouped below by what they are for.

## Archives

```bash
wc3files unpack MyMap.w3x out/          # extract to loose files
wc3files pack out/ MyMap.edited.w3x     # repack a directory
```

For a folder that is text rather than binary, use [Lni mode](lni.md):
`wc3files lni MyMap.w3x MyMap/` and `wc3files lni MyMap/ Out.w3x`.
`wc3files slk` and `wc3files obj` convert a whole map's object data to the
game's tables and back ([Slk and Obj modes](lni.md#5-slk-and-obj-modes)).
`wc3files optimize MyMap.w3x Release.w3x` removes unused objects and
editor-only files ([Optimizing a map](lni.md#6-optimizing-a-map)).

`unpack` then `pack` is the whole edit cycle: unpack, change files on disk,
pack. `pack` round-trips through the archive writer, so the result is a fresh
archive rather than a patched copy of the original.

A protected map has no `(listfile)`, or one that leaves files out. `unpack`,
`lni`, `slk`, `obj`, `optimize` and `run-map` refuse it, because the files
the listfile does not name would be lost:

```
error: MyMap.w3x is protected: it has no (listfile). Reading it as it is would lose those files.
```

## Imported files

```bash
wc3files import-folder MyMap.w3x Assets/ Out.w3x                             # same relative paths
wc3files import-folder MyMap.w3x Assets/ Out.w3x --prefix war3mapImported    # under war3mapImported\
wc3files import-folder MyMap.w3x Assets/ Out.w3x --keep-existing             # do not replace members
```

`import-folder` adds every file under a folder, subfolders included, to the
map at the same relative path, and lists each one in `war3map.imp`. The World
Editor's import manager takes one file or one folder at a time, without its
subfolders. A new `war3map.imp` entry takes the flags the map's own entries
use most, or 13 for a map with no imports yet.

A member the map already has is matched ignoring case and replaced, or kept
with `--keep-existing`. A file at the root of the folder that is named as map
data (`war3map.w3i`, `war3map.j`, `war3mapUnits.doo`, ...) is skipped and
reported, since the map owns these files and `war3map.imp` does not list
them.

```bash
wc3files import-move MyMap.w3x Out.w3x --move war3mapImported\\BTNHero.blp ReplaceableTextures\\CommandButtons\\BTNHero.blp
wc3files import-move MyMap.w3x Out.w3x --strip-prefix         # every war3mapImported\ member
wc3files import-move MyMap.w3x --strip-prefix --report-only   # print what would change
```

`import-move` renames imported files and rewrites every reference to them:

1. the object data (`war3map.w3u` and the others, `war3mapSkin.*`), the map
   info, the sounds and the GUI triggers;
2. the script, the custom-text triggers of `war3map.wct`, FDF, TOC, TXT, SLK,
   MDL and `war3map.wts`;
3. the texture, attachment and particle paths inside MDX models;
4. the entries of `war3map.imp`.

Names are matched ignoring case and with either separator, and a reference
keeps its own separator. A model is matched as `.mdl` or `.mdx`, since the
game loads either, and its `_portrait` model moves with it (`--no-portraits`
to leave it). `--strip-prefix` takes `war3mapImported\` off every member
under it, or off the members `--only` names; a member whose new name the map
already has is skipped.

What it cannot update is printed as `not updated`:

- the old path, or the file name alone, where a reference was not rewritten;
- a script string that ends in a moved file's old folder, such as
  `"war3mapImported\\" + name`, which builds the path while the script runs;
- a path an MDX field has no room for: the field is 260 bytes;
- a binary file that does not read before or after the edit, which is left
  as it was.

## Inspecting map sub-files

```bash
wc3files dump w3i war3map.w3i                 # to stdout as JSON
wc3files dump w3i war3map.w3i info.json       # to a file
wc3files build w3i info.json war3map.w3i      # and back again
```

`dump` reads one sub-file and prints it as JSON; `build` turns that JSON back
into the binary file. Together they are the quickest way to change one field
by hand. The format name comes first, and can be left out when the file name
says it (`dump war3map.w3i`, `build info.json war3map.w3i`):

```
cfg  doo  doodads  imp  mmp  obj  shd  slk  units  w3c
w3e  w3f  w3g  w3i  w3r  w3s  wct  wes  wpm  wtg  wts
```

`units` and `doo` are `war3mapUnits.doo` (units and items); `doodads` is
`war3map.doo`. They share an extension and a magic number but not a layout.
`doo` keeps its old meaning, so existing commands still work; by its name,
`dump war3mapUnits.doo` and `dump war3map.doo` pick the right one.

A shadow map (`shd`) has no header. Its size is read from a `war3map.wpm` or
`war3map.w3e` next to it, or given with `--width` and `--height`.

## Maps in git

A map saved as a folder (the World Editor's *Save as folder*, or `wc3files
unpack`) can be kept in git, but most of its files are binary, so `git diff`
only says that they changed. `textconv` prints a file as text that git can
compare:

```bash
wc3files textconv war3map.doo
```

What it prints depends on the file name:

1. a file `dump` reads (`war3map.w3i`, `war3map.doo`, `war3mapUnits.doo`,
   `war3map.w3e`, the object files, ...): its `dump` JSON, with each record on
   one line, so a moved doodad or a changed tile is one changed line;
2. a text file (`war3map.j`, `war3map.lua`, `war3map.wts`, SLK, FDF, MDL,
   `(listfile)`): the file as it is, and an MDX model as MDL;
3. any other file (images, sounds): one line with its size and SHA-256.

A file that does not read gives the third form, with the error on a second
line, so the diff of the other files still runs.

To have `git diff` use it, add a `.gitattributes` to the repository:

```
war3map.* diff=wc3files
war3mapUnits.doo diff=wc3files
war3mapSkin.* diff=wc3files
*.mdx diff=wc3files
```

and tell git what the `wc3files` diff driver runs, once per clone:

```bash
git config diff.wc3files.textconv "wc3files textconv"
git config diff.wc3files.cachetextconv true
```

`cachetextconv` keeps the text of each committed version, so `git log -p`
does not read the same file twice. The driver changes only what `git diff`,
`git log -p` and `git show` print; merges and the files themselves are not
affected.

git gives `textconv` a temporary copy of the old version, named after the
file, with no other map file beside it. A shadow map has no header and its
size comes from the terrain next to it, so the old copy of a `war3map.shd` is
printed in rows of 64 cells rather than rows of the map's width.

## Object data

```bash
wc3files obj-to-slk war3map.w3u out/
wc3files obj-to-override war3map.w3u out/
wc3files override-to-obj out/ war3map.w3u
wc3files set-object-icon war3map.w3u hfoo ReplaceableTextures/.../BTNFoo.blp
```

`obj-to-slk` flattens an object file into readable tables - what the map
actually customises, with real column names from the bundled schema.
Unrecognised fields keep their raw 4-character code as the column name rather
than being dropped.

`obj-to-override` / `override-to-obj` convert between an object file and
literal game-loadable default-table overrides, in both directions. Not
everything can move (icons, models, tooltips and some editor-only fields stay
behind), and what cannot is kept rather than silently dropped.

### Checking object data

`obj-to-slk --validate` reports fields that break the bundled Blizzard
metadata (and still writes the tables); `build obj --validate` refuses to
write instead. Both check the map's own values, not the base game's:

- numeric range (`minVal`, `maxVal`) and sign for `int`, `real` and `unreal`
  fields, and a number where text is stored or the other way round;
- emptiness, except for list fields (an empty ability or build list is a
  normal edit);
- for the fields with a fixed set of values (race, armor type, attack type,
  unit class, targets, ...), each value of a comma-separated list against
  that set.

Fields the metadata does not know, and free-form fields such as rawcodes and
paths, are not checked. A finding has one of two severities: `note` for a
value below a minimum of 0 (often a deliberate negative effect), `issue` for
everything else. `--validate` on `build` stops only on `issue`. Even an
`issue` can be intentional: `maxVal` is the World Editor's slider limit, not
something the game enforces, and maps often pass it on purpose.

### Strings and tooltips

```bash
wc3files map-strings map.w3x            # every name, tooltip and description, by object
wc3files map-strings map.w3x --check    # missing strings, broken references, copied tips
```

`map-strings` lists the text fields of each object of the map (name,
tooltips, description, editor suffix, ...) at each level, with the
`TRIGSTR_` id when the text is in `war3map.wts`, or `inline` when it is in
the object data itself, as in an optimized map. It then gives the other
files that use `war3map.wts` (map info, script, triggers) and the number of
strings nothing uses. `--json` gives all of it, the unused strings included.

`--check` reports:

- `missing string` (issue): a `TRIGSTR_` reference with no entry in
  `war3map.wts`. The game shows the reference itself;
- `unresolved reference` (issue): a `<A000,DataA1>` in the text whose object
  or field does not exist, resolved as `optimize --computed-text` resolves
  them. The game shows nothing in its place;
- `other object's name` (note): a short tooltip that names another object
  of the same type of this map and not the object's own name, which is
  usually a copied object whose tooltip was not changed. It is reported once
  per object, with the levels it covers. It is a note because some maps mean
  it: a tower's tip can read "Upgrade to Aura Tower 3". Long tooltips are not
  checked this way, since a barracks names the units it trains.

The command exits with 1 when it finds an issue. It does not compare the
numbers written in a tooltip with the object's fields: free text does not
say which field a number stands for, and on corpus maps such a comparison
matched mostly unrelated numbers.

### Override tables

`obj-to-override` writes the technique some maps use to have no
`war3map.w3u`, `w3t`, `w3h` or `w3q` at all: the fields go into literal-path
copies of the game's own tables (`Units\UnitData.slk`, `Units\UnitBalance.slk`,
...), split across the right file by the metadata. What has no such table
(icons, models, tooltips and a few World Editor fields) stays in a smaller
`war3map.<ext>` next to them, and the command lists those fields.

`override-to-obj` rebuilds a normal object file from such tables. Three
things cannot always be recovered with certainty, and each is reported rather
than guessed silently: a column name shared by several fields (Abilities'
`Data` is shared by hundreds), a custom object with no hint of the object it
was copied from, and a field whose binary storage type the corpus did not
show.

### Base game data

`obj-to-slk --with-base-data` merges each row onto the bundled base game's
row for the same object (or, for a custom object, the object it was copied
from), so the tables show full stats and default strings, with the map's own
values on top. A base table does not always name a field as the override
does: an ability's base row has `DataA1` where the override has `Data1`.
Both are kept side by side rather than merged into one column.

## Images and icons

```bash
wc3files img-to-png BTNFootman.blp footman.png
wc3files img-convert input.blp output.dds
wc3files img-convert-batch in_dir/ out_dir/ --to png
wc3files make-icon source.png BTNThing.blp
wc3files make-icon-set source.png out/
```

Input format is detected from the file's own header, not its extension.
`make-icon` is the classic icon workflow (resize, border, disabled variant);
`--border` takes any image you supply, since no border artwork is bundled.

## Models

```bash
wc3files model-convert Footman.mdx Footman.mdl     # direction follows extensions
wc3files model-info Footman.mdx                    # version, sequences, textures
```

## Scripts and triggers

```bash
wc3files jass war3map.j                      # parse and print back
wc3files jass war3map.lua --check            # check calls against the game API
wc3files jass war3map.j --to-lua             # translate JASS to Lua 5.3
wc3files run-map MyMap.w3x                   # run config/main and timers, report errors
wc3files trigger-text war3map.wtg            # render as World Editor text
wc3files script-to-triggers war3map.j        # rebuild GUI triggers from a script
```

`jass --check` prints one `file:line: severity: message` per finding instead
of the script, and exits with status 1 when there is an issue. It works on
JASS and on Reforged Lua; what it checks is described in [Triggers and
scripts](triggers.md#checking-a-script).

`jass` and `trigger-text` also take a `.w3x`/`.w3m` map and read its
`war3map.lua` or `war3map.j`, or its `war3map.wtg`, without unpacking it:
`wc3files jass --check MyMap.w3x`. Findings then name the member, as in
`MyMap.w3x:war3map.lua:120: issue: ...`.

`run-map` runs a map's script against stubs of the game API and prints its
runtime errors and argument warnings, exiting with status 1 on an error;
`--seconds`, `--players` and `--messages` are explained in [Running a map
script](triggers.md#running-a-map-script).

`trigger-text` takes `--trigger-data` for a map built with an extended editor,
whose GUI functions stock Warcraft III does not declare. Both trigger
commands take `--json` to emit structured output instead of text.

`script-to-triggers` is covered in [Triggers and scripts](triggers.md).

```bash
wc3files trigger-ui TriggerData.txt TriggerStrings.txt ui/   # editor files -> YDWE ui folder
wc3files trigger-ui ui/ plugin1/ out/                        # ui folders, merged -> editor files
```

`trigger-ui` converts the editor's GUI function definitions to YDWE's `ui`
folder and back; see [Triggers and scripts](triggers.md).

### vJass and Zinc

```bash
wc3files vjass war3map.j -o compiled.j       # compile to plain JASS
wc3files vjass a.j b.j main.j -o out.j       # several files, as one script
wc3files vjass MyMap.w3x -o Compiled.w3x     # a map: compile its war3map.j
wc3files vjass war3map.j --preprocess        # source after imports, macros, Zinc
```

`--debug` builds with `debug` code kept, `-O` inlines, `--check` validates the
result against the natives, and `-I DIR` adds a folder for `//! import`.
`--conf jasshelper.conf` reads JassHelper's own settings. Warnings go to
stderr. The compiler itself is covered in [vJass and Zinc](vjass.md).

`--externals` decides what happens to the `//! external` calls and external
blocks of Lua:

```bash
wc3files vjass war3map.j --externals check            # run in a sandbox, report errors
wc3files vjass MyMap.w3x -o Out.w3x --externals run   # write the objects they make
wc3files vjass war3map.j -o out.j --externals run --externals-dir objects/
```

`note` (the default) only lists them. `check` runs them and changes nothing;
it exits with an error when one fails. `run` writes what they change into the
map, or, for a script, writes the changed files (`war3map.w3a`, ...) next to
`-o` or into `--externals-dir`. `--workspace DIR` keeps the tools' sandbox
folder between runs. See [External tools and Lua](grimex.md).

### Lua

```bash
wc3files lua script.lua arg1 arg2                     # plain Lua 5.1, sandboxed
wc3files lua script.lua --lua-version 5.3
wc3files lua make.lua --tool ObjectMerger --map MyMap.w3x -o Out.w3x
```

Plain mode lets the script read its own folder (`--root` to change it) and
write nothing unless `--allow-write` is given. `--tool` runs the script as
that Grim Extension Pack tool would, with its functions defined, against
`--map` (or an empty map); `-o` is the map to write, or a folder for the
changed files.

## Replays

```bash
wc3files replay-info replay.w3g           # game, players, who left, chat, winners
wc3files replay-info replay.w3g --json    # the same as JSON (--no-chat leaves chat out)
wc3files replay-mmd replay.w3g            # the W3MMD meta data a replay carries
wc3files replay-actions replay.w3g        # per-player action totals
```

`replay-info` lists the players from the game's slot table, with team
(`obs` for an observer), colour, race and handicap, then who left and when,
the W3MMD winners when the map records them, and the chat with timestamps
(the first 50; `--max-chat` shows more). A leave is shown with its reason,
remote or local, and its result as the raw code: the replay format
specification reads the result differently by reason and context and does
not document the two codes most Reforged replays use, so the W3MMD results
are the reliable account of who won. Chat and leave times count the
recorded time slots, which can run past the length the header gives.

An unfinished replay also reads: the `TempReplay.w3g` the game leaves after a
crash or a hang has no header yet (the game writes it when the game ends),
and its last block may be cut short. Its players, chat and actions are read
up to the last whole block, and `replay-actions` takes the length from the
recorded time slots. Such a replay is written back only unchanged.

## An installed game

```bash
wc3files casc-info "C:/Program Files (x86)/Warcraft III"
wc3files casc-list "C:/Program Files (x86)/Warcraft III"
wc3files casc-search "C:/Program Files (x86)/Warcraft III" "*/UnitData.slk"
wc3files casc-extract "C:/Program Files (x86)/Warcraft III" "*.slk" --out-dir out/
wc3files casc-export-gamedata "C:/Program Files (x86)/Warcraft III" out/
```

Local only - these never contact Blizzard's CDN. `--locale` selects a locale,
but only `enUS` is readable from a normal install; the others are encrypted.

```bash
wc3files gamedata-extract "C:/Games/Warcraft III 1.27" profiles/enUS-1.27.1
wc3files --game-data profiles/enUS-1.27.1 jass war3map.j --check
```

`gamedata-extract` copies the game tables of another installed version into a
profile, and `--game-data` makes any command use it; see [Game data for other
versions](gamedata.md).

## UI and effect files

```bash
wc3files ui-info ResourceBar.fdf      # also .toc, .pkb, .pkcf, .pkmm, .wai
```

`ui-build` writes a UI layout's frames as the JASS or Lua that creates them,
as an .fdf, or as a trigger in a map; see [In-game UI frames](ui.md).

```bash
wc3files ui-build ui.json --jass ui.j --lua ui.lua
wc3files ui-build ui.json --map MyMap.w3x --out MyMap.ui.w3x
```
