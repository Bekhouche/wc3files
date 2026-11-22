"""How deep the JASS and Lua parsers let an expression nest.

Both parsers are recursive descent, so each level of nesting is a few Python
calls deep. With Python's default limit of 1,000 frames they failed with a
RecursionError at about 80 nested parentheses (Lua) or 120 (JASS), which is
not the parser's own error type, so callers that catch parse errors missed
it. Real Lua accepts 200 levels (LUAI_MAXCCALLS, in 5.1 and 5.3).

Now both stop at MAX_LEVELS with their own error, and `room_for()` makes
sure Python's limit leaves enough frames to get there.
"""
import contextlib
import sys

# Lua's own limit, used for JASS as well: no real script comes near it.
MAX_LEVELS = 200

# Python frames one level of nesting can take, with some to spare.
_FRAMES_PER_LEVEL = 10


@contextlib.contextmanager
def room_for(levels=MAX_LEVELS):
    """Raise Python's recursion limit, for the duration of a parse, far
    enough for `levels` levels of nesting on top of the caller's own frames."""
    old = sys.getrecursionlimit()
    needed = old + levels * _FRAMES_PER_LEVEL
    if needed > old:
        sys.setrecursionlimit(needed)
    try:
        yield
    finally:
        sys.setrecursionlimit(old)
