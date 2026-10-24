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
- [Command line](docs/cli.md) - the `wc3files` command and its 26 subcommands
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

## License

This project is licensed under the MIT License - see the LICENSE file for details.