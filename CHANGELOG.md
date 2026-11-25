# Changelog

All notable changes to wc3files are listed here, newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version numbers follow [Semantic Versioning](https://semver.org/). Numbers
in parentheses are GitHub issues.

Each pull request adds its line under [Unreleased]. A release renames that
section to the new version and its date.

## [Unreleased]

### Added

- `wc3files replay-info` and `wc3files.w3g.summary.summarize`: what happened in a replay. Players come from the game's slot table, with team, observers, colour by its World Editor name, race and handicap; the summary adds who left and when, the chat with timestamps and mode, and the W3MMD results. The MCP `replay_info` tool returns the same summary, with a `chat` switch and `max_chars`; its players now give `player_id` and slot rather than `id`. The slot layout fits all 294 local replays that carry the record, and all 296 summarize, with 44,673 chat messages and 3,010 leaves (#263).
- `wc3files map-strings` and `wc3files.w3x.strings`: the strings of a map by object, field and level, from `war3map.wts` or inline in the object data, with the other files that use `war3map.wts` and the strings nothing uses. `--check` reports `TRIGSTR_` references with no string, `<A000,DataA1>` references that do not resolve, and short tooltips naming another object of the map (#252).

### Fixed

- Ability, doodad and upgrade levels are named as Blizzard's tables name them: level n of a field is `{name}{n}` (`DataA1` for level 1) and a field without levels has no suffix (`Name`). `to_slk`, `object_data`, the SLK export and the override tables named every level one too high (`DataA2`, `Name1`). Over 342 corpus maps, fields with levels are stored from level 1 and fields without levels at level 0. The override tables now drop an upgrade or ability value that only restates the base default, as they already did for units: on 60 corpus maps, the 1,746 values affected all equal their default (#277).
- `optimize --computed-text` resolves a reference to a level the map added to an ability and left unset, such as `<A000,Dur5>` on a five-level copy of a three-level ability: the level takes the last value of the game's table, as w3x2lni fills it. Such references were left in the text (#252).
- Items take their name, tooltip, icon and the other shared fields from the unit metadata rows marked `useItem`, so `object_data` names an item's name `Name` rather than `unam` (#283).

## [0.6.4] - 2026-11-25

### Added

- MCP `edit_script` and `import_file` (with `--allow-write`). `edit_script` adds JASS or Lua code before `main`, with an optional call at the top of `main` (after its locals in JASS), merges a JASS `globals` block into the script's own, or replaces the script; the result is parsed and checked, and refused on an issue the original did not have. `import_file` adds or replaces a file and lists it in war3map.imp; map data and the script are refused. On the 58 readable maps of a 90-map corpus sample, 58 script edits (55 JASS, 3 Lua) kept every original line exactly and in order, and 58 imports left every other file unchanged (#275).

- MCP `edit_records` (with `--allow-write`): add, change or delete regions and cameras, and add or change strings. A region or camera the script or GUI triggers use cannot be deleted or renamed; new cameras start as the game's default camera; a new string comes back with its `TRIGSTR_` reference. On the 74 readable maps of a 100-map corpus sample, 222 edits (a region, a camera and a string on each) left every existing record and every other file unchanged (#274).

- MCP `edit_placed` (with `--allow-write`): place, move, change or delete placed units or doodads, picked by index, type id or editor number. Type ids are checked against the game's and the map's objects, positions against the terrain bounds, and new records get the next free editor number. On the 92 readable maps of a 120-map corpus sample, 86 unit edits (one unit added, one moved) and 90 doodad deletions each left every other record and every other file unchanged (#273).

- `wc3files-mcp --allow-write` registers edit tools, starting with `edit_objects`: create custom objects from standard ones, change or reset their fields by the names `object_data` shows or by field id, or delete them. An edit writes a new map (`<name>.edited.w3x` unless `out` says otherwise), reads it back and checks it; every other file is copied unchanged. Without the option the server stays read-only and lists no edit tool. On the 84 readable maps of a 120-map corpus sample, 441 edits (a new object in each object file a map has, all seven types) each left every existing object and every other file unchanged (#272).

- Three MCP tools that read the rest of a map: `map_overview` (what a map holds, in one call), `placed_objects` (placed units and doodads, filtered by type or owner, with facing in degrees) and `map_records` (regions, cameras, sounds, imports, strings, or a terrain summary). Each has an output schema; on 1,602 results from 250 corpus maps, each validates and comes back unchanged (#271).

## [0.6.3] - 2026-11-25

### Changed

- Every `wc3files-mcp` tool publishes an output schema, one TypedDict per result in `wc3files.mcp.results`, so a client knows what comes back. On 2,523 results from real maps, models and replays, each validates and the structured result equals what the tool function returns. `object_data`'s `object_type` is an enum, and the descriptions are shorter now that the schemas carry the fields (#268).
- `model_info` gives each sequence with its start and end frame, and each texture as its path and replaceable id (#268).

### Fixed

- `model_info` returned null for every texture: the model reader keys a texture `path`, and the tool read `image` (#268).

## [0.6.2] - 2026-11-25

### Added

- `wc3files-mcp` tools declare that they only read (the MCP annotations `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`), have a title, describe every parameter in their input schema, and say when to use each tool instead of another. glama.ai rated the old definitions 2.8 of 5. The repository has a `glama.json` naming its maintainer (#265).
- The README links to the server's page on glama.ai, an MCP directory, with its score badge. glama builds and checks the server on each release (#200).
- A `Dockerfile` that runs `wc3files-mcp` on a mounted `/data` folder, with a `.dockerignore` that only lets the package sources into the build. CI builds the image and checks that the server answers an MCP `initialize` request. See `docs/mcp.md` (#198).

## [0.6.1] - 2026-11-25

### Added

- `server.json` describes `wc3files-mcp` for the official MCP Registry as `io.github.Bekhouche/wc3files`, started with `uvx --from "wc3files[mcp]" wc3files-mcp --root FOLDER`, and the README carries the `mcp-name:` line the registry checks on PyPI. `docs/mcp.md` covers both (#195).

## [0.6.0] - 2026-11-25

### Added

- `wc3files.ui`: in-game UI frames for 1.31 and later, described as a layout (JSON or Python) and written as the JASS or Lua that creates them with the frame natives, as an .fdf, or as a custom-text trigger merged into a map's `war3map.wtg` and `war3map.wct` for World Editor users, classic or Reforged. An .fdf reads back into a layout. CLI `ui-build`; see `docs/ui.md` (#172).
- An MCP server, `wc3files-mcp` (`pip install "wc3files[mcp]"`), so an AI assistant such as Claude can read maps, models and replays: `list_files`, `map_info`, `list_members`, `read_member`, `object_data`, `trigger_text`, `check_script`, `model_info` and `replay_info`. Every tool only reads, opens files under the `--root` folders only, and refuses protected maps. The tools are plain functions in `wc3files.mcp.tools`. See `docs/mcp.md` (#179).
- Every map, model, table and script file class has `get_data()` and `import_data(data)`, which returns the handler. `RegionFile` and `PreviewIconsFile` had neither, eleven classes had no `import_data()`, and seven had only `export_data()`, which stays as the same method. The CLI no longer sets their private `_data` (#174).
- Every map sub-file class has `to_bytes()`, so a parsed member can be changed and put back into `W3xFile.files` without a temporary file; `write()` saves what `to_bytes()` returns. Only `MapDoodadsFile` had it (#173).

### Changed

- MDX keyframe tracks are read in one pass per track, with one unpack per key. Over every fifth corpus model (1,144 models, 164 MB, read from memory), MDX reads at 46.5 MB/s, up from 25.6, with the same data for all 5,716 models (#176).
- MDL vectors, float lists and keyframe tracks are read from the token list directly, and the tokenizer drops comments in its regular expression. Over every fifth corpus model as MDL (1,142 models, 459 MB), MDL reads at 9.4 MB/s, up from 5.9, with the same data for every model (#177).

### Removed

- The empty `wc3files.gui` package; `wc3files.ui` takes its place (#172).
- Python 3.9, which reached its end of life in October 2025. The package needs 3.10 or newer, and CI tests 3.10 as the oldest version on Linux, Windows and macOS (#178).

### Fixed

- A replay with a damaged data block raises `ReplayBlockError`, a `ValueError`, with the zlib error as its cause, instead of `zlib.error`. A block cut short raises `TruncatedDataError` instead of a plain `EOFError` (#166).
- `JassLexError`, `JassParseError`, `LuaLexError` and `LuaParseError` are `ValueError` subclasses, as the other readers' errors are, so `except ValueError` around `ScriptFile.read` catches a script with a syntax error (#167).
- MDL reads `-0` as a negative zero float, so MDX to MDL to MDX keeps the sign of zero. On every tenth corpus model (571), MDL to MDL is a fixed point for 523, up from 441, and MDX to MDL to MDX is exact for 117, up from 102 (#168).
- The CLI help and error messages no longer send users to TODO.md, which is not in the sdist or the wheel. They say what is needed, or link to `docs/cli.md`, which has new sections on checking object data, override tables and base game data. A `war3map.w3i` version 39 error asks for the map to be reported. The `img-to-png` summary lists every image format it reads (#169).
- `docs/lni.md` gives current Lni round-trip figures. It said 2 object files were refused, which #30 fixed in 0.3.0 (#175).
- The build prints no warnings: the deprecated license classifier is gone (`license='MIT'` already said it), and the data folders are listed as namespace packages. CI fails the package job on a build warning (#171).
- Python 3.14 is declared and tested: CI runs it on Linux, Windows and macOS as the newest version, and builds the package with it (#170).

## [0.5.0] - 2026-11-24

### Added

- This changelog (#133).
- `W3xFile.read(members=...)` and `read_map(members=...)` read only the named files, so the rest of the map is not decompressed (#139).

### Changed

- `W3xFile()` and `MpqFiles()` no longer read the 332,532 reference names when they are created. A DEEP read loads them once, on first use, and probes them in sorted order. Reading 30 small corpus maps goes from 5.2 to 1.9 s (#135).
- `BinaryFile` no longer asks the file for its position before every read, and reads a file of up to 64 MB into memory. Reading w3e, wtg and object data files is about twice as fast: w3e goes from 1.0 to 2.7 MB/s, wtg from 3.3 to 6.7 MB/s (#136).
- `TerrainFile` reads and writes the tile grid in one pass. war3map.w3e reads at 10.3 MB/s, up from 2.7, and writes at 11.0 MB/s, up from 3.2 (#137).
- The JASS lexer walks the source with `finditer` and takes columns from the line start. It gives the same tokens on all 703 corpus scripts, 32% faster, and `ScriptFile.read` parses JASS at 3.1 MB/s, up from 2.7 (#138).
- `jass --check`, `trigger-text`, `script-to-triggers` and `run-map` read only the members they use from a map, and `script-to-triggers` reads it once instead of twice. On a 350 MB map, `script-to-triggers` goes from 6.0 to 2.0 s (#139).
- The MPQ modules and `W3xFile` import their names explicitly instead of with `from .data import *`, a dead branch and unused parameters are gone, and CI runs pyflakes over `src/` (#148).
- The package no longer prints log lines unless the application enables them with `logger.enable('wc3files')`. Reading an SLK table printed an INFO line to stderr. The command line still shows warnings (#145).

### Fixed

- A truncated or damaged `war3map.doo` raises `TruncatedDataError` instead of `struct.error` or `IndexError` (#140).
- A truncated MDX model raises `TruncatedDataError`, which is still an `EOFError`, instead of a plain `EOFError` (#141).
- A BLP or DDS image cut short raises the new `BlpTruncatedError` or `DdsTruncatedError` instead of `struct.error`. Each is both the format's error and a `TruncatedDataError` (#142).
- A damaged zlib or bzip2 MPQ sector raises `MpqCompressionError`, with the zlib or bzip2 error as its cause, instead of `zlib.error` or `OSError` (#143).
- An unknown GUI function in `war3map.wtg` raises the new `TriggerDataError`, which is both a `KeyError` and a `ValueError`, and an invalid function type raises `ValueError` instead of `KeyError` (#144).
- `RegionFile` and `PreviewIconsFile` read bytes, as the other readers do. `ObjectFile` uses `object_type=` for a path whose extension is not an object type, and its error says when `object_type=` is needed (#146).
- The README no longer counts 28 subcommands (there are 35), and `docs/cli.md` shows `gamedata-extract`, `trigger-ui` and `optimize` (#147).
- On Windows, a command whose output goes to a file or a pipe writes UTF-8, instead of stopping with a `charmap` codec error on text outside the code page. `PYTHONIOENCODING` is respected (#153).

## [0.4.0] - 2026-11-23

### Added

- `python -m wc3files` runs the CLI (#73).
- `jass --check`, `trigger-text` and `script-to-triggers` take a map as well as a single file (#74).
- `dump` and `build` take the format from the file name when it is not given, and `units` names `war3mapUnits.doo` (#76).
- DDS reads BC4 and BC5 (ATI1, ATI2), also behind a DX10 header (#88).
- MDL reads and writes lights: their type, static values and animated tracks (#86).
- `W3iFile.map_flag_names()` names every map flag, and `encode_flags` keeps bits it has no name for (#67).
- The six unknown `war3map.w3s` dialogue fields have names: `listener_name`, `asset_flags`, `speaker_unit`, `animation_label`, `animation_group`, `animation_set_path`. The old names still read (#66).
- GUI triggers rebuilt from a script tell `SetPlayerUnitMaxAllowed` from `SetPlayerTechMaxAllowedSwap` by the map's object data (`object_ids=`) (#81).
- A `progress=` parameter on map reads. Progress bars need the new `progress` extra, `pip install wc3files[progress]` (#64).
- The README credits the projects wc3files bundles data from or follows (#89, #91).
- The test suite runs in GitHub Actions on Linux, Windows and macOS, Python 3.9 to 3.13, and the package is built and installed there (#72).

### Changed

- A map whose `(listfile)` is missing or leaves files out counts as protected. `unpack`, `lni`, `slk`, `obj`, `optimize` and `run-map` refuse it with `ProtectedMapError`, instead of writing an almost empty result and exiting with 0 (#62, #63).
- tqdm is no longer a dependency, and the library prints no progress unless asked (#64).
- MPQ decryption goes from 4.0 to 6.6 MB/s and encryption from 4.7 to 6.7 MB/s (#78). PKWare DCL decompression goes from 1.9 to 4.9 MB/s (#79).
- The Lua printer adds only the parentheses it needs (#69).
- The JASS and Lua parsers take 200 nested levels (#70).
- MDL writes floats with as many digits as float32 needs. MDX to MDL to MDX gives the original bytes for 927 of 5,712 corpus models, up from 154 (#84). The rest is #110.
- The README and the docs no longer describe reading protected maps (#122, #130).

### Fixed

- `W3xFile.encrypt` works again, and `W3xFile` uses the one shared MPQ cipher (#77).
- A `(listfile)` that names only itself no longer makes a protected map read as empty (#62).
- `MpqWriter` no longer adds an empty sector to a file whose size is a multiple of the sector size (#68).
- A doodad or unit added by code gets the file's skin layout, so the file is not written with mixed records (#65).
- `lni` writes a trigger name that is not valid UTF-8 (#83).
- The MDL tokenizer no longer cuts a quoted string at `//` (#87).
- MDL reads back the `nan` and `inf` it writes (#85).
- Rebuilt `TriggerRegisterVariableEvent` passes the variable, not its name as a string (#80).
- `run-map --max-steps` stops the run (#75).
- The README examples use classes and methods that exist, and run as tests (#71).
- The bundled game files keep their CRLF line endings on Linux and macOS (#124).
- Deep Lua recursion no longer crashes Python 3.9 and 3.10 on Windows (#126), and nested Lua coroutines start there (#128).

## [0.3.0] - 2026-11-22

A fixes release: 21 issues found in a full check of the package against 1,025 real maps.

### Added

- `wc3files --version` (#37).
- Every reader and writer accepts `pathlib.Path` and other path-like objects (#18).

### Changed

- MPQ hash lookups probe from the hash index, as the game does, instead of scanning the whole table. A lookup on the largest protected table in the corpus goes from 3.0 ms to 0.045 ms (#34).
- `wc3files.files`, `wc3files.data`, `wc3files.mpq` and `wc3files.utils` are the real packages again, and each lists its exports in `__all__` (#35).
- A truncated file raises `TruncatedDataError` instead of being read with zeros (#19).

### Fixed

- On Linux and macOS, `export()` writes members into folders instead of files named with a backslash (#21), and encrypted members stored in folders are read (#22).
- Member names with characters above U+00FF are hashed as their bytes instead of raising `KeyError` (#23).
- A DEEP read skips a block it cannot read instead of stopping (#39).
- Empty files are kept on read (#33).
- `build_from_files()` no longer keeps a stale `(attributes)` or `(signature)` (#24).
- Object data: version-3 files without modifications (#26), a field set twice (#27), a field whose levels use different types (#28), an original entry with a non-zero new id (#29) and a table with two entries of the same id (#30) are written back exactly. All 3,706 corpus object files round-trip, up from 3,646.
- A w3i that ends inside its trailing tables keeps the bytes that are left (#31).
- In a WTS file, an empty entry no longer swallows the next one (#25).
- Trigger files that use natives, BJ functions or `OperatorCompare*` conditions missing from TriggerData.txt are read (#32).
- REAL values are little-endian on every platform (#36).

### Security

- Unpacking no longer writes outside the output folder. A member named like `..\..\file`, an absolute path or a drive name is rejected (#20).

## [0.2.0] - 2026-11-22

### Added

- A vJass and Zinc compiler. `//! external` Grimex tools run through a pure-Python Lua interpreter, and `vjass --check` reports each finding at its vJass source line.
- A Lua interpreter that runs a map script without the game. Its 5.3 mode passes 18 files of the Lua 5.3.4 test suite.
- Whole-map modes: Lni (a map as a folder of text files and back), Slk and Obj.
- A map optimizer: removes unused objects and editor-only files, squeezes models and computes text.
- GUI trigger definitions convert to and from YDWE's ui folder.
- Game tables can be read from a profile of another game version.
- JASS and Lua findings carry their line, and Reforged `war3map.lua` is checked against the game API.
- Image conversion, and the missing Grimex tools.
- `ObjectFile.filter`: filter by object type, table and ids (#3).

### Removed

- pandas is no longer a dependency (#5). `import wc3files` goes from about 0.82 s to 0.28 s.
- Dead code reported by pyflakes (#7).

### Fixed

- Every version-39 w3i is read, and misaligned parses are no longer kept.
- Uncompressed MPQ sectors are read, and every MPQ compression method is decoded in Python.
- Any MPQ format version is read as format 0, as the game does, without printing anything (#12).
- MDX rewrites are byte-exact.
- Unfinished replays are read.
- `ObjectTable.filter(remove=...)` drops the listed ids instead of keeping them (#2).
- The MPQ version-4 header fields are defined once, and completely (#4).
- The two SYLK currency format keys no longer raise invalid-escape warnings (#1).
- CLI commands exit with the status they return.

## [0.1.0] - 2026-10-24

First release. A pure-Python reader and writer for Warcraft III's file
formats: MPQ archives and W3X/W3M/W3N maps, the map sub-files, object data
with the game's own field schema, GUI and custom text triggers, JASS and Lua
scripts, MDL/MDX models, BLP/DDS/TGA/BMP/TIFF/PNG images, replays, UI and
effect files, and the CASC storage of an installed game.

[Unreleased]: https://github.com/Bekhouche/wc3files/compare/v0.6.4...HEAD
[0.6.4]: https://github.com/Bekhouche/wc3files/compare/v0.6.3...v0.6.4
[0.6.3]: https://github.com/Bekhouche/wc3files/compare/v0.6.2...v0.6.3
[0.6.2]: https://github.com/Bekhouche/wc3files/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/Bekhouche/wc3files/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/Bekhouche/wc3files/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Bekhouche/wc3files/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Bekhouche/wc3files/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Bekhouche/wc3files/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Bekhouche/wc3files/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Bekhouche/wc3files/tree/v0.1.0
