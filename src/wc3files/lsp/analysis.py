"""
What the language server knows about a document: its language, and its
diagnostics, as `wc3files jass --check` reports them.

Positions follow the protocol: lines and characters count from 0. The
checkers give a 1-based line and no column, so a finding covers its whole
line; a parse or lex error has a column too and covers the token there.
"""

JASS = 'jass'
LUA = 'lua'

# The protocol's DiagnosticSeverity.
ERROR, WARNING, INFORMATION = 1, 2, 3
_SEVERITY = {'issue': ERROR, 'warning': WARNING, 'note': INFORMATION}

_EXTENSIONS = {'.j': JASS, '.ai': JASS, '.lua': LUA}
_LANGUAGE_IDS = {'jass': JASS, 'lua': LUA}


def language_of(uri, language_id=None):
    """JASS, LUA or None for a document, from the editor's language id or
    else the file's extension."""
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


def diagnostics(language, text):
    """The diagnostics of a document of `language`; none for a language this
    server does not check."""
    if language == JASS:
        return jass_diagnostics(text)
    if language == LUA:
        return lua_diagnostics(text)
    return []
