# Command line

Installing the package puts a `wc3files` command on your path.

```bash
wc3files --help
wc3files <command> --help     # every command documents its own options
```

26 commands, grouped below by what they are for.

## Archives

```bash
wc3files unpack MyMap.w3x out/          # extract to loose files
wc3files pack out/ MyMap.edited.w3x     # repack a directory
```

`unpack` then `pack` is the whole edit cycle: unpack, change files on disk,
pack. `pack` round-trips through the archive writer, so the result is a fresh
archive rather than a patched copy of the original.

## Inspecting map sub-files

```bash
wc3files dump w3i war3map.w3i                 # to stdout as JSON
wc3files dump w3i war3map.w3i info.json       # to a file
wc3files build w3i info.json war3map.w3i      # and back again
```

`dump` reads one sub-file and prints it as JSON; `build` turns that JSON back
into the binary file. Together they are the quickest way to change one field
by hand. Both take a format name:

```
cfg  doo  doodads  imp  mmp  obj  shd  slk  w3c  w3e
w3f  w3g  w3i  w3r  w3s  wct  wes  wpm  wtg  wts
```

`dump doo` is `war3mapUnits.doo` (units); `dump doodads` is `war3map.doo`.
They share an extension and a magic number but not a layout, which is why
they are separate names here.

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
wc3files trigger-text war3map.wtg            # render as World Editor text
wc3files script-to-triggers war3map.j        # rebuild GUI triggers from a script
```

`trigger-text` takes `--trigger-data` for a map built with an extended editor,
whose GUI functions stock Warcraft III does not declare. Both trigger
commands take `--json` to emit structured output instead of text.

`script-to-triggers` is covered in [Triggers and scripts](triggers.md).

## Replays

```bash
wc3files replay-mmd replay.w3g            # the W3MMD meta data a replay carries
wc3files replay-actions replay.w3g        # per-player action totals
```

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

## UI and effect files

```bash
wc3files ui-info ResourceBar.fdf      # also .toc, .pkb, .pkcf, .pkmm, .wai
```
