# MCP server

`wc3files-mcp` serves wc3files to an AI assistant over the Model Context
Protocol (MCP). An assistant such as Claude can then open your maps, models
and replays and answer questions from their parsed contents: which units a
map adds, what an ability costs, what a trigger does, whether the script
calls a function the game does not have.

By default every tool only reads. Started with `--allow-write`, the server
also edits maps, writing each result as a new map (see "Editing maps"). It
opens files under the folders you give it and nothing else, and it refuses
a protected map, as the command line does.

## Install

```bash
pip install "wc3files[mcp]"
```

The extra adds the official `mcp` package (2.3 or newer, under 3), which
needs Python 3.10. `import wc3files` itself still needs only loguru.

## Run

```bash
wc3files-mcp --root D:/Maps
wc3files-mcp --root D:/Maps --root "C:/Users/me/Documents/Warcraft III/BattleNet"
```

The server talks over stdin and stdout, so it is started by the client, not
by hand. `--root` can be given more than once; a relative path in a tool
call is taken from the first root. `python -m wc3files.mcp` is the same
command.

### Claude Code

```bash
claude mcp add wc3files -- wc3files-mcp --root D:/Maps
```

### Claude Desktop

In `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "wc3files": {
      "command": "wc3files-mcp",
      "args": ["--root", "D:/Maps"]
    }
  }
}
```

If `wc3files-mcp` is not on the client's `PATH`, give the full path to it,
or use `"command": "python"` with `"args": ["-m", "wc3files.mcp", "--root", "D:/Maps"]`.

### Without installing it first

With [uv](https://docs.astral.sh/uv/), a client can start the server
without a separate `pip install`:

```bash
uvx --from "wc3files[mcp]" wc3files-mcp --root D:/Maps
```

### Docker

The `Dockerfile` at the top of the repository builds an image that runs the
server on `/data`. Mount the folder of maps there:

```bash
docker build -t wc3files-mcp .
docker run -i --rm -v D:/Maps:/data:ro wc3files-mcp
```

For a client, the command is `docker` with the arguments
`["run", "-i", "--rm", "-v", "D:/Maps:/data:ro", "wc3files-mcp"]`.

### MCP Registry

The server is described for the official MCP Registry
(registry.modelcontextprotocol.io) as `io.github.Bekhouche/wc3files`, by
`server.json` at the top of the repository. The registry checks the line
`mcp-name: io.github.Bekhouche/wc3files` in the README, which is the PyPI
description, to confirm that the package belongs to the publisher. A new
release is published there with
[mcp-publisher](https://github.com/modelcontextprotocol/registry), after it
is on PyPI:

```bash
mcp-publisher login github
mcp-publisher publish
```

## Tools

| Tool | What it returns |
|---|---|
| `list_files(folder, pattern)` | Maps, campaigns, models and replays under the roots, with their sizes (at most 1000) |
| `map_info(path)` | Name, author, description, size, script language, game version, players and forces, from `war3map.w3i` with the map's strings resolved |
| `list_members(path)` | The files inside a map, with unpacked and stored sizes, read from the archive tables without unpacking anything |
| `read_member(path, member)` | One file inside a map: the data `wc3files dump` gives for its format, the text of a script or text file, or only its size |
| `object_data(path, object_type, ids)` | The objects a map changes or adds (units, items, destructables, doodads, abilities, buffs, upgrades), with readable field names, for the whole map or one object type or some ids |
| `trigger_text(path, name, names_only)` | GUI triggers as the World Editor shows them; `names_only` lists the trigger names first, which is the way to start on a large map |
| `check_script(path)` | JASS or Lua findings for the map's script, as `wc3files jass --check` reports them |
| `model_info(path, member)` | A model's name, format, version, geoset count, sequences with their start and end frames, and textures with their replaceable ids; `member` names a model inside a map |
| `replay_info(path)` | A replay's game name, map, length, version and players, and its W3MMD winners and losers when it records them |
| `map_overview(path)` | What a map holds in one call: info, script, terrain size and tileset, and the number of placed units, doodads, regions, cameras, sounds, imports, strings, triggers and custom objects |
| `placed_objects(path, kind, id, owner, limit)` | The units or doodads placed on a map, with type, position, facing in degrees, scale, owner and editor numbers, filtered by type or owner |
| `map_records(path, kind, max_chars)` | A map's regions, cameras, sounds, imports or strings as records, or a terrain summary: tilesets, size, cliff levels and ground texture use |

Every tool is declared read-only, not destructive, idempotent and closed-world
in its MCP annotations, and every parameter has a description in the tool's
input schema, so a client can tell what a call does before making it.
Each tool also publishes an output schema, the TypedDicts in
`wc3files.mcp.results`, which gives every field of its result.

A result longer than `max_chars` characters (200,000 by default) is refused
with a message that says how to ask for less, so a whole map's terrain or
trigger text does not fill the assistant's context by accident.

Bytes come back as `{"base64": ...}`, and a NaN or infinite float as the
string `"nan"`, `"inf"` or `"-inf"`, so every result is plain JSON.

## Editing maps

```bash
wc3files-mcp --root D:/Maps --allow-write
```

Without `--allow-write` the edit tools are not registered at all, so a
client cannot call them. With it:

- every edit writes a new map, `<name>.edited.w3x` beside the source,
  unless `out` names another path under the roots. The source changes only
  when `out` is the source itself;
- the new map is read back and the changed files are compared with what
  was written before the tool reports success; every other file of the map
  is copied unchanged;
- a protected map is refused, and so is any change that cannot be applied
  exactly, before anything is written;
- the edit tools are annotated as writing and destructive (since `out` may
  be the source), and every reading tool stays read-only.

Close the map in the World Editor before editing it, and open the written
map to see the changes.

| Tool | What it does |
|---|---|
| `edit_objects(path, object_type, changes, out)` | Creates custom units, items, abilities and the other objects from a standard one (`{"id": "h000", "base": "hfoo"}`), changes fields of new or standard objects, takes fields back to their default (`reset`) or deletes objects. Fields are named as `object_data` shows them (`goldcost`, `Name`) or by their four-character id (`ugol`); an ability field with levels takes `{"1": 7, "2": 9}`. Values are checked against each field's type; a value past the World Editor's limits is written and reported in `warnings`, since real maps go past them on purpose |
| `edit_placed(path, kind, add, change, delete, out)` | Places, moves, changes or deletes units (war3mapUnits.doo) or doodads (war3map.doo). `change` and `delete` pick records by `index`, type `id`, `creation_number` (units) or `editor_id` (doodads), with indices of the file before the call; `add` needs `id`, `x` and `y`. A type id must be standard or defined by the map, and a position must be on the map. New records get the next free editor number |
| `edit_records(path, kind, add, change, delete, out)` | Adds, changes or deletes regions or cameras, and adds or changes strings. A region or camera that the script or GUI triggers use (`gg_rct_...`, `gg_cam_...`) cannot be deleted or renamed, names stay unique, and regions must lie on the map. A new camera starts as the game's default camera (the `bj_CAMERA_DEFAULT_*` values of blizzard.j). A new string gets the next id and its `TRIGSTR_` reference is returned; strings are never deleted. Rewriting war3map.wts keeps every string but drops its comment lines |
| `edit_script(path, code, init_function, replace, out)` | Adds JASS or Lua code to the map script, before `main`, and with `init_function` a call to it at the top of `main` (after its `local` lines in JASS). A JASS `globals` block in the code is merged into the script's own. With `replace` the code replaces the script and needs its own `main` and `config`. The edited script is parsed and checked as `check_script` does, and refused if it has an issue the original did not have. The World Editor rebuilds the script from the triggers when it saves the map, which drops code added here |
| `import_file(path, member, source, out)` | Adds a file to the map, or replaces one, and lists it in war3map.imp so the World Editor keeps it. A new entry takes the flags the map's other entries use most, or 13, which 42,786 of the 48,487 entries in the corpus maps use. Map data files and the script are refused |

```json
{"path": "MyMap.w3x", "object_type": "w3u", "changes": [
  {"id": "h000", "base": "hfoo", "set": {"Name": "Knight of the Hill", "goldcost": 175, "HP": 600}},
  {"id": "hfoo", "set": {"goldcost": 140}}
]}
```

## Use without MCP

The tools are plain functions in `wc3files.mcp.tools`, and work without the
`mcp` package:

```python
from wc3files.mcp import tools

roots = tools.Roots(['D:/Maps'])
print(tools.map_info(roots, 'MyMap.w3x')['name'])
```
