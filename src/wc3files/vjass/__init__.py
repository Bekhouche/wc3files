"""vJass and Zinc compiler - a pure-Python JassHelper.

    from wc3files.vjass import VJassCompiler

    result = VJassCompiler(optimize=True).compile_file('war3map.j')
    result.write('war3map.compiled.j')

The output is plain JASS that Warcraft III runs as is. Generated names follow
JassHelper's (`s__Struct_member`, `si__Struct`, `Library__private`), so code
written against JassHelper's output keeps working.

JassHelper is Copyright (c) 2006 Victor Hugo Soliz Kuncar (Vexorian), released
under the zlib license. This package is an independent reimplementation that
follows its documented behaviour and output conventions; it contains no code
from it.
"""
from .api import VJassCompiler, CompileResult, compile_vjass, compile_file
from .errors import VJassError, VJassWarning, Location
from .options import CompileOptions
from .preprocessor import preprocess

__all__ = ['VJassCompiler', 'CompileResult', 'CompileOptions', 'VJassError',
           'VJassWarning', 'Location', 'compile_vjass', 'compile_file',
           'preprocess']
