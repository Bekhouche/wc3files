import ast
import pandas as pd
from loguru import logger


class RawToken(str):
    """A bare SYLK literal that is neither quoted nor numeric.

    Real tables carry `KFALSE` and `K#VALUE!` - a spreadsheet boolean and a
    propagated error - written without quotes. They behave as strings here,
    but quoting them on the way out would change the file, so the distinction
    is kept.
    """
    __slots__ = ()

class SlkFile:
    def __init__(self):
        """
        Initializes the SlkFile object with an empty data dictionary.
        """
        self._data = {}
        self._id_column = None
        # The `B` record declares the sheet's extent, which is not always the
        # extent of the cells that carry data: of the 20 tables Blizzard
        # ships, UnitUI.slk declares 2 more columns than it fills,
        # UnitData.slk 1 more, and AbilityBuffData.slk 165 more rows. Those
        # trailing empties cannot be derived from the data, so the original
        # declaration is kept and reused when the data still fits inside it.
        self._declared_columns = None
        self._declared_rows = None
        self._declared_tail = 'D0'
        # Real tables have gaps in the header row - UnitBalance.slk and
        # UnitWeapons.slk among them. Those columns still hold data, so they
        # need a name here, but inventing one and then writing it back would
        # add a cell the file never had.
        self._unnamed_columns = set()
        # Likewise for rows: Doodads.slk has 1 row and AbilityData.slk 4 that
        # hold data in other columns but no key cell. Dropping them would
        # renumber every row after them.
        self._keyless_rows = set()
        # Doodads.slk declares a row (717) that holds no cells at all, and
        # keeps numbering the rows after it as if it were there. Renumbering
        # them would shift every id that follows.
        self._empty_rows = set()


    @staticmethod
    def _decode_cell(token: str):
        """Decode a `K` cell literal into a Python value.

        SYLK writes strings quoted with embedded quotes doubled, and numbers
        bare. Keeping that distinction is what lets a file be written back as
        it was found: `K600` and `K"600"` are different cells, and the older
        CSV-based reader turned every one of them into a string.
        """
        if token.startswith('"'):
            body = token[1:-1] if token.endswith('"') and len(token) >= 2 else token[1:]
            return body.replace('""', '"')
        if token == '':
            return ''
        try:
            return int(token)
        except ValueError:
            pass
        try:
            return float(token)
        except ValueError:
            return RawToken(token)

    def _read_records(self, file_path: str):
        """Parse the SYLK records into {(x, y): value} plus the declared extent.

        This replaces a SYLK -> CSV -> pandas chain that could not round-trip:
        it turned numbers into strings, filled gaps with invented values, and
        lost which cells were absent in the first place.
        """
        cells = {}
        current_y = None
        with open(file_path, 'r', encoding='cp1252', errors='replace',
                  newline='') as handle:
            for raw in handle:
                line = raw.rstrip('\r\n')
                if not line:
                    continue
                kind, _, rest = line.partition(';')
                if kind == 'E':
                    break
                if kind == 'B':
                    for field in rest.split(';'):
                        if field.startswith('X') and field[1:].isdigit():
                            self._declared_columns = int(field[1:])
                        elif field.startswith('Y') and field[1:].isdigit():
                            self._declared_rows = int(field[1:])
                        elif field and field[0] not in 'XY':
                            self._declared_tail = field
                    continue
                if kind != 'C':
                    continue

                x = y = None
                value = None
                # A K field may itself contain ';' inside a quoted string, so
                # scan fields and treat everything from K onward as the value.
                fields = rest.split(';')
                for index, field in enumerate(fields):
                    if not field:
                        continue
                    if field[0] == 'X' and field[1:].isdigit():
                        x = int(field[1:])
                    elif field[0] == 'Y' and field[1:].isdigit():
                        y = int(field[1:])
                    elif field[0] == 'K':
                        value = ';'.join(fields[index:])[1:]
                        break
                if y is not None:
                    current_y = y
                if x is None or current_y is None or value is None:
                    continue
                cells[(x, current_y)] = self._decode_cell(value)
        return cells

    def read(self, file_path: str):
        """
        Reads a .slk file and processes its data into a dictionary format.

        Parameters:
        file_path (str): The path to the .slk file.

        Logs:
        Logs the file reading process, errors encountered, and data conversion.
        """
        self._declared_columns = None
        self._declared_rows = None
        self._declared_tail = 'D0'
        self._unnamed_columns = set()
        self._keyless_rows = set()
        self._empty_rows = set()
        cells = self._read_records(file_path)

        if not cells:
            logger.warning(f'No cells found in {file_path}')
            self._data = {}
            self._id_column = None
            return

        width = max(x for x, _ in cells)
        height = max(y for _, y in cells)

        headers = []
        for x in range(1, width + 1):
            name = cells.get((x, 1))
            if name is None:
                name = f'column{x}'
                self._unnamed_columns.add(name)
            headers.append(name)
        self._id_column = headers[0]

        data = {}
        for y in range(2, height + 1):
            key = cells.get((1, y))
            if key is None:
                if not any((x, y) in cells for x in range(2, width + 1)):
                    self._empty_rows.add(y)
                    continue      # a row the sheet declares but never fills
                key = f'row{y}'   # has data, just no key cell
                self._keyless_rows.add(key)
            row = {}
            for index, name in enumerate(headers, start=1):
                row[name] = cells.get((index, y))
            data[key] = row
        self._data = data
        logger.info(f'Read {len(self._data)} rows from {file_path}')
        return self





    @staticmethod
    def _literal_eval(value):
        """
        Safely parse a raw SLK-derived token (e.g. '"unitBalanceID"', '123', '1.5') into its
        Python literal, using ast.literal_eval rather than eval() - map files are untrusted
        input, and eval() would execute arbitrary Python if a column header or cell value
        were crafted to contain it. Falls back to fix_strings() (a plain quote-strip) for
        tokens that aren't valid Python literals, such as a genuinely blank column header.
        """
        if not isinstance(value, str):
            return value  # pandas already inferred a proper type (e.g. int64) for this column
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return SlkFile.fix_strings(value)

    @staticmethod
    def fix_strings(value) -> str:
        """
        Fixes string values by stripping surrounding quotes.

        Parameters:
        value: The value to process.

        Returns:
        str: The processed value with quotes stripped, if applicable.

        Logs:
        Logs the string fixing process.

        Only strips the first/last character when they're actually the quote character -
        a bare numeric-looking string (e.g. '1200') can end up in a pandas 'object' column
        (and thus reach here as a plain str, not an int/float) whenever that column also
        contains SYLK's single-space blank-cell placeholder for some other row, which is
        common in a wide/sparse table (e.g. per-object-instance data where most columns are
        unset for most rows). Blindly slicing [1:-1] there would eat real leading/trailing
        digits instead of quote characters.
        """
        if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            return value[1:-1]  # Strip surrounding quotes
        return value

    def import_data(self, data: dict, id_column: str = None):
        """
        Imports data into the internal dictionary.

        Parameters:
        data (dict): A dictionary to import into the internal _data storage.
        id_column (str): Name of the primary-key column (the row key), used when writing.
            Not needed if this SlkFile was populated via read().

        Logs:
        Logs the size of the imported data.
        """
        if not isinstance(data, dict):
            logger.error("Invalid data format. Expected a dictionary.")
            raise ValueError("Data must be a dictionary.")

        self._data = data
        if id_column is not None:
            self._id_column = id_column
        logger.info(f"Imported data with {len(self._data)} entries.")

    def write(self, file_path: str, id_column: str = None):
        """
        Writes the internal data dictionary out as a SYLK (.slk) file - the inverse of
        read()/export_data(): {row_key: {column: value}} becomes a header row of column
        names (with the row key under id_column) followed by one data row per entry.

        Parameters:
        file_path (str): The path to write the .slk file to.
        id_column (str): Name of the primary-key column. Defaults to the column name seen
            by read() (self._id_column), or 'id' if this data was built via import_data()
            without one.
        """
        id_column = id_column or self._id_column or 'id'

        columns = []
        for row in self._data.values():
            for key in row:
                if key not in columns:
                    columns.append(key)

        # read()/export_data() already store the id column redundantly inside each row's
        # own dict (row[id_column] == the row's key) - reuse that slot instead of adding a
        # second column with the same name, and fall back to prepending it for data that
        # was built by hand via import_data() without that redundancy.
        if id_column in columns:
            columns.remove(id_column)
        columns = [id_column] + columns

        # Keep the sheet's declared extent when the data still fits inside it
        # (an unmodified round-trip), and grow to the data otherwise.
        used_columns = len(columns)
        used_rows = len(self._data) + 1
        declared_columns = self._declared_columns or 0
        declared_rows = self._declared_rows or 0
        extent_columns = max(used_columns, declared_columns)
        extent_rows = max(used_rows, declared_rows)

        lines = ['ID;PWXL;N;E',
                 f'B;X{extent_columns};Y{extent_rows};{self._declared_tail}']

        # SYLK carries `Y` only when the row changes - every C record after
        # the first in a row omits it. Blizzard's own tables follow this:
        # across the 20 bundled ones, 559,609 C records omit Y and 10,956
        # carry it, one per row. Emitting it every time produces a valid but
        # ~43% larger file.
        previous_y = None

        def cell(x, y, value):
            nonlocal previous_y
            encoded = self._encode_cell(value)
            if y == previous_y:
                return f'C;X{x};K{encoded}'
            previous_y = y
            return f'C;X{x};Y{y};K{encoded}'

        for x, value in enumerate(columns, start=1):
            if value in self._unnamed_columns:
                continue          # this column had no header cell to begin with
            lines.append(cell(x, 1, value))

        y = 1
        for key, row in self._data.items():
            y += 1
            while y in self._empty_rows:
                y += 1            # keep the gap the original left
            keyless = key in self._keyless_rows
            for x, column in enumerate(columns, start=1):
                if keyless and column == id_column:
                    continue
                value = row[column] if column in row else (key if column == id_column else None)
                if value is None or pd.isna(value):
                    continue
                lines.append(cell(x, y, value))

        lines.append('E')

        logger.info(f"Writing {len(self._data)} entries to {file_path}")
        # SYLK has no Unicode escape mechanism at all - it's a pure system-ANSI-codepage text
        # format (confirmed: no numeric-character-reference convention exists in the real
        # spec or in any WC3 tool's own SLK backend, including w3x2lni's, which writes cell
        # text straight through with no transcoding). A real Reforged map's tooltip text can
        # still contain a genuine non-cp1252 Unicode character (e.g. U+221E '∞') that classic
        # SLK simply has no way to represent - the documented real-world behavior for this
        # exact situation (per SYLK's own spec and how spreadsheet apps of that era behaved)
        # is to substitute '?' for anything the codepage can't hold, not to fail outright.
        # 'replace' matches that rather than inventing a non-standard escape of our own.
        with open(file_path, 'w', encoding='cp1252', newline='\r\n', errors='replace') as handle:
            handle.write('\n'.join(lines) + '\n')

    @staticmethod
    def _encode_cell(value) -> str:
        """
        Encode a Python value as a SYLK 'K' cell literal: numbers unquoted, everything else
        as a quoted string with embedded quotes doubled (the standard SYLK escaping rule).
        """
        if isinstance(value, RawToken):
            return str(value)
        if isinstance(value, bool):
            return '1' if value else '0'
        if isinstance(value, (int, float)):
            return str(value)
        return '"' + str(value).replace('"', '""') + '"'

    def export_data(self) -> dict:
        """
        Exports the internal data dictionary.

        Returns:
        dict: The internal _data dictionary.

        Logs:
        Logs the export process.
        """
        logger.info(f"Exporting data with {len(self._data)} entries.")
        return self._data

    def __repr__(self) -> str:
        """
        Returns a string representation of the SlkFile object.

        Returns:
        str: A string representation showing the number of entries in the data.
        """
        return f"<SlkFile with {len(self._data)} entries>"
