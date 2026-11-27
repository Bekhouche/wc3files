"""
What the language server knows about a document: its language, and its
diagnostics, as `wc3files jass --check` reports them.

Positions follow the protocol: lines and characters count from 0. The
checkers give a 1-based line and no column, so a finding covers its whole
line; a parse or lex error has a column too and covers the token there.
"""

import re

JASS = 'jass'
LUA = 'lua'
VJASS = 'vjass'
ZINC = 'zinc'

# The protocol's DiagnosticSeverity.
ERROR, WARNING, INFORMATION = 1, 2, 3
_SEVERITY = {'issue': ERROR, 'warning': WARNING, 'note': INFORMATION}

_EXTENSIONS = {'.j': JASS, '.ai': JASS, '.lua': LUA, '.vj': VJASS, '.zn': ZINC}
_LANGUAGE_IDS = {'jass': JASS, 'lua': LUA, 'vjass': VJASS, 'zinc': ZINC}

# A .j file with any of these at the start of a line is vJass (#392); a plain
# war3map.j has none, and keeps the plain JASS check, which is faster.
_VJASS_MARKERS = re.compile(
    r'^[ \t]*(?:(?:library|library_once|scope|struct|module|interface)\b'
    r'|//![ \t]*(?:zinc|textmacro|import|novjass)\b)', re.MULTILINE)


def effective_language(language, text):
    """The language a document is checked as: JASS that has vJass blocks
    is checked as vJass."""
    if language == JASS and _VJASS_MARKERS.search(text):
        return VJASS
    return language


def language_of(uri, language_id=None):
    """JASS, LUA, VJASS, ZINC or None for a document, from the editor's
    language id or else the file's extension."""
    if language_id and language_id.lower() in _LANGUAGE_IDS:
        return _LANGUAGE_IDS[language_id.lower()]
    path = uri.lower().split('?', 1)[0]
    for extension, language in _EXTENSIONS.items():
        if path.endswith(extension):
            return language
    return None


def _line_range(lines, line):
    """The range of 1-based `line`, clamped to the document."""
    index = min(max((line or 1) - 1, 0), max(len(lines) - 1, 0))
    text = lines[index] if lines else ''
    start = len(text) - len(text.lstrip())
    return {'start': {'line': index, 'character': start},
            'end': {'line': index, 'character': len(text)}}


def _error_range(lines, error):
    """The range of a parse or lex error: its token, or its line."""
    line, col = getattr(error, 'line', None), getattr(error, 'col', None)
    if not line or not col:
        return _line_range(lines, line)
    token = getattr(error, 'token', None)
    width = max(len(str(getattr(token, 'value', '') or '')), 1)
    start = {'line': line - 1, 'character': col - 1}
    return {'start': start, 'end': {'line': line - 1, 'character': col - 1 + width}}


def _diagnostic(range_, severity, message):
    return {'range': range_, 'severity': severity, 'source': 'wc3files', 'message': message}


def _parse_error(lines, error):
    message = str(error)
    # The lexers and parsers end the message with the position, which the
    # range already shows.
    for marker in (' at line ',):
        if marker in message:
            message = message[:message.rindex(marker)]
    return [_diagnostic(_error_range(lines, error), ERROR, message)]


def _findings(lines, findings):
    return [_diagnostic(_line_range(lines, f.get('line')), _SEVERITY.get(f.get('severity'), WARNING),
                        f.get('message', ''))
            for f in findings]


def jass_diagnostics(text):
    from wc3files.scripts.checker import check_program
    from wc3files.scripts.jass_lexer import JassLexError
    from wc3files.scripts.jass_parser import JassParseError, parse
    from wc3files.scripts.leaks import find_leaks
    lines = text.splitlines()
    try:
        program = parse(text)
    except (JassLexError, JassParseError) as error:
        return _parse_error(lines, error)
    findings = check_program(program) + find_leaks(program)
    return _findings(lines, findings)


def lua_diagnostics(text):
    from wc3files.scripts.lua_checker import check_chunk
    from wc3files.scripts.lua_lexer import LuaLexError
    from wc3files.scripts.lua_parser import LuaParseError, parse
    lines = text.splitlines()
    try:
        chunk = parse(text)
    except (LuaLexError, LuaParseError) as error:
        return _parse_error(lines, error)
    return _findings(lines, check_chunk(chunk))


def vjass_diagnostics(text, path=None, zinc=False):
    """A vJass or Zinc document compiled with the vJass compiler: its compile
    error, or its warnings and the JASS checker's findings on the compiled
    code, each at the source line the compiler maps it to. A finding in code
    the compiler generated, with no source line, is left out. `path` is the
    file, so that `//! import` finds the files next to it."""
    from wc3files.scripts.checker import check_program
    from wc3files.scripts.leaks import find_leaks
    from wc3files.vjass import VJassCompiler, VJassError
    lines = text.splitlines()
    filename = path or '<input>'
    source, offset = text, 0
    if zinc:
        # A .zn file is Zinc throughout; the compiler takes Zinc in a block.
        source, offset = '//! zinc\n' + text + '\n//! endzinc\n', 1

    def here(location):
        """The 1-based line of `location` in this document, or None."""
        if location is None or not location.line:
            return None
        if location.file not in (None, filename, '<input>'):
            return None
        line = location.line - offset
        return line if 1 <= line <= max(len(lines), 1) else None

    try:
        result = VJassCompiler().compile(source, filename)
    except VJassError as error:
        line = here(error.location)
        message = error.message
        if line is None and error.location.line:
            message = f'{error.location}: {message}'  # in an imported file
        return [_diagnostic(_line_range(lines, line or 1), ERROR, message)]
    out = []
    for warning in result.warnings:
        line = here(warning.location)
        if line is not None:
            out.append(_diagnostic(_line_range(lines, line), WARNING, warning.message))
    program = result.program
    for finding in check_program(program) + find_leaks(program):
        if not finding.get('line'):
            continue
        line = here(result.source_location(finding['line']))
        if line is not None:
            out.append(_diagnostic(_line_range(lines, line),
                                   _SEVERITY.get(finding.get('severity'), WARNING),
                                   finding.get('message', '')))
    return out


def diagnostics(language, text, path=None):
    """The diagnostics of a document of `language`; none for a language this
    server does not check."""
    language = effective_language(language, text)
    if language == JASS:
        return jass_diagnostics(text)
    if language == LUA:
        return lua_diagnostics(text)
    if language in (VJASS, ZINC):
        return vjass_diagnostics(text, path, zinc=language == ZINC)
    return []
