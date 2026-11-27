# wc3files
Warcraft III maps files reader and writer for Python

[![PyPI](https://img.shields.io/pypi/v/wc3files.svg)](https://pypi.python.org/pypi/wc3files)
[![Python](https://img.shields.io/pypi/pyversions/wc3files.svg)](https://pypi.python.org/pypi/wc3files)
[![License](https://img.shields.io/pypi/l/wc3files.svg)](LICENSE)
[![wc3files MCP server](https://glama.ai/mcp/servers/Bekhouche/wc3files/badges/score.svg)](https://glama.ai/mcp/servers/Bekhouche/wc3files)

A Python library for reading and writing various Warcraft III file formats. This library allows you to work with map files, object data, regions, replays, and more.

## Installation

```bash
pip install wc3files
```

A conda-forge package is in review and not published yet.

Python 3.10 or newer. Pure Python - no compiler, and no StormLib or game
install needed. The only dependency is loguru.

## Documentation

Full documentation lives in [docs/](docs/README.md):

- [Getting started](docs/getting-started.md) - install, then the three core workflows
- [Concepts](docs/concepts.md) - the rules that explain the API's shape
- [Formats](docs/formats.md) - every format, its class, and how far support goes
- [API reference](docs/api-reference.md) - every public class and method
- [Command line](docs/cli.md) - the `wc3files` command and every subcommand
- [vJass and Zinc](docs/vjass.md) - compiling vJass and Zinc to JASS, as JassHelper does
- [External tools and Lua](docs/grimex.md) - running a map's ObjectMerger, FileImporter and other tool calls, and the Lua interpreter
- [Game data for other versions](docs/gamedata.md) - profiles of 1.24, 1.27, 1.32... tables extracted from an install, used with `--game-data`
- [Lni mode](docs/lni.md) - a map as a folder of text files (objects as Lni, the rest as JSON) and back, for git
- [Triggers and scripts](docs/triggers.md) - GUI triggers, JASS/Lua, and rebuilding editor triggers from a compiled script
- [In-game UI frames](docs/ui.md) - describe a UI as data, and write the JASS, Lua, .fdf or map trigger that creates it
- [MCP server](docs/mcp.md) - `wc3files-mcp`, which lets an AI assistant such as Claude read your maps, models and replays
- [Language server](docs/lsp.md) - `wc3files-lsp`, which shows script errors and leaks in your editor as you type

What changed in each release is in [CHANGELOG.md](CHANGELOG.md).

## Features

### MPQ Archives
- Read and extract MPQ archive files (Blizzard's proprietary archive format)
- Access individual files within MPQ archives
- Handle encryption, compression, and other MPQ-specific features

### Map Files (W3X)
- Read and write Warcraft III map files (.w3x)
- Access map resources and components

### Object Data (W3O)
- Parse and modify object data files (.w3o)
- Work with SLK (Symbolic Link) files
- Handle WES (World Editor Strings) files
- Process configuration files (CFG)

### Region Data (W3R)
- Read and write region files (.w3r)
- Manipulate region information in maps

### Replay Files (W3G)
- Parse Warcraft III replay files (.w3g)
- Extract game information, player data, and actions

### vJass and Zinc
- Compile vJass and Zinc to plain JASS, as JassHelper does, without the World Editor
- Compile a single script, a set of library files, or the `war3map.j` of a whole map
- `wc3files vjass war3map.j -o compiled.j`, or `VJassCompiler().compile_file(...)` from Python

### In-game UI frames
- Describe frames (backdrops, texts, buttons, their size, anchors, textures and texts) in JSON or Python
- Write the JASS or Lua that creates them, an .fdf, or a custom-text trigger in a map: `wc3files ui-build ui.json --map MyMap.w3x --out MyMap.ui.w3x`
- Read an .fdf back into a layout

### MCP server
- `pip install "wc3files[mcp]"`, then `wc3files-mcp --root D:/Maps`: read-only tools for an AI assistant such as Claude (map info, members, object data, trigger text, script checks, models, replays)
- Opens only files under the given folders, and refuses protected maps
- With `--allow-write`, edits maps too, writing each result as a new map: object data, placed units and doodads, regions, cameras, strings, the script and imported files
- Listed on [Glama](https://glama.ai/mcp/servers/Bekhouche/wc3files) and [MCP Rush](https://mcprush.com/bekhouche/wc3files-mcp)

<a href="https://mcprush.com/to/wc3files-mcp"><img width="380" height="200" src="https://mcprush.com/badge/wc3files-mcp/card.svg" alt="wc3files MCP server" loading="lazy" /></a>

<!-- mcp-name: io.github.Bekhouche/wc3files -->

### Language server
- `wc3files-lsp`: JASS and Lua checks in any editor with a Language Server Protocol client, as you type: syntax errors, unknown functions, wrong arguments, handle leaks
- Comes with the package, no extra needed

### External tools and Lua
- Run the `//! external` calls and Lua `//! externalblock` blocks of a vJass map (ObjectMerger, FileImporter, FileExporter, ConstantMerger), as the World Editor does when saving
- Check that Lua before opening the editor: errors name the `.j` line they come from
- A Lua 5.1/5.3 interpreter in pure Python, sandboxed: `wc3files lua script.lua`
- Check a Reforged `war3map.lua` against the game API (undefined globals, missing `FourCC`, wrong argument counts and types): `wc3files jass war3map.lua --check`
- Run a map's script without the game (common.j stubs, blizzard.j, timers) to find its start-up errors: `wc3files run-map MyMap.w3x`; translate JASS to Lua with `wc3files jass war3map.j --to-lua`

## Usage Examples

These examples are run by the test suite (`tests/test_readme.py`), on the
checked-in sample files, so they stay correct.

### Working with a map

```python
from wc3files import W3xFile, W3iFile

# Read a map: every member it lists, by name, as bytes.
archive = W3xFile()
archive.read("MyMap.w3x")
print(sorted(archive.files))

# Parse a member, change it, and put it back, all in memory.
info = W3iFile()
info.read(archive.files["war3map.w3i"])
info.get_data()["map_name"] = "My edited map"
archive.files["war3map.w3i"] = info.to_bytes()

# Raw bytes can be changed too. Then build the map again.
script = archive.files["war3map.j"]
archive.files["war3map.j"] = script + b"// edited with wc3files\r\n"
archive.build_from_files(archive.files, "MyMap.edited.w3x")
```

### Working with MPQ archives

```python
from wc3files.mpq import MpqFile

mpq = MpqFile()
mpq.read("MyMap.w3x")   # .mpq, .w3x, .w3m and .w3n
mpq.read_files()
for name, data in mpq.files.items():
    print(name, len(data or b""))
```

### Working with object data

```python
from wc3files.w3o import ObjectFile

units = ObjectFile()
units.read("war3map.w3u")
tables = units.export_data()["w3u"]
for table in ("original", "custom"):
    for object_id, fields in tables[table].items():
        print(table, object_id, sorted(code for code in fields if code != "ID"))
```

### Working with regions

```python
from wc3files.w3r import RegionFile

regions = RegionFile()
regions.read("war3map.w3r")
for region in regions.get_regions().values():
    print(region.name, (region.left, region.bottom), (region.right, region.top))
```

### Working with replays

```python
from wc3files.w3g import ReplayFile

replay = ReplayFile()
replay.read("replay.w3g")
data = replay.get_data()
game = data["compressed_data"]
print(game["MapName"], game["GameName"], data["header"]["sub"]["version_number"])
```

## Credits

wc3files stands on the work of the Warcraft III modding community. The projects below are either bundled with it, ported into it, or were followed or checked against while writing it. Where a module took something from one of them, its docstring says what.

### Bundled data

- **Blizzard Entertainment**: `common.j`, `blizzard.j`, `common.ai`, and the SLK and TXT game tables under `w3o/gamedata/` are copies of the game's own files, used so that maps can be read without an installed game. Warcraft III and its data belong to Blizzard Entertainment.
- **[War3Net](https://github.com/Drake53/War3Net)** by Drake53 (MIT): the bundled `TriggerData.txt`, itself derived from Blizzard's `UI/TriggerData.txt`.
- **[Warcraft Image Lab](https://www.hiveworkshop.com/threads/warcraft-image-lab.333142/)** by LaZz: the 13 icon borders in `img/gamedata/borders/`.
- **blue & nagger**: the replay action and order names, generated from their `w3g_actions.txt` v1.01 (2006, w3g.deepnode.de).

### Ported code

- **[StormLib](https://github.com/ladislav-zezula/StormLib)** by Ladislav Zezula (MIT): the Huffman and IMA ADPCM decoders in `mpq/compression.py` are ports of its `huff.cpp` and `adpcm.cpp`. Its source comments were also the reference for the hash table probing and for what protected maps do to an archive.

### Designs followed

These were reimplemented from their documentation and behaviour. wc3files contains none of their code.

- **[w3x2lni](https://github.com/sumneko/w3x2lni)**: the Lni, Slk and Obj map modes, the optimizer passes, computed text, MDX squeezing, and the JASS dead-code removal and renaming.
- **JassHelper** by Vexorian (zlib): the vJass and Zinc languages, and the names of the generated code.
- **YDWE**: the ui folder format for GUI trigger definitions.
- **Grim Extension Pack** by PitzerMike: the `//! external` tools (ObjectMerger, FileImporter, FileExporter, ConstantMerger).

### Format references

The parsers were cross-checked against these, field by field, and every disagreement was settled against real map files:

- [War3Net](https://github.com/Drake53/War3Net) (War3Net.Build and War3Net.Build.Core)
- [HiveWE](https://github.com/stijnherfst/HiveWE), its code and its wiki
- [mdx-m3-viewer](https://github.com/flowtsohg/mdx-m3-viewer)
- [WC3MapSpecification](https://github.com/ChiefOfGxBxL/WC3MapSpecification) by ChiefOfGxBxL
- The format threads and tools of [Hive Workshop](https://www.hiveworkshop.com)

### Checks

- **pjass**: the reference JASS syntax checker, used to validate the compiled vJass and the obfuscator output.
- **The Lua test suites** ([lua.org/tests](https://www.lua.org/tests/)): the Lua interpreter was run against the 5.1 and 5.3.4 suites. They are not checked in.

If your work should be on this list and is not, please open an issue.

## License

This project is licensed under the MIT License - see the LICENSE file for details. The bundled data above keeps its own terms.