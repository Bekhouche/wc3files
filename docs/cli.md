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

`--fix-dots` renames every file whose name has more than one dot, turning
each dot but the last into an underscore (`TownHallElfV1.01.mdx` becomes
`TownHallElfV1_01.mdx`), and rewrites the references the same way. As the
author of Import Fixer for Reforged 3.00's Multi-Dot Bug describes it, patch
3.0 takes the extension of an imported file from its first dot, so the game
does not load such a file, with no error, while the World Editor still shows
it. This has not been checked here on the current patch, so the check runs
only when it is asked for: `--fix-dots --report-only` lists the files. A dot
in a folder name is left as it is, and a file whose new name the map already
has is skipped.

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

## Merging doodads

```bash
wc3files doodads-merge Out.w3x MyMap.w3x Forest.w3x Ruins.doo    # into a copy of MyMap.w3x
wc3files doodads-merge war3map.doo a.doo b.doo --renumber-all     # a war3map.doo, ids from 0
```

`doodads-merge` puts every doodad and destructable of the inputs, maps or
`war3map.doo` files, into one `war3map.doo`, in order. This places the
objects of several edits of one map in the same terrain, or gets past what
the World Editor lets one edit place. When the output is a map, it is the
first input map with the merged `war3map.doo`; its other files are kept.

1. **Format:** the newest format among the inputs is written, so no record
   loses a field. A format 13 file needs a Reforged game.
2. **Editor ids:** the first input keeps its own, and the records of the
   others are numbered on from its highest. `--renumber-all` numbers every
   record from 0 in order, the first input too.
3. **Variables:** the World Editor names a destructable that the script or
   the GUI triggers use `gg_dest_<rawcode>_<editor id>`. A renumbered one that
   its map's `war3map.j`, `war3map.lua`, `war3map.wtg` or `war3map.wct` names
   is printed as `breaks:`, and the command exits with 1.
4. **Item tables:** a record of an input after the first that drops items
   from an item table points to a table of its own map, and is printed.
5. **Special doodads** (cliff and terrain decoration) are joined, and exact
   duplicates left out.

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

### Merging object data

```bash
wc3files objects-merge Out.w3u Mine.w3u Theirs.w3u                       # remap conflicts
wc3files objects-merge Out.w3a Mine.w3a Theirs.w3a --on-conflict first   # keep mine
wc3files objects-merge Out.w3u Mine.w3u Theirs.w3u --table ids.json      # save the id table
```

`objects-merge` puts the objects of the second file into the first. Two kinds
of conflict take a policy each:

1. a custom object whose rawcode both files use (`--on-conflict`);
2. an edit of a default object both files change (`--on-edit-conflict`).

The policies are `remap` (the default), `first` and `second`. With `remap`,
the second file's object takes a free rawcode with the same first letter, one
that neither file and no default object of the game uses; a conflicting edit
of a default object becomes a new custom object based on it (`H` for a hero,
`h` for another unit, `A`, `I`, `B`, `D` and `R` for the other types, as the
World Editor names them). `first` keeps the first file's object and `second`
takes the second's.

The second file's own lists (abilities, build and train lists, requirements,
and any other string field) are rewritten to the new rawcodes. `--table`
writes the old-to-new table, to apply to that map's script and other files.
`TRIGSTR_` names of the second file point into its own `war3map.wts`, so
their count is printed.

In Python, `wc3files.w3o.merge.merge_objects(a, b)` takes the `export_data()`
of two `ObjectFile`s and merges every type at once.

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

## Transferring map data

```bash
wc3files transfer MyMod.w3x out/ Melee/*.w3m                       # every part
wc3files transfer MyMod.w3x out/ Melee/*.w3m --parts objects,constants
```

`transfer` copies parts of one source map into each target map and writes
each result to the output folder under its target's name. The target keeps
its terrain, placed units and doodads, regions, cameras and map info, so a
mod made on one map can ship on many melee maps. The parts are:

| Part | What is copied |
|---|---|
| `objects` | the object data (`war3map.w3u` ... `war3map.w3q`, `war3mapSkin.*`), merged with the target's; on a rawcode both use, the source's object is kept |
| `triggers` | `war3map.wtg`, `war3map.wct` and the script, merged as described below |
| `interface` | `war3mapSkin.txt` (the game interface) |
| `constants` | `war3mapMisc.txt` (the gameplay constants) |
| `imports` | the imported files and their `war3map.imp` entries; a target file the source also imports is replaced, or kept with `--keep-imports` |
| `sounds` | `war3map.w3s` |

A part the source does not have leaves the target's as it is.

**Strings.** What is copied names its strings as `TRIGSTR_` numbers in the
source's `war3map.wts`. Each such string is added to the target's
`war3map.wts`, under its own number when the target does not use it, and
under a new one otherwise; the copied files are rewritten to match.

**The script.** The World Editor writes `war3map.j` or `war3map.lua` from the
map info, the placed objects and the triggers, so neither map's script can be
copied as it is. The merged script takes, function by function:

1. from the target, what the World Editor writes from the target's map info
   and placed objects: `config`, the player slots, teams and ally priorities,
   `CreateAllUnits` and its helpers, item drop tables, upgrades, tech tree,
   random groups, regions and cameras;
2. from the source, every other function: the triggers, the custom script code
   and what JassHelper adds for vJass;
3. in `main`, the target's statements up to and including `InitBlizzard()`
   (camera bounds, environment, placement), then the source's after it
   (globals, library initializers, triggers);
4. `InitSounds` from the source when the sounds are copied;
5. the source's globals, with the placement variables (`gg_unit_`, `gg_rct_`,
   `gg_cam_`, `gg_dest_`, `gg_item_`) of the target. A placement variable the
   source's triggers name and the target lacks is declared as `null` so that
   the script still loads, and is printed as `not in the target`.

The merged script is refused when the script checker finds an issue in it
that neither map's script has. A JASS source is translated to Lua for a Lua
target, and `war3map.wct` then still holds JASS, which the World Editor would
write into the Lua script on its next save; a Lua source cannot go into a
JASS target. When the World Editor saves the result, it writes the script
again from the copied triggers and the target's map info and placed objects.

## Splitting a campaign

```bash
wc3files campaign-split MyCampaign.w3n maps/                # one map per mission
wc3files campaign-split MyCampaign.w3n maps/ --keep-names   # leave each map's own name
```

`campaign-split` writes each map of a `.w3n` campaign as a standalone map, so
the missions can be played on their own or in co-op. A campaign keeps its
object data, strings, gameplay constants, interface and imported files in
`war3campaign.*` members beside its maps; each map gets:

1. the campaign's object data merged under its own: on a rawcode both have,
   the map's object is kept, as the game applies a map's object data over the
   campaign's;
2. the campaign's gameplay constants (`war3campaignMisc.txt`) and interface
   (`war3campaignSkin.txt`) when it has none of its own;
3. the campaign's imported files, where it has no file of the same name;
4. the campaign strings these use, added to its `war3map.wts` and renumbered
   where the map uses the number.

Each file is named after its mission index and campaign button, as
`01 Chapter One - The Gate.w3x`, in the order of the campaign's map list, and
that title becomes the map's name in its `war3map.w3i` (color codes are kept
in the map name and left out of the file name). `--parts` chooses among
`objects`, `interface`, `constants` and `imports`.

## Map descriptions

```bash
wc3files map-description MyMap.w3x                          # BBCode, for the Hive Workshop
wc3files map-description MyMap.w3x --format markdown -o README.md
wc3files map-description --print-template > mine.txt         # the default, to change
wc3files map-description MyMap.w3x --template mine.txt
```

`map-description` writes a submission page from the map's own data:

1. the name, author, description and recommended players of
   `war3map.w3i`, with their `TRIGSTR_` references read from `war3map.wts`;
2. the players (controller and race) and the forces;
3. the playable size and the main tileset;
4. for a melee map, the start locations and the gold mines placed in
   `war3mapUnits.doo`, each mine counted at a start location when it is
   within 1200 game units of one and as an expansion otherwise.

A map counts as melee when its map info says so or when it places both gold
mines and start locations: of 120 corpus maps, 9 set the melee flag and 36
more place both without it. Warcraft III color codes become `[COLOR]` tags in
BBCode and are dropped in Markdown; `|n` becomes a line break.

A template is a Python `string.Template`. Its fields are `$name`, `$author`,
`$description`, `$players_recommended`, `$player_count`, `$players`,
`$forces`, `$size`, `$tileset`, `$melee` (empty for a map that is not melee)
and `$editor_version`; the lists are written in the chosen format, and a
field the template names that does not exist is left as it is.

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

A `.blp` is written paletted by default: at most 256 colours, lossless for
most icons. `--blp-jpeg QUALITY` (1 to 100) writes the JPEG kind most of the
game's own icons and textures are, with mipmaps down to 1x1 (`--no-mipmaps`
for one, where the command has it). The game's icons use quality 68: its
quantization table is that quality's, value for value. The JPEG stream is
split between a shared header and each mip as the game's files split it, and
both this package's decoder and Pillow read it back (Pillow as inverted CMYK,
as it reads the game's own files), within 1 of each other on every plane.

```bash
wc3files img-convert icon.png BTNThing.blp --blp-jpeg 68
wc3files make-icon source.png BTNThing.blp --blp-jpeg 68
```

## Models

```bash
wc3files model-convert Footman.mdx Footman.mdl     # direction follows extensions
wc3files model-info Footman.mdx                    # version, sequences, textures
```

### Model edits

```bash
wc3files model-extents Footman.mdx --in-place      # recompute the bounds
wc3files model-extents Models/ -o Fixed/           # every model of a folder
wc3files model-transform Tree.mdx -o Out/ --scale 1.5 --translate 0,0,10
wc3files model-normals Imported/ -o Fixed/           # normals from the faces
wc3files model-remove-unused Imported/ --in-place     # drop unused materials
wc3files model-team-glow Doodads/ -o Out/ --mode remove
wc3files model-texture-paths Models/ --in-place --pattern "\.blp$" ".dds"
```

Each model edit takes models and folders, searched with their subfolders, and
writes the results to `-o` under the same layout, or over the models with
`--in-place`. A model is written back in its own format, MDX or MDL. One that
cannot be read or edited is reported, and the others go on; the command then
exits with status 1. So is a model whose output path another input already
took, as two folders holding the same names would.

`model-extents` sets the extents of each geoset, sequence and model from the
vertices. Each sequence is posed by its bones at `--frames` frames spread
over it (default 24), and a geoset counts only at the frames where its
geoset animation shows it. A geoset never seen in a sequence gets the empty
box the game's models use. The radius is the farthest vertex from the box's
center, as the game's SD models store it, or half the box's diagonal with
`--radius diagonal`, as its HD models do. A model of particles only, with no
vertex, keeps its extents, since an empty box would hide it.

The game's own extents were not all made by one rule, so they are matched
only roughly. Over 49 of the game's SD models, the box of a geoset in a
sequence is within 2% of the game's in 72% of cases and within 10% in 87%;
over 23 HD models, a sequence's box is within 2% in 83% and within 10% in 98%. Where the two differ, the box made here is
mostly the larger one, since it holds every posed vertex. That is the safe
side: a box that is too small is what makes a model vanish at the edge of
the screen.

`model-transform` scales a model about its origin by `--scale`, then moves it
by `--translate`. Scaling changes every length together: vertices, pivots,
translation keys, extents, cameras, collision shapes, particle speeds, gravity
and sizes, ribbon heights, light attenuation and the sequences' move speed.
Rotations, scaling keys, lifespans and emission rates stay as they are, and so
do PopcornFX emitters, whose effect files hold their own sizes; their number is
reported. Moving changes vertices, pivots, extents, cameras and collision
shapes, since translation keys are offsets from the pivot. On 163 real models,
the vertices posed at the start, middle and end of each sequence came out as
the original ones times 2.5, or plus the offset, to within 1e-13.

`model-normals` sets each vertex normal to the sum of the normals of its
triangles, weighted by their area. With `--weld`, the vertices of a geoset at
one position are also taken together when their normals are within `--angle`
degrees (default 60): a UV seam then gets one smooth normal, and a sharper edge
stays hard. A vertex in no triangle keeps its normal. Without `--weld`, the
normals come closest to the game's own: a median of 1.0 degrees from those of
49 SD models and 1.6 degrees from those of 22 HD models, with 87% and 89%
within 15 degrees. Welding at 60 degrees gives 2.7 and 2.6 degrees.

`model-remove-unused` removes the materials no geoset or ribbon emitter uses,
then the textures and texture animations that no kept layer or particle
emitter uses, and renumbers the references. A layer uses a texture through its
texture id, its flipbook (`KMTF`) frames and, from version 1100 on, its texture
list. Nothing drawn changes. The game's own models have nothing to remove; of
300 models imported into corpus maps, 48 lost 28 materials, 64 textures and 9
texture animations, and every geoset, ribbon and particle emitter kept the same
textures, frames and texture animations.

`model-team-glow` finds the layers that show the team glow, the replaceable
texture 2, through their texture, their flipbook or their texture list. By
default it makes them transparent: alpha 0 and no alpha track. With `--mode
remove` it takes them out; a material left with no layer goes with the geosets
that use it, the references to later geosets and geoset animations are
renumbered, and `model-remove-unused` then runs. A ribbon emitter that uses
such a material keeps one transparent layer. Over 34 of the game's SD models
and 137 models imported into corpus maps that have team glow, both ways left no
glow drawn, and every other geoset kept its vertices and geoset animation. The
game's HD models have no team glow texture.

`model-texture-paths` rewrites texture paths with `--replace OLD NEW` (the
whole path), `--prefix OLD NEW` (its start) and `--pattern REGEX REPLACEMENT`
(each match of a regular expression, `\1` and the like allowed). The rules are
tried in the order given, and the first that matches a path rewrites it. Paths
compare as the game reads them, without regard to case or to `\` against `/`.
Each change is printed. A replaceable texture has no path and is left alone,
and a new path of 260 bytes or more, which would not fit its MDX field, is
reported and not written. On 718 real models, `--pattern "\.blp$" ".dds"`
rewrote 2,802 paths and `--prefix war3mapImported\ ""` 8, and every model came
out byte for byte as the same model with only those paths changed.

```bash
wc3files model-textures Models/                     # each texture and its models
wc3files model-textures MyMap.w3x --game "C:/Games/Warcraft III"
```

`model-textures` lists each texture that the models use, with the models that
use it. It takes models, folders and maps, and `--json` prints the report as
JSON. For a map it reads the map's own models and also lists:

- the imported images that no model uses and no other file of the map names
  (object data, map info, triggers, scripts, interface files): the imports
  that can go;
- the imported images that no model uses but another file names, such as an
  icon in the object data;
- the textures that models use but that are not in the map, nor, with `--game`,
  in the installed game.

Against the game, a texture counts under any image extension, since the game's
models name `.blp` files that Reforged stores as `.dds`, and a `_hd.w3mod:`
prefix is dropped. Against the map, only the path given counts. Whether the
game takes a map's `X.dds` for a model's `X.blp` has not been checked, so such
pairs are printed as a note.

On 116 corpus maps with the game's files, 85 have models: 8,796 models using
19,727 textures. Of 15,360 imported images, 7,337 were found unused in 82 maps,
5,160 named only by other files, and 526 textures used by models were neither
in the map nor in the game. 212 of the unused images have their file name in
another file, which a check by hand found to be another folder, another file of
the same name, a comment or a model's name, not a reference to them.

```bash
wc3files model-keyframes Models/ -o Out/                    # keys that change nothing
wc3files model-keyframes Models/ -o Out/ --tolerance 0.001 --dead
```

`model-keyframes` removes a keyframe when its track gives the same values
without it, at every frame of every sequence: on a step track, a key that
repeats the one before; on a linear track, a key its neighbours interpolate to
(a rotation by slerp); on a Hermite or Bezier track, a key in the middle of a
flat stretch. A key at the start or end of a sequence, or of a global
sequence, always stays, and neighbours are taken only inside one sequence.
`--tolerance` lets a value move by that much, never more in total, since each
key dropped is checked against the line that replaces it. `--dead` also removes
the keys that no sequence plays. That rests on the reading that a sequence uses
only the keys inside its interval, which model viewers follow but which was not
checked in the game, so it is not the default.

Every track was sampled at every 10 ms and at every key of each sequence,
before and after. At the default tolerance of 0, nothing moved by more than
2e-6, the rounding of a slerp; with `--tolerance 0.001`, nothing moved by more
than 0.001. At tolerance 0, the pass removed 1,210 of 45,900 keys on 50 corpus
imports, 77 of 19,606 on 35 SD game models and 376 of 383,820 on 36 HD ones.
`--dead` removed 7,684 more on the corpus imports, and 17 on the SD models.

```bash
wc3files model-linear Models/ -o Out/                  # curves to lines (lossy)
wc3files model-linear Models/ -o Out/ --tolerance 0.5  # with keys where needed
```

`model-linear` turns Hermite and Bezier tracks into linear ones and drops their
tangents, which halves the size of their keys. It is lossy: the track then moves
in straight lines between its keys. With `--tolerance`, keys are added at the
frame where the line moves furthest from the curve, until it is that close
everywhere a sequence plays (a rotation is measured on its quaternion's
components). Each model's MDX size before and after is printed. Without a
tolerance, 50 corpus imports went from 4,679,255 to 4,336,183 bytes and 35 SD
game models from 3,750,186 to 3,240,666; the game's HD models have no curved
tracks. At `--tolerance 0.01`, every played frame stayed within 0.01 of the
curve, but the 80,475 keys added made both sets larger than before, so a
tolerance pays only when it is loose.

```bash
wc3files model-remove-sequences Doodads/ -o Out/ --sequence "Stand Victory" --sequence Death
```

`model-remove-sequences` removes the sequences named by `--sequence` (repeat
it; names compare without regard to case) with each geoset's extent for them,
the keys that no kept sequence plays and the event frames inside them. A name
the model does not have is reported. Keys outside every sequence, which nothing
played before either, stay, and so do global sequence tracks, which run on
their own clock. With `--pack`, the kept sequences move up in time, in the
order they start, each after the one before with the smallest gap the model
had between two sequences; their keys and event frames move with them, and
keys outside every kept sequence go. Removing every other sequence of 41 corpus
imports and 25 SD game models removed 27,709 keys and event frames, and every
kept sequence played the same on every track, sampled at every 10 ms, packed
or not.

```bash
wc3files model-transfer-sequences Footman.mdx MyFootman.mdx --in-place --sequence "Attack Slam"
```

`model-transfer-sequences SOURCE TARGET...` copies sequences of the source
into each target, all of them or those named by `--sequence`, after the
target's last sequence and last key, `--gap` ms apart (by default the target's
smallest gap). Bones and the other nodes match by name, without regard to case
or to spaces at either end; when a model repeats a name, the nth node of that
name matches the nth. Each matched node brings its keys inside the sequence,
moved to the new interval, and event objects their frames. Where the target's
track has another interpolation, the keys are made fit for it: from linear to
Hermite or Bezier exactly, with tangents that keep each segment straight, and
the other ways at the cost of the curve's shape, which is reported. Printed for
each target: the source nodes with no match, the sequence names the target
already had, and the geoset animations not copied, since geosets have no names
to match. The new sequence takes the source's extent; `model-extents` sets it
from the target's own vertices.

Stripping 41 corpus imports and 25 SD game models down to their first sequence
and copying the 366 others back from the original, every copied sequence
played like the original on every track. Between the game's SD and HD models
of the same 21 units, 341 of 449 SD nodes had no node of that name in the HD
model, so the HD skeletons are named apart, and the report says so.

```bash
wc3files model-obj Crate.obj Crate.mdx --texture-dir war3mapImported   # OBJ to a static model
wc3files model-obj Footman.mdx Footman.obj --sequence "Attack - 1" --frame 1200
```

`model-obj` converts between Wavefront OBJ and MDX or MDL, in the direction
the extensions give. From OBJ it makes a static model: a geoset for each group
and material used together, polygons cut into triangles, a vertex for each
distinct position, texture coordinate and normal, normals from the faces where
the file has none, a material for each MTL material with its `map_Kd` texture
as `.blp` (in `--texture-dir` when given; `Textures\White.blp` when there is
none), one root bone, a `Stand` sequence and extents. OBJ files most often have
Y up while the game has Z up: `--up y`, the default, turns the model upright,
and `--up z` keeps it. `--scale` scales it. To OBJ, the model is posed at
`--frame` of `--sequence` (its first frame by default; the rest pose without a
sequence), with one group per geoset shown at that frame and an `.mtl` beside it
naming each material's texture.

Two public OBJ test models (the Stanford bunny, 69,451 triangles, and a cow)
came out with every triangle and the same bounding box. 95 game and corpus
models taken to OBJ and back in the rest pose kept every triangle and corner
position, and their OBJ at the middle of their first sequence held the same
vertices as the model posed there.

```bash
wc3files model-gltf Knight.glb Knight.mdx --scale 60 --texture-dir war3mapImported
wc3files model-gltf Footman.mdx Footman.glb --textures extracted/   # to open in Blender
```

`model-gltf` converts glTF 2.0 (`.glb`, or `.gltf` with its buffers) to a
version 800 MDX or MDL, and a model to `.glb`, in the direction the extensions
give.

From glTF, every node of the scene becomes a bone. Its MDX matrix is the glTF
skinning matrix, broken down at each key time into what MDX keeps, so a skinned
mesh, a mesh moved by its own node and a static one all pose as in glTF. Each
animation becomes a sequence, the base color texture of each material a `.blp`
texture written beside the model (`--texture-dir` names its folder in the
model; PNG and JPEG images are converted), `alphaMode` MASK and BLEND the
Transparent and Blend filter modes, and `doubleSided` a two-sided layer. A
model without animations gets a one-second `Stand` in its rest pose. glTF has Y
up and faces +Z where the game has Z up and faces +X: a point (x, y, z) becomes
(z, x, y), times `--scale`, since glTF counts in metres.

What MDX version 800 cannot hold, and how it is handled:

- bone weights: a vertex follows a group of bones in equal parts. Each vertex
  keeps the bones that carry at least `--min-weight` of its weight (0.2 by
  default; its heaviest bone at least), up to 4;
- key times: MDX counts whole milliseconds, so a glTF key moves to the nearest
  one;
- morph targets and their animations, other textures (normal, occlusion,
  emissive, metallic-roughness), vertex colours, a second set of texture
  coordinates, cameras and lights: left out and listed. Sparse accessors are
  refused.

On the Khronos sample models Box, BoxTextured, BoxAnimated, Duck, CesiumMan,
Fox, RiggedSimple and RiggedFigure, every bone matrix matched the glTF skinning
matrix at every key time, rounded to the millisecond, within 1e-4. Posed
vertices then differ only by the equal weights: by at most 2.6% of the model's
size on CesiumMan, 5.2% on Fox, 1.5% on RiggedSimple and 0.8% on RiggedFigure,
which `--min-weight` 0.2 to 0.3 keeps smallest (0, keeping every bone, gives
8.3% and 12.7% on the first two). The JPEG texture of CesiumMan decoded within
3 levels of 255 of Pillow's decoding.

To glTF, the model is one mesh with a primitive per geoset, a skin with a joint
per node and an animation per sequence. A joint rests at its pivot, moved by the
pivot's offset from its parent's, which makes the MDX node matrices carry over
exactly; keys are written LINEAR or STEP, every 1/30 s for a Hermite or Bezier
track (`--curve-ms` sets how often). Each vertex keeps up to 4 bones. glTF has
no global sequences, so a global sequence track is baked into every animation
on its own clock; geoset visibility is not written. A texture is named by its
path as `.png`, or, with `--textures FOLDER`, read from that folder by its path
and embedded.

59 game and corpus models taken to glTF and back, posed at the start, middle and
end of each sequence, came out as follows, relative to the model's size:

| Models | Count | Within 1e-4 | Within 1% |
|---|---|---|---|
| no curved tracks, no global sequence, equal bone weights | 22 | 21 | 22 |
| Hermite or Bezier tracks, sampled every 33 ms | 12 | 6 | 12 |
| global sequences, baked into each animation | 17 | 6 | 14 |
| unequal skin weights, which version 800 holds in equal parts | 7 | 0 | 2 |
| a vertex of more than 4 bones | 1 | 0 | 0 |

## Pathing textures

```bash
wc3files pathing-texture show 12x12Goldmine.tga                   # the cells as digits
wc3files pathing-texture make cells.txt Barracks.tga              # from rows of digits
wc3files pathing-texture from-model Barracks.mdx Barracks.tga --size 12x12 --border 2
```

Buildings, doodads and destructables block pathing with a small TGA. Each
pixel is one pathing cell, 32 game units across, and the image is centered on
the object, its top row to the north. A color channel of 128 or more blocks
one kind of pathing: red walking, green flying, blue building. The game's own
`PathTextures` follow it: the Unbuildable ones are blue, the Unflyable ones
white, the SimpleSolid ones magenta, and the Simple ones a magenta core in a
blue ring. Alpha is not used.

`show` and `make` write a cell as one digit, the sum of 1 (no walking),
2 (no flying) and 4 (no building): 5 is a building's cell, 4 an unbuildable
one, 7 blocked to all, 0 open. All 209 textures of the game read and write
back with the same cells.

`from-model` makes a texture from a model's footprint seen from above: the
cells under its triangles are 5. They are snapped to 2x2 blocks (`--snap 1`
for none), since the game's textures follow its 64-unit build grid, and a
block is blocked when at least half its cells are. `--border N` adds N rings
of unbuildable cells around it, and `--max-height H` counts only the
triangles that come within H units of the ground. On the 196 game buildings
that have a model and a texture with blocked cells, the footprint and the
game's texture overlap with a median intersection over union of 0.80, and
180 of them reach 0.5. It is a start to draw on rather than a finished
texture: the game's are drawn by hand.

## Terrain

### Terrain images

```bash
wc3files terrain-image export MyMap.w3x height height.png               # 16-bit, as stored
wc3files terrain-image export MyMap.w3x height view.png --stretch       # to look at
wc3files terrain-image import MyMap.w3x height height.png Edited.w3x
wc3files terrain-image import MyMap.w3x height drawn.png Edited.w3x --range 7000 9500
```

`terrain-image` writes one layer of `war3map.w3e` as a grayscale PNG, or sets
it from one. The input and output are a map or a `war3map.w3e`. The image has
one pixel per tile corner (the map's tiles plus one in each direction), north
up as the World Editor shows the map. The layers are:

- `height`: the ground height as the file stores it, 0 to 65535, where 8192 is
  the ground of cliff level 0;
- `water`: the water level as stored, 0 to 16383;
- `water-flag`: black where a corner has no water and white where it has.

`height` and `water` are written as 16-bit PNGs of the values themselves, so
exporting and importing leaves `war3map.w3e` byte for byte as it was; this held
on all 342 readable corpus maps. These images are dark, since the values are
small next to 65535: `--stretch` spreads them from black to white and prints
the range it used, and `--range` with that range brings the values back
exactly. An image with other values, such as an 8-bit one drawn in an image
editor, is mapped from black to white onto `--range LOW HIGH`, or onto the
layer's lowest and highest values in the terrain it goes into. A colour image
counts as the mean of its red, green and blue. For `water-flag`, a pixel
brighter than half is water. `cliff-level` is the cliff level of each corner,
0 to 15, in 16 gray steps (level 2 is the editor's ground level); any other
gray takes the nearest step.

```bash
wc3files terrain-image export MyMap.w3x ground ground.png      # and ground.json
wc3files terrain-image import MyMap.w3x ground ground.png Painted.w3x --variation random
wc3files terrain-image export MyMap.w3x cliff-type cliffs.png
```

`ground` and `cliff-type` are colours: each entry of the map's ground or cliff
tileset list gets a colour of its own, and `export` writes the legend next to
the image (`ground.json`, or `--legend`), colour to tileset code. Corners with
no cliff type are black, `none` in the legend. Paint with the legend's
colours; to bring in another texture, add its colour and code to the legend,
and `import` adds the code to the map's list (at most 16 ground textures in a
version 11 terrain, 64 in version 12, and 15 cliff types). A colour the legend
does not have is an error, or with `--nearest` takes the closest one, for an
image whose edges the editor smoothed. A corner whose texture changes keeps its
variation, or gets 0 (`--variation zero`), or one drawn as often as each
variation is in the corpus (`--variation random`, seeded with `--seed`): over
342 maps, variations 0, 4, 8, 12, 16 and 17 make about 14% each. That is what
the World Editor's maps hold, not its own rule, which is not known.

All six layers, exported and imported back, left `war3map.w3e` byte for byte
on all 342 readable corpus maps. What has not been checked in the game is a
terrain painted this way: the game builds cliffs and their edges from the
corners' levels, and a ramp, which the editor marks on its corners, is not
set here, so a level changed by hand may need its ramps and cliff edges
touched up in the World Editor.

### Shadow maps

```bash
wc3files shadow-image export MyMap.w3x shadow.png
wc3files shadow-image import MyMap.w3x painted.png Shaded.w3x
wc3files shadow-image export war3map.shd shadow.png --size 256x256
```

`war3map.shd` is the shadow the World Editor bakes onto the ground: one cell
per quarter tile in each direction, either in shadow or not. `shadow-image`
writes it as a PNG, one pixel per cell, north up, black for shadow and white
for none, and reads it back from one. On import, a pixel darker than half gray
is shadow, so a picture painted in any image editor works, and the image must
be the size of the map's shadow map, which is taken from its `war3map.w3e`. A
bare `war3map.shd` has no size of its own, so `export` needs `--size` for one,
and `import` checks the size against a `war3map.w3e` given in place of the
map. All 342 readable corpus maps hold only these two values, and their shadow
maps came back byte for byte. The map is rebuilt without its old
`(attributes)`, which would no longer match it, so it does not need to be
deleted by hand.

```bash
wc3files shadow-compute MyMap.w3x Shaded.w3x --game "C:/Program Files/Warcraft III"
wc3files shadow-compute MyMap.w3x Shaded.w3x               # the terrain only
```

`shadow-compute` makes the shadow map from the map itself, in place of the
World Editor's Calculate Shadows. The editor's algorithm is not documented, so
this one is a model of it, fitted to the shadow maps the editor wrote into 64
corpus maps:

- the terrain casts, and so do the doodads whose shadow flag is set in the
  game's `DoodadSkins.txt` or in the map's object data. Destructables, trees
  among them, do not: counting them shadowed most of a forested map where the
  editor's shadow map has a few strokes;
- the light comes from the south-west at 30 degrees above the horizon
  (`--azimuth 225 --elevation 30`), the best of the directions tried;
- a cliff is a wall in the middle of its tile, and a doodad is its model at
  rest, placed, turned and scaled as on the map, and solid from the ground to
  the model's top.

The doodad table and models come from the installed game (`--game`, 1.32 or
later), and a model imported into the map is read from the map first. Without
`--game`, only the terrain casts. The doodads that cast, those that do not and
why, and the models not found are printed.

Against the editor's own shadow maps of the 64 corpus maps that have one (one
copy of each), the shadow cells overlap with a median intersection over union
of 0.67 (a quarter of the maps below 0.58, a quarter above 0.73), and a median
of 96% of all cells agree; the terrain alone gives 0.55. On 9 maps the doodads
make it worse than the terrain alone. A map's shadow map is only as fresh as
the last time its author ran Calculate Shadows, so some of the difference may
be the maps, not the model. A map of 256 by 256 tiles takes a few seconds.

### Minimaps

```bash
wc3files minimap MyMap.w3x WithMinimap.w3x --game "C:/Program Files/Warcraft III"
wc3files minimap MyMap.w3x minimap.png --game "C:/Program Files/Warcraft III"
```

`minimap` renders `war3mapMap.blp`, the minimap the game shows, which only the
World Editor makes otherwise, and writes it into a map, or to a `.blp` or a
`.png`. As the editor's on all 342 readable corpus maps, it is 256 by 256 and
shows the playable area (the map less its camera bounds complements), scaled to
fill the square with its shape kept, on black; the `.blp` is JPEG of one mip
at quality 40.

The ground at each pixel blends the corners of its tile, each the mean colour
of its tileset's texture, read from the installed game (`--game`, 1.32 or
later). Water is tinted with the tileset's minimap water colour, and trees and
the other destructables and doodads that show on the minimap in a colour of
their own are a dot of it; a custom object takes its base object's colour.
Cliffs, terrain lighting and models are not drawn.

Against the editor's minimaps of 266 corpus maps (one copy of each), the mean
absolute difference per colour channel inside the playable area has a median
of 19.8 out of 255 (a quarter of the maps below 14.6, a quarter above 27.0).
For scale, a flat image of each editor minimap's own mean colour gives 24.6,
and the rendering does better than that on 176 of the 266 maps. It is a fair
likeness rather than the editor's picture; a map takes about a second.

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
