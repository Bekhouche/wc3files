"""
`Desync.txt`, the short report of a desync folder (#258).

Lines `<Key> value`, and blocks opened by `<Key:>` and closed by `<:Key>`.
The summary block reads `Network desync on turn <n> in game <id>`, and the
assertion block lists the game's checksums, `War3 <name> checksum <hex>`.
Other lines (the sound devices and drivers) are kept as they are.
"""

import re

_FIELD = re.compile(r'^<([A-Za-z][\w.]*)>\s?(.*)$')
_OPEN = re.compile(r'^<([A-Za-z][\w.]*):>$')
_CLOSE = re.compile(r'^<:([A-Za-z][\w.]*)>$')
_SUMMARY = re.compile(r'Network desync on turn (\d+) in game (\S+)')
_CHECKSUM = re.compile(r'^War3 (.+?) checksum ([0-9A-Fa-f]+)$')


class DesyncReport(object):
    """Reader of `Desync.txt`: `fields` ({key: value}), `sections`
    ({key: [lines]}), `other` (the lines of neither), and from them
    `turn`, `game`, `build` and `checksums` ({name: int})."""

    def __init__(self):
        self.fields, self.sections, self.other = {}, {}, []

    def read(self, source):
        """Read a path, or the file's bytes or text."""
        if isinstance(source, (bytes, bytearray)):
            text = bytes(source).decode('utf-8', 'replace')
        elif isinstance(source, str) and '\n' in source:
            text = source
        else:
            with open(source, 'rb') as handle:
                text = handle.read().decode('utf-8', 'replace')
        self.fields, self.sections, self.other = {}, {}, []
        section = None
        for line in text.lstrip('﻿').split('\n'):
            line = line.rstrip('\r')
            if section is not None:
                if _CLOSE.match(line) and _CLOSE.match(line).group(1) == section:
                    section = None
                else:
                    self.sections[section].append(line)
                continue
            opened = _OPEN.match(line)
            if opened:
                section = opened.group(1)
                self.sections[section] = []
                continue
            field = _FIELD.match(line)
            if field:
                self.fields[field.group(1)] = field.group(2).strip()
            elif line.strip():
                self.other.append(line)
        if section is not None:
            raise ValueError(f'Desync.txt: <{section}:> is not closed')
        return self

    def _summary(self):
        text = '\n'.join(self.sections.get('Exception.Summary', []))
        return _SUMMARY.search(text)

    @property
    def turn(self):
        found = self._summary()
        return int(found.group(1)) if found else None

    @property
    def game(self):
        found = self._summary()
        return found.group(2) if found else None

    @property
    def build(self):
        value = self.fields.get('Exception.BuildNumber')
        return int(value) if value and value.isdigit() else None

    @property
    def checksums(self):
        out = {}
        for line in self.sections.get('Exception.Assertion', []):
            found = _CHECKSUM.match(line.strip())
            if found:
                out[found.group(1)] = int(found.group(2), 16)
        return out
