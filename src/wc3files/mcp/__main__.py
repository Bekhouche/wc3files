"""`python -m wc3files.mcp --root FOLDER`, the same as `wc3files-mcp`."""
import sys

from .server import main

if __name__ == '__main__':
    sys.exit(main())
