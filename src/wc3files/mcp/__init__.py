"""An MCP server for wc3files (#179).

It lets an AI assistant such as Claude read Warcraft III maps, models and
replays with this package, through read-only tools. It needs the optional
`mcp` package:

    pip install "wc3files[mcp]"
    wc3files-mcp --root D:/Maps

Only files under the --root folders can be opened, and a protected map is
refused, as the command line refuses it. The tools themselves are plain
functions in wc3files.mcp.tools.
"""
from .server import build_parser, build_server, main

__all__ = ['build_parser', 'build_server', 'main']
