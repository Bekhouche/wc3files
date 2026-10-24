"""Shared setup for the wc3files test suite: put src/ on sys.path and quiet
loguru down to WARNING so `python -m unittest discover` output stays readable
(every format module logs at INFO/DEBUG by default)."""
import glob
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

SAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'samples')


def sample_path(name):
    """Resolves a checked-in fixture by filename. `tests/samples/` is grouped
    into one subfolder per source package (samples/img/sample.blp,
    samples/mpq/... etc. - mirrors src/wc3files/'s own layout) rather than
    one flat folder, so this searches recursively instead of assuming a
    fixed subfolder - existing call sites (`sample_path('sample.blp')`) don't
    need to know or care which subfolder a fixture actually lives in."""
    direct = os.path.join(SAMPLES_DIR, name)
    if os.path.isfile(direct):
        return direct
    matches = glob.glob(os.path.join(SAMPLES_DIR, '**', name), recursive=True)
    if not matches:
        raise FileNotFoundError(f'no such sample fixture: {name!r} (looked under {SAMPLES_DIR})')
    if len(matches) > 1:
        raise FileNotFoundError(f'ambiguous sample fixture: {name!r} matches {matches}')
    return matches[0]


def read_bytes(path):
    with open(path, 'rb') as fh:
        return fh.read()


try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level='WARNING')
except ImportError:
    pass
