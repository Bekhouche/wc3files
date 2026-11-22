"""Progress reporting for the long loops of a deep archive scan.

The library prints nothing unless it is asked to. `progress` is one of:

- None or False: no reporting (the default);
- True: a tqdm bar on stderr, if tqdm is installed (`pip install
  wc3files[progress]`), and nothing otherwise;
- a callable: called as `progress(done, total, label)` after each item.

The deep scan used to wrap its loops in tqdm unconditionally, so any library
call that read a protected map wrote progress bars to stderr, and tqdm was a
required dependency for that alone.
"""


def track(items, progress, label=''):
    """`items`, reporting each one as `progress` asks (see the module)."""
    if not progress:
        return items
    if progress is True:
        try:
            from tqdm import tqdm
        except ImportError:
            return items
        return tqdm(items, desc=label or None, leave=False)
    return _calling(list(items), progress, label)


def _calling(items, callback, label):
    total = len(items)
    for done, item in enumerate(items, 1):
        yield item
        callback(done, total, label)
