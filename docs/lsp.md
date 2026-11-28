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

The language comes from the editor's language id (`jass` or `lua`), or
else from the file's extension. Each change is checked again: the largest
`war3map.j` of the first 120 corpus maps, 89,392 lines, takes 1.9 s.

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
