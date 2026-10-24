# wc3files documentation

`wc3files` reads and writes Warcraft III's file formats in pure Python. No
StormLib, no game install, no C extensions: open a map, read or change what
is inside it, write it back.

```python
from wc3files import W3xFile

archive = W3xFile()
archive.read('MyMap.w3x')
print(sorted(archive.files))          # war3map.w3i, war3map.j, war3map.doo, ...
```

## Where to go

| | |
|---|---|
| [Getting started](getting-started.md) | Install, then the three workflows almost everything is built from |
| [Concepts](concepts.md) | Why the API looks the way it does - read this before writing much code against it |
| [Formats](formats.md) | Every format, the class that handles it, and how far support goes |
| [API reference](api-reference.md) | Every public class and its methods, grouped by what it is for |
| [Command line](cli.md) | The `wc3files` command and its 26 subcommands |
| [Triggers and scripts](triggers.md) | GUI triggers, JASS/Lua, and rebuilding editor triggers from a compiled script |

## What a Warcraft III map is

A `.w3x` (or `.w3m`, or `.w3n` campaign) is an **MPQ archive** - a container
format - holding a set of files with fixed names. `wc3files` gives you a
reader for the archive and a reader for each file inside it:

```
MyMap.w3x                 MPQ archive            W3xFile
├── war3map.w3i           map settings           W3iFile
├── war3map.w3e           terrain                TerrainFile
├── war3map.doo           doodads                MapDoodadsFile
├── war3mapUnits.doo      units and items        MapUnitsFile
├── war3map.w3u .w3t ...  object data            ObjectFile
├── war3map.wtg           GUI triggers           MapTriggersFile
├── war3map.j             compiled script        ScriptFile
├── war3map.wts           translatable strings   WtsFile
└── ...                   see Formats
```

Nothing in the library requires the game to be installed. If you *do* have it
installed, [`CascStorage`](api-reference.md#reading-an-installed-game) reads
the game's own files out of it as well.

## A note on how this library behaves

Two things surprise people coming from other tooling, and both are
deliberate:

- **A parser refuses rather than guesses.** Given a file whose layout it does
  not recognise, it raises instead of returning a plausible-looking misread.
  [Concepts](concepts.md) explains why, and what to do when it happens.
- **Writing back is byte-exact where it can be.** Read a file and write it
  unchanged and you get the same bytes, padding and quirks included. That is
  the correctness bar the parsers are held to, and it is what makes editing
  one field in a real map safe.
