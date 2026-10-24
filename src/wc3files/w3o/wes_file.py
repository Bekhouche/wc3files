from loguru import logger
from typing import Dict

class WesFile:
    def __init__(self):
        """
        Initialize the WesFile object with an empty data dictionary.
        """
        self._data: Dict[str, str] = {}
        # Everything below exists only so write() can reproduce a real file
        # faithfully instead of emitting a bare key=value dump. The real
        # bundled WorldEditStrings.txt is 10465 lines of which ~1000 carry no
        # data at all (502 `//` comments, 460 blank lines, 1 `[Section]`
        # header), and 2132 of its values are quoted - none of which survives
        # in a flat {key: value} dict. _layout records the original line order
        # ('raw' passthrough vs. an 'entry' for a key), _quoted remembers which
        # keys were written with quotes, and the newline/trailing state is kept
        # as found rather than normalized.
        self._layout: list = []
        self._quoted: set = set()
        self._raw_keys: dict = {}
        self._line_ending: str = '\r\n'
        self._trailing_newline: bool = True

    def read(self, file_path: str):
        """
        Read key-value pairs from a file and store them in the _data dictionary.

        Parameters:
        file_path (str): Path to the file to read.

        Logs:
        Logs the file reading process and any errors encountered.
        """
        self._data.clear()
        self._layout = []
        self._quoted = set()
        self._raw_keys = {}
        try:
            with open(file_path, mode='rb') as raw:
                blob = raw.read()
            self._line_ending = '\r\n' if b'\r\n' in blob else '\n'
            self._trailing_newline = blob.endswith(b'\n')

            with open(file_path, mode='r', encoding='utf-8') as file:
                logger.info(f"Reading file: {file_path}")
                lines = file.readlines()

                # A flat {key: value} dict can only represent a *flat* strings
                # file. A file with several [Section] headers repeats its keys
                # per section (the bundled UnitEditorData.txt has 36 sections
                # all using '00='/'01='/'NumValues=') and collapsing that into
                # one dict silently loses almost everything - it is CfgFile's
                # shape, not this one. Say so instead of returning nonsense.
                section_count = sum(1 for line in lines
                                    if line.strip().startswith('[') and line.strip().endswith(']'))
                if section_count > 1:
                    logger.warning(
                        f"{file_path} has {section_count} [Section] headers - this is a "
                        f"sectioned config, not a flat World Edit strings file. Keys repeat "
                        f"per section and will collapse into one another here; use CfgFile "
                        f"for this file instead."
                    )

                for line in lines:
                    line = line.rstrip('\r\n')
                    stripped = line.strip()
                    # Split the line into key and value at the first '='.
                    # Deliberately split the *unstripped* line: a real WES value's
                    # leading/trailing space is meaningful, not noise. 20 values in
                    # the bundled WorldEditStrings.txt are suffixes that carry their
                    # own separating space (' <gen>', ' (Dead)', ' (Campaign)',
                    # ' <Caster>', ...), and stripping it made the editor render
                    # 'Footman(Dead)' instead of 'Footman (Dead)' - a real
                    # correctness bug, not just a round-trip detail.
                    parts = line.split('=', 1)
                    if len(parts) == 2:
                        raw_key, value = parts
                        key = raw_key.strip()
                        # Keep the key exactly as written when it differs from
                        # the stripped form: a real file has entries spelled
                        # 'KEY = value', and rewriting those as 'KEY= value'
                        # changes bytes that were never ours to change.
                        if raw_key != key:
                            self._raw_keys[key] = raw_key
                        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]  # match CfgFile's convention
                            self._quoted.add(key)
                        if key in self._data:
                            # Real duplicate key (the bundled WorldEditStrings.txt
                            # has 12 of them). _data is last-wins, so demote the
                            # earlier occurrence to a verbatim line and let the
                            # last one stay bound to the dict value.
                            for idx in range(len(self._layout) - 1, -1, -1):
                                if self._layout[idx] == ('entry', key):
                                    self._layout[idx] = ('raw', self._rendered_line(key))
                                    break
                        self._data[key] = value
                        self._layout.append(('entry', key))
                        logger.debug(f"Loaded key-value pair: {key} = {value}")
                    elif not stripped or stripped.startswith('//') or (
                            stripped.startswith('[') and stripped.endswith(']')):
                        # Blank lines, // comments, and [Section] headers are expected
                        # structure in this format (the same [Section]key=value shape
                        # CfgFile parses), not malformed content - don't warn about them.
                        # Kept verbatim so write() can put them back where they were.
                        self._layout.append(('raw', line.rstrip('\r\n')))
                        logger.debug(f"Skipping non-data line: {stripped}")
                    else:
                        self._layout.append(('raw', line.rstrip('\r\n')))
                        logger.warning(f"Skipping invalid line: {stripped}")

        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            raise
        except Exception as e:
            logger.exception(f"Error occurred while reading file: {e}")
            raise

    def write(self, file_path: str):
        """
        Write the key-value pairs back out as a World Edit strings file.

        When this object came from read(), the original line order, `//`
        comments, blank lines, `[Section]` header, per-value quoting, and line
        endings are all reproduced, so an unmodified read -> write is
        byte-for-byte identical to the input. A changed value is substituted in
        place; a key deleted from the data is dropped along with its line; a key
        added since the read is appended at the end.

        For data built by hand via import_data() there is no layout to follow,
        so entries are written in dict order. A value is quoted only if it would
        not survive the round-trip unquoted (it has leading/trailing whitespace,
        or it already starts and ends with a quote character, which read() would
        otherwise strip as this format's own quoting).

        Parameters:
        file_path (str): The path to write the file to.
        """
        def render(key):
            value = self._data[key]
            written_key = self._raw_keys.get(key, key)
            if key in self._quoted or self._needs_quoting(value):
                return f'{written_key}="{value}"'
            return f'{written_key}={value}'

        lines = []
        written = set()
        for item in self._layout:
            if item[0] == 'raw':
                lines.append(item[1])
                continue
            key = item[1]
            if key in written or key not in self._data:
                continue  # deleted since the read, or a duplicate key line
            lines.append(render(key))
            written.add(key)

        for key in self._data:
            if key not in written:
                lines.append(render(key))
                written.add(key)

        text = self._line_ending.join(lines)
        if self._trailing_newline and lines:
            text += self._line_ending

        logger.info(f"Writing {len(self._data)} entries to {file_path}")
        with open(file_path, mode='w', encoding='utf-8', newline='') as file:
            file.write(text)

    def _rendered_line(self, key: str) -> str:
        """The `key=value` text for a key as currently held, used to freeze an
        earlier duplicate occurrence in place when a later one supersedes it."""
        value = self._data[key]
        written_key = self._raw_keys.get(key, key)
        if key in self._quoted or self._needs_quoting(value):
            return f'{written_key}="{value}"'
        return f'{written_key}={value}'

    @staticmethod
    def _needs_quoting(value: str) -> bool:
        """Whether a value has to be written quoted to survive a read() back.

        Only one case does: a value that itself starts and ends with a quote
        character, which read() would otherwise strip as this format's own
        quoting. Surrounding whitespace deliberately does NOT need quoting -
        read() preserves it verbatim (see the note there about ' <gen>'-style
        suffixes), so quoting it would add characters the original never had.
        """
        return len(value) >= 2 and value.startswith('"') and value.endswith('"')

    def import_data(self, data: Dict[str, str]):
        """
        Import key-value pairs into the WesFile object.

        Parameters:
        data (Dict[str, str]): Dictionary containing key-value pairs to import.

        Logs:
        Logs the import process and size of the imported data.
        """
        if not isinstance(data, dict):
            logger.error("Invalid data format. Expected a dictionary.")
            raise ValueError("Data must be a dictionary.")

        self._data = data
        logger.info(f"Imported data with {len(self._data)} entries.")

    def export_full(self) -> dict:
        """Export everything `write()` needs to reproduce the file exactly.

        `export_data()` returns only the flat {key: value} dict, which is
        lossy for any file that repeats a key across sections - and real ones
        do, heavily: the bundled UnitEditorData.txt has 43 sections and 404
        `key=` lines but only 53 distinct keys, so a flat dict keeps 53 of
        them. This carries the layout alongside the values so a dump/build
        pair round-trips instead of silently dropping most of the file.
        """
        return {
            'data': dict(self._data),
            'layout': [[kind, value] for kind, value in self._layout],
            'quoted': sorted(self._quoted),
            'raw_keys': dict(self._raw_keys),
            'line_ending': self._line_ending,
            'trailing_newline': self._trailing_newline,
        }

    def import_full(self, state: dict):
        """Restore what `export_full()` produced.

        Also accepts a plain {key: value} dict, which is what `export_data()`
        returns - that path stays lossy, but it keeps older dumps readable.
        """
        if not isinstance(state, dict):
            raise ValueError('Data must be a dictionary.')
        if 'data' not in state or 'layout' not in state:
            return self.import_data(state)

        self._data = dict(state['data'])
        self._layout = [tuple(item) for item in state['layout']]
        self._quoted = set(state.get('quoted') or ())
        self._raw_keys = dict(state.get('raw_keys') or {})
        self._line_ending = state.get('line_ending', '\r\n')
        self._trailing_newline = bool(state.get('trailing_newline', True))
        return self

    def export_data(self) -> Dict[str, str]:
        """
        Export the stored key-value pairs as a dictionary.

        Returns:
        Dict[str, str]: The internal _data dictionary containing key-value pairs.

        Logs:
        Logs the export process and the size of the data being exported.
        """
        logger.info(f"Exporting data with {len(self._data)} entries.")
        return self._data

    def __repr__(self) -> str:
        """
        Return a string representation of the WesFile object.

        Returns:
        str: String representation of the object showing number of entries.
        """
        return f"<WesFile with {len(self._data)} entries>"
