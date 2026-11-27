"""
A cJass compiler (#227): cJass, the C-like JASS dialect of AdicHelper, to
JASS with vJass blocks, and on to plain JASS through `wc3files.vjass`, the
way AdicHelper runs before JassHelper.

It follows the cJass 1.4.0.2 manual (https://cjass.xgm.guru/manual-en). The
features covered and not covered are listed in docs/vjass.md.
"""

from .preprocessor import CJassError, Preprocessor, tokenize, LIB_DIR
from .syntax import compile_statements


def cjass_to_vjass(source, filename='<input>', include_paths=(), debug=False,
                   war3ver='WAR3VER_24'):
    """The JASS (with vJass blocks) that the cJass `source` stands for."""
    preprocessor = Preprocessor(include_paths=include_paths, debug=debug, war3ver=war3ver)
    tokens = preprocessor.run(tokenize(source, filename))
    return compile_statements(tokens, filename, debug=debug)


def compile_cjass(source, filename='<input>', include_paths=(), debug=False,
                  war3ver='WAR3VER_24', vjass=True, **options):
    """cJass to plain JASS: cjass_to_vjass, then the vJass compiler (with
    `options`, as VJassCompiler takes them). With vjass=False, the
    intermediate text is returned as it is."""
    text = cjass_to_vjass(source, filename, include_paths, debug, war3ver)
    if not vjass:
        return text
    from wc3files.vjass import VJassCompiler
    options.setdefault('debug', debug)
    return VJassCompiler(**options).compile(text, filename).jass


__all__ = ['CJassError', 'cjass_to_vjass', 'compile_cjass', 'LIB_DIR']
