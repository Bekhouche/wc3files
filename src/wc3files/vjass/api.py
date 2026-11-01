"""Public entry points: `VJassCompiler` and the `compile_*` helpers."""
import os

from .compiler import Compiler
from .emitter import emit
from .errors import VJassError
from .gamedata import load_api
from .options import CompileOptions
from .preprocessor import Preprocessor, _read_text


class CompileResult(object):
    """What a compilation produced.

    jass
        The compiled script, plain JASS text.
    warnings
        `VJassWarning` objects (unknown directives, a missing main, ...).
    externals
        `//! external` commands and external blocks, which the World Editor
        runs at save time (ObjectMerger and friends). They are not run here;
        a build pipeline that wants them can.
    imports
        Every file pulled in by `//! import`.
    """

    def __init__(self, jass, program, warnings, externals, imports, options):
        self.jass = jass
        self._program = program
        self.warnings = warnings
        self.externals = externals
        self.imports = imports
        self.options = options

    @property
    def program(self):
        """The output as a `wc3files.scripts.ir.Program`, parsed back from the
        text so it is exactly what the rest of this package works with."""
        from wc3files.scripts.jass_parser import parse
        return parse(self.jass)

    def validate(self):
        """Check the output with `wc3files.scripts.check_program` against the
        same common.j/Blizzard.j it was compiled against."""
        from wc3files.scripts.checker import check_program
        return check_program(self.program)

    def write(self, path):
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(self.jass)
        return path

    def __str__(self):
        return self.jass


class VJassCompiler(object):
    """Compile vJass and Zinc to plain JASS - what JassHelper does.

        compiler = VJassCompiler(debug=False, optimize=True)
        result = compiler.compile_file('war3map.j')
        result.write('out.j')

    Keyword arguments are `CompileOptions` fields; pass `options=` to reuse an
    options object, or `conf=` to read a JassHelper `jasshelper.conf`.
    """

    def __init__(self, options=None, conf=None, **kwargs):
        if conf is not None:
            options = CompileOptions.from_conf(conf, **kwargs)
        elif options is None:
            options = CompileOptions(**kwargs)
        elif kwargs:
            options = options.copy(**kwargs)
        self.options = options

    def _api(self):
        o = self.options
        common = o.common_source
        blizzard = o.blizzard_source
        if common is None and o.common_j:
            common = _read_text(o.common_j)
        if blizzard is None and o.blizzard_j:
            blizzard = _read_text(o.blizzard_j)
        return load_api(common, blizzard)

    def compile(self, source, filename='<input>'):
        """Compile vJass source text. Raises `VJassError` on the first
        error."""
        options = self.options
        pre = Preprocessor(options).run(source, filename)
        compiler = Compiler(options, self._api())
        compiler.warnings.extend(pre.warnings)
        program = compiler.compile(pre)
        text = emit(program)
        return CompileResult(text, program, compiler.warnings, pre.externals,
                             pre.imports, options)

    def compile_file(self, path, output=None):
        """Compile a file. `//! import` paths resolve relative to it. With
        `output`, the result is written there as well."""
        result = self.compile(_read_text(path), os.path.abspath(path))
        if output is not None:
            result.write(output)
        return result

    def compile_files(self, paths, output=None):
        """Compile several files as one script, in the given order (the way a
        project of separate library files is usually laid out)."""
        parts = []
        for path in paths:
            parts.append(f'//! import "{os.path.abspath(path)}"')
        result = self.compile('\n'.join(parts) + '\n', os.getcwd())
        if output is not None:
            result.write(output)
        return result

    def compile_map(self, map_path, output_path=None):
        """Compile the `war3map.j` inside a map and write the map back with the
        compiled script - what saving in the World Editor does with JassHelper
        enabled. The map is rewritten in place when `output_path` is None.
        Returns the CompileResult."""
        from wc3files.w3x import W3xFile
        files = _read_map_files(map_path)
        name = _find_script(files)
        if name is None:
            raise VJassError(f'{map_path} has no war3map.j (a Lua map has no '
                             'vJass to compile)', phase='compile')
        source = files[name].decode('utf-8-sig', errors='replace')
        result = self.compile(source, os.path.join(map_path, name))
        files[name] = result.jass.encode('utf-8')
        target = output_path or map_path
        if os.path.isdir(target) or (output_path is None and
                                     os.path.isdir(map_path)):
            # A folder map (or a folder as the destination): write files out.
            for member, data in files.items():
                path = os.path.join(target, *member.split('\\'))
                os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                with open(path, 'wb') as fh:
                    fh.write(data)
        else:
            W3xFile().build_from_files(files, target)
        return result


_STALE = ('(listfile)', '(attributes)', '(signature)')


def _read_map_files(map_path):
    files = {}
    if os.path.isdir(map_path):
        for folder, _, names in os.walk(map_path):
            for filename in names:
                full = os.path.join(folder, filename)
                rel = os.path.relpath(full, map_path).replace(os.sep, '\\')
                if rel in _STALE:
                    continue
                with open(full, 'rb') as fh:
                    files[rel] = fh.read()
        return files
    from wc3files.w3x import W3xFile
    w3x = W3xFile()
    w3x.read(map_path)
    if w3x.protected:
        raise VJassError(f'{map_path} has no (listfile); its files cannot be '
                         'listed to repack it', phase='compile')
    for member, data in w3x.files.items():
        if member in _STALE or data is None:
            continue
        files[member] = data
    return files


def _find_script(files):
    for name in files:
        normalized = name.replace('/', '\\').lower()
        if normalized in ('war3map.j', 'scripts\\war3map.j'):
            return name
    return None


def compile_vjass(source, filename='<input>', **options):
    """Compile vJass source text to JASS text. Shorthand for
    `VJassCompiler(**options).compile(source).jass`."""
    return VJassCompiler(**options).compile(source, filename).jass


def compile_file(path, output=None, **options):
    return VJassCompiler(**options).compile_file(path, output)
