"""The game API a script compiles against: natives and types from
`common.j`, functions and globals from `Blizzard.j`.

The compiler needs them to know what a call returns (`GetTriggerUnit()` is a
`unit`, so `GetTriggerUnit().x` is an error, not a member access) and which
names are taken. Parsed once per distinct source and cached.
"""
import os

from wc3files.scripts.jass_parser import parse as parse_jass

_GAMEDATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), 'scripts', 'gamedata')

PRIMITIVES = ('integer', 'real', 'boolean', 'string', 'code', 'handle')

_cache = {}


class ApiFunction(object):
    __slots__ = ('name', 'params', 'returns', 'is_native', 'is_constant')

    def __init__(self, name, params, returns, is_native, is_constant=False):
        self.name = name
        self.params = params          # [(type, name)]
        self.returns = returns
        self.is_native = is_native
        self.is_constant = is_constant


class ApiGlobal(object):
    __slots__ = ('name', 'type', 'is_array', 'is_constant')

    def __init__(self, name, type, is_array, is_constant):
        self.name = name
        self.type = type
        self.is_array = is_array
        self.is_constant = is_constant


class GameApi(object):
    """Everything `common.j` + `Blizzard.j` declare. `types` maps each type to
    its parent (`unit` -> `widget`, `widget` -> `handle`)."""

    def __init__(self):
        self.types = {name: None for name in PRIMITIVES}
        self.functions = {}
        self.globals = {}

    def add_program(self, program):
        for type_def in program.type_defs:
            self.types[type_def.name] = type_def.base
        for var in program.globals:
            self.globals[var.name] = ApiGlobal(var.name, var.type, var.is_array,
                                               var.is_constant)
        for native in program.natives:
            self.functions[native.name] = ApiFunction(
                native.name, [(p.type, p.name) for p in native.params],
                native.returns, True, native.is_constant)
        for function in program.functions:
            self.functions[function.name] = ApiFunction(
                function.name, [(p.type, p.name) for p in function.params],
                function.returns, False, function.is_constant)

    def is_handle(self, type_name):
        seen = set()
        while type_name is not None and type_name not in seen:
            if type_name == 'handle':
                return True
            seen.add(type_name)
            type_name = self.types.get(type_name)
        return False

    def extends(self, child, parent):
        seen = set()
        while child is not None and child not in seen:
            if child == parent:
                return True
            seen.add(child)
            child = self.types.get(child)
        return False


def _read(path):
    with open(path, encoding='utf-8-sig', errors='replace', newline='') as fh:
        return fh.read()


def load_api(common_source=None, blizzard_source=None, common_path=None,
             blizzard_path=None):
    if common_source is None:
        common_source = _read(common_path or os.path.join(_GAMEDATA, 'common.j'))
    if blizzard_source is None:
        blizzard_source = _read(blizzard_path or os.path.join(_GAMEDATA,
                                                              'blizzard.j'))
    key = (hash(common_source), len(common_source), hash(blizzard_source),
           len(blizzard_source))
    api = _cache.get(key)
    if api is None:
        api = GameApi()
        api.add_program(parse_jass(common_source))
        api.add_program(parse_jass(blizzard_source))
        _cache[key] = api
    return api
