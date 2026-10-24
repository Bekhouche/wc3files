# Formats

Every format the library handles, the class that handles it, and whether it
can be written as well as read.

"Write" below means the class has a working writer, and the parser is held to
the round-trip bar described in [Concepts](concepts.md#round-tripping-is-the-correctness-bar).
It does not promise every version of every format: the parsers document the
layouts they accept, and refuse the ones they do not.

## Containers

| Format | Class | Read | Write | Notes |
|---|---|:--:|:--:|---|
| MPQ archive | `MpqFile`, `MpqWriter` | ✓ | ✓ | Classic format-0 archives. No encryption, no 64-bit (v2/v3) archives |
| `.w3x` `.w3m` `.w3n` | `W3xFile` | ✓ | ✓ | The map wrapper over MPQ: `read` → edit `files` → `build_from_files` |
| CASC storage | `CascStorage` | ✓ | – | Reads an installed game. Local only, never contacts a CDN |

Archives using PKWare-imploded (DCL) sectors cannot be opened; that
decompressor is not implemented.

## Map sub-files

| File | Class | Read | Write | Holds |
|---|---|:--:|:--:|---|
| `war3map.w3i` | `W3iFile` | ✓ | ✓ | Map name, author, players, forces, camera bounds, loading screen, fog, tech availability |
| `war3map.w3e` | `TerrainFile` | ✓ | ✓ | Terrain: tiles, heights, textures |
| `war3map.doo` | `MapDoodadsFile` | ✓ | ✓ | Doodad placement |
| `war3mapUnits.doo` | `MapUnitsFile` | ✓ | ✓ | Unit and item placement. Same magic as `.doo`, different layout |
| `war3map.w3r` | `RegionFile` | ✓ | ✓ | Named regions |
| `war3map.w3c` | `CameraFile` | ✓ | ✓ | Cameras |
| `war3map.w3s` | `SoundsFile` | ✓ | ✓ | Sound definitions, including dialogue asset paths |
| `war3map.wpm` | `PathingFile` | ✓ | ✓ | Pathing map |
| `war3map.shd` | `ShadowMapFile` | ✓ | ✓ | Shadow map (one byte per cell) |
| `war3map.mmp` | `PreviewIconsFile` | ✓ | ✓ | Minimap preview icons |
| `war3map.imp` | `ImpFile` | ✓ | ✓ | Imported-file table |
| `war3map.wts` | `WtsFile` | ✓ | ✓ | Translatable strings, referenced as `TRIGSTR_000` |
| `war3campaign.w3f` | `CampaignInfoFile` | ✓ | ✓ | Campaign info |

## Object data

The customised units, items, abilities, buffs, upgrades, doodads and
destructibles - `war3map.w3u` `.w3t` `.w3a` `.w3h` `.w3q` `.w3b` `.w3d`.

| Class | Purpose |
|---|---|
| `ObjectFile` | Read/write any of the seven. `to_slk()` flattens to readable tables; `validate()` checks fields against the bundled schema |
| `ObjectMetaData` | The game's field schema - what each 4-character rawcode means |
| `BaseObjectData`, `BaseObjectStrings`, `CombinedBaseData` | Blizzard's default stat tables and their strings |
| `SlkFile` | SYLK tables, read and write |
| `WesFile`, `WorldEditStrings` | World Editor string tables |
| `CfgFile` | `.cfg`-style key/value files |
| `JsonFile` | JSON import/export of object tables |
| `ObjectEditorEnums` | Valid enumerated values per field |

An object file stores only what a map *changes*; the defaults live in the
game's tables, which is why the metadata classes exist.

## Triggers and scripts

| File | Class | Read | Write | Holds |
|---|---|:--:|:--:|---|
| `war3map.wtg` | `MapTriggersFile` | ✓ | ✓ | GUI triggers as the editor stores them |
| `war3map.wct` | `MapCustomTextTriggersFile` | ✓ | ✓ | Custom-text trigger bodies |
| `war3map.j` | `ScriptFile` | ✓ | ✓ | Compiled JASS - full lexer, parser, printer |
| `war3map.lua` | `ScriptFile` | ✓ | ✓ | Lua (5.2+, including `goto`/labels) |
| - | `TriggerData` | ✓ | – | The editor's function declarations |

Scripts are parsed to a real syntax tree, not pattern-matched. See
[Triggers and scripts](triggers.md), including rebuilding editor triggers
from a compiled script.

## Models

| Format | Class | Read | Write |
|---|---|:--:|:--:|
| `.mdx` (binary) | `ModelFile` | ✓ | ✓ |
| `.mdl` (text) | `ModelFile` | ✓ | ✓ |

One shared model, both directions, so `ModelFile` converts between them.

## Images

All decode to and from a common RGBA8 buffer, with no third-party imaging
dependency.

| Format | Class | Read | Write | Notes |
|---|---|:--:|:--:|---|
| `.blp` | `BlpFile` | ✓ | ✓ | BLP1, Warcraft III's own format. Writing supports the paletted variant; there is no JPEG encoder. BLP2 (a WoW format) reads only, and is implemented from spec without real fixtures - treat as best-effort |
| `.dds` | `DdsFile` | ✓ | ✓ | Reads the classic DXT1-DXT5 family and uncompressed RGB(A); writes DXT1/DXT5/uncompressed |
| `.tga` | `TgaFile` | ✓ | ✓ | |
| `.bmp` | `BmpFile` | ✓ | ✓ | |
| `.tif` | `TiffFile` | ✓ | ✓ | |
| `.png` | `read_png`, `write_png` | ✓ | ✓ | Functions, not a class |

Plus icon authoring: `resize_box`, `resize_nearest`, `composite_over`,
`make_disabled_variant`, `quantize_median_cut`.

## UI, effects and replays

| Format | Class | Read | Write | Holds |
|---|---|:--:|:--:|---|
| `.fdf` | `FdfFile` | ✓ | ✓ | Frame definitions |
| `.toc` | `TocFile` | ✓ | ✓ | Frame-definition manifests |
| `.pkb` `.pkcf` `.pkmm` | `PkbFile`, `PkcfFile`, `PkmmFile` | ✓ | ✓ | Popcorn effect files |
| `.wai` | `WaiFile` | ✓ | ✓ | AI scripts. The condition tree is not fully pinned down |
| `.w3g` | `ReplayFile` | ✓ | ✓ | Replays, including W3MMD meta data and player actions |

## Known limits

- **DCL (PKWare implode) sectors** - archives using them cannot be opened.
- **Encrypted CASC chunks** (BLTE mode `E`) - unreadable without the key, so
  non-`enUS` locale files in an install generally cannot be read.
- **`.w3xd`** - encrypted; not addressable.
- **Extended-editor trigger files** need that editor's `TriggerData.txt`; see
  [Concepts](concepts.md#bundled-game-data).
- **64-bit MPQ archives** and MPQ encryption are not implemented.
