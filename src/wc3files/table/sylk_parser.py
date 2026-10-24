# -*- coding:utf-8 -*-
import csv
from .sylk import SYLK

SYLK_CHARSET = "cp1252"

class SylkParser:
    def __init__(
        self,
        filename,
        headers=None,
        encoding=SYLK_CHARSET,
    ):
        if headers is None:
            headers = []

        self.headers = headers
        self.sylk_handler = SYLK()
        with open(filename, encoding=encoding) as handle:
            self.sylk_handler.parse(handle)

    def to_csv(self, fbuf, quotechar='"', delimiter=','):
        """
        Convert the SYLK data to CSV and write it to the provided file buffer.
        :param fbuf: File-like object to write the CSV data to
        :param quotechar: Character used to quote fields in CSV
        :param delimiter: Delimiter used between CSV fields
        """
        csvwriter = csv.writer(
            fbuf,
            quotechar=quotechar,
            delimiter=delimiter,
            lineterminator="\n",
            quoting=csv.QUOTE_ALL
        )

        if self.headers:
            csvwriter.writerow(self.headers)

        for line in self.sylk_handler.stream_rows():
            csvwriter.writerow(line)

    def _get_line_as_dict(self, line):
        """
        Transform a line (list) into a dictionary mapping headers to values.
        :param list line: The line from the SYLK data
        :rtype: dict
        """
        return dict(zip(self.headers, line))

    def __iter__(self):
        """
        Iterate through the rows of SYLK data, yielding dictionaries if headers
        are provided, or raw lines otherwise.
        """
        for line in self.sylk_handler.stream_rows():
            if self.headers:
                yield self._get_line_as_dict(line)
            else:
                yield line
