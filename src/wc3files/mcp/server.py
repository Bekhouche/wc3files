"""The wc3files MCP server (#179): the tools of wc3files.mcp.tools, served
over stdio with the official `mcp` package (`pip install wc3files[mcp]`).

    wc3files-mcp --root D:/Maps --root D:/Replays
    wc3files-mcp --root D:/Maps --allow-write

Without --allow-write every tool only reads. With it, the edit tools of
wc3files.mcp.edits are registered too (#272); they write a new map.
"""
import argparse
import functools
import json
import sys

from wc3files.version import __version__
from . import tools as _tools

INSTRUCTIONS = (
    'Reads Warcraft III maps (.w3x, .w3m), models (.mdx, .mdl) and replays '
    '(.w3g) with wc3files. list_files finds files; map_overview says what a '
    'map holds. placed_objects, map_records, object_data, trigger_text and '
    'check_script answer questions about placed units, regions, objects, '
    'triggers and scripts; compare_maps says what changed between two '
    'versions; map_image shows a map; game_files, game_file and game_image read '
    'the installed game; replay_actions lists what players did; '
    'missing_files finds missing textures and unused imports; map_translation '
    'lists the strings to translate; '
    'compile_script compiles and checks vJass, Zinc, '
    'cJass and Luck code, and run_script runs a map script without the game; '
    'read_member gives any member as data. Every tool '
    'only reads. Files outside '
    'the folders the server was started with are refused, and so are '
    'protected maps.')
WRITE_INSTRUCTIONS = (
    ' This server was started with --allow-write: the edit_ tools change a '
    'map and write the result as a new map, <name>.edited.w3x beside it '
    'unless out names another path; the source changes only when out is the '
    'source itself. Close the map in the World Editor first, and reopen the '
    'written map to see the changes.')


def _missing_mcp():
    return SystemExit(
        'wc3files-mcp needs the mcp package: pip install "wc3files[mcp]"')


# Said once here and used by every tool, so a client sees the same rules on
# each parameter of the same kind (#265).
_PATH = ('A path under the folders the server was started with (--root). A '
         'relative path is taken from the first of them; list_files gives '
         'full paths that can be passed as they are.')
_MAP = 'The .w3x or .w3m map. ' + _PATH
_MAX_CHARS = ('The longest result, in characters of JSON, before the call is '
              f'refused with a hint on how to ask for less. Default {_tools.DEFAULT_MAX_CHARS}.')
_READ_ONLY = ('Only reads: it changes no file, and the same call on the same '
              'file gives the same result.')
_WRITES = ('Writes a new map, <name>.edited.w3x beside the source unless out '
           'names another path under the roots, and reads it back to check it; '
           'the source changes only when out is the source. A protected map is '
           'refused. The result names the map written.')
_OUT = ('The map to write, under the roots. Empty writes <name>.edited.w3x '
        'beside the source; giving the source itself overwrites it.')


def build_server(folders, allow_write=False, game=None):
    """An MCPServer whose tools read files under `folders` only; with
    `allow_write`, also the edit tools, which write maps under them. `game`
    is the installed game's folder, which the game_ tools read."""
    try:
        from typing import Annotated

        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.server.mcpserver.utilities.types import Image
        from mcp.types import TextContent, ToolAnnotations
        from pydantic import Field

        from . import edits as _edits
        from . import results as R
    except ImportError:
        raise _missing_mcp() from None
    roots = _tools.Roots(folders)
    installed = _tools.Game(game)
    server = MCPServer('wc3files', version=__version__,
                       instructions=INSTRUCTIONS + (WRITE_INSTRUCTIONS if allow_write else ''))

    def tool(title, writes=False):
        """Register a tool. Every tool is closed-world: it touches files
        under the roots and nothing else. A reading tool is read-only and
        its description ends with _READ_ONLY; an edit tool is neither
        read-only nor idempotent, is destructive since out may name the
        source, and its description ends with _WRITES. A failure the tools
        expect (bad input, which is a ValueError in wc3files, a protected
        map, a missing file or a path outside the roots) reaches the client
        with its message; the SDK would hide the message of any other
        exception."""
        def wrap(function):
            @functools.wraps(function)
            def run(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (ValueError, OSError) as exc:
                    raise ToolError(str(exc)) from exc
            description = (' '.join(function.__doc__.split()) + ' '
                           + (_WRITES if writes else _READ_ONLY))
            return server.tool(title=title, description=description,
                               annotations=ToolAnnotations(
                                   title=title, readOnlyHint=not writes,
                                   destructiveHint=writes, idempotentHint=not writes,
                                   openWorldHint=False))(run)
        return wrap

    MapPath = Annotated[str, Field(description=_MAP)]
    MaxChars = Annotated[int, Field(description=_MAX_CHARS, ge=1)]

    @tool('List Warcraft III files')
    def list_files(
            folder: Annotated[str, Field(description=(
                'The folder to search, with its subfolders. Empty searches every '
                'folder the server may read. ' + _PATH))] = '',
            pattern: Annotated[str, Field(description=(
                'Keep only the files whose name contains this text, ignoring case, '
                'such as "dota" or ".w3g". Empty keeps all.'))] = '') -> R.ListFilesResult:
        """Find the maps (.w3x, .w3m), campaigns (.w3n), models (.mdx, .mdl)
        and replays (.w3g) the server may read, with full paths the other
        tools accept as they are. Use it when the user names a file but not
        its folder. It lists at most 1000 files; narrow the search with
        folder or pattern."""
        return _tools.list_files(roots, folder, pattern)

    @tool('Map information')
    def map_info(path: MapPath) -> R.MapInfoResult:
        """Read a map's summary from its war3map.w3i, with the map's
        strings resolved: name, author, players and forces, size, script
        language and the game version it was saved with. Use it to answer
        what a map is; use read_member on war3map.w3i only for every raw
        field. Fails on a protected map."""
        return _tools.map_info(roots, path)

    @tool('List the files inside a map')
    def list_members(path: MapPath) -> R.ListMembersResult:
        """List the files a map holds (war3map.j, war3map.w3u, imported
        models and textures, ...) with their sizes, from the archive tables
        without unpacking anything. Use it to find the member names that
        read_member and model_info take. Fails on a protected map."""
        return _tools.list_members(roots, path)

    @tool('Read a file inside a map')
    def read_member(
            path: MapPath,
            member: Annotated[str, Field(description=(
                'The file inside the map, as list_members names it, such as '
                'war3map.w3e, war3map.j or war3mapImported\\Hero.mdx.'))],
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.ReadMemberResult:
        """Read one file inside a map: parsed data for a format wc3files
        reads (war3map.w3i, w3e, doo, w3r, w3c, w3s, wts, wtg, wct and
        more), the text of a script or text file, or else only its size.
        Prefer the focused tools when they fit: map_info for war3map.w3i,
        object_data for the object files, trigger_text for war3map.wtg,
        check_script for the script and model_info for a model. A large
        result, such as a whole terrain, is refused past max_chars."""
        return _tools.read_member(roots, path, member, max_chars)

    @tool('Object data')
    def object_data(
            path: Annotated[str, Field(description=(
                'A .w3x or .w3m map, or a single object file (.w3u, .w3t, .w3b, '
                '.w3d, .w3a, .w3h or .w3q). ' + _PATH))],
            object_type: Annotated[R.ObjectType, Field(description=(
                'Keep one kind of object: w3u units, w3t items, w3b destructables, '
                'w3d doodads, w3a abilities, w3h buffs, w3q upgrades. Empty keeps '
                'every kind.'))] = '',
            ids: Annotated[list[str] | None, Field(description=(
                'Keep only these four-character object ids, such as hfoo (Footman) '
                'or A000 (a custom ability). Empty keeps every object.'))] = None,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.ObjectDataResult:
        """Read the units, items, destructables, doodads, abilities, buffs
        and upgrades a map adds or changes, with the map's strings resolved.
        Use it to answer what a unit costs or what an ability does in a map.
        Only the fields the map changes are given, not the game's defaults.
        On a large map, ask for one object_type or some ids."""
        return _tools.object_data(roots, path, object_type, ids, max_chars)

    @tool('Trigger text')
    def trigger_text(
            path: Annotated[str, Field(description=(
                'A .w3x or .w3m map, or a war3map.wtg file. ' + _PATH))],
            name: Annotated[str, Field(description=(
                'Keep only the triggers whose name contains this text, ignoring '
                'case. Empty keeps all.'))] = '',
            names_only: Annotated[bool, Field(description=(
                'Return only the trigger names. Use it first on a large map, then '
                'ask for single triggers by name.'))] = False,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.TriggerTextResult:
        """Read a map's GUI triggers as the World Editor shows them, one
        line per event, condition and action. Use it to explain what a map's
        triggers do. A large map's triggers run to hundreds of thousands of
        characters: ask for names_only, then one name. Triggers written as
        custom text are in the script, which read_member on war3map.j or
        war3map.lua gives."""
        return _tools.trigger_text(roots, path, name, names_only, max_chars)

    @tool('Check a script')
    def check_script(
            path: Annotated[str, Field(description=(
                'A .w3x or .w3m map, whose war3map.j or war3map.lua is checked, or '
                'a .j or .lua script file. ' + _PATH))],
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.CheckScriptResult:
        """Check a map's JASS or Lua script against the game's natives and
        Blizzard functions, as `wc3files jass --check` does: calls to
        unknown functions, wrong argument counts, arguments of the wrong
        type and similar errors. Use it to find why a map's script may fail
        to load; read_member on war3map.j gives the script text itself."""
        return _tools.check_script(roots, path, max_chars)

    @tool('Compile a script')
    def compile_script(
            code: Annotated[str, Field(description=(
                'Source code to compile. Give code or path, not both.'))] = '',
            path: Annotated[str, Field(description=(
                'A source file (.j, .vj, .zn, .cj, .luck), a folder of Luck sources '
                'compiled as one program, or a .w3x or .w3m map whose war3map.j is '
                'compiled. ' + _PATH))] = '',
            language: Annotated[R.CompileLanguage, Field(description=(
                'vjass (also plain JASS with vJass blocks), zinc, cjass, luck, or jass '
                'to only check JASS. Empty: from the file extension, luck for a folder, '
                'vjass otherwise.'))] = '',
            include_jass: Annotated[bool, Field(description=(
                'Return the compiled JASS too. False returns only errors and findings, '
                'for a large script.'))] = True,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.CompileScriptResult:
        """Compile vJass, Zinc, cJass or Luck to plain JASS, as the wc3files
        vjass, cjass and luck commands do, and check the result against the
        game's natives as check_script does, leaks included. Use it to see
        whether code written for a map compiles and calls the game
        correctly before it goes into the map; edit_script then adds it. A
        compile error is returned in errors, with its line."""
        return _tools.compile_script(roots, code, path, language, include_jass, max_chars)

    @tool('Picture of a map')
    def map_image(
            path: MapPath,
            kind: Annotated[R.ImageKind, Field(description=(
                'minimap (war3mapMap.blp, the map seen from above) or preview (the '
                'lobby picture, war3mapPreview.tga, else the minimap).'))] = 'minimap',
            render: Annotated[bool, Field(description=(
                "Draw the minimap from the terrain even when the map has its own: "
                'ground in grey, water in blue, trees and doodads as dots.'))] = False,
    ) -> list[TextContent | Image]:
        """Show a map as a PNG image: its minimap or its lobby preview, as
        the map has them, or a minimap drawn from the terrain when the map
        has none. Use it to see the layout of a map: lanes, bases, water
        and forests. A text part says where the picture came from. Fails on
        a protected map."""
        found = _tools.map_image(roots, path, kind, render, installed)
        about = {'path': path, 'kind': kind, 'source': found['source'],
                 'width': found['width'], 'height': found['height']}
        return [TextContent(type='text', text=json.dumps(about)),
                Image(data=found['png'], format='png')]

    @tool('Find files of the game')
    def game_files(
            pattern: Annotated[str, Field(description=(
                'Text the path contains, ignoring case, such as "UnitData" or "Footman", '
                'or a pattern with * and ?, such as "*Abilit*.slk".'))],
            limit: Annotated[int, Field(description='At most this many files. Default 200.',
                                        ge=1, le=1000)] = _tools.GAME_SEARCH_LIMIT,
    ) -> R.GameFilesResult:
        """Find files of the installed Warcraft III by path, with their
        sizes: the game's tables (Units/UnitData.slk, AbilityData.slk),
        texts, models and textures, whose paths look like
        War3.w3mod:Units/UnitData.slk. Use it to find what game_file and
        game_image read, when a question needs the game's own data rather
        than a map's. Needs the server started with --game."""
        return _tools.game_files(installed, pattern, limit)

    @tool('Read a file of the game')
    def game_file(
            path: Annotated[str, Field(description=(
                'The path as game_files gives it, such as War3.w3mod:Units/UnitData.slk.'))],
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.GameFileResult:
        """Read one file of the installed game: a table or object file as
        data (as read_member gives a map's), a model's name, sequences and
        textures, or a text file as text. Use it for the game's base values
        of units, abilities and items, or to see a game model's animations.
        Images go to game_image. Needs the server started with --game."""
        return _tools.game_file(installed, path, max_chars)

    @tool('Show a texture of the game')
    def game_image(
            path: Annotated[str, Field(description=(
                'A .blp, .dds or .tga path as game_files gives it, such as '
                'War3.w3mod:ReplaceableTextures/CommandButtons/BTNFootman.blp.'))],
    ) -> list[TextContent | Image]:
        """Show a texture of the installed game, such as an icon or a
        model's skin, as a PNG image. Use it to see what an icon or texture
        path a map uses looks like. Needs the server started with --game."""
        found = _tools.game_image(installed, path)
        about = {'path': path, 'width': found['width'], 'height': found['height']}
        return [TextContent(type='text', text=json.dumps(about)),
                Image(data=found['png'], format='png')]

    @tool('Replay actions')
    def replay_actions(
            path: Annotated[str, Field(description='A .w3g replay. ' + _PATH)],
            player: Annotated[int | None, Field(description=(
                'Keep the actions of this player id (from 1, as players lists them).'))] = None,
            action: Annotated[str, Field(description=(
                'Keep one kind of action, by name as by_action gives it, such as '
                '"Unit/building ability (no target)", or by id, such as 0x10.'))] = '',
            rawcode: Annotated[str, Field(description=(
                'Keep actions naming this object id, such as hfoo or AHbz.'))] = '',
            limit: Annotated[int, Field(description='Return at most this many actions.',
                                        ge=1, le=2000)] = _tools.ACTIONS_LIMIT,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.ReplayActionsResult:
        """Count and list the actions in a replay, as wc3files
        replay-actions does: actions and APM per player, the most frequent
        kinds of action, and with a filter (player, action or object id)
        the matching actions with their game time. Also the stats the map
        recorded with W3MMD (winners, losers, variables), when it did. Use
        replay_info for the game and its players; this tool for what they
        did."""
        return _tools.replay_actions(roots, path, player, action, rawcode, limit, max_chars)

    @tool('Missing and unused files')
    def missing_files(
            path: MapPath,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.MissingFilesResult:
        """Find the textures a map's models use that the map does not have
        (nor the installed game, when the server has --game), which show as
        green or black in game, and the imported files nothing in the map
        names (no model, object data, script, trigger or other file), which
        only make it larger. A model's _Portrait counts as used with the
        model. Use it when a model looks wrong or a map is too large. A map
        with thousands of imports takes a minute or more."""
        return _tools.missing_files(roots, path, installed, max_chars)

    @tool('Strings of a map to translate')
    def map_translation(
            path: MapPath,
            keys: Annotated[list[str], Field(description=(
                'Keep the keys that start with one of these, such as "w3u/" for units or '
                '"script/" for trigger text. Empty keeps all.'))] = [],
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.MapTranslationResult:
        """List the text of a map under keys that stay the same between
        versions: object names and tooltips, map info, and the strings of
        triggers and the script. Use it to translate a map: translate the
        texts, keep the keys and the markup (|cffffcc00, |r, |n,
        <A000,DataA1>), and give them to apply_translation, which the
        server has with --allow-write."""
        return _tools.map_translation(roots, path, keys, max_chars)

    @tool('Compare two versions of a map')
    def compare_maps(
            old: Annotated[str, Field(description='The older .w3x or .w3m map. ' + _PATH)],
            new: Annotated[str, Field(description='The newer .w3x or .w3m map. ' + _PATH)],
            part: Annotated[R.ComparePart, Field(description=(
                'all, or one part: members (files added, removed, changed), objects '
                '(custom and changed objects, with the fields that changed) or strings '
                '(names, tooltips, map info and script text by stable key).'))] = 'all',
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.CompareMapsResult:
        """Compare two versions of a map: the files added, removed and
        changed, the objects added, removed and changed with each field's
        old and new value, and the strings by keys that do not depend on
        TRIGSTR numbers, as wc3files map-translate diff gives them. Use it
        to answer what changed in a new version. The summary has the
        counts; for a large map, ask for one part. Fails on a protected
        map."""
        return _tools.compare_maps(roots, old, new, part, max_chars)

    @tool('Run a map script')
    def run_script(
            path: Annotated[str, Field(description=(
                'A .w3x or .w3m map, whose war3map.lua or war3map.j is run, or a .lua '
                'or .j script file. ' + _PATH))],
            seconds: Annotated[float, Field(description=(
                'Game time to let pass after main, with its timers and triggers, up to '
                '600. Default 10.'), ge=0, le=_tools.RUN_MAX_SECONDS)] = 10.0,
            players: Annotated[int, Field(description=(
                'Player slots in use, as users who are playing. Default 1.'),
                ge=1, le=24)] = 1,
            max_steps: Annotated[int, Field(description=(
                'Stop after this many interpreter steps, so a script that never ends '
                f'returns. At most {_tools.RUN_MAX_STEPS}.'), ge=1,
                le=_tools.RUN_MAX_STEPS)] = _tools.RUN_MAX_STEPS,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.RunScriptResult:
        """Run a map's script without the game, against stubs of the game
        API, as wc3files run-map does: config and main, then some seconds of
        game time. Use it to find why a map fails when it loads or in its
        first seconds (a nil call, a missing function, a bad argument) and
        to see the messages it displays. A JASS script is translated to Lua
        first. Units, sounds and the screen do not exist: what depends on
        them is not tested."""
        return _tools.run_script(roots, path, seconds, players, max_steps, max_chars)

    @tool('Model information')
    def model_info(
            path: Annotated[str, Field(description=(
                'A .mdx or .mdl model file, or a .w3x or .w3m map that holds the '
                'model. ' + _PATH))],
            member: Annotated[str, Field(description=(
                'For a map, the model inside it, as list_members names it, such as '
                'war3mapImported\\Hero.mdx. Leave it empty for a model file.'))] = '') -> R.ModelInfoResult:
        """Read a model's summary: its animation sequences with their
        frames, the textures it uses, its format, version and geoset count.
        Use it to check which animations or textures a model has, in a
        model file or in a model imported into a map; for a map, member is
        required."""
        return _tools.model_info(roots, path, member)

    @tool('Replay information')
    def replay_info(
            path: Annotated[str, Field(description='The .w3g replay. ' + _PATH)],
            chat: Annotated[bool, Field(description=(
                'Include the chat. A long game has hundreds of messages; false leaves '
                'them out.'))] = True,
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.ReplayInfoResult:
        """Read what happened in a replay: game name, map, host and length,
        the players with team, colour, race and handicap, who left and when,
        the chat with timestamps, and the W3MMD winners and losers when the
        map records them. Use it to answer who played, who won, who left
        early or what was said; leave result codes are raw, so winners come
        from w3mmd."""
        return _tools.replay_info(roots, path, chat, max_chars)

    @tool('Map overview')
    def map_overview(path: MapPath) -> R.MapOverviewResult:
        """Say in one call what a map holds: its name, author, players,
        script, terrain size and tileset, and how many units, doodads,
        regions, cameras, sounds, imports, strings, triggers and custom
        objects it has. Use it to size up a map before asking for any of
        these with placed_objects, map_records, object_data or
        trigger_text. Fails on a protected map."""
        return _tools.map_overview(roots, path)

    @tool('Placed units and doodads')
    def placed_objects(
            path: MapPath,
            kind: Annotated[R.PlacedKind, Field(description=(
                'units for the units and items placed on the map (war3mapUnits.doo), '
                'doodads for the doodads and destructables (war3map.doo).'))] = 'units',
            id: Annotated[str, Field(description=(
                'Keep only objects of this four-character type id, such as hfoo or '
                'LTlt; case matters. Empty keeps all.'))] = '',
            owner: Annotated[int | None, Field(description=(
                'Units only: keep the units of this player number, counted from 0, '
                'as the owner field gives it. The neutral players come after the '
                'last playable slot.'), ge=0)] = None,
            limit: Annotated[int, Field(description=(
                f'Most objects to return; total says how many match. Default '
                f'{_tools.DEFAULT_LIMIT}.'), ge=1)] = _tools.DEFAULT_LIMIT) -> R.PlacedObjectsResult:
        """List the units or doodads placed on a map, in file order, with
        type, position, facing, scale and owner, and the editor numbers
        the World Editor uses for them in triggers. Use it to answer where
        things are or what a player starts with; object_data gives what a
        type of unit is, not where it stands."""
        return _tools.placed_objects(roots, path, kind, id, owner, limit)

    @tool('Map records')
    def map_records(
            path: MapPath,
            kind: Annotated[R.RecordKind, Field(description=(
                'regions (war3map.w3r), cameras (war3map.w3c), sounds (war3map.w3s), '
                'imports (war3map.imp), strings (war3map.wts) or terrain (a summary '
                'of war3map.w3e).'))],
            max_chars: MaxChars = _tools.DEFAULT_MAX_CHARS) -> R.MapRecordsResult:
        """Read one kind of map record: the regions with their bounds, the
        cameras, the sound definitions, the imported files, the strings
        the map's text refers to (TRIGSTR ids), or a terrain summary with
        its tilesets, size, cliff levels and how much of each ground
        texture is used. Use read_member for the full terrain grid."""
        return _tools.map_records(roots, path, kind, max_chars)

    if not allow_write:
        return server

    Out = Annotated[str, Field(description=_OUT)]

    @tool('Edit object data', writes=True)
    def edit_objects(
            path: MapPath,
            object_type: Annotated[R.EditObjectType, Field(description=(
                'The object file to edit: w3u units, w3t items, w3b destructables, '
                'w3d doodads, w3a abilities, w3h buffs, w3q upgrades.'))],
            changes: Annotated[list[R.ObjectChange], Field(description=(
                'The changes, applied in order. All are checked before anything is '
                'written; one that cannot be applied refuses the whole call.'),
                min_length=1)],
            out: Out = '') -> R.EditObjectsResult:
        """Create, change or delete units, items, abilities and the other
        objects of a map's object data. A change with base creates a custom
        object from a standard one (h000 from hfoo); without base it changes
        a standard or custom object already there. Fields are named as
        object_data shows them (goldcost, HP, Name) or by their
        four-character id (ugol); an ability field with levels takes
        {"1": value, "2": value}. Values are checked against the game's
        field types and limits. Use object_data first to see what the map
        already changes."""
        return _edits.edit_objects(roots, path, object_type, changes, out)

    @tool('Apply a translation', writes=True)
    def apply_translation(
            path: MapPath,
            translations: Annotated[dict[str, str], Field(description=(
                'Key to translated text, with the keys map_translation gives. Keys not '
                'given keep their text; a key the map does not have refuses the call.'),
                min_length=1)],
            out: Out = '') -> R.ApplyTranslationResult:
        """Write translated texts into a map by the keys of map_translation:
        into war3map.wts for the strings kept there, or into the object
        data and map info for text kept inline. Every key is checked before
        anything is written."""
        return _edits.apply_translation(roots, path, translations, out)

    @tool('Edit placed units and doodads', writes=True)
    def edit_placed(
            path: MapPath,
            kind: Annotated[R.PlacedKind, Field(description=(
                'units for the units and items placed on the map (war3mapUnits.doo), '
                'doodads for the doodads and destructables (war3map.doo).'))],
            add: Annotated[list[R.PlacedValues], Field(description=(
                'New records; each needs id, x and y. Units default to player 0, '
                'facing 270, scale 1.'))] = [],
            change: Annotated[list[R.PlacedChange], Field(description=(
                'Records to change: select picks them, set gives the new values.'))] = [],
            delete: Annotated[list[R.PlacedSelector], Field(description=(
                'Records to delete, each picked like select.'))] = [],
            out: Out = '') -> R.EditPlacedResult:
        """Place, move, change or delete the units or doodads placed on a
        map. Changes and deletions pick records by index, type id or the
        editor number placed_objects gives; indices are those of the file
        before the call. A type id must be a standard one or one the map
        defines, and a position must be on the map. New records get the
        next free editor number. Use placed_objects first to find the
        records."""
        return _edits.edit_placed(roots, path, kind, add, change, delete, out)

    @tool('Edit regions, cameras and strings', writes=True)
    def edit_records(
            path: MapPath,
            kind: Annotated[R.EditRecordKind, Field(description=(
                'regions (war3map.w3r), cameras (war3map.w3c) or strings (war3map.wts).'))],
            add: Annotated[list[R.RecordValues], Field(description=(
                'New records. A region needs name, left, right, bottom and top; a camera '
                'name, target_x and target_y; a string its text.'))] = [],
            change: Annotated[list[R.RecordChange], Field(description=(
                'Regions and cameras: {select, set}. Strings: {id, text}.'))] = [],
            delete: Annotated[list[R.RecordSelector], Field(description=(
                'Regions and cameras to delete, picked by index or name.'))] = [],
            out: Out = '') -> R.EditRecordsResult:
        """Add, change or delete a map's regions or cameras, or add and
        change its strings. A region or camera that the script or GUI
        triggers name (gg_rct_..., gg_cam_...) cannot be deleted or
        renamed, and names must stay unique. Regions must lie on the map.
        A new camera starts as the game's default camera. A new string
        gets the next id, and its TRIGSTR_ reference is returned; strings
        are not deleted, as other files refer to them. Rewriting
        war3map.wts keeps every string but drops its comment lines. Use
        map_records first to see what is there."""
        return _edits.edit_records(roots, path, kind, add, change, delete, out)

    @tool('Edit the map script', writes=True)
    def edit_script(
            path: MapPath,
            code: Annotated[str, Field(description=(
                'JASS or Lua, in the language of the map script. Added before main; a JASS '
                'globals block in it is merged into the script\'s own.'), min_length=1)],
            init_function: Annotated[str, Field(description=(
                'A function of code to call when the map starts, at the top of main. '
                'Empty adds no call.'))] = '',
            replace: Annotated[bool, Field(description=(
                'Replace the whole script with code, which then needs its own main and '
                'config.'))] = False,
            out: Out = '') -> R.EditScriptResult:
        """Add JASS or Lua code to a map's script, called at map start when
        init_function is given, or replace the script. The edited script
        is parsed and checked as check_script does, and refused if it has
        an issue the original did not have. The World Editor rebuilds the
        script from the map's triggers when it saves the map, which drops
        code added here; for code that must survive that, add it as a
        custom-text trigger instead."""
        return _edits.edit_script(roots, path, code, init_function, replace, out)

    @tool('Import a file into a map', writes=True)
    def import_file(
            path: MapPath,
            member: Annotated[str, Field(description=(
                'The name inside the map, such as war3mapImported\\Hero.mdx or '
                'UI\\MyFrame.fdf. Map data files and the script are refused.'), min_length=1)],
            source: Annotated[str, Field(description='The file to import. ' + _PATH)],
            out: Out = '') -> R.ImportFileResult:
        """Add a file to a map, or replace one, and list it in war3map.imp
        so the World Editor keeps it: models, textures, sounds, frame
        definitions and other imports. Map data (war3map.w3u, w3e and the
        rest) and the script are refused; the other edit tools change them
        and check what they write."""
        return _edits.import_file(roots, path, member, source, out)

    return server


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wc3files-mcp',
        description='Serve wc3files to an MCP client (such as Claude) over stdio.')
    parser.add_argument('--root', action='append', required=True, metavar='FOLDER',
                        help='A folder the tools may read. Repeat it for more folders. '
                             'A relative path given to a tool is taken from the first.')
    parser.add_argument('--game', metavar='FOLDER', default=None,
                        help='The installed Warcraft III (1.32 or later), which the game_ '
                             'tools read and map_image takes its colours from.')
    parser.add_argument('--allow-write', action='store_true',
                        help='Also register the edit tools, which write maps under the roots. '
                             'Without it every tool only reads.')
    parser.add_argument('--version', action='version', version=f'wc3files-mcp {__version__}')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        server = build_server(args.root, allow_write=args.allow_write, game=args.game)
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    # stdout carries the protocol, so the package's log goes nowhere (it is
    # disabled on import) and nothing here prints to stdout.
    server.run()
    return 0
