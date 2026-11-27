"""
wc3files-lsp: a language server for JASS and Lua map scripts (#257).

It speaks the Language Server Protocol over stdin and stdout, and is
written on the standard library, so it needs no extra package. It reports
what `wc3files jass --check` reports: parse errors, the JASS checker's
findings and handle leaks, and the Lua checker's findings. See docs/lsp.md
for the editor setup.
"""

import argparse
import sys

from .server import Server, serve

__all__ = ['Server', 'serve', 'main']


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='wc3files-lsp',
        description='A language server for JASS and Lua map scripts, over stdin and stdout.')
    parser.add_argument('--stdio', action='store_true',
                        help='serve over stdin and stdout (the default; accepted because '
                             'editors pass it)')
    parser.parse_args(argv)
    return serve()


if __name__ == '__main__':
    sys.exit(main())
