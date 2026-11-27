"""
A multi-file Lua project bundled into one war3map.lua (#256).

The game's Lua runs one file and has no `require`, so tools such as Ceres
put a project's modules into the map script with a small loader. This does
the same: starting from an entry module, it follows every `require("name")`
with a literal name to `<root>/name.lua` or `<root>/name/init.lua` (dots
are folders), and writes each module as a function in a table, with a local
`require` that runs a module once and keeps what it returns, as Lua's own
does. The loader is a local of the script, so it replaces no global and the
Lua checker still checks the modules' global names.

Each module keeps its own source text, so its line numbers are those of the
bundle plus the line where it starts; a comment names the file above it.

`insert_bundle` puts the bundle into a map's war3map.lua in front of the
code the World Editor generated, between two marker comments, and starts
the entry module at the end of `main` (or when the script loads). Running
it again on the result replaces the bundle and the call, so the generated
part is kept as it is.
"""

import os
import re

from . import ir
from .lua_parser import parse

START = '--[[ wc3files bundle start ]]'
END = '--[[ wc3files bundle end ]]'
CALL = '--[[ wc3files bundle run ]]'

_LOADER = '''local __modules, __loaded = {}, {}
local function require(name)
    local value = __loaded[name]
    if value ~= nil then
        return value
    end
    local module = __modules[name]
    if module == nil then
        error("module '" .. tostring(name) .. "' not found in the bundle", 2)
    end
    __loaded[name] = true
    value = module(name)
    if value ~= nil then
        __loaded[name] = value
    end
    return __loaded[name]
end
'''


class BundleError(ValueError):
    pass


def _requires(node, found, dynamic):
    """Literal-name require calls under `node`; other require calls counted."""
    if isinstance(node, ir.LuaCallExpr) and isinstance(node.func, ir.VarExpr) \
            and node.func.name == 'require':
        if node.args and isinstance(node.args[0], ir.Literal) and node.args[0].kind == 'string':
            found.append(node.args[0].value)
        else:
            dynamic.append(node)
    if isinstance(node, ir.Node):
        for field in node._fields:
            _requires(getattr(node, field), found, dynamic)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _requires(item, found, dynamic)


def module_path(root, name):
    """The file a module name stands for, or None."""
    parts = name.split('.')
    if not all(parts) or any(p in ('..',) for p in parts):
        return None
    base = os.path.join(root, *parts)
    for path in (base + '.lua', os.path.join(base, 'init.lua')):
        if os.path.isfile(path):
            return path
    return None


def collect(entry, root):
    """The modules `entry` needs, in the order they are first required:
    [(name, path, source)]. `entry` is a module name or a .lua file under
    `root`. Raises BundleError for a module not found or not parsed; returns
    also the number of require calls whose name is not a literal."""
    root = os.path.abspath(root)
    if entry.lower().endswith('.lua'):
        path = os.path.abspath(entry if os.path.isabs(entry) else os.path.join(root, entry))
        if not os.path.isfile(path):
            path = os.path.abspath(entry)
        relative = os.path.relpath(path, root)
        if relative.startswith('..'):
            raise BundleError(f'{entry} is not under the source folder {root}')
        name = relative[:-4].replace(os.sep, '.')
        if name.endswith('.init'):
            name = name[:-5]
    else:
        name = entry
    modules, order, dynamic_total = {}, [], 0
    pending = [(name, None)]
    while pending:
        name, needed_by = pending.pop(0)
        if name in modules:
            continue
        path = module_path(root, name)
        if path is None:
            where = f' (required by {needed_by})' if needed_by else ''
            raise BundleError(f"module '{name}'{where} not found under {root}")
        with open(path, 'rb') as handle:
            source = handle.read().decode('utf-8-sig')
        try:
            chunk = parse(source)
        except Exception as error:
            raise BundleError(f'{path}: {error}') from error
        found, dynamic = [], []
        _requires(chunk, found, dynamic)
        dynamic_total += len(dynamic)
        modules[name] = (path, source)
        order.append(name)
        pending.extend((n, name) for n in found)
    return [(n, modules[n][0], modules[n][1]) for n in order], dynamic_total


def bundle(entry, root, run=True):
    """The bundle text for `entry` under `root`, and a report. With `run`,
    the entry module is required at the end of the bundle; otherwise only
    defined, for `insert_bundle` to start."""
    modules, dynamic = collect(entry, root)
    root = os.path.abspath(root)
    parts = [START, '', _LOADER]
    for name, path, source in modules:
        relative = os.path.relpath(path, root).replace(os.sep, '/')
        body = source if source.endswith('\n') else source + '\n'
        parts.append(f'-- {relative}\n__modules[{_quote(name)}] = function(...)\n{body}end\n')
    entry_name = modules[0][0]
    if run:
        parts.append(f'require({_quote(entry_name)})\n')
    parts.append(END)
    report = {'entry': entry_name, 'modules': [n for n, _, _ in modules],
              'files': [p for _, p, _ in modules], 'dynamic requires': dynamic}
    return '\n'.join(parts) + '\n', report


def _quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def strip_bundle(script):
    """`script` without a bundle `insert_bundle` put in it."""
    pattern = re.compile(re.escape(START) + r'.*?' + re.escape(END) + r'\n?', re.S)
    script = pattern.sub('', script, count=1)
    return '\n'.join(line for line in script.split('\n') if CALL not in line)


def insert_bundle(script, bundle_text, entry, at='main'):
    """`script` (a map's war3map.lua) with `bundle_text` (from `bundle(...,
    run=False)`) in front of it and the entry module started at the end of
    `main` (`at='main'`) or when the script loads (`at='load'`). A bundle
    inserted before is replaced."""
    script = strip_bundle(script.lstrip('﻿'))
    if at == 'load':
        call = f'require({_quote(entry)}) {CALL}\n'
        return bundle_text + call + script
    if at != 'main':
        raise ValueError(f"at is 'main' or 'load', not {at!r}")
    lines = script.split('\n')
    start = next((i for i, line in enumerate(lines)
                  if re.match(r'\s*function\s+main\s*\(\s*\)', line)), None)
    if start is None:
        raise BundleError('the map script has no function main(); '
                          "use at='load' (--at load) to start the bundle when the script loads")
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == 'end'
                and len(lines[i]) - len(lines[i].lstrip()) == indent), None)
    if end is None:
        raise BundleError('the end of function main() was not found')
    lines.insert(end, ' ' * (indent + 4) + f'require({_quote(entry)}) {CALL}')
    return bundle_text + '\n'.join(lines)
