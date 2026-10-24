"""The Warcraft III root - the name-to-content-key table.

Warcraft III's root is refreshingly plain next to other Blizzard games':
it is a CRLF-separated text table of

    <path>|<content key hex>|<locale>|<install path>

with all four fields always present and the last two often empty. Measured
on a real 3.0.0 install: 174,908 lines, every one with exactly three pipes;
87,964 carry a locale tag and 289 an install path (loose files like
`x86_64/war3_loader.dll`, which the installer also writes to disk).

This matters for what the rest of this package can offer. Where World of
Warcraft's root stores Jenkins hashes of filenames - so a name is only
recoverable if you already have a listfile to hash and compare - Warcraft
III ships the names themselves. Enumeration and search are therefore exact
and complete here, with no guessing and nothing missed.

Paths use the game's `:`-separated mod-archive notation, e.g.
`War3.w3mod:_HD.w3mod:buildings/human/Farm/Farm.mdx`, where each `:`
segment is a layered archive. The final segment is the path within it.
"""


class CascRootError(Exception):
    pass


class RootEntry(object):
    __slots__ = ('path', 'content_key', 'locale', 'install_path')

    def __init__(self, path, content_key, locale, install_path):
        self.path = path
        self.content_key = content_key
        self.locale = locale
        self.install_path = install_path

    @property
    def name(self):
        """The path with its mod-archive prefixes stripped - what the file is
        actually called inside the game's virtual filesystem."""
        return self.path.rsplit(':', 1)[-1]

    def __repr__(self):
        locale = f' [{self.locale}]' if self.locale else ''
        return f'<RootEntry {self.path}{locale}>'


class CascRoot(object):
    def __init__(self):
        self.entries = []
        self._by_path = {}

    def read(self, data):
        if isinstance(data, bytes):
            # Real roots are ASCII paths plus hex; replace rather than fail on
            # anything stray, since one odd byte should not cost the whole table.
            data = data.decode('utf-8', errors='replace')

        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            fields = line.split('|')
            if len(fields) < 2:
                raise CascRootError(f'root line has no content key: {line[:80]!r}')
            path, content_key = fields[0], fields[1]
            locale = fields[2] if len(fields) > 2 else ''
            install_path = fields[3] if len(fields) > 3 else ''

            entry = RootEntry(path, content_key, locale, install_path)
            self.entries.append(entry)
            # A path can legitimately repeat once per locale; keep every one and
            # let the caller choose, rather than silently dropping all but one.
            self._by_path.setdefault(path, []).append(entry)

        return self

    def find(self, path):
        """Every entry for an exact path (one per locale it exists in)."""
        return list(self._by_path.get(path, ()))

    def paths(self):
        return self._by_path.keys()

    def __len__(self):
        return len(self.entries)
