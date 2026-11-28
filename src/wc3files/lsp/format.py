"""
Indentation for JASS and vJass (#404).

The JASS printer could reprint a script, but it drops comments and blank
lines. This sets only the indentation: each line is indented by the depth of
the blocks it is in, and its trailing spaces are removed; its text, the
comments, the blank lines and the line endings are kept.

A line is read by its first word, after any modifiers (`private`, `static`,
...): `function`, `if`, `loop`, `globals`, and in vJass `library`, `scope`,
`struct`, `interface`, `module` and `method` open a block, their `end`
words close it, and `else` and `elseif` sit at the depth of their `if`.
A `method` in an interface has no body and opens nothing, nor does a
`function interface`. The lines of a `/* */` comment, and of a `//!
textmacro`, `//! zinc` or `//! novjass` block, are kept as they are.
"""

import re

_OPENS = frozenset(('function', 'if', 'loop', 'globals', 'library', 'library_once', 'scope',
                    'struct', 'interface', 'module', 'method'))
_CLOSES = {'endfunction': 'function', 'endif': 'if', 'endloop': 'loop',
           'endglobals': 'globals', 'endlibrary': 'library', 'endscope': 'scope',
           'endstruct': 'struct', 'endinterface': 'interface', 'endmodule': 'module',
           'endmethod': 'method'}
_MODIFIERS = frozenset(('private', 'public', 'static', 'stub', 'constant', 'readonly', 'debug'))
_WORDS = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# Blocks whose lines are kept as they are, by the directive that opens them.
_VERBATIM = {'textmacro': 'endtextmacro', 'textmacro_once': 'endtextmacro',
             'zinc': 'endzinc', 'novjass': 'endnovjass'}
_DIRECTIVE = re.compile(r'\s*//!\s*(\w+)')


def _words(code):
    """The first words of a line of code, modifiers left out."""
    words = _WORDS.findall(code.split('//', 1)[0])
    while words and words[0] in _MODIFIERS:
        words = words[1:]
    return words


def indents(text):
    """The depth of each line of `text` (split on '\\n'), or None for a line
    kept as it is."""
    return _scan(text)[0]


def _scan(text):
    """(depths, the lines inside a textmacro, zinc or novjass block)."""
    depths = []
    directive_lines = set()
    stack = []  # the kinds of the blocks open
    verbatim = None  # the directive that ends the block being kept
    in_comment = False
    for raw in text.split('\n'):
        line = raw.rstrip('\r')
        directive = _DIRECTIVE.match(line)
        if in_comment:
            depths.append(None)
            if '*/' in line:
                in_comment = False
            continue
        if verbatim is not None:
            depths.append(None)
            if directive and directive.group(1).lower() == verbatim:
                verbatim = None
            else:
                directive_lines.add(len(depths) - 1)
            continue
        if directive:
            depths.append(len(stack))
            verbatim = _VERBATIM.get(directive.group(1).lower())
            continue
        stripped = line.strip()
        if stripped.startswith('/*') and '*/' not in stripped:
            depths.append(len(stack))
            in_comment = True
            continue
        words = _words(stripped)
        first = words[0] if words else ''
        if first in _CLOSES:
            # Close up to the block it ends, so a missing end word does not
            # push the rest of the file in.
            kind = _CLOSES[first]
            kinds = ('library', 'library_once') if kind == 'library' else (kind,)
            if any(k in kinds for k in stack):
                while stack and stack[-1] not in kinds:
                    stack.pop()
                stack.pop()
            depths.append(len(stack))
            continue
        if first in ('else', 'elseif') and stack and stack[-1] == 'if':
            depths.append(len(stack) - 1)
            continue
        depths.append(len(stack))
        if first in _OPENS:
            if first == 'function' and len(words) > 1 and words[1] == 'interface':
                continue  # function interface: a declaration
            if first == 'method' and stack and stack[-1] == 'interface':
                continue  # an interface's method has no body
            stack.append('library' if first == 'library_once' else first)
    return depths, directive_lines


def format_lines(text, options=None, first=0, last=None):
    """(line, old length, new text) for each line from `first` to `last`
    (0-based, inclusive) whose indentation or trailing spaces change, or []
    for a document that does not lex."""
    depths, directive_lines = _scan(text)
    lines = text.split('\n')
    # The body of a textmacro, zinc or novjass block is not vJass.
    if not _lexes(['' if n in directive_lines else line for n, line in enumerate(lines)]):
        return []
    options = options or {}
    unit = ' ' * int(options.get('tabSize', 4)) if options.get('insertSpaces', True) else '\t'
    last = len(lines) - 1 if last is None else min(last, len(lines) - 1)
    out = []
    for number, depth in enumerate(depths):
        if number < first or number > last or depth is None:
            continue
        line = lines[number].rstrip('\r')
        stripped = line.strip()
        new = unit * depth + stripped if stripped else ''
        if new != line:
            out.append((number, len(line), new))
    return out


def _lexes(lines):
    """Whether `lines` lex as vJass, which covers JASS too."""
    from wc3files.vjass.errors import SourceLine, VJassError
    from wc3files.vjass.lexer import tokenize
    try:
        tokenize([SourceLine(line.rstrip('\r'), None, n) for n, line in enumerate(lines, 1)])
    except VJassError:
        return False
    return True
