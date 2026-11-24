# Changelog

All notable changes to wc3files are listed here, newest first. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
version numbers follow [Semantic Versioning](https://semver.org/). Numbers
in parentheses are GitHub issues.

Each pull request adds its line under [Unreleased]. A release renames that
section to the new version and its date.

## [Unreleased]

### Added

- Every map, model, table and script file class has `get_data()` and `import_data(data)`, which returns the handler. `RegionFile` and `PreviewIconsFile` had neither, eleven classes had no `import_data()`, and seven had only `export_data()`, which stays as the same method. The CLI no longer sets their private `_data` (#174).
- Every map sub-file class has `to_bytes()`, so a parsed member can be changed and put back into `W3xFile.files` without a temporary file; `write()` saves what `to_bytes()` returns. Only `MapDoodadsFile` had it (#173).

### Fixed

- A replay with a damaged data block raises `ReplayBlockError`, a `ValueError`, with the zlib error as its cause, instead of `zlib.error`. A block cut short raises `TruncatedDataError` instead of a plain `EOFError` (#166).
- `JassLexError`, `JassParseError`, `LuaLexError` and `LuaParseError` are `ValueError` subclasses, as the other readers' errors are, so `except ValueError` around `ScriptFile.read` catches a script with a syntax error (#167).
- MDL reads `-0` as a negative zero float, so MDX to MDL to MDX keeps the sign of zero. On every tenth corpus model (571), MDL to MDL is a fixed point for 523, up from 441, and MDX to MDL to MDX is exact for 117, up from 102 (#168).
- The CLI help and error messages no longer send users to TODO.md, which is not in the sdist or the wheel. They say what is needed, or link to `docs/cli.md`, which has new sections on checking object data, override tables and base game data. A `war3map.w3i` version 39 error asks for the map to be reported. The `img-to-png` summary lists every image format it reads (#169).
- `docs/lni.md` gives current Lni round-trip figures. It said 2 object files were refused, which #30 fixed in 0.3.0 (#175).
- The build prints no warnings: the deprecated license classifier is gone (`license='MIT'` already said it), and the data folders are listed as namespace packages. CI fails the package job on a build warning (#171).

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

[Unreleased]: https://github.com/Bekhouche/wc3files/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/Bekhouche/wc3files/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Bekhouche/wc3files/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Bekhouche/wc3files/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Bekhouche/wc3files/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Bekhouche/wc3files/tree/v0.1.0
