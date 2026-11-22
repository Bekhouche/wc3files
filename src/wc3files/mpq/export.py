"""Writing the members of an archive out as loose files.

Shared by MpqFile.export() and W3xFile.export(). Member names come from the
archive, which may be untrusted, so a name is only written if it resolves to a
place inside the output folder.
"""
import os

from loguru import logger


def member_path(root, name):
    """The path member `name` is written to under `root`, or None when the
    name would land outside it (`..` parts, or an absolute or drive path)."""
    if not name:
        return None
    root = os.path.abspath(root)
    # Archive names separate folders with a backslash. os.path.join only
    # splits on it on Windows; elsewhere `Units\x.txt` would become one file
    # named with a backslash, so the name is split here, on every platform.
    parts = name.split('\\')
    # An absolute name, or a part carrying a drive ("C:"), would make
    # os.path.join drop the folder, so neither gets as far as the join.
    if os.path.isabs(name) or any(os.path.isabs(part) or os.path.splitdrive(part)[0]
                                  for part in parts):
        return None
    target = os.path.abspath(os.path.join(root, *parts))
    try:
        inside = os.path.commonpath([root, target]) == root
    except ValueError:  # a different drive on Windows
        inside = False
    if not inside or target == root:
        return None
    return target


def export_files(files, root):
    """Write each {name: bytes} member under `root`, creating folders as
    needed. A name that would land outside `root` is skipped with a warning.
    Returns the list of names skipped."""
    os.makedirs(root, exist_ok=True)
    skipped = []
    for name, data in files.items():
        target = member_path(root, name)
        if target is None:
            logger.warning('skipped archive member {!r}: it would be written outside {}',
                           name, root)
            skipped.append(name)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as handle:
            handle.write(data or b'')
    return skipped
