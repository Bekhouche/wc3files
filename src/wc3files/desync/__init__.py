"""
The desync folders the game writes in `Documents\\Warcraft III\\Errors`
(#258): `Desync.txt` (`DesyncReport`), the `.log` file (`DesyncLog`), a
summary of a folder and the first difference between two players' logs.
See docs/cli.md (`desync-info`, `desync-diff`) for what was measured.
"""

from wc3files.desync.log import DesyncLog, code_name, diff_logs
from wc3files.desync.report import DesyncReport
from wc3files.desync.folder import summarize_folder, find_files

__all__ = ['DesyncLog', 'DesyncReport', 'code_name', 'diff_logs', 'summarize_folder',
           'find_files']
