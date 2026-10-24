"""Lets the CLI run as `python -m wc3files.cli ...`.

Without this, `python -m wc3files.cli` fails ("is a package and cannot be
directly executed") and `python -m wc3files.cli.main` works but prints a
RuntimeWarning, because importing the package already pulled main into
sys.modules. Both are papercuts for anyone who has the source but has not
run `pip install .` to get the `wc3files` console script.
"""
import sys

from .main import main

if __name__ == '__main__':
    sys.exit(main())
