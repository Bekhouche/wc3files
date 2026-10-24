import re

from loguru import logger

# A section header is any `[...]` line. The name is taken verbatim: real
# files written by the common map tools use dotted names such as
# `[w3u.original.hgtw]`, and an earlier `[A-Za-z0-9]+` pattern here matched
# none of them - it read 0 of 2,084 sections and 0 of 88,098 keys out of a
# real 2.9 MB export without reporting anything wrong.
_SECTION = re.compile(r'^\[(.*)\]$')


class CfgFile(object):
    """An INI-style config: `[section]` headers over `key = value` lines.

    `read()` -> `write()` reproduces the file byte-for-byte. That needs more
    than the values, so the original line order, the exact spelling around
    each `=`, which values were quoted, and the line endings are all kept
    alongside `_data`; `export_data()` returns just the nested
    {section: {key: value}} mapping, which is what callers edit.
    """

    def __init__(self):
        self._data = {}
        # Everything below exists only so write() can reproduce a real file
        # rather than emitting a normalized dump. _layout records the original
        # line order ('raw' passthrough, 'section', or an 'entry' bound to a
        # section/key), _raw_keys the exact text left of each '=' (files use
        # `key = value`, not `key=value`), and _quoted which values carried
        # quotes.
        self._layout = []
        self._raw_keys = {}
        self._quoted = set()
        self._line_ending = '\r\n'
        self._trailing_newline = True

    # -- reading ----------------------------------------------------------

    def read(self, cfg_file):
        self._data = {}
        self._layout = []
        self._raw_keys = {}
        self._quoted = set()

        with open(cfg_file, 'rb') as handle:
            raw = handle.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('cp1252', errors='replace')

        self._line_ending = '\r\n' if '\r\n' in text else '\n'
        self._trailing_newline = text.endswith(self._line_ending)

        # Split on the file's own line ending rather than with splitlines():
        # tooltip values carry a bare CR as an in-value line break, and
        # splitlines() treats each one as a new line - turning a single
        # `key = value` into several and rewriting the CR as CRLF on the way
        # back out.
        source_lines = text.split(self._line_ending)
        if self._trailing_newline and source_lines and source_lines[-1] == '':
            source_lines.pop()

        current = None
        duplicates = 0
        for line in source_lines:
            stripped = line.strip()

            if not stripped or stripped.startswith(('#', '//', ';')):
                self._layout.append(('raw', line))
                continue

            match = _SECTION.match(stripped)
            if match:
                current = match.group(1)
                if current not in self._data:
                    self._data[current] = {}
                self._layout.append(('section', current))
                continue

            if current is None or '=' not in line:
                self._layout.append(('raw', line))
                continue

            left, _, right = line.partition('=')
            key = left.strip()
            # Keep the whitespace on both sides of the value: real files are
            # written `key = value`, and normalizing that to `key=value`
            # changes every line in the file.
            if right.strip():
                lead = right[:len(right) - len(right.lstrip())]
                trail = right[len(right.rstrip()):]
            else:
                # An empty value is all separator - splitting it into a lead
                # and a trail would count the same spaces twice.
                lead, trail = right, ''
            value, quoted = self._unquote(right.strip())

            if key in self._data[current]:
                # A real duplicate key. _data is last-wins, so the earlier
                # occurrence is demoted to a verbatim line and the last one
                # stays bound to the dict value - the same approach WesFile
                # takes, and it keeps both in the written file.
                duplicates += 1
                for index in range(len(self._layout) - 1, -1, -1):
                    if self._layout[index] == ('entry', current, key):
                        self._layout[index] = ('raw', self._render(current, key))
                        break

            self._data[current][key] = value
            self._raw_keys[(current, key)] = (left, lead, trail)
            if quoted:
                self._quoted.add((current, key))
            else:
                self._quoted.discard((current, key))
            self._layout.append(('entry', current, key))

        logger.info(f'Read {len(self._data)} sections '
                    f'({sum(len(v) for v in self._data.values())} keys) '
                    f'from {cfg_file}')
        if duplicates:
            logger.debug(f'{duplicates} duplicate keys kept as verbatim lines')
        return self

    @staticmethod
    def _unquote(value):
        """Strip a *balanced* pair of quotes, and say whether there was one.

        `.strip('"')` would also eat a stray quote on one side only, silently
        changing the value - and would strip two where only one pair was
        meant.
        """
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            return value[1:-1], True
        return value, False

    # -- writing ----------------------------------------------------------

    def _render(self, section, key):
        value = self._data[section][key]
        value = '' if value is None else str(value)
        if (section, key) in self._quoted or value != value.strip():
            # Leading/trailing whitespace only survives inside quotes, so it
            # forces them even where the original had none.
            value = f'"{value}"'
        left, lead, trail = self._raw_keys.get((section, key), (key, '', ''))
        return f'{left}={lead}{value}{trail}'

    def write(self, cfg_file):
        lines = []
        if self._layout:
            written = set()
            placed = {(item[1], item[2]) for item in self._layout
                      if item[0] == 'entry'}
            for item in self._layout:
                if item[0] == 'raw':
                    lines.append(item[1])
                elif item[0] == 'section':
                    lines.append(f'[{item[1]}]')
                else:
                    _kind, section, key = item
                    if section in self._data and key in self._data[section]:
                        lines.append(self._render(section, key))
                        written.add((section, key))
            # Anything added to _data after read() has no place in the layout;
            # append it under its section rather than dropping it silently.
            for section, entries in self._data.items():
                extra = [k for k in entries
                         if (section, k) not in written
                         and (section, k) not in placed]
                if not extra:
                    continue
                lines.append(f'[{section}]')
                lines.extend(self._render(section, key) for key in extra)
        else:
            for section, entries in self._data.items():
                lines.append(f'[{section}]')
                lines.extend(self._render(section, key) for key in entries)

        text = self._line_ending.join(lines)
        if self._trailing_newline and lines:
            text += self._line_ending
        with open(cfg_file, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        logger.info(f'Wrote {len(lines)} lines to {cfg_file}')

    # -- data in and out --------------------------------------------------

    def export_data(self):
        return self._data

    def import_data(self, data):
        self._data = data
        return self

    def export_full(self):
        """Everything `write()` needs to reproduce the file exactly."""
        return {
            'data': {section: dict(entries)
                     for section, entries in self._data.items()},
            'layout': [list(item) for item in self._layout],
            'raw_keys': {f'{section}\x00{key}': left
                         for (section, key), left in self._raw_keys.items()},
            'quoted': [f'{section}\x00{key}' for section, key in self._quoted],
            'line_ending': self._line_ending,
            'trailing_newline': self._trailing_newline,
        }

    def import_full(self, state):
        """Restore what `export_full()` produced.

        Also accepts a plain {section: {key: value}} mapping, which is what
        `export_data()` returns - that path cannot reproduce the layout, but
        it keeps older dumps and hand-built data working.
        """
        if not isinstance(state, dict):
            raise ValueError('Data must be a dictionary.')
        if 'data' not in state or 'layout' not in state:
            return self.import_data(state)

        self._data = {section: dict(entries)
                      for section, entries in state['data'].items()}
        self._layout = [tuple(item) for item in state['layout']]
        self._raw_keys = {tuple(composite.split('\x00', 1)): left
                          for composite, left in (state.get('raw_keys') or {}).items()}
        self._quoted = {tuple(composite.split('\x00', 1))
                        for composite in (state.get('quoted') or ())}
        self._line_ending = state.get('line_ending', '\r\n')
        self._trailing_newline = bool(state.get('trailing_newline', True))
        return self

    def __repr__(self):
        keys = sum(len(entries) for entries in self._data.values())
        return f'<CfgFile {len(self._data)} sections, {keys} keys>'
