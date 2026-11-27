"""
Texture paths rewritten in models (#241), as Mdx Pather does.

`rewrite_texture_paths(model, rules)` goes through the model's textures and
gives each path to the rules in turn; the first that matches rewrites it.
A rule is (kind, old, new):

- 'exact': the whole path is `old`;
- 'prefix': the path starts with `old`, which is replaced by `new`;
- 'pattern': `old` is a regular expression searched in the path, and
  `new` its replacement (`\\1` and the like), with every match replaced.

The game reads paths without regard to case or to `\\` against `/`, and
'exact' and 'prefix' compare them the same way; a 'pattern' is matched
ignoring case. The new path keeps what the rule gives, separators included.
A replaceable texture (an empty path) is left alone, and so is a path that
would not fit the 260-byte field MDX keeps it in: it is reported instead.
"""
import re

from .batch import model_data

KINDS = ('exact', 'prefix', 'pattern')
FIELD = 260


def _canonical(path):
    return path.replace('/', '\\').lower()


def _apply(rule, path):
    kind, old, new = rule[:3]
    if kind == 'exact':
        return new if _canonical(path) == _canonical(old) else None
    if kind == 'prefix':
        if _canonical(path).startswith(_canonical(old)):
            return new + path[len(old):]
        return None
    regex = rule[3] if len(rule) > 3 else re.compile(old, re.IGNORECASE)
    result, count = regex.subn(new, path)
    return result if count else None


def compile_rules(rules):
    """Checked rules, each pattern compiled once."""
    out = []
    for rule in rules:
        kind, old, new = rule[:3]
        if kind not in KINDS:
            raise ValueError(f'a rule is one of {", ".join(KINDS)}, not {kind!r}')
        if kind == 'pattern':
            try:
                out.append((kind, old, new, re.compile(old, re.IGNORECASE)))
            except re.error as error:
                raise ValueError(f'bad pattern {old!r}: {error}') from None
        else:
            out.append((kind, old, new))
    return out


def rewrite_texture_paths(model, rules):
    """Rewrite the texture paths of `model` (a ModelFile or its data).
    Returns {'changed': [(old, new)], 'refused': [(old, new)]}."""
    model = model_data(model)
    rules = compile_rules(rules)
    changed, refused = [], []
    for texture in model.get('textures') or []:
        path = texture.get('path') or ''
        if not path:
            continue
        for rule in rules:
            new = _apply(rule, path)
            if new is None:
                continue
            if new != path:
                if len(new.encode('utf-8')) >= FIELD:
                    refused.append((path, new))
                else:
                    texture['path'] = new
                    changed.append((path, new))
            break
    return {'changed': changed, 'refused': refused}
