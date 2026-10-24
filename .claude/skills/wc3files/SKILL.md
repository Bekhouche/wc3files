---
name: wc3files
description: Work on the wc3files codebase itself (a pure-Python Warcraft III file format reader/writer) - use when adding or fixing a format parser/writer, touching MPQ archive handling, or checking format-support status against w3x2lni.
---

# wc3files development

`wc3files` is a pure-Python library for reading and writing Warcraft III file
formats (MPQ archives, W3X maps, W3O object data, W3R regions, W3G replays,
MDL/MDX models). No StormLib dependency is required for the formats it
targets - MPQ is documented well enough to implement natively.

## Where things live

- `src/wc3files/mpq/` - MPQ archive reader + `MpqWriter` (pure-Python,
  classic format-0 archives: header, sector-compressed data, hash/block
  tables). No encryption, no v2/v3 (64-bit) archives, no `(signature)`
  generation. `(attributes)` generation (`mpq/attributes.py` -
  `MpqWriter(attributes=True)`) *is* implemented - per-block CRC32/FILETIME/
  MD5, matching the real Blizzard format. `mpq/protection.py`
  (`MpqWriter(protection=...)`, an `MpqProtectionLevel`) adds opt-in
  MPQ-container "protection" - `(listfile)` removal + decoy hash-table
  padding (SAFE, safe by construction), duplicate/decoy hash entries per file
  (EXPERIMENTAL/AGGRESSIVE - also known to defeat this package's own reader
  by design, see TODO.md). All four tiers confirmed to load and play in a
  real Warcraft III Reforged client (2026-08-29, via a sibling project's
  release pipeline). The "BOBA protector" block-table-relocation trick is
  deliberately not implemented (too underspecified to safely reproduce).
  `mpq/files.py`'s no-`(listfile)` file discovery is iterative/multi-source
  ("deep scan") - any newly-found script/object-data/model (`.mdx`/`.mdl`,
  parsed via `ModelFile` - texture list + attachment/particle references)/
  SLK/TXT file gets mined for further real file references, repeating until a
  pass finds nothing new, not just a single fixed pass - measured
  97/238 -> 164/238 real files found on a real protected map with no listfile
  at all (see TODO.md).
- `src/wc3files/w3x/` - `W3xFile`: the top-level map wrapper. `build()` /
  `build_from_files()` repack a map from a directory or an in-memory
  `{filename: bytes}` table - this is the read -> edit `self.files` ->
  repack workflow.
- `src/wc3files/w3i/` - `W3iFile`, `war3map.w3i` (map info: head, players,
  forces, camera bounds, loading screen, fog, environment, tech/upgrade
  availability, random unit/item tables). Verified byte-offset-by-byte
  against real maps for versions 18/25/28/31. Raises on version > 31 rather
  than guessing - see TODO.md for the version-33 gap.
- `src/wc3files/table/` - generic tabular text formats reused across formats
  (not object-data-specific despite originating in `w3o/`): `SlkFile`/`Sylk`/
  `SylkParser` (SYLK spreadsheet format, read/write) and `WtsFile` (war3map.wts
  string table, read/write) - e.g. `w3i_file.py` uses `WtsFile` for
  `apply_strings()`.
- `src/wc3files/w3o/` - object data (`ObjectFile`), the metadata-driven
  schema/validator/override pipeline (`object_metadata.py`, `object_base_data.py`,
  `object_base_strings.py`, `object_editor_enums.py`, `object_override.py`,
  `object_slk.py`, `object_validator.py`), WES strings (`wes_file.py`),
  WESTRING resolution (`world_edit_strings.py`), CFG (`cfg_file.py`),
  `units_file.py`, `json_file.py`. Bundled real Blizzard data (SLK/txt/json
  schema and base-value tables, sourced from a real Reforged `war3.w3mod`
  install - see TODO.md for full provenance) lives in `src/wc3files/w3o/gamedata/`.
  `ObjectFile.register_hook('read'|'write', callback)` - per-instance mutation
  hooks, the wc3files-native equivalent of a "scripted object-data mutation"
  capability (see TODO.md for why it's shaped this way instead of mirroring
  w3x2lni's plugin architecture directly).
- `src/wc3files/scripts/` - WC3 map scripts. `ScriptFile` reads/writes
  `war3map.j` (JASS): hand-rolled lexer (`jass_lexer.py`) + recursive-descent
  parser (`jass_parser.py`) building a shared AST (`ir.py`) + printer
  (`jass_printer.py`) serializing back to JASS text (structural, not
  byte-identical, round-trip - same lossy-but-correct bar as `WtsFile`).
  `checker.py`'s `NativeSignatures`/`check_program` validate calls (unknown
  function, wrong arg count, incompatible type - handle-hierarchy-aware,
  JASS's real integer->real widening explicitly not flagged) against the
  bundled real `common.j`/`blizzard.j` (`src/wc3files/scripts/gamedata/`) plus
  the program's own natives/functions - `ScriptFile.validate()`/
  `write(path, validate=True)` mirror `ObjectFile`'s report-mode/raising
  pattern. `optimizer.py` (`find_reachable`/`remove_dead_code`) and
  `obfuscator.py` (`build_rename_table`/`apply_rename`) implement phase 3:
  reachability-based dead-code elimination and shortest-available-name
  identifier renaming, ported from `w3x2lni`'s `simplify.lua`/`confuser.lua`
  *design* (not code) - `ExecuteFunc`/`TriggerRegisterVariableEvent`
  string-literal dynamic-dispatch targets are recognized and protected from
  both removal and renaming; locals/params are never renamed (function-scoped
  in JASS) but are excluded from the generated-name pool so a renamed
  global/function can never collide with one. `war3map.lua` (Reforged's
  alternative script language, targeting Lua 5.3 - not 5.1, confirmed by a
  real-fixture sweep finding 5.3-only floor-division/bitwise syntax in actual
  use, see TODO.md) is fully supported too: `lua_lexer.py`/`lua_parser.py`/
  `lua_printer.py` (phase 4) target the same shared `ir.py`, reusing
  `Literal`/`VarExpr`/`UnaryExpr`/`BinaryExpr`/`IfStmt` where the shape
  genuinely matches JASS's, with new Lua-only node types everywhere else (see
  `ir.py`'s "Lua-only nodes" section) - `ScriptFile` dispatches `.lua` to this
  frontend (`validate=True` against a `.lua` path still raises - no native/BJ
  checker exists for Lua). `lua_obfuscator.py`'s `obfuscate_lua` (phase 5) is
  a genuinely different design from JASS's flat-namespace `obfuscator.py`, not
  a reuse of it - a real scope-tracking walk that renames every local/param/
  loop-var binding and never touches a global at all (Lua's dynamic global
  access has no closed set to detect, unlike JASS's two narrow
  `ExecuteFunc`/`TriggerRegisterVariableEvent` mechanisms) and does not
  attempt dead-code elimination (removing a Lua local could discard its
  initializer's side effects). See TODO.md for the full accounting, including
  three real bugs a 23-fixture real-world sweep found and fixed.
- `src/wc3files/w3r/` - region files (`RegionFile`), full read/write.
- `src/wc3files/mmp/` - `PreviewIconsFile`, war3map.mmp (minimap preview-icon
  overlays - gold-mine/neutral-building/player-start markers on the map preview
  image, NOT per-match ping data). Full read/write.
- `src/wc3files/w3e/` - `TerrainFile`, war3map.w3e (tileset lists + per-tile-corner
  height/water/texture/variation/cliff grid). Full read/write, versions 11/12.
  Uses a 5-bit-ground/3-bit-cliff split for the variation byte - confirmed
  against real fixtures with variation values of 16/17 (see TODO.md), not the
  4-bit/4-bit split War3Net uses (a bug there). Both are part of the
  previously-opaque-format roadmap (see TODO.md).
- `src/wc3files/wpm/` - `PathingFile`, war3map.wpm (per-cell walkability/
  buildability flags, 4x4 cells per terrain tile). Full read/write.
  `decode_flags`/`encode_flags` name the bits; two of them (`harvestable`,
  `amphibious`) are HiveWE's documented names but not independently confirmed
  against real data (see TODO.md).
- `src/wc3files/shd/` - `ShadowMapFile`, war3map.shd (terrain shadow map, boolean
  per cell). No header/version at all - `read(path, width, height)` takes grid
  dimensions from an already-read `w3e`/`wpm` rather than self-describing.
- `src/wc3files/w3c/` - `CameraFile`, war3map.w3c (saved World Editor cameras).
  `version` is always 0 even though the record layout silently grew 3 floats at
  some point - `read()` trial-parses both layouts per-file and keeps whichever
  consumes the file exactly (see TODO.md).
- `src/wc3files/w3s/` - `SoundsFile`, war3map.w3s (sound object definitions,
  versions 1-3). The version>=2 tail is a genuinely unresolved ambiguity across
  every reference source - kept as an opaque bytes blob per record rather than
  assigning unverified field names (see TODO.md).
- `src/wc3files/w3g/` - replay files. `read()` currently has dev-debug
  `print()`/`exit()` left in; `write()` is unimplemented. Known-broken, see
  TODO.md.
- `src/wc3files/mdl/` - MDL/MDX model files, full read/write for text and
  binary formats. Ahead of w3x2lni here (it doesn't parse models
  structurally).
- `src/wc3files/img/` - `BlpFile` (BLP1, Warcraft III's own format, both
  sub-variants read/write, plus read-only BLP2 support - see below),
  `DdsFile` (reads the full classic DXT1-DXT5/BC1-3 family - DXT2/DXT4 via
  the DXT3/DXT5 block codec plus un-premultiply, since they share the same
  bitstream - and uncompressed RGB(A); writes DXT1/DXT5/uncompressed only),
  `TgaFile`,
  `BmpFile`, `TiffFile`, and `read_png()`/`write_png()` - all decoding
  to/from a common RGBA8 buffer, no third-party imaging dependency. Includes
  its own baseline+progressive JPEG *decoder* (`jpeg_decoder.py`,
  purpose-built for BLP1's raw-plane embedded JPEG streams - no colorspace
  transform), a DXT block codec (`dxt.py`, decode and a range-fit encode),
  median-cut color quantization (`palette.py`, for BLP1 writing), and
  icon-authoring helpers (`transform.py`: resize, alpha-composite,
  disabled-icon generation). `BlpFile.write()` only supports BLP1's paletted
  sub-variant (no JPEG *encoder*). `borders.py` bundles 13 real icon-border
  PNGs (classic 64x64 + Reforged 256x256, one per icon kind - copied from a
  real third-party tool with its owner's explicit authorization, see TODO.md
  for provenance) as defaults for `make-icon --bundled-border`/`make-icon-set
  --bundled-borders` - not a universal ground truth (confirmed not to match a
  real, independently-produced benchmark's own borders byte-for-byte - see
  TODO.md's `composite_over` verification entry), so an explicit
  `--border`/`--<kind>-border` always takes precedence. CLI: `img-to-png`,
  `img-convert`, `img-convert-batch`, `make-icon`, `make-icon-set`,
  `set-object-icon`. **BLP2 is a special case** - unlike everything else in
  this package it's implemented from the public spec alone, not verified
  against any real WC3 fixture (none exist; BLP2 is a WoW format), only
  self-tested against synthetic data - treat it as best-effort. See TODO.md's
  entries for the full gap list (WebP/DX10-DDS not built at all -
  disproportionate effort for zero real WC3 benefit).
- `src/wc3files/data/` - only truly cross-format tables now: `bin.py`
  (`BinaryDataType`, used everywhere) and `reference/` (two real WC3 map-member
  filename lists loaded at runtime by `MpqFiles.get_filelist()`/
  `W3xFile.get_filelist()`). Every per-format field/enum table lives beside its
  own parser instead (`w3i/data.py`, `w3o/data.py`, `w3r/data.py` +
  `w3r/region.py`, `w3g/data.py`, `mpq/data.py`, `mdl/data.py`, `wtg/data.py`,
  `doo/data.py`, `imp/data.py`, `w3f/data.py`, `scripts/data.py`, ...) -
  `data/__init__.py` re-exports all of them so `from wc3files.data import *`
  still works as one flat namespace.
- `src/wc3files/files/binary_file.py` - shared binary read/write primitives
  every format parser is built on.
- `src/wc3files/gui/`, `src/wc3files/utils/` - GUI front end and shared
  helpers.
- `w3x2lni/` - a mature Lua/C++ WC3 map converter/protector, kept as a local
  dev-only reference clone (gitignored, not part of the versioned project -
  not every checkout will have it). When adding a new format parser, check
  its `frontend_*.lua`/`backend_*.lua` for the field layout, but always
  verify independently against real map files too - don't trust it blindly
  (the w3i work found and documented a gap it doesn't cover either).
- `StormLib/` - another local dev-only reference clone (gitignored, same as
  `w3x2lni/`), present for reference/comparison only; not a runtime
  dependency of this package.
- `TODO.md` - the authoritative gap list against w3x2lni. Check it before
  starting work to avoid duplicating a documented "not implemented" item,
  and update it when a gap is closed or a new one is found.

## Conventions

- Each format module follows a `read`/`write` (or `from_file`/`build`) pair
  on a `*File` class; keep new formats consistent with that shape.
- When a parser can't confidently handle a case (e.g. an undocumented field
  layout), raise rather than silently mis-parsing - see `W3iFile`'s
  version > 31 guard for the pattern to follow.
- Round-trip correctness is the bar: read a real file, write it back
  unmodified, and diff against the original (or reread and compare
  structurally) before considering a parser/writer done.
- Lazy-import optional/heavy decompression paths (see `dclimplode` in the
  MPQ reader) rather than importing them eagerly at module load - eager
  imports of unbuildable deps previously broke the package on plain
  installs.

## Testing

`tests/` is a real `unittest`-based suite, one module per format
(`test_mpq.py`, `test_w3i.py`, `test_doo.py`, `test_object.py`,
`test_table.py`, `test_mdl.py`, `test_img.py`, `test_jass.py`, `test_lua.py`,
`test_cli.py`, ...) - run the whole thing with `python -m unittest discover -s
tests`, or
one module directly with `python -m unittest test_<format>` (from inside
`tests/`). Every test asserts something concrete (a known real field value, a
byte-identical round-trip, a real bug the checker/validator should catch) -
no bare prints, no hardcoded personal paths.

`tests/samples/` holds small, real, checked-in fixture files - built once via
each format's own writer (see git history for the generation commands used),
not personal map data, so the suite needs nothing from the local machine. The
one exception is `samples/img/BTNMountainKing.blp` - a genuine real Warcraft
III command-card icon (64x64, JPEG-compressed BLP1, 7 mips), not
writer-generated - it's what `test_img.py`'s `TestRealIconFixture` uses to
exercise the full decode + icon-authoring pipeline against real, non-synthetic
art (found two real BLP1 bugs on first use - see TODO.md). Grouped into one
subfolder per source package (`samples/img/sample.blp`,
`samples/mpq/...`, `samples/scripts/sample.j` + `sample.lua`, etc. - mirrors
`src/wc3files/`'s own layout) rather than one flat folder of 20+ files -
`_helpers.sample_path(name)` resolves a bare filename by searching
recursively, so call sites (`sample_path('sample.blp')`) never need to know
or care which subfolder a fixture actually lives in. `w3o/gamedata/*.slk` and
`scripts/gamedata/{common,blizzard}.j` (real bundled Blizzard data) are used
directly as fixtures too, rather than duplicated into `tests/samples/`. The
one exception is `war3map.w3g`: `ReplayFile.write()` rebuilds from internal
reader state, not a plain data dict, so there's no safe synthetic fixture -
that test skips unless a real replay is pointed at via the
`WC3FILES_TEST_REPLAY` env var.

When adding a new format or fixing a bug, add a real test for it here rather
than a scratch script - a scratch script only proves the fix once, on one
machine; a real test proves it stays fixed.
