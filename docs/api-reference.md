# API reference

Everything here is importable from the top-level package:

```python
from wc3files import W3xFile, ObjectFile, ScriptFile
```

Signatures are given as they are, including defaults. Where a method is
absent from a class it is noted, because the absence is sometimes the
interesting part.

---

## Archives

### `W3xFile`

The map wrapper. Reading populates `files`, a `{filename: bytes}` dict that is
the handle for everything else.

```python
read(file_path, mode=W3xReadMode.LISTFILE)
build(src_path, dst_path, protection=MpqProtectionLevel.NONE, attributes=False)
build_from_files(files, dst_path, sector_size_shift=3,
                 protection=MpqProtectionLevel.NONE, attributes=False)
export(path)                      # write every member as loose files
get_filelist()
read_file(reader, filename)
read_without_listfile(reader)
clear()
```

Name-recovery helpers, useful on archives with no `(listfile)`:
`find_manifest_referenced_files()`, `find_object_referenced_files()`,
`find_script_referenced_files()`, `find_txt_referenced_files()`.

Low-level MPQ access, if you need it: `read_header()`, `read_hash_table()`,
`read_block_table()`, `get_hash_table_entry()`, `hash()`,
`initialize_crypt_table()`, `decompress()`, `encrypt()`, `decrypt()`.

`mode` is a `W3xReadMode`: `SIMPLE`, `LISTFILE` (default) or `DEEP` - see
[Concepts](concepts.md#archives-without-a-listfile).

### `MpqFile`, `MpqWriter`

```python
MpqFile().read(file_path)
MpqFile().read_files()
MpqFile().export(path)
MpqFile().build(files, dst_path, sector_size_shift=3,
                protection=MpqProtectionLevel.NONE, attributes=False)

MpqWriter(sector_size_shift=3, protection=MpqProtectionLevel.NONE,
          attributes=False, rng=None)
    .build(files)             # -> bytes
    .write(files, dst_path)
```

`MpqProtectionLevel` is `NONE`, `SAFE`, `EXPERIMENTAL` or `AGGRESSIVE`.

### Reading an installed game

```python
from wc3files.casc import CascStorage

storage = CascStorage()
storage.open(r'C:/Program Files (x86)/Warcraft III')
print(storage.version)                       # attribute, not a method
print(storage.product)

for path in storage.files():                 # every root entry
    ...

storage.search(pattern, locale=None, limit=None)
blob = storage.read(path_or_entry, locale=None, verify=True)
storage.extract(path_or_entry, dest_path, locale=None)
storage.info(path_or_entry, locale=None)
storage.read_by_ckey(ckey)
```

Only `enUS` is readable from a normal install; other locales' chunks are
encrypted. Game files live under layered paths such as
`War3.w3mod:Units/UnitData.slk`, and a table may also exist under
`War3.w3mod:_Balance/<mode>.w3mod:...` as a per-gamemode override holding only
the rows that mode changes.

---

## Map sub-files

All of these share the same shape:

```python
handler = SomeFile()
handler.read(file_path)
data = handler.get_data()
handler.write(file_path)
handler.clear()
```

| Class | Extra methods |
|---|---|
| `W3iFile` | `apply_strings(wts)`, `decode_flags`/`encode_flags`, `decode_force_flags`/`encode_force_flags`, `decode_player_mask`/`encode_player_mask` |
| `TerrainFile` | - |
| `MapDoodadsFile` | `import_data(data)`, `to_bytes()` |
| `MapUnitsFile` | - |
| `RegionFile` | `get_regions() -> {int: Region}`, `add_region(index, region)`, `remove_region(index)` |
| `CameraFile` | - |
| `SoundsFile` | `decode_flags`/`encode_flags` |
| `PathingFile` | `decode_flags`/`encode_flags` |
| `ShadowMapFile` | `read(path, width, height)` - see below. `to_bool_grid()`, `from_bool_grid(width, height, bools)` (static), `is_shadowed(byte_value)` (static: tests one byte, not a coordinate) |
| `PreviewIconsFile` | `get_icons()`, `add_icon(icon)`, `remove_icon(icon)` |
| `ImpFile` | - |
| `CampaignInfoFile` | `decode_flags`/`encode_flags` |

`war3map.shd` is a bare grid with no header - one byte per cell and nothing
saying how wide it is - so `ShadowMapFile.read()` has to be told. It is the
same grid as `war3map.wpm` (4x4 cells per terrain tile, row-major from the
bottom-left), and `war3map.wpm` *does* carry its dimensions, which makes it
the place to get them:

```python
from wc3files import ShadowMapFile, PathingFile

pathing = PathingFile()
pathing.read('war3map.wpm')
grid = pathing.get_data()

shadow = ShadowMapFile()
shadow.read('war3map.shd', grid['width'], grid['height'])
```

Passing the terrain's own width and height from `war3map.w3e` will not work:
those count terrain tiles, not pathing cells. Get it wrong and `read()`
refuses with the size it expected against the size of the file, rather than
returning a misaligned grid.

`apply_strings(wts)` resolves `TRIGSTR_000` references into real text using a
`WtsFile`:

```python
from wc3files import W3iFile, WtsFile

strings = WtsFile(); strings.read('war3map.wts')
info = W3iFile(); info.read('war3map.w3i')
info.apply_strings(strings)
print(info.get_data()['name'])      # readable, not TRIGSTR_000
```

---

## Object data

### `ObjectFile`

```python
read(file_path, object_type=None, skin='auto')
write(file_path, skin='as-read', validate=False)
export_data()
import_data(data)
to_slk(object_type=None, metadata=None, base_data=None)
validate(object_type=None, metadata=None)
apply_strings(wts)
get_object_type() / set_object_type(t)
filter(...)
to_override_tables(...) / from_override_tables(...)
register_hook(...) / unregister_hook(...) / clear_hooks()
read_skin(...)
```

`object_type` is usually inferred from the extension. `skin='as-read'` on
write preserves whichever variant the file used, which is what keeps the round
trip byte-exact.

```python
from wc3files import ObjectFile

units = ObjectFile()
units.read('war3map.w3u')
for rawcode, fields in units.export_data().items():
    print(rawcode, len(fields), 'changed fields')
```

### Tables and strings

```python
SlkFile().read(file_path)
SlkFile().write(file_path, id_column=None)
SlkFile().export_data() -> dict
SlkFile().import_data(data, id_column=None)
SlkFile().fix_strings(...)

WtsFile().read(file_path)
WtsFile().get(string_id)
WtsFile().resolve(value)          # 'TRIGSTR_007' -> the text
WtsFile().export_data() / import_data(data) / write(path)

CfgFile().read(path) / write(path) / export_data() / import_data(d)
          / export_full() / import_full(d)
WesFile()  # same shape as CfgFile
JsonFile().load_slk(...) / load_wes(...) / export_data() / import_data(d) / write(path)
```

### Schema

```python
from wc3files import ObjectMetaData, BaseObjectData, ObjectEditorEnums

from wc3files import ObjectFileType

meta = ObjectMetaData.default()
meta.fields(ObjectFileType.Units)             # every field for one type
meta.field(ObjectFileType.Units, field_code)  # what one rawcode means
meta.unknown_fields()

BaseObjectData.default().get(object_id)      # Blizzard's defaults
ObjectEditorEnums.default().valid_codes(...)
```

---

## Triggers and scripts

See [Triggers and scripts](triggers.md) for the workflows; this is the
surface.

```python
MapTriggersFile(trigger_data=None)
    .read(file_path) / .write(file_path)
    .get_triggers() / .get_data()

MapCustomTextTriggersFile().read(path) / .write(path)

TriggerData()
    .read(path_or_text)
TriggerData.default()                 # the bundled declarations
    .arg_types(function_type, name)
    .arg_count(function_type, name)
    .return_type(name)
    .script_name(gui_name) / .gui_names(script_name)
    .comparison_for_type(gui_type)

ScriptFile()
    .read(file_path)                  # .j or .lua, or raw bytes
    .write(file_path, validate=False)
    .get_data()                       # ir.Program (JASS) or ir.Chunk (Lua)
    .get_file_type()
    .import_data(program)
    .validate(natives=None)           # JASS only
```

Module-level helpers:

```python
from wc3files import (parse_jass, print_jass, tokenize_jass,
                      parse_lua, print_lua, tokenize_lua,
                      check_jass, find_reachable, remove_dead_code,
                      build_rename_table, apply_rename, obfuscate_lua)
from wc3files.wtg.from_script import reconstruct_from_jass
from wc3files.wtg import trigger_text
```

Exceptions: `JassLexError`, `JassParseError`, `LuaLexError`, `LuaParseError` -
all carry `line` and `col`.

---

## Models

```python
from wc3files import ModelFile

model = ModelFile()
model.read('Footman.mdx')
model.get_file_type()        # which of MDX/MDL it was
data = model.export_data()   # one shared model for both formats
model.import_data(data)
model.write('Footman.mdl')   # format follows the extension
```

---

## Images

Every image class has the same core:

```python
read(file_or_data)       # BlpFile takes bytes as well as a path
write(path)
get_rgba(mip=0)          # -> bytes, width*height*4, row-major RGBA8
from_rgba(width, height, rgba, alpha='auto')     # static
save_png(path, mip=0)
to_bytes() / to_png_bytes()
clear()
```

`get_rgba()` returns the pixels alone. Dimensions come from the `width` and
`height` attributes, or `mip_size(mip)` for a given level. `BlpFile` and
`DdsFile` add `mip_count()`; `BlpFile` also has `aliased_mip_count()`,
`mip_size(mip)`, and the attributes `version`, `compression`, `alpha_bits`,
`picture_type` and `has_mipmaps`.

```python
from wc3files import BlpFile

icon = BlpFile()
icon.read('BTNFootman.blp')
print(icon.width, icon.height)       # 64 64
rgba = icon.get_rgba()               # bytes: width * height * 4
icon.save_png('out.png')
```

`read_png`/`write_png` work in memory rather than on paths - note
`write_png` *returns* the encoded bytes:

```python
from wc3files import read_png, write_png

blob = write_png(icon.width, icon.height, rgba)
with open('out.png', 'wb') as handle:
    handle.write(blob)
```

Authoring helpers: `resize_box`, `resize_nearest`, `composite_over`,
`make_disabled_variant`, `quantize_median_cut`, `border_path`,
`CLASSIC_BORDERS`, `REFORGED_BORDERS`.

Icon border art *is* bundled, in both the classic 64x64 and Reforged 256x256
styles, so `--border` works without supplying your own image:

```python
from wc3files import border_path, CLASSIC_BORDERS, REFORGED_BORDERS

print(sorted(CLASSIC_BORDERS))        # active, attack, autocast, disabled,
                                      # passive, upgrade
classic = border_path('active')                   # 64x64
reforged = border_path('active', reforged=True)   # 256x256
```

`border_path(kind, reforged=False)` takes the kind, not a filename, and
raises `KeyError` for an unknown one. Reforged adds `passive_disabled`.

It is one real, usable style rather than a universal ground truth - border
art genuinely varies between maps and mods - which is why every
`--border`-family flag still prefers a caller-supplied override.

---

## UI, effects and replays

```python
FdfFile().read(path) / .write(path) / .get_data() / .import_data(d)
    .blocks() / .frames() / .includes() / .strings() / .to_text()
TocFile().read(path) / .write(path) / .get_data() / .import_data(d)

PkbFile().read(path)  / .get_data() / .to_bytes()  / .write(path)
PkcfFile().read(path) / .get_data() / .to_text()   / .write(path)
PkmmFile().read(path) / .get_data() / .to_bytes()  / .write(path)

WaiFile().read(path) / .get_data() / .strings() / .to_bytes() / .write(path)

ReplayFile().read(path) / .get_data() / .import_data(d) / .write(path)
```

Exceptions: `FdfError`, `PopcornError`, `WaiError`, `BlpError`, `DdsError`,
`TgaError`, `BmpError`, `TiffError`, `PngError`, and the CASC family
(`CascStorageError`, `BlteError`, `BlteEncryptedError`, `CascConfigError`,
`CascEncodingError`, `CascIndexError`, `CascRootError`).

---

## Low-level

`BinaryFile` is the primitive every parser is built on - little-endian reads
and writes with a `BinaryDataType` tag. Reach for it when adding a format, not
when using one.

```python
from wc3files import BinaryFile, BinaryDataType

with BinaryFile(path, 'read') as reader:
    version = reader.read(BinaryDataType.INT)
    name = reader.read(BinaryDataType.STRING)
```

Strings go through `errors='surrogateescape'` in both directions; see
[Concepts](concepts.md#strings-are-reversible-not-necessarily-valid-utf-8).
