"""Lets the CLI run as `python -m wc3files ...`, the same as `wc3files ...`.

`python -m` is the usual way to run a tool whose script is not on PATH,
which is common on Windows. Only `python -m wc3files.cli` worked before.
"""
import sys

from wc3files.cli.main import main

if __name__ == '__main__':
    sys.exit(main())
