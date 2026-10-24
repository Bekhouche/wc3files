# Concepts

Six ideas explain most of the API. They are worth the ten minutes before you
write much code against it.

## One pattern per format

Every format handler is a class with `read()` and `write()`, and a getter for
what it holds:

```python
handler = SomeFile()
handler.read(path)
handler.write(path)
```

`get_data()` returns plain dicts, lists, ints, floats and strings - no custom
node classes to learn, and `json.dumps` works on most of it. Where a format
has a better word for its contents the getter says so: `get_regions()`,
`get_triggers()`, `get_icons()`. Formats meant to be rewritten add
`import_data()` and `export_data()`.

The constructor takes no path. `read()` does.

## Refusing beats guessing

Warcraft III's formats are version-gated and only partly documented. A record
layout that is wrong by four bytes still *parses* - it just returns nonsense,
and the nonsense looks like data. So when a parser meets a layout it cannot
account for, it raises:

```python
>>> info = W3iFile()
>>> info.read('war3map.w3i')
ValueError: Unsupported war3map.w3i version: 42 ...
```

Some parsers go further and verify themselves: they re-serialise what they
just read, or check that parsing landed exactly at end-of-file, and refuse if
it did not. `war3map.w3s` does this, because a wrong per-record length made
every later sound name get cut out of the middle of the previous record - and
only the byte count gave it away.

**What to do when it raises.** The message names the version or field it
stopped on. The parser's own docstring documents which layouts it accepts and
which cases it deliberately refuses, usually with the reasoning. An
unsupported version is a genuine gap, not something to force.

## Round-tripping is the correctness bar

A parser is considered done when a real file can be read, written back
unmodified, and come out byte-for-byte identical - padding, trailing garbage
in fixed-length fields, and all. That matters for more than tidiness: it is
what makes changing one field in someone's map safe, because everything you
did not touch is returned exactly as it was.

Two consequences you can rely on:

- Unknown-but-present data is preserved rather than dropped. Fields nobody
  has identified are carried through verbatim under neutral names.
- Writing is not normalising. The library will not "clean up" a file's
  spelling of a value, because that would change bytes the game may care
  about.

## Strings are reversible, not necessarily valid UTF-8

Real maps carry names written in whatever code page their author used. The
library decodes with `errors='surrogateescape'` and encodes the same way, so
a byte sequence that is not valid UTF-8 survives the round trip unchanged
instead of raising or being replaced. The practical effect: a name may contain
lone surrogates, and printing it can fail even though writing it back is
exact. Encode explicitly if you need to display it:

```python
name.encode('utf-8', 'replace').decode('utf-8')
```

## Bundled game data

Some things cannot be read from a map alone. Object data stores fields by
4-character rawcode and the *meaning* of those codes lives in the game's
`*MetaData.slk` tables; GUI triggers store function names whose argument
counts live in the game's `TriggerData.txt`. The package bundles copies of
those tables, which is why `ObjectFile.to_slk()` can give columns real names
and why trigger files can be read at all.

Two things follow:

- **A map built with an extended editor may need that editor's
  `TriggerData.txt`**, because it uses GUI functions stock Warcraft III does
  not declare. Pass your own:

  ```python
  from wc3files.wtg import TriggerData, MapTriggersFile

  data = TriggerData()
  data.read('path/to/their/TriggerData.txt')
  triggers = MapTriggersFile(trigger_data=data)
  ```

- **Bundled tables go stale when the game patches.** They are refreshed from
  an installed game through this package's own CASC reader, and each refreshed
  file is re-parsed before being accepted.

## Archives without a listfile

An MPQ stores filenames as hashes, not as text, plus an internal `(listfile)`
member naming them. Published maps often strip that member, so names have to
be recovered by hashing candidates and seeing what matches. `W3xFile.read()`
takes a mode for this:

```python
from wc3files import W3xFile, W3xReadMode

archive = W3xFile()
archive.read('MyMap.w3x', mode=W3xReadMode.DEEP)
```

| mode | what it does |
|---|---|
| `SIMPLE` | header and tables only |
| `LISTFILE` | use the archive's own `(listfile)` - the default |
| `DEEP` | also try known names and names referenced from files it finds |

A member whose name cannot be recovered is still *in* the archive and still
readable by hash; it just has no name to list. That is a property of the
format, not a limitation of the reader.
