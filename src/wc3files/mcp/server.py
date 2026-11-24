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


def build_server(folders):
    """An MCPServer whose tools read files under `folders` only."""
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        raise _missing_mcp() from None
    roots = _tools.Roots(folders)
    server = MCPServer('wc3files', version=__version__, instructions=INSTRUCTIONS)
    register = server.tool()

    def tool(function):
        """Register `function`. A failure the tools expect (bad input, which
        is a ValueError in wc3files, a protected map, a missing file or a
        path outside the roots) reaches the client with its message; the SDK
        would hide the message of any other exception."""
        @functools.wraps(function)
        def run(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except (ValueError, OSError) as exc:
                raise ToolError(str(exc)) from exc
        return register(run)

    @tool
    def list_files(folder: str = '', pattern: str = '') -> dict:
        """Maps, campaigns, models and replays under `folder` (every allowed
        folder by default), with their sizes. `pattern` keeps the file names
        that contain it. At most 1000 files are listed."""
        return _tools.list_files(roots, folder, pattern)

    @tool
    def map_info(path: str) -> dict:
        """A map's name, author, description, size, script language, game
        version, players and forces, from its war3map.w3i."""
        return _tools.map_info(roots, path)

    @tool
    def list_members(path: str) -> dict:
        """The files inside a map, with their unpacked and stored sizes."""
        return _tools.list_members(roots, path)

    @tool
    def read_member(path: str, member: str, max_chars: int = _tools.DEFAULT_MAX_CHARS) -> dict:
        """One file inside a map: parsed data for the formats wc3files reads
        (war3map.w3i, w3e, doo, w3r, wts, w3u and the other object files,
        wtg, ...), the text of a script or text file, or only its size.
        Results longer than max_chars characters are refused."""
        return _tools.read_member(roots, path, member, max_chars)

    @tool
    def object_data(path: str, object_type: str = '', ids: list[str] | None = None,
                    max_chars: int = _tools.DEFAULT_MAX_CHARS) -> dict:
        """Units, items, destructables, doodads, abilities, buffs and upgrades
        a map changes or adds, with readable field names and its strings
        resolved. object_type is one of w3u, w3t, w3b, w3d, w3a, w3h, w3q;
        ids keeps those object ids (such as hfoo or A000)."""
        return _tools.object_data(roots, path, object_type, ids, max_chars)

    @tool
    def trigger_text(path: str, name: str = '', names_only: bool = False,
                     max_chars: int = _tools.DEFAULT_MAX_CHARS) -> dict:
        """A map's GUI triggers as the World Editor shows them, one line per
        event, condition and action. name keeps the triggers whose name
        contains it; names_only gives only the trigger names, which is the
        way to start on a large map."""
        return _tools.trigger_text(roots, path, name, names_only, max_chars)

    @tool
    def check_script(path: str, max_chars: int = _tools.DEFAULT_MAX_CHARS) -> dict:
        """Problems in a map's JASS or Lua script, or in a script file:
        unknown functions, wrong argument counts and types, and more."""
        return _tools.check_script(roots, path, max_chars)

    @tool
    def model_info(path: str, member: str = '') -> dict:
        """A model's name, format, version, sequences and textures. For a
        map, member names the model inside it."""
        return _tools.model_info(roots, path, member)

    @tool
    def replay_info(path: str) -> dict:
        """A replay's game name, map, length, version and players, and its
        W3MMD winners and losers when it records them."""
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
