# Getting started

## Install

```bash
pip install wc3files
```

Pure Python, no build step and no third-party runtime dependencies for the
formats themselves. From a checkout:

```bash
pip install -e .
```

## The shape of every parser

One pattern covers the whole library, so learning it once is enough:

```python
handler = SomeFile()        # construct
handler.read(path)          # parse
data = handler.get_data()   # plain dicts and lists
handler.write(other_path)   # serialise
```

Some formats name the accessor for what they hold - `get_regions()`,
`get_triggers()`, `get_icons()` - and the editable ones add
`import_data()`/`export_data()`. The constructor never takes a path; `read()`
does.

## Workflow 1: look inside a map

```python
from wc3files import W3xFile

archive = W3xFile()
archive.read('MyMap.w3x')

for name in sorted(archive.files):
    print(f'{name:28} {len(archive.files[name]):>9,} bytes')
```

`archive.files` is a `{filename: bytes}` dict - the whole archive in memory.
That is the handle for everything else: pass a member's bytes to the parser
for that format.

An archive whose internal file list has been stripped (common in published
maps) still opens; names that cannot be recovered are simply absent. See
[Concepts](concepts.md#archives-without-a-listfile).

## Workflow 2: read one file out of a map

Most parsers take a path, so write the member out first:

```python
import tempfile, os
from wc3files import W3xFile, W3iFile

archive = W3xFile()
archive.read('MyMap.w3x')

scratch = tempfile.mkdtemp()
info_path = os.path.join(scratch, 'war3map.w3i')
with open(info_path, 'wb') as handle:
    handle.write(archive.files['war3map.w3i'])

info = W3iFile()
info.read(info_path)
data = info.get_data()
print(data['name'], '-', data['author'])
print('players:', len(data['players']), 'forces:', len(data['forces']))
```

## Workflow 3: change something and repack

The round trip is: read the archive, edit `files`, build a new one.

```python
from wc3files import W3xFile, W3iFile

archive = W3xFile()
archive.read('MyMap.w3x')

# ... write war3map.w3i out, edit it with W3iFile, read the bytes back ...
with open(info_path, 'rb') as handle:
    archive.files['war3map.w3i'] = handle.read()

archive.build_from_files(archive.files, 'MyMap.edited.w3x')
```

`build_from_files(files, dst_path)` writes a fresh archive from a
`{filename: bytes}` dict. `build(src_path, dst_path)` does the same from a
directory of loose files, as produced by `export()` or the `unpack` command.

For a one-off edit the command line is less ceremony:

```bash
wc3files unpack MyMap.w3x out/
#  ... edit out/war3map.w3i ...
wc3files pack out/ MyMap.edited.w3x
```

## Reading a file you already have on disk

Nothing requires an archive. Every parser works on a loose file:

```python
from wc3files import ModelFile, BlpFile

model = ModelFile()
model.read('Footman.mdx')
print(model.get_file_type())        # binary (mdx) or text (mdl)

icon = BlpFile()
icon.read('BTNFootman.blp')
icon.save_png('footman.png')
```

## When a parser raises

That is the library telling you it does not recognise the file, not a bug to
work around. The message says which version or field it stopped on. Start
with [Concepts](concepts.md#refusing-beats-guessing), and check the parser's
own docstring - they document the layouts they accept and the cases they
deliberately refuse.

## Next

- [Concepts](concepts.md) - the handful of rules that explain the rest
- [Formats](formats.md) - what is supported, and how far
- [API reference](api-reference.md) - every class and method
