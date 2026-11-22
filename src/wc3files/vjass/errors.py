"""Errors and source locations for the vJass compiler."""


class SourceLine(object):
    """One line of input after preprocessing, still pointing at where it came
    from. Imports and text macros splice lines from elsewhere into the stream,
    so a plain line number would be wrong for every error after the first
    `//! import`."""

    __slots__ = ('text', 'file', 'line')

    def __init__(self, text, file, line):
        self.text = text
        self.file = file
        self.line = line

    def __repr__(self):
        return f'SourceLine({self.file}:{self.line}: {self.text!r})'


class Location(object):
    __slots__ = ('file', 'line', 'col')

    def __init__(self, file=None, line=0, col=0):
        self.file = file
        self.line = line
        self.col = col

    def __str__(self):
        where = self.file or '<input>'
        if self.line:
            where += f':{self.line}'
        return where

    def __repr__(self):
        return f'Location({self})'


class VJassError(Exception):
    """A compile error. `location` says where; `phase` says which step of the
    compiler found it (preprocess, parse, compile), which helps tell a syntax
    problem from a semantic one."""

    def __init__(self, message, location=None, phase='compile', note=None):
        self.message = message
        self.location = location or Location()
        self.phase = phase
        self.note = note
        text = f'{self.location}: {message}'
        if note:
            text += f' ({note})'
        super().__init__(text)


class VJassWarning(object):
    __slots__ = ('message', 'location')

    def __init__(self, message, location=None):
        self.message = message
        self.location = location or Location()

    def __str__(self):
        return f'{self.location}: warning: {self.message}'

    def __repr__(self):
        return f'VJassWarning({self})'
