"""Compiler options, and reading them from a JassHelper `jasshelper.conf`."""
import os


class CompileOptions(object):
    """Everything that changes what the compiler emits.

    debug
        Keep `debug` statements and set DEBUG_MODE to true, as JassHelper's
        debug mode does. Allocation failures and double frees also report a
        message in-game.
    optimize
        Run the inliner over the output (JassHelper's "optimize" option).
    max_array_size
        Size of one JASS array. 8191 matches JassHelper and every game version
        before 1.29; 32768 is the limit since 1.29. Struct instance limits,
        dynamic array storage and the point where a sized array needs more
        than one JASS array all follow from this.
    lookup_folders
        Extra folders searched by `//! import`, after the importing file's own
        folder (JassHelper's `[lookupfolders]`).
    auto_method_evaluate
        A method called above its declaration is routed through a trigger
        evaluation, as JassHelper does by default. False makes it an error
        (`[forcemethodevaluate]`).
    implicit_this
        Bare member names resolve inside methods (`set x = 1` meaning
        `set this.x = 1`). False is JassHelper's `[noimplicitthis]`.
    strict
        Make an undeclared name an error. By default it passes through as
        written with a warning, as JassHelper does (the JASS checker run
        afterwards reports it) - which a script compiled in parts needs.
    common_j / blizzard_j
        Paths (or source text via `common_source` / `blizzard_source`) of the
        game API the script compiles against. The bundled copies are used when
        none is given.
    """

    def __init__(self, debug=False, optimize=False, max_array_size=8191,
                 lookup_folders=None, auto_method_evaluate=True,
                 implicit_this=True, common_j=None, blizzard_j=None,
                 common_source=None, blizzard_source=None,
                 constant_overrides=None, strict=False):
        self.debug = debug
        self.optimize = optimize
        self.max_array_size = int(max_array_size)
        self.lookup_folders = list(lookup_folders or [])
        self.auto_method_evaluate = auto_method_evaluate
        self.implicit_this = implicit_this
        self.common_j = common_j
        self.blizzard_j = blizzard_j
        self.common_source = common_source
        self.blizzard_source = blizzard_source
        # name -> bool, for `static if` on constants a caller wants to force.
        self.constant_overrides = dict(constant_overrides or {})
        self.strict = strict

    @property
    def max_index(self):
        """Highest usable index of one JASS array, as JassHelper sees it
        (8190 for the classic 8191-slot array: slot 8191 is not addressable
        before 1.29)."""
        return self.max_array_size - 1

    def copy(self, **changes):
        new = CompileOptions.__new__(CompileOptions)
        new.__dict__.update(self.__dict__)
        new.lookup_folders = list(self.lookup_folders)
        new.constant_overrides = dict(self.constant_overrides)
        new.__dict__.update(changes)
        return new

    @classmethod
    def from_conf(cls, path, **overrides):
        """Read a JassHelper `jasshelper.conf`. Only the sections that change
        compilation are taken: `[lookupfolders]`, `[forcemethodevaluate]` /
        `[automethodevaluate]` and `[noimplicitthis]`. `[jasscompiler]`,
        `[externaltools]` and `[wewarlock]` configure programs this package
        does not run."""
        options = cls(**overrides)
        base = os.path.dirname(os.path.abspath(path))
        section = None
        with open(path, encoding='utf-8', errors='replace') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('//'):
                    continue
                if line.startswith('[') and line.endswith(']'):
                    section = line.lower()
                    if section == '[forcemethodevaluate]':
                        options.auto_method_evaluate = False
                    elif section == '[automethodevaluate]':
                        options.auto_method_evaluate = True
                    elif section == '[noimplicitthis]':
                        options.implicit_this = False
                    continue
                if section == '[lookupfolders]':
                    folder = line.strip('"')
                    if not os.path.isabs(folder):
                        folder = os.path.join(base, folder)
                    options.lookup_folders.append(folder)
        return options
