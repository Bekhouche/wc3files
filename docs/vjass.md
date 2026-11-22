# vJass and Zinc

`wc3files.vjass` is a vJass and Zinc compiler written in pure Python: it does
what JassHelper does when the World Editor saves a map, and turns vJass into
the plain JASS that Warcraft III runs. It is used to compile a map without the
editor, to build a map from source files in a pipeline, to check vJass on a
machine with no game installed, and to read the compiled script with the rest
of this package.

Output follows JassHelper's conventions: generated names, initialization
order and library order are the same, so code that names generated functions
(`ExecuteFunc("s__Foo_onInit")`) keeps working.

## Quick start

From the command line:

```bash
wc3files vjass war3map.j -o war3map.compiled.j      # one script
wc3files vjass lib/*.j main.j -o out.j              # several, in this order
wc3files vjass MyMap.w3x -o MyMap.compiled.w3x      # a whole map
wc3files vjass war3map.j --debug -O --check         # debug build, inlined, validated
```

From Python:

```python
from wc3files.vjass import VJassCompiler

compiler = VJassCompiler(optimize=True)
result = compiler.compile_file('war3map.j')
result.write('war3map.compiled.j')

for warning in result.warnings:
    print(warning)
```

`compile()` takes source text, `compile_file()` a path, `compile_files()`
several paths compiled as one script, and `compile_map()` a `.w3x`/`.w3m` (or a
folder map), whose `war3map.j` is compiled and written back. `ScriptFile` can
load vJass directly as well:

```python
from wc3files.scripts import ScriptFile

script = ScriptFile().read_vjass('war3map.j', optimize=True)
program = script.get_data()          # ir.Program of the compiled JASS
```

## How it works

Compilation runs in four stages:

1. **Preprocessing.** `//! import`, `//! novjass`, text macros, `debug` lines,
   and the split between vJass and `//! zinc` blocks. Zinc is translated to
   vJass at this point, the way JassHelper does it, so every later stage
   handles both languages.
2. **Declarations.** Libraries are ordered by their requirements, every name is
   declared with its scope prefix, and structs, interfaces and modules are
   built: members, inheritance, operators, type ids.
3. **Code generation.** Each function and method body is resolved against the
   types it uses. `x.y` can be a member, a static member, a method, a property
   operator or `.evaluate` on a function, and which one depends on the type of
   `x`.
4. **Runtime and layout.** Allocators, dispatch triggers, `.evaluate`
   prototypes, hooks and initialization are generated, calls that end up above
   their target are routed through a trigger, and the inliner runs if asked.

The JASS limits shape the generated code. A function can only call functions
declared above it, there are no pointers and no objects. Instances are
integers into parallel arrays (`s__Struct_member[this]`), recycled through a
free list. Anything that has to be called from above (a method declared
further down, a virtual method, `.evaluate`, a function interface value) goes
through `TriggerEvaluate` on a trigger whose condition is a small adapter
placed at the end of the script. A code value (`function f`) cannot go through
a trigger, so when `f` is declared further down, a copy of it is placed above
the first function that refers to it.

## What is supported

vJass:

- libraries (`requires`/`needs`/`uses`, `optional`, `initializer`,
  `library_once`), scopes and nested scopes, `public`/`private`, `keyword`,
  `SCOPE_PREFIX`/`SCOPE_PRIVATE`, globals anywhere, `key`;
- structs: members with defaults, static members, array members, methods,
  `create`/`destroy`/`onDestroy`/`onInit`, `allocate`/`deallocate`,
  `thistype`, `.typeid`, `.getType()`, implicit `this`, `extends array`,
  instance limits (`struct S[20000]`);
- inheritance (`extends`), `stub` methods, `super`, interfaces with
  `defaults`, interface `create` by type id, `.exists`;
- modules, `implement` and `implement optional`, module-private members and
  module `onInit`;
- operators: `[]`, `[]=`, `<`, `==`, property get/set (`x`, `x=`), and the
  static forms;
- `delegate` members;
- function interfaces, function pointers, `.evaluate`, `.execute`, `.name` on
  functions and methods;
- dynamic arrays (`type T extends integer array [size, storage]`), sized and
  2D global arrays, and arrays larger than one JASS array (split over several,
  with accessor functions);
- `static if` (constants, `LIBRARY_x`, `DEBUG_MODE`, `.exists`), `debug`,
  `hook`, `//! inject main`/`config` with `//! dovjassinit`, `//! loaddata`;
- the inliner (`optimize=True`, `-O`).

Zinc: libraries, the private-by-default rule, structs, modules, interfaces,
`type` definitions (dynamic arrays and function interfaces), anonymous
functions and methods, `for` ranges and C-style `for`, `while`, `do ... while`,
`break`, `debug { }`, `static if`, compound assignment and the `x:member`
syntax. Zinc and vJass mix freely: a Zinc struct can implement a vJass module
and the other way round.

`//! external` commands and `//! externalblock` blocks (ObjectMerger and the
other editor tools) are collected in `result.externals`, and
[`wc3files.grimex`](grimex.md) runs them: `result.run_externals(map_files)`,
`compile_map(..., externals=True)` or `--externals check|run` on the command
line.

## Options

Every option is a keyword argument of `VJassCompiler` (and a field of
`CompileOptions`); the command line has a flag for each.

| Option | Flag | Effect |
|---|---|---|
| `debug` | `--debug` | Keep `debug` code, set `DEBUG_MODE` to true, report bad allocations and double frees in game |
| `optimize` | `-O` | Inline one-line functions |
| `max_array_size` | `--max-array-size` | 8191 by default, as JassHelper; 32768 for game 1.29 and later |
| `lookup_folders` | `-I` | Folders searched by `//! import` |
| `common_j`, `blizzard_j` | `--common`, `--blizzard` | The game API to compile against; the bundled copies by default |
| `strict` | `--strict` | Undeclared names and calls above a declaration are errors, not warnings |
| `auto_method_evaluate` | `--force-method-evaluate` | False makes a method called above its declaration an error |
| `implicit_this` | `--no-implicit-this` | Bare member names inside methods mean `this.member` |
| `constant_overrides` | `-D NAME=false` | Force a constant for `static if` |

`VJassCompiler(conf='jasshelper.conf')` reads JassHelper's own configuration
file: `[lookupfolders]`, `[forcemethodevaluate]` and `[noimplicitthis]`.

By default an undeclared name is left in the output as written, with a warning,
because JassHelper does the same and a script compiled in parts (custom code
without the editor's generated globals) relies on it. `--check`, or
`result.validate()`, runs the JASS checker over the output. A finding is reported at the vJass
line its code came from (`result.source_map`, `result.source_location(line)`),
or at the compiled line for code the compiler generated itself.

## Differences from JassHelper

Two Zinc constructs are compiled to what the source means rather than to what
JassHelper writes:

- compound assignment keeps its right side together: `x *= a + b` is
  `set x = x * (a + b)`, where JassHelper writes `x*a+b`;
- `!` parenthesizes its operand, so `!a == b` keeps Zinc's meaning,
  `(not a) == b`.

Private names always use two underscores (`Lib__name`). JassHelper picks two
or three at random on purpose, so that code cannot rely on the exact name.

The inliner is more conservative than JassHelper's. It inlines only when the
argument order, the side effects and the integer-to-real conversions stay the
same, so it inlines somewhat less, never something it should not.

JassHelper's return fixer and shadow helper are not implemented. They work
around bugs of game versions older than 1.24, which Reforged does not have.

## Verification

The compiler is checked against the JassHelper that ships with the game, in
three ways:

1. **Behaviour.** Fifteen feature programs (`tests/samples/vjass/`) were
   compiled by JassHelper, release and debug, and run in
   `wc3files.scripts.jass_vm`. The output of this compiler has to print exactly
   the same, release and debug, with and without the inliner. This is what
   `tests/test_vjass.py` runs.
2. **Library corpus.** Every `.j` file of
   [nestharus/JASS](https://github.com/nestharus/JASS) was compiled together
   with the libraries it requires, by both compilers. Of the 135 files that
   JassHelper compiles, all 135 compile here too, and pjass reports the same
   errors on both outputs.
3. **Real maps.** The custom vJass code of 75 maps from the benchmark corpus
   (their enabled custom-text triggers), and the 60 script files of a
   Better Triggers project, compile with the same pjass result as JassHelper's
   output.

## The JASS interpreter

`wc3files.scripts.jass_vm` is the small JASS interpreter the tests run on. It
executes a program together with Blizzard.j, with the natives that make
triggers, `ExecuteFunc`, hashtables, timers and text output work, and collects
what `BJDebugMsg` and `DisplayTimedTextToPlayer` show:

```python
from wc3files.scripts import run_script

vm = run_script(result.jass, entry='main')
print(vm.output)
```

It is not the game: there are no units, no waits, and a timer fires once,
immediately. A native it does not implement raises `JassRuntimeError`.

## Limitations

- `//! external` tools run only when asked (`--externals`), and only the four
  Grim Extension Pack tools are implemented; see [External tools and
  Lua](grimex.md).
- Compiling a map compiles the `war3map.j` it contains. The World Editor
  builds that file from the GUI triggers when it saves, so a map saved with
  JassHelper enabled already holds compiled JASS; recompiling it changes
  nothing.
- Errors stop at the first one, as JassHelper's do.

## Credits

JassHelper, vJass and Zinc are by Victor Hugo Soliz Kuncar (Vexorian), released
under the zlib license. This package reimplements their documented behaviour
and output conventions; it contains no JassHelper code.
