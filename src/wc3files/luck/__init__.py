"""
A Luck compiler (#228): the indentation-based language of LuckyParser, to
plain JASS. See compiler.py for what it covers, and docs/vjass.md.
"""

import os

from .compiler import (LuckError, LuckResult, compile_sources, merge_into_script, parse_luck,
                       normalize)

SOURCE_EXTENSIONS = ('.luck', '.j', '.jass', '.vj', '.vjass', '.zn', '.zinc', '.cj', '.cjass')


def compile_luck(source, filename='source.luck', debug=False):
    """One Luck source to JASS: a LuckResult (jass, init_function, warnings)."""
    return compile_sources([(filename, source)], debug=debug)


def project_sources(paths):
    """[(file, text)] for files and folders (every source file under a
    folder, in path order), as one program."""
    files = []
    for path in paths:
        if os.path.isdir(path):
            for folder, _, names in sorted(os.walk(path)):
                for name in sorted(names):
                    if name.lower().endswith(SOURCE_EXTENSIONS):
                        files.append(os.path.join(folder, name))
        else:
            files.append(path)
    sources = []
    for path in files:
        with open(path, 'rb') as handle:
            sources.append((path, handle.read().decode('utf-8-sig', 'replace')))
    return sources


def compile_project(paths, debug=False):
    return compile_sources(project_sources(paths), debug=debug)


__all__ = ['LuckError', 'LuckResult', 'compile_luck', 'compile_project', 'compile_sources',
           'merge_into_script', 'parse_luck', 'normalize', 'project_sources']
