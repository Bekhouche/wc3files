import sys

from . import main

# Guarded, so that importing every module (as the tests do) does not start
# a server.
if __name__ == '__main__':
    sys.exit(main())
