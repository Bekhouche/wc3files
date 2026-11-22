# External tools and Lua

`wc3files.grimex` runs the external tool calls of a vJass map: the
`//! external` commands and the `//! externalblock` blocks of Lua that the
World Editor hands to Grim Extension Pack's tools (ObjectMerger, FileImporter,
FileExporter, ConstantMerger, PathMapper, TileSetter, TriggerMerger) when the
map is saved. It does what those tools
do, without the editor, without the tools and without Windows. The Lua runs in
`wc3files.scripts.lua_vm`, a Lua interpreter written in Python.

It is used to generate a map's objects in a build pipeline, to check the Lua
of a map or a library before opening the editor, to rebuild an old
JassNewGenPack project whose scripts create their own abilities and units,
and to run ordinary Lua scripts in a sandbox.

## Quick start

From the command line:

```bash
wc3files vjass war3map.j --externals check          # run them in a sandbox, report errors
wc3files vjass MyMap.w3x -o Out.w3x --externals run # the map gets the objects they make
wc3files lua make_units.lua --tool ObjectMerger --map MyMap.w3x -o Out.w3x
wc3files lua script.lua                             # plain Lua, sandboxed
```

From Python:

```python
from wc3files.vjass import VJassCompiler

compiler = VJassCompiler()
result = compiler.compile_map('MyMap.w3x', 'Out.w3x', externals=True)
print(result.externals_result.changed)      # ['war3map.w3a', 'war3map.w3u']
```

or, with more control:

```python
from wc3files.grimex import ExternalRunner

result = VJassCompiler().compile_file('war3map.j')
with ExternalRunner(map_files, workspace='tools') as runner:
    done = runner.run(result.externals)
for error in done.errors:
    print(error)            # war3map.j:120: Invalid object field anaam
print(done.output)          # what the scripts printed
new_files = done.changed_files()
```

## How it works

An external runs in four steps, in the order JassHelper uses:

1. **Collect.** The compiler keeps every `//! external` line and every block,
   with the text of each `//! i` line exactly as JassHelper passes it on
   (everything after `i` and one separator, trailing spaces kept) and the
   source position of each line.
2. **Prepare.** Each external gets a fresh tool, as each one is a separate
   process in the editor. A block's `$FILENAME$` stands for its text; a command
   line is split the way a Windows program splits its arguments.
3. **Run.** A block runs as a Lua 5.1 chunk, with the tool's functions
   defined; a command line runs the tool's command-line form. A Lua error
   names the `//! i` line it happened on: `war3map.j:120: attempt to call
   global 'getfilename' (a nil value)`.
4. **Commit.** The tables, imports and constants the tool changed are written
   back into the map's files. An external that fails changes nothing, and
   the run stops there, as the save does in the editor (`stop_on_error=False`
   runs them all, to report every error at once).

## The tools

| Tool | Lua functions | Command line |
|---|---|---|
| ObjectMerger | `setobjecttype`, `currentobjecttype`, `createobject`, `modifyobject`, `objectexists`, `currentobject`, `makechange`, `resetchange`, `resetobject`, `generateid`, `mergefile` | `w3a AHbz A000 anam "Name" Hbz1 1 33`, `[m\|r\|i] file.w3a ...`, `script.lua` |
| FileImporter | `importfile(disk, archive)`, `importdirectory(dir)`, `registercallback(fn)` | `directory`, or `diskpath archivepath ...` |
| FileExporter | `exportfile(archive, disk)`, `exportgroup(group, dir)` | `[targetdir]` |
| ConstantMerger | `mergefile(path)`, `setvalue(section, key, value)` | `war3mapMisc.txt ...` |
| PathMapper | none | `LdrtLgrs 2 \|`, `CE FD 1 &` |
| TileSetter | none | `L LdrtLgrsLgrd` |
| TriggerMerger | none | `war3map.wtg ...` (or the `.wct` of the pair) |

Every tool also has `log`, `logf` and `logt`, which write to the log
(`done.log`). The group identifiers are globals: `current`, `original`,
`custom`, `modded` and `unmodded` for ObjectMerger, `triggers`, `objects`,
`constants`, `environment` and `imports` for FileExporter, and `merge`,
`replace` and `import` for `mergefile`.

The names, arguments and error messages are those of Grim Extension Pack 1.0.
They were read from its binaries rather than from forum posts, which
disagree with each other. A few behaviours that matter for real scripts:

- `objectexists(id)` is true for a custom object, a changed standard object
  or any standard object of the game data.
- `generateid(base)` is a counter, not a search. It starts at `u~~~`, takes
  the base id's first character and steps the last three through the
  printable punctuation (`A!!!`, `A!!"`, `A!!#`, ...), skipping letters,
  digits, the quote, the backslash and the backquote. It does not check
  whether the id is free.
- `makechange` takes a level exactly when the field repeats per level (or
  per doodad variation): `makechange(current, "Hbz1", "2", "40")`, but
  `makechange(current, "anam", "Name")`. The value has to parse as the
  field's type, and the field has to exist for the object type.
- `setobjecttype` accepts `units`, `items`, `abilities`, `buffs`
  (`effects`), `upgrades`, `doodads`, `destructables` (`destructibles`) and
  the extensions `w3u` to `w3q`. `currentobjecttype()` returns the extension.

## The sandbox

Lua written for JassNewGenPack expects a Windows install: it finds the
Warcraft III folder in `package.path`, lists folders with `dir /b`, creates
them with `os.execute("mkdir ...")` and keeps its own libraries as `.lua`
files that later blocks load with `dofile`. The runner gives it a workspace
folder laid out like such an install:

```
<workspace>/                    = C:\Program Files\Warcraft III
<workspace>/jassnewgenpack/     the editor's folder, the tools' working directory
<workspace>/jassnewgenpack/jass/      JassHelper's lookup folder
<workspace>/jassnewgenpack/grimext/   the tools, and package.path
```

The rules are:

1. files can be read in the workspace and the lookup folders, and written
   only in the workspace;
2. `os.execute` and `io.popen` interpret `mkdir`, `if [not] exist`, `del`,
   `rmdir`, `dir`, `copy`, `move`, `echo` and `cd` inside the workspace;
   anything else is refused and recorded in `done.denied`, and no program is
   ever started;
3. a script is stopped after a step budget (200 million steps by default),
   so an endless loop cannot hang a build.

Files opened in text mode use Windows line endings, as the tools' C library
does. A workspace passed with `workspace=` (or `--workspace`) persists between
runs, which is how the LUA_FILE_HEADER family of libraries is installed once
and used afterwards.

## The Lua interpreter

`wc3files.scripts.lua_vm` is a complete Lua in pure Python:

```python
from wc3files.scripts import LuaVM, LuaHost

vm = LuaVM(version='5.1', host=LuaHost(root='scripts', writable=False))
print(vm.execute('return ("%d items"):format(3)'))    # ['3 items']
vm.globals.set('size', 10)
add = vm.execute('return function(a, b) return a + b end')[0]
print(add(2, 3))                                       # [5]
```

It covers the whole language: closures with a fresh variable per loop
iteration, multiple results and varargs, metatables with every metamethod,
weak tables, `goto`, proper tail calls, coroutines, `pcall`/`error` with
levels, `setfenv`, and the standard libraries (`string` with Lua patterns,
`table`, `math`, `os`, `io`, `coroutine`, `package`, `debug`, `utf8` in 5.3).
`version='5.1'` is what Grim Extension Pack embeds: every number prints as a
double (`10/2` is `5`), the 5.1-only functions are there (`unpack`,
`loadstring`, `table.getn`, `module`, ...) and the grammar is 5.1's (no empty
statements, unknown escapes such as `\/` allowed). `version='5.3'` is Lua 5.3,
with integers. Error messages read like Lua's own: `test:2: attempt to index
local 't' (a nil value)`.

Speed is that of an interpreter written in Python: a loop of one million
iterations takes about two seconds, which is far more than a map tool script
needs.

## Verification

The interpreter and the tools were checked in three ways:

1. **The official Lua 5.1 test suite** (`lua5.1-tests` from lua.org): the
   closure, constructs, events, literals, locals, math, nextvar, pm, sort,
   strings, vararg, attrib, code and checktable tests pass unchanged. The
   remaining failures depend on Lua's one-pass compiler (`load` reading a
   chunk lazily; a limit error found before a later syntax error) or on POSIX
   file semantics under Windows.
   The 5.3.4 suite, run in 5.3 mode with its `_port` and `_soft` switches:
   attrib, big, bitwise, closure, code, constructs, coroutine, events, goto,
   literals, locals, math, nextvar, pm, strings, tpack, utf8 and vararg pass
   unchanged. The rest test what an interpreter in Python does not have:
   bytecode (`string.dump` headers, stripped dumps), exact line hooks, the
   garbage collector's steps, the C API, and the `lua` executable. One is
   left out on purpose: `table.sort` does not raise "invalid order function"
   for an inconsistent comparator, because Lua itself raises it only when
   its own partition happens to overrun, and a stricter check would reject
   `a <= b` comparators that work in real maps.
2. **Real Lua.** All 36 `war3map.lua` scripts found on the development
   machine compile.
3. **The nestharus/JASS corpus.** Every external of its `lua/` folder runs:
   the LUA_FILE_HEADER libraries install themselves into the workspace and
   the demos use them. The externals of `jass/` fail for reasons that are
   the scripts' own, the same in the real tools: the shipped
   LUA_GET_VAR_OBJECT calls a `getfilename()` that its LUA_FILE_HEADER
   version does not define, and one installer loads a library without the
   header that makes `dofile` find it.

### FileImporter conversion

Asking for a `.blp` from a `.bmp`, `.tga` or `.png`, or for a `.mdx` from a
`.mdl`, converts the file; `importdirectory` converts every `.jpg`, `.bmp`,
`.tga` and `.mdl` and changes only the extension. A file that cannot be
converted is imported as it is, under its own path, which is the manual's
rule. BLP files are never changed.

The icon prefixes of the manual work too. A file named `CMD...`, `PAS...` or
`AUT...` (combined, as in `AUTPASCMDBTNIcon.tga`) is resized to 64x64, given
the normal, passive or autocast border and written to
`ReplaceableTextures\CommandButtons\`, `PassiveButtons\` or
`AutocastButtons\` under the rest of its name. One disabled version goes to
`ReplaceableTextures\CommandButtonsDisabled\DIS<rest of the name>`.
`infocard-` and `level-infocard-` images get the attack/armor and the caster
upgrade border, and keep their imported path.

### PathMapper, TileSetter and TriggerMerger

PathMapper changes `war3map.wpm` where a ground tile is (`Ldrt 40 !`: every
cell not on Lordaeron Summer Dirt) or where the pathing value matches a mask
(`CE FD 1 &`). The check and apply modes are those of the manual. A pathing
cell is a quarter of a tile in each direction, and `war3map.w3e` keeps one
ground tile per tile corner; the tile of a cell is taken from its nearest
corner.

TileSetter sets the tileset letter (in `war3map.w3e` and `war3map.w3i`) and
up to 16 ground tiles. The terrain keeps its tile indices, as the editor's
own tileset dialog does, so a list shorter than the indices in use is
refused.

TriggerMerger merges a `wtg`/`wct` pair (`wc3files.wtg.merge`). A variable,
trigger or comment with the name of an existing one replaces it; the others
are added in a category of the same name; the source's custom-script header
becomes a new custom-text trigger, `<file name> Header`. On 38 real maps the
`wct` holds one text per GUI or custom-script trigger, in the order of the
`wtg`, and the merge keeps that order. Both maps must have the same layout
(classic, or Reforged's item list).

The corpus also shows why exact behaviour matters. LUA_OBJECT_ID's helper
loops `until objectexists(id) or <the id has a bad character>`, the inverted
condition, so with ObjectMerger's counter it returns the first id with a
comma in it (`h!!,`) rather than a free one. That is what the real tool does
with that script, and what this one does too.

## Limitations

- **JPEG images are not converted.** The package decodes the JPEG planes
  inside BLP files, not ordinary JPEG files, so a `.jpg` is imported as it
  is. The converted BLP files are paletted (no JPEG encoder), and the
  borders are the classic ones of `wc3files.img.borders`, not the TGA files of
  a Grim Extension Pack install.
- **No Lua for PathMapper, TileSetter and TriggerMerger.** Their Lua
  functions are not documented, so only the command-line form runs; a Lua
  script or block for them fails with a clear message.
- **TriggerMerger does not convert layouts.** A classic `wtg` cannot be
  merged into a Reforged one, or the reverse. Like the real tool, it does not
  look for duplicate JASS functions in the merged headers.
- **PatchGenerator is not implemented.** It writes World Editor patches
  (UMSWE), which have no use without an installed editor; calling it fails
  with a clear message.
- **Generated vJass needs a second compile.** A script that writes a `.j`
  file for the map to `//! import` (LUA_FILE_HEADER's `writejass`) affects the
  next compilation, as in the editor, where the map has to be saved twice.
- **No real object-data base files.** Standard objects are those of the
  game data bundled with this package (Reforged); a field or object id that
  only exists in an older game version is reported as unknown.
- **`string.dump` produces a key, not bytecode.** It can be loaded back by
  `load` in the same interpreter, and nowhere else.

## Credits

Grim Extension Pack and its tools are by PitzerMike; JassHelper is by
Vexorian. This package reimplements their behaviour; it contains none of
their code.
