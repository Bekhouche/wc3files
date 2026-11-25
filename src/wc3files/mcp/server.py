"""The wc3files MCP server (#179): the tools of wc3files.mcp.tools, served
over stdio with the official `mcp` package (`pip install wc3files[mcp]`).

    wc3files-mcp --root D:/Maps --root D:/Replays
"""
import argparse
import functools
import sys

from wc3files.version import __version__
from . import tools as _tools

INSTRUCTIONS = (
    'Reads Warcraft III maps (.w3x, .w3m), models (.mdx, .mdl) and replays '
    '(.w3g) with wc3files. Start with list_files to find files, then map_info '
    'and list_members for a map. read_member gives one member as data; '
    'object_data, trigger_text and check_script answer questions about units, '
    'abilities, triggers and scripts. Every tool only reads. Files outside '
    'the folders the server was started with are refused, and so are '
    'protected maps.')


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


def build_server(folders):
    """An MCPServer whose tools read files under `folders` only."""
    try:
        from typing import Annotated

        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
        from pydantic import Field

        from . import results as R
    except ImportError:
        raise _missing_mcp() from None
    roots = _tools.Roots(folders)
    server = MCPServer('wc3files', version=__version__, instructions=INSTRUCTIONS)

    def tool(title):
        """Register a tool as read-only and closed-world: every tool reads
        files under the roots and nothing else. Its description is its
        docstring followed by _READ_ONLY. A failure the tools expect (bad
        input, which is a ValueError in wc3files, a protected map, a missing
        file or a path outside the roots) reaches the client with its
        message; the SDK would hide the message of any other exception."""
        def wrap(function):
            @functools.wraps(function)
            def run(*args, **kwargs):
                try:
                    return function(*args, **kwargs)
                except (ValueError, OSError) as exc:
                    raise ToolError(str(exc)) from exc
            description = ' '.join(function.__doc__.split()) + ' ' + _READ_ONLY
            return server.tool(title=title, description=description,
                               annotations=ToolAnnotations(
                                   title=title, readOnlyHint=True, destructiveHint=False,
                                   idempotentHint=True, openWorldHint=False))(run)
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
            path: Annotated[str, Field(description='The .w3g replay. ' + _PATH)]) -> R.ReplayInfoResult:
        """Read a replay's summary: game name, map, host, length, game
        version, players, and the W3MMD winners and losers when the map
        records them. Use it to answer who played a game, on which map, for
        how long and who won."""
        return _tools.replay_info(roots, path)

    return server


def build_parser():
    parser = argparse.ArgumentParser(
        prog='wc3files-mcp',
        description='Serve wc3files to an MCP client (such as Claude) over stdio.')
    parser.add_argument('--root', action='append', required=True, metavar='FOLDER',
                        help='A folder the tools may read. Repeat it for more folders. '
                             'A relative path given to a tool is taken from the first.')
    parser.add_argument('--version', action='version', version=f'wc3files-mcp {__version__}')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        server = build_server(args.root)
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    # stdout carries the protocol, so the package's log goes nowhere (it is
    # disabled on import) and nothing here prints to stdout.
    server.run()
    return 0
