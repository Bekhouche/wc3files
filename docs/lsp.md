# Language server

`wc3files-lsp` is a language server for JASS and Lua map scripts. An editor
that speaks the Language Server Protocol starts it, sends it the files you
open, and shows what it finds as you type.

It comes with the package and needs nothing else: it is written on the
standard library and talks over stdin and stdout.

```bash
pip install wc3files
wc3files-lsp --help
```

## What it reports

The same as `wc3files jass --check`:

- `.j` and `.ai` files: syntax errors, with their line and column; calls to
  unknown functions, wrong argument counts and argument types, checked
  against the bundled `common.j` and `blizzard.j` and the file's own
  natives and functions; and handle leaks, as warnings;
- `.lua` files: syntax errors, undefined globals, unknown members of the Lua
  5.3 libraries, and native calls with the wrong number or kind of
  arguments.

- vJass and Zinc (`.vj` and `.zn` files, and `.j` files with a `library`,
  `scope`, `struct`, `module`, `interface` or `//! zinc` block): the file is
  compiled with the vJass compiler, and its compile error, its warnings, and
  the checks above on the compiled code are reported at the source line they
  come from. A finding in code the compiler generated, with no source line,
  is left out. `//! import` finds files next to the one you edit.

The language comes from the editor's language id (`jass`, `vjass`, `zinc`
or `lua`), or else from the file's extension. Each change is checked again: the largest
`war3map.j` of the first 120 corpus maps, 89,392 lines, takes 1.9 s.

## Hover

Hovering over a name shows its declaration and where it comes from: a
native or BJ function with its signature from the bundled `common.j` and
`blizzard.j`, a constant or `bj_` global of those files, or a type, global,
native, function, parameter or local of the file itself. It works while a
file has errors too, since the declarations are found from the file's
tokens, not from a parse of the whole file.

In JASS a name is looked up as JASS scopes it: a parameter or local of the
function you are in, then the file, then `common.j` and `blizzard.j`. In
Lua it is the nearest declaration above the cursor (`function`, `local`, or
a parameter), then the natives: Lua's block scopes are not followed.

## vJass

In vJass (`.vj` files, and `.j` files with vJass blocks), hover, go to
definition, completion, references, rename, signature help and the outline
know its libraries, scopes, structs, interfaces and modules, with their
methods and members, and the globals and functions of libraries and scopes,
`private` and `public` ones too.

A member or method is found after a `.`, from what comes before it: `this`,
`thistype`, `super` or a struct name; a local, parameter, global or member whose
type is a struct; a call that returns one (`Pool.create().add`); an array
element; or nothing (`.size`), which is the struct the line is in. A struct
has the members of the struct it extends, of the modules it implements and
of its delegates, and those the compiler makes (`create`, `allocate`,
`destroy`, `deallocate`, `getType`, `typeid`), which hover shows but which
have no declaration to go to and cannot be renamed. After a `.`, completion
offers the members and methods of that struct. The outline nests methods
and members in their struct, and structs, functions and globals in their
library or scope.

The members of functions and function interfaces (`.evaluate`, `.execute`,
`.name`) and of arrays and dynamic array types (`.size`, `create`) are not
found: on the 17 vJass samples of the tests, they are 27 of the 140 names
after a dot, and the other 113 are found. Zinc is checked but not indexed
this way: in a Zinc file, only its plain JASS names are found.

## Go to definition

Go to definition jumps to a name's declaration, found as hover finds it. A
native, BJ function or constant opens the bundled `common.j` or
`blizzard.j` of the installed package at its line.

## Completion

Completion offers the names that start with what you have typed: the
file's own (a parameter or local only in its function), then the natives,
BJ functions, constants and types of `common.j` and `blizzard.j`, then the
keywords. Each name comes with its declaration. Before anything is typed,
only the names of the function you are in and the keywords are offered; the
list holds at most 300 names, and the editor asks again as you type.

## Signature help

While a call is typed, after `(` and `,`, the editor shows the signature of
the function being called, with the argument the cursor is on highlighted:
a native or BJ function of `common.j` and `blizzard.j`, or a function or
native of the file. In a nested call it is the innermost one; commas in
strings, arrays and inner calls do not count. In Lua a call can run over
several lines, and the 30 lines before the cursor are read to find it; a
method call (`a:b(` or `a.b(`) gets none.

## References and highlights

Find references lists every use of the name under the cursor in the file,
resolved as hover resolves it: in JASS, a local `count` of one function is
not the local `count` of another, nor the global `count`. The editor also
highlights the uses of the name under the cursor, its declaration marked as
a write. A name after `.` or `:` is a field or method and is not a use, and
names in strings and comments are not uses either. For a native or BJ
function, the uses are those in the file: the bundled files are not
searched. In Lua, a key of a table constructor (`{x = 1}`) is still taken
as a use of `x`.

## Rename

Rename changes a name's declaration and every use found by references. In
JASS it changes the strings that name it too: a function given to
`ExecuteFunc("...")`, and a global given to `TriggerRegisterVariableEvent`,
since the game looks those up by name. The new name is refused when it is
not a name, is a keyword, or would change what another name refers to: a
local named as another name its function uses, a global or function named
as one of the file or of `common.j` and `blizzard.j`, or a global named as
a local of a function that uses it. In Lua, a name used anywhere in the file
is refused. Names declared in `common.j` and `blizzard.j` cannot be renamed.

## Formatting

Format document, and format selection, set the indentation of a JASS or
vJass file and nothing else: each line is indented by the blocks it is in
(`globals`, `function`, `if`, `loop`, and in vJass `library`, `scope`,
`struct`, `interface`, `module`, `method` and `static if`), with `else` and
`elseif` at the depth of their `if`, using the editor's indent size and
tabs or spaces; trailing spaces are removed. The text of each line, the
comments, the blank lines and the line endings are kept. The lines of a
`/* */` comment, and the bodies of `//! textmacro`, `//! zinc` and
`//! novjass` blocks, are kept as they are. A file that does not lex is left
as it is, and Lua files are not formatted.

The JASS printer is not used for this: it would reprint the code, but
without its comments and blank lines.

## Outline

The outline of a file (document symbols) lists its types, globals and
constants, natives and functions, each function with its parameters and
locals; in Lua, its functions.

## Neovim

Neovim (0.10 or later) has a client built in. Give `.j` files a file type
and start the server for it, for example in `init.lua`:

```lua
vim.filetype.add({ extension = { j = 'jass', ai = 'jass' } })

vim.api.nvim_create_autocmd('FileType', {
  pattern = 'jass',
  callback = function(args)
    vim.lsp.start({
      name = 'wc3files',
      cmd = { 'wc3files-lsp' },
      root_dir = vim.fs.root(args.buf, { '.git' }) or vim.fs.dirname(args.file),
    })
  end,
})
```

For a map's `war3map.lua`, add the same `vim.lsp.start` call for the `lua`
file type, limited to the files of your map: other Lua files have no
Warcraft III natives, so the server would report every call to them.

## VS Code

VS Code has no setting that starts a language server on its own; it needs
an extension that does. Any extension that runs a language server from a
command can run this one, with `wc3files-lsp` as the command and `jass` and
`lua` as the languages.

## Other editors

Any editor with a Language Server Protocol client can use it the same way:
the command is `wc3files-lsp`, over stdio (`--stdio` is accepted, as some
clients pass it).
