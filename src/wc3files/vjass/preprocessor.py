"""The textual passes JassHelper runs before it parses anything.

In order:

1. `//! import [vjass|zinc|comment] "file"` - splice another file in, each
   file at most once, searched for next to the importing file and then in the
   configured lookup folders.
2. `//! novjass` ... `//! endnovjass` - dropped. Libraries use it to keep
   documentation or example code inside the file without compiling it.
3. Text macros - `//! textmacro`, `//! textmacro_once`, `//! runtextmacro
   [optional]`, expanded until nothing is left.
4. `//! zinc` ... `//! endzinc` - split out, since Zinc is a different
   language with its own parser.
5. `//! externalblock` / `//! external` / `//! i` - tool commands run by the
   editor when the map is saved (ObjectMerger and friends). They do not
   change the script, so they are collected for the caller and removed.

What is left is a list of segments, each `('vjass' | 'zinc', [SourceLine])`.
The directives that do change the script - `inject`, `endinject`,
`dovjassinit` and `loaddata` - stay in the stream for the parser.
"""
import os
import re

from .errors import SourceLine, Location, VJassError, VJassWarning

_DIRECTIVE = re.compile(r'^\s*//!\s*(\w+)\s*(.*?)\s*$')
_IMPORT = re.compile(
    r'^(?:(vjass|zinc|comment)\s+)?"([^"]*)"\s*$', re.I)
_TEXTMACRO = re.compile(r'^(\w+)(?:\s+takes\s+(.*?))?$', re.I)
_RUNTEXTMACRO = re.compile(r'^(optional\s+)?(\w+)\s*\((.*)\)$', re.I | re.S)

_DEBUG_LINE = re.compile(r'^(\s*)debug(?=\s)\s*')
_DEBUG_BLOCK = re.compile(
    r'^(?:(?:public|private|static|stub|constant)\s+)*(method|function)\b')

KEPT_DIRECTIVES = {'inject', 'endinject', 'dovjassinit', 'loaddata'}
MAX_MACRO_DEPTH = 64


class TextMacro(object):
    __slots__ = ('name', 'parameters', 'body', 'once', 'location')

    def __init__(self, name, parameters, body, once, location):
        self.name = name
        self.parameters = parameters
        self.body = body
        self.once = once
        self.location = location


class Preprocessed(object):
    """Result of preprocessing.

    segments
        `[(kind, [SourceLine])]` with kind 'vjass' or 'zinc', in file order.
    externals
        `[(location, tool, arguments)]` for each `//! external` command, and
        `[(location, tool, [lines])]` for each external block.
    imports
        Absolute paths of every imported file, in import order.
    macros
        name -> TextMacro, every macro defined.
    """

    def __init__(self):
        self.segments = []
        self.externals = []
        self.imports = []
        self.macros = {}
        self.warnings = []


def split_lines(text, file):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if text.startswith('﻿'):
        text = text[1:]
    return [SourceLine(t, file, n) for n, t in enumerate(text.split('\n'), 1)]


def _directive(line):
    match = _DIRECTIVE.match(line.text)
    if not match:
        return None, None
    return match.group(1).lower(), match.group(2)


def _loc(line):
    return Location(line.file, line.line)


class Preprocessor(object):

    def __init__(self, options, read_file=None):
        self.options = options
        self.read_file = read_file or _read_text
        self.result = Preprocessed()
        self._imported = set()

    # -- 1. imports ---------------------------------------------------------

    def _resolve_import(self, name, origin):
        candidates = []
        if os.path.isabs(name):
            candidates.append(name)
        else:
            if origin and origin != '<input>':
                candidates.append(os.path.join(os.path.dirname(origin), name))
            for folder in self.options.lookup_folders:
                candidates.append(os.path.join(folder, name))
            candidates.append(name)
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return None

    def expand_imports(self, lines, depth=0):
        if depth > 32:
            raise VJassError('imports nested too deeply (an import cycle?)',
                             _loc(lines[0]) if lines else None, 'preprocess')
        out = []
        for line in lines:
            kind, rest = _directive(line)
            if kind != 'import':
                out.append(line)
                continue
            match = _IMPORT.match(rest)
            if not match:
                raise VJassError('malformed //! import, expected '
                                 '//! import [zinc|vjass] "file"', _loc(line),
                                 'preprocess')
            mode = (match.group(1) or 'vjass').lower()
            path = self._resolve_import(match.group(2), line.file)
            if path is None:
                raise VJassError(f'Unable to find file: {match.group(2)}',
                                 _loc(line), 'preprocess')
            # JassHelper imports a file once however often it is named, which
            # is what lets several libraries import a shared dependency.
            key = os.path.normcase(path)
            if key in self._imported:
                continue
            self._imported.add(key)
            self.result.imports.append(path)
            if mode == 'comment':
                continue
            imported = split_lines(self.read_file(path), path)
            imported = self.expand_imports(imported, depth + 1)
            if mode == 'zinc':
                out.append(SourceLine('//! zinc', path, 0))
                out.extend(imported)
                out.append(SourceLine('//! endzinc', path, 0))
            else:
                out.extend(imported)
        return out

    # -- 2. novjass ----------------------------------------------------------

    @staticmethod
    def drop_novjass(lines):
        out = []
        start = None
        for line in lines:
            kind, _ = _directive(line)
            if start is None:
                if kind == 'novjass':
                    start = line
                    continue
                if kind == 'endnovjass':
                    raise VJassError('//! endnovjass without //! novjass',
                                     _loc(line), 'preprocess')
                out.append(line)
            elif kind == 'endnovjass':
                start = None
        if start is not None:
            raise VJassError('//! novjass is never closed', _loc(start),
                             'preprocess')
        return out

    # -- 3. text macros --------------------------------------------------------

    def collect_macros(self, lines):
        macros = self.result.macros
        out = []
        current = None
        for line in lines:
            kind, rest = _directive(line)
            if current is None:
                if kind in ('textmacro', 'textmacro_once'):
                    match = _TEXTMACRO.match(rest)
                    if not match:
                        raise VJassError('malformed //! textmacro', _loc(line),
                                         'preprocess')
                    params = [p.strip() for p in (match.group(2) or '').split(',')
                              if p.strip()]
                    current = TextMacro(match.group(1), params, [],
                                        kind == 'textmacro_once', _loc(line))
                    continue
                if kind == 'endtextmacro':
                    raise VJassError('//! endtextmacro without //! textmacro',
                                     _loc(line), 'preprocess')
                out.append(line)
                continue
            if kind == 'endtextmacro':
                previous = macros.get(current.name)
                if previous is not None and not (previous.once and current.once):
                    raise VJassError(f'textmacro redeclared: {current.name}',
                                     current.location, 'preprocess',
                                     note=f'first declared at {previous.location}')
                if previous is None:
                    macros[current.name] = current
                current = None
                continue
            if kind in ('textmacro', 'textmacro_once'):
                raise VJassError('textmacro declared inside another textmacro',
                                 _loc(line), 'preprocess')
            current.body.append(line)
        if current is not None:
            raise VJassError(f'textmacro {current.name} is never closed',
                             current.location, 'preprocess')
        return out

    @staticmethod
    def split_macro_arguments(text, line):
        args = []
        i = 0
        n = len(text)
        while i < n:
            while i < n and text[i] in ' \t':
                i += 1
            if i >= n:
                break
            if text[i] == '"':
                j = i + 1
                value = []
                while j < n and text[j] != '"':
                    if text[j] == '\\' and j + 1 < n and text[j + 1] == '"':
                        value.append('"')
                        j += 2
                        continue
                    value.append(text[j])
                    j += 1
                if j >= n:
                    raise VJassError('unclosed string in runtextmacro arguments',
                                     _loc(line), 'preprocess')
                args.append(''.join(value))
                i = j + 1
            else:
                j = i
                while j < n and text[j] != ',':
                    j += 1
                args.append(text[i:j].strip())
                i = j
            while i < n and text[i] in ' \t':
                i += 1
            if i < n:
                if text[i] != ',':
                    raise VJassError('expected , between runtextmacro arguments',
                                     _loc(line), 'preprocess')
                i += 1
        return args

    def expand_macros(self, lines):
        macros = self.result.macros
        used_once = set()

        def expand(lines, depth):
            if depth > MAX_MACRO_DEPTH:
                raise VJassError('text macro expansion nested too deeply '
                                 '(a macro probably runs itself)',
                                 _loc(lines[0]) if lines else None, 'preprocess')
            out = []
            for line in lines:
                kind, rest = _directive(line)
                if kind != 'runtextmacro':
                    out.append(line)
                    continue
                match = _RUNTEXTMACRO.match(rest)
                if not match:
                    raise VJassError('malformed //! runtextmacro, expected '
                                     '//! runtextmacro NAME("arg", ...)',
                                     _loc(line), 'preprocess')
                optional, name, raw = match.groups()
                macro = macros.get(name)
                if macro is None:
                    if optional:
                        continue
                    raise VJassError(f'Unable to find textmacro: {name}',
                                     _loc(line), 'preprocess')
                if macro.once:
                    if name in used_once:
                        continue
                    used_once.add(name)
                args = self.split_macro_arguments(raw, line) if raw.strip() else []
                if len(args) != len(macro.parameters):
                    raise VJassError(
                        f'textmacro {name} takes {len(macro.parameters)} '
                        f'argument(s), {len(args)} given', _loc(line),
                        'preprocess')
                body = []
                for body_line in macro.body:
                    text = body_line.text
                    for param, value in zip(macro.parameters, args):
                        text = text.replace('$' + param + '$', value)
                    body.append(SourceLine(text, body_line.file, body_line.line))
                out.extend(expand(body, depth + 1))
            return out

        return expand(lines, 0)

    # -- 4/5. segments and tool directives -----------------------------------

    def split_segments(self, lines):
        segments = []
        current_kind = 'vjass'
        current = []
        zinc_start = None
        external_block = None
        for line in lines:
            kind, rest = _directive(line)
            if external_block is not None:
                if kind == 'endexternalblock':
                    self.result.externals.append(external_block)
                    external_block = None
                elif kind == 'i':
                    external_block[2].append(rest)
                continue
            if kind is None:
                current.append(line)
                continue
            if kind == 'zinc':
                if zinc_start is not None:
                    raise VJassError('Nested //! zinc', _loc(line), 'preprocess')
                segments.append((current_kind, current))
                current_kind, current, zinc_start = 'zinc', [], line
                continue
            if kind == 'endzinc':
                if zinc_start is None:
                    raise VJassError('Unexpected: //! endzinc', _loc(line),
                                     'preprocess')
                segments.append((current_kind, current))
                current_kind, current, zinc_start = 'vjass', [], None
                continue
            if kind == 'externalblock':
                tool = rest.split()
                external_block = (_loc(line), tool[-1] if tool else '', [])
                continue
            if kind == 'external':
                parts = rest.split(None, 1)
                self.result.externals.append(
                    (_loc(line), parts[0] if parts else '',
                     parts[1] if len(parts) > 1 else ''))
                continue
            if kind in KEPT_DIRECTIVES:
                current.append(line)
                continue
            if kind == 'i':
                continue
            # Anything else after `//!` is an ordinary comment as far as the
            # script goes, but it is almost always a typo for a directive.
            self.result.warnings.append(VJassWarning(
                f'unknown preprocessor directive //! {kind} ignored', _loc(line)))
        if zinc_start is not None:
            raise VJassError('Missing //! endzinc', _loc(zinc_start), 'preprocess')
        if external_block is not None:
            raise VJassError('//! externalblock is never closed',
                             external_block[0], 'preprocess')
        segments.append((current_kind, current))
        self.result.segments = [(k, self.apply_debug(s) if k == 'vjass' else s)
                                for k, s in segments if s]
        return self.result.segments

    def apply_debug(self, lines):
        """`debug` is a line prefix in vJass, not a statement modifier: a
        release build drops every line that starts with it - `debug if`,
        `debug endif` and all - and a debug build drops just the word. This is
        what lets a whole `if` block exist only in debug builds."""
        out = []
        debug = self.options.debug
        skip_until = None
        for line in lines:
            if skip_until is not None:
                out.append(SourceLine('', line.file, line.line))
                if skip_until.match(line.text):
                    skip_until = None
                continue
            match = _DEBUG_LINE.match(line.text)
            if match is None:
                out.append(line)
            elif debug:
                out.append(SourceLine(match.group(1) + line.text[match.end():],
                                      line.file, line.line))
            else:
                # `debug method ...` / `debug function ...`: the whole
                # declaration exists only in debug builds.
                rest = line.text[match.end():]
                block = _DEBUG_BLOCK.match(rest)
                if block is not None:
                    skip_until = re.compile(r'^\s*end' + block.group(1) +
                                            r'\b')
                out.append(SourceLine('', line.file, line.line))
        return out

    def run(self, source, file='<input>'):
        lines = split_lines(source, file)
        lines = self.expand_imports(lines)
        lines = self.drop_novjass(lines)
        lines = self.collect_macros(lines)
        lines = self.expand_macros(lines)
        self.split_segments(lines)
        return self.result


def _read_text(path):
    with open(path, 'rb') as fh:
        data = fh.read()
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('utf-8', errors='replace')


def preprocess(source, file='<input>', options=None, read_file=None):
    from .options import CompileOptions
    return Preprocessor(options or CompileOptions(), read_file).run(source, file)
