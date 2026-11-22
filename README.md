# wc3files
Warcraft III maps files reader and writer for Python

[![PyPI](https://img.shields.io/pypi/v/wc3files.svg)](https://pypi.python.org/pypi/wc3files)
[![conda-forge](https://img.shields.io/conda/vn/conda-forge/wc3files.svg)](https://anaconda.org/conda-forge/wc3files)
[![Python](https://img.shields.io/pypi/pyversions/wc3files.svg)](https://pypi.python.org/pypi/wc3files)
[![License](https://img.shields.io/pypi/l/wc3files.svg)](LICENSE)

A Python library for reading and writing various Warcraft III file formats. This library allows you to work with map files, object data, regions, replays, and more.

## Installation

```bash
pip install wc3files
```

Or with conda:

```bash
conda install -c conda-forge wc3files
```

Python 3.9 or newer. Pure Python - no compiler, and no StormLib or game
install needed.

## Documentation

Full documentation lives in [docs/](docs/README.md):

- [Getting started](docs/getting-started.md) - install, then the three core workflows
- [Concepts](docs/concepts.md) - the rules that explain the API's shape
- [Formats](docs/formats.md) - every format, its class, and how far support goes
- [API reference](docs/api-reference.md) - every public class and method
- [Command line](docs/cli.md) - the `wc3files` command and its 28 subcommands
- [vJass and Zinc](docs/vjass.md) - compiling vJass and Zinc to JASS, as JassHelper does
- [External tools and Lua](docs/grimex.md) - running a map's ObjectMerger, FileImporter and other tool calls, and the Lua interpreter
- [Game data for other versions](docs/gamedata.md) - profiles of 1.24, 1.27, 1.32... tables extracted from an install, used with `--game-data`
- [Lni mode](docs/lni.md) - a map as a folder of text files (objects as Lni, the rest as JSON) and back, for git
- [Triggers and scripts](docs/triggers.md) - GUI triggers, JASS/Lua, and rebuilding editor triggers from a compiled script

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

### External tools and Lua
- Run the `//! external` calls and Lua `//! externalblock` blocks of a vJass map (ObjectMerger, FileImporter, FileExporter, ConstantMerger), as the World Editor does when saving
- Check that Lua before opening the editor: errors name the `.j` line they come from
- A Lua 5.1/5.3 interpreter in pure Python, sandboxed: `wc3files lua script.lua`
- Check a Reforged `war3map.lua` against the game API (undefined globals, missing `FourCC`, wrong argument counts and types): `wc3files jass war3map.lua --check`
- Run a map's script without the game (common.j stubs, blizzard.j, timers) to find its start-up errors: `wc3files run-map MyMap.w3x`; translate JASS to Lua with `wc3files jass war3map.j --to-lua`

## Usage Examples

### Working with MPQ Archives
```python
from wc3files.mpq import MPQ

# Open an MPQ archive
mpq = MPQ("path/to/archive.mpq")

# List files in the archive
files = mpq.list_files()
print(files)

# Extract a file
data = mpq.extract_file("path/in/archive.txt")
```

### Working with Object Data
```python
from wc3files.w3o import ObjectFile

# Load an object file
obj_file = ObjectFile.from_file("path/to/war3map.w3u")

# Access object data
for obj in obj_file.objects:
    print(f"Object ID: {obj.id}")
    print(f"Modifications: {obj.modifications}")
```

### Working with Region Data
```python
from wc3files.w3r import RegionFile

# Load a region file
region_file = RegionFile.from_file("path/to/war3map.w3r")

# Access regions
for region in region_file.regions:
    print(f"Region: {region.name}")
    print(f"Position: {region.position}")
    print(f"Size: {region.size}")
```

### Working with Replay Files
```python
from wc3files.w3g import ReplayFile

# Load a replay file
replay = ReplayFile.from_file("path/to/replay.w3g")

# Access replay data
print(f"Game: {replay.header.game_identifier}")
print(f"Version: {replay.header.version}")
print(f"Players: {len(replay.player_records)}")
```

## Credits

wc3files stands on the work of the Warcraft III modding community. The projects below are either bundled with it, ported into it, or were followed or checked against while writing it. Where a module took something from one of them, its docstring says what.

### Bundled data

- **Blizzard Entertainment**: `common.j`, `blizzard.j`, `common.ai`, and the SLK and TXT game tables under `w3o/gamedata/` are copies of the game's own files, used so that maps can be read without an installed game. Warcraft III and its data belong to Blizzard Entertainment.
- **[War3Net](https://github.com/Drake53/War3Net)** by Drake53 (MIT): the bundled `TriggerData.txt`, itself derived from Blizzard's `UI/TriggerData.txt`.
- **[Warcraft Image Lab](https://www.hiveworkshop.com/threads/warcraft-image-lab.333142/)** by LaZz: the 13 icon borders in `img/gamedata/borders/`, copied from it with LaZz's permission.
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