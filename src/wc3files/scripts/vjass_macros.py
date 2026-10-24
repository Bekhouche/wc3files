"""vJass text macros - the preprocessor pass JassHelper runs first.

vJass is the extended JASS dialect JassHelper (Vexorian's compiler, bundled
with the game at `_retail_/x86_64/JassHelper/`) compiles down to plain JASS.
Its text macros are a pure textual substitution that has to happen before
anything else can parse the file:

    //! textmacro NEW_ARRAY_BASIC takes SUPER, FUNC, TYPE
    private struct $TYPE$s extends array
        method operator [] takes integer key returns $TYPE$
            return Load$FUNC$(ht, this, key)
        endmethod
    endstruct
    //! endtextmacro

    //! runtextmacro NEW_ARRAY_BASIC("Real", "Real", "real")

A `$NAME$` in the body is replaced by the matching argument, quotes stripped.
Bodies may run other macros, so expansion repeats until nothing is left -
bounded, because a macro that expands to itself would otherwise never stop.

**This is the preprocessor only, not a vJass compiler.** Expanding macros
leaves vJass behind, not JASS: `struct`, `method`, `library`, `scope`,
`module`, `implement`, `key`, `private`/`public` and operator overloading all
remain, and turning those into plain JASS - struct allocation, inheritance,
module injection, library dependency ordering, scope name-mangling - is the
bulk of what JassHelper does. See `vjass_scan` for reading that structure.

Directives handled: `textmacro`, `textmacro_once`, `endtextmacro`,
`runtextmacro`, and `runtextmacro optional` (which is silently skipped when
the macro is not defined, as JassHelper does). Other `//!` directives -
`import`, `inject`, `zinc`, `novjass` - are left untouched rather than
guessed at; a caller can see them in `directives`.
"""
import re

_TEXTMACRO = re.compile(
    r'^[ \t]*//!\s*(textmacro_once|textmacro)\s+(\w+)(?:\s+takes\s+(.*?))?\s*$',
    re.I)
_ENDTEXTMACRO = re.compile(r'^[ \t]*//!\s*endtextmacro\s*$', re.I)
_RUNTEXTMACRO = re.compile(
    r'^([ \t]*)//!\s*runtextmacro\s+(optional\s+)?(\w+)\s*\((.*)\)\s*$', re.I)
_OTHER_DIRECTIVE = re.compile(r'^[ \t]*//!\s*(\w+)', re.I)

# Arguments are comma-separated, quoted, and may themselves contain commas.
_ARGUMENT = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|([^,]+)')

MAX_DEPTH = 64

DQ = chr(34)
SQ = chr(39)


class MacroError(ValueError):
    pass


class TextMacro(object):
    __slots__ = ('name', 'parameters', 'body', 'once')

    def __init__(self, name, parameters, body, once=False):
        self.name = name
        self.parameters = parameters
        self.body = body
        self.once = once

    def __repr__(self):
        return (f'<TextMacro {self.name}({", ".join(self.parameters)}) '
                f'{len(self.body)} lines>')


def _split_arguments(text):
    """Split a runtextmacro argument list, stripping the quotes.

    The unquoted branch of the pattern is deliberately permissive so an
    argument written without quotes still works, but that makes it greedy
    enough to swallow a quoted one whole - which substituted `"integer"`,
    quotes and all, where the macro expected `integer`. Quotes are therefore
    stripped after matching, whichever branch produced the value.
    """
    args = []
    for match in _ARGUMENT.finditer(text):
        value = match.group(1) if match.group(1) is not None else (
            match.group(2) if match.group(2) is not None else match.group(3))
        if value is None:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in (DQ, SQ):
            value = value[1:-1]
        if value:
            args.append(value)
    return args


def collect(source):
    """Split source into its macro definitions and the rest of the text.

    Returns `(macros, remaining_lines, directives)`.
    """
    macros = {}
    remaining = []
    directives = []

    current = None
    body = []
    for number, line in enumerate(source.splitlines(), 1):
        if current is None:
            match = _TEXTMACRO.match(line)
            if match:
                kind, name, params = match.group(1), match.group(2), match.group(3)
                parameters = [p.strip() for p in (params or '').split(',') if p.strip()]
                current = TextMacro(name, parameters, [],
                                    once=kind.lower() == 'textmacro_once')
                body = []
                continue
            other = _OTHER_DIRECTIVE.match(line)
            if other and other.group(1).lower() not in (
                    'runtextmacro', 'textmacro', 'textmacro_once', 'endtextmacro'):
                directives.append((number, other.group(1), line.strip()))
            remaining.append(line)
            continue

        if _ENDTEXTMACRO.match(line):
            current.body = body
            macros[current.name] = current
            current = None
            continue
        body.append(line)

    if current is not None:
        raise MacroError(f'textmacro {current.name!r} is never closed '
                         f'with //! endtextmacro')
    return macros, remaining, directives


def _substitute(body, parameters, arguments, indent):
    mapping = {}
    for index, parameter in enumerate(parameters):
        mapping[parameter] = arguments[index] if index < len(arguments) else ''

    out = []
    for line in body:
        for parameter, value in mapping.items():
            line = line.replace('$' + parameter + '$', value)
        out.append(indent + line if line.strip() else line)
    return out


def expand(source, macros=None, max_depth=MAX_DEPTH, strict=False):
    """Expand every `runtextmacro` in `source`.

    Returns `(text, report)`. The report carries how many expansions ran, any
    macro names that were called but never defined, and the non-macro `//!`
    directives seen - so a caller can tell "nothing to expand" from "I could
    not expand it".
    """
    collected, lines, directives = collect(source)
    available = dict(macros or {})
    available.update(collected)

    report = {
        'defined': sorted(collected),
        'expansions': 0,
        'missing': [],
        'directives': directives,
        'depth': 0,
    }
    used_once = set()

    for depth in range(max_depth):
        out = []
        changed = False
        for line in lines:
            match = _RUNTEXTMACRO.match(line)
            if not match:
                out.append(line)
                continue

            indent, optional, name, raw_args = match.groups()
            macro = available.get(name)
            if macro is None:
                if optional:
                    # JassHelper skips an optional macro that does not exist.
                    changed = True
                    continue
                report['missing'].append(name)
                if strict:
                    raise MacroError(f'runtextmacro calls undefined macro {name!r}')
                out.append(line)
                continue

            if macro.once and name in used_once:
                changed = True
                continue
            used_once.add(name)

            out.extend(_substitute(macro.body, macro.parameters,
                                   _split_arguments(raw_args), indent))
            report['expansions'] += 1
            changed = True

        lines = out
        if not changed:
            report['depth'] = depth
            break
    else:
        raise MacroError(
            f'text macro expansion did not settle after {max_depth} passes - '
            f'a macro probably expands to itself')

    report['missing'] = sorted(set(report['missing']))
    return '\n'.join(lines), report


_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT = re.compile(r'//.*?$', re.M)


def strip_comments(text):
    """Remove JASS comments. Crude but adequate here: it does not track string
    literals, and a `//` inside a string would be cut too. Used only for
    scanning, never for producing output."""
    return _LINE_COMMENT.sub('', _BLOCK_COMMENT.sub('', text))


def unexpanded_placeholders(text, ignore_comments=True):
    """Any `$NAME$` left behind, which means a parameter was never supplied.

    Comments are skipped by default: a library's own API documentation
    routinely spells out `$TYPE$` to describe what a macro generates, and
    counting that as an unexpanded placeholder reports a problem where there
    is none (it is the only such hit across every real vJass file here).
    """
    if ignore_comments:
        text = strip_comments(text)
    return sorted(set(re.findall(r'\$(\w+)\$', text)))
