"""
Grim Extension Pack's external tools, run without the World Editor.

vJass maps call ObjectMerger, FileImporter, FileExporter and ConstantMerger
from their scripts (`//! external ...` and `//! externalblock` blocks of Lua)
to generate objects, import files and write helper files when the map is
saved. `wc3files.vjass` collects those calls; this package runs them:

     from wc3files.vjass import VJassCompiler
     from wc3files.grimex import ExternalRunner

     result = VJassCompiler().compile_file('war3map.j')
     with ExternalRunner(map_files) as runner:
          done = runner.run(result.externals)
     for error in done.errors:
          print(error)                # war3map.j:120: Invalid object field anaam
     new_files = done.changed_files() # {'war3map.w3a': b'...'}

The Lua runs in `wc3files.scripts.lua_vm` (Lua 5.1, as the real tools embed),
in a sandbox; see `runner` for its layout.
"""

from .objects import ObjectData, ObjectMergerError, resolve_type
from .tools import (
     ObjectMerger, FileImporter, FileExporter, ConstantMerger, ToolContext, ToolError,
     split_arguments, TOOLS, UNSUPPORTED_TOOLS,
)
from .runner import ExternalRunner, ExternalsResult, ExternalError, DEFAULT_INSTALL

__all__ = [
     'ObjectData', 'ObjectMergerError', 'resolve_type',
     'ObjectMerger', 'FileImporter', 'FileExporter', 'ConstantMerger', 'ToolContext',
     'ToolError', 'split_arguments', 'TOOLS', 'UNSUPPORTED_TOOLS',
     'ExternalRunner', 'ExternalsResult', 'ExternalError', 'DEFAULT_INSTALL',
]
