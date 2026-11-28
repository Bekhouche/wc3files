"""
A desync folder as a whole (#258): `Desync.txt`, the `.log` file and the
replay, `<machine>_<date>_<time>_replay.w3g` (or `TempReplay.w3g`).
"""

import glob
import os
import re

from wc3files.desync.log import DesyncLog, code_name
from wc3files.desync.report import DesyncReport

_FOLDER_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2}) (\d{2})\.(\d{2})\.(\d{2})\b')
# Desync.txt's checksums and the log category each is found in at the
# desync turn, on the 64 folders measured; the others were not found.
CHECKSUM_CATEGORIES = {
    'custom unit': 'cust', 'custom item': 'cust', 'custom destructable': 'cust',
    'custom ability': 'cust', 'custom upgrade': 'cust', 'custom misc': 'misc',
    'game': 'gwar', 'rand': 'rand',
}


def find_files(folder):
    """{'report', 'log', 'replay'}: the paths of a desync folder's files, or
    None for one it does not have."""
    def first(pattern):
        found = sorted(glob.glob(os.path.join(glob.escape(folder), pattern)))
        return found[0] if found else None
    return {'report': first('Desync.txt'), 'log': first('*_Desync.log'),
            'replay': first('*_replay.w3g') or first('TempReplay.w3g')}


def summarize_folder(folder, replay=True):
    """A summary of a desync folder: the date from the folder's name, what
    Desync.txt says (build, map, game id, desync turn, checksums), the
    records of the log, whether each checksum is found in the log's blocks
    at the desync turn, and the players and leaves of the replay."""
    files = find_files(folder)
    if not files['report'] and not files['log']:
        raise ValueError(f'{folder}: no Desync.txt or desync .log file')
    out = {'folder': os.path.basename(os.path.normpath(folder)), 'date': None}
    found = _FOLDER_TIME.match(out['folder'])
    if found:
        out['date'] = f'{found.group(1)} {found.group(2)}:{found.group(3)}:{found.group(4)}'
    report = DesyncReport().read(files['report']) if files['report'] else None
    if report is not None:
        fields = report.fields
        out.update({
            'build': report.build, 'game': report.game, 'turn': report.turn,
            'map': fields.get('Map'), 'map_hash': fields.get('MapHash'),
            'game_type': fields.get('GameType'), 'player_count': _int(fields.get('PlayerCount')),
            'checksums': {name: f'{value:08x}' for name, value in report.checksums.items()},
        })
    log = DesyncLog().read(files['log']) if files['log'] else None
    if log is not None:
        records = log.records()
        out['records'] = [{'turn': r['turn'], 'turns': r['turns'], 'blocks': len(r['blocks'])}
                          for r in records]
        if report is not None and report.turn is not None:
            at = {}
            for block in log.blocks:
                if block['turn'] == report.turn:
                    at.setdefault(code_name(block['category']), set()).add(block['checksum'])
            out['checksums_in_log'] = {
                name: name in CHECKSUM_CATEGORIES and
                value in at.get(CHECKSUM_CATEGORIES[name], ())
                for name, value in report.checksums.items() if value}
    if replay and files['replay']:
        from wc3files.w3g.replay_file import ReplayFile
        from wc3files.w3g.summary import summarize
        try:
            data = ReplayFile()
            data.read(files['replay'])
            summary = summarize(data.get_data(), chat=False)
        except Exception as error:  # a replay cut off by the desync, read as far as it goes
            out['replay_error'] = str(error)
        else:
            out['replay_length_ms'] = summary['length_ms']
            out['players'] = [{'slot': p['slot'], 'name': p['name'], 'team': p['team'],
                               'observer': p['observer'], 'host': p['host']}
                              for p in summary['players']]
            out['leaves'] = [{'time_ms': leave['time_ms'], 'name': leave['name'],
                              'slot': leave['slot'], 'reason': leave['reason']}
                             for leave in summary['leaves']]
    return out


def _int(value):
    return int(value) if value and value.isdigit() else None
