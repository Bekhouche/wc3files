"""
A map description for Hive Workshop or GitHub, from the map's own data (#253).

`map_summary(files)` collects what a submission page lists: the name,
author, description and recommended players from war3map.w3i (with their
TRIGSTR_ references resolved in war3map.wts), the players and forces, the
playable size, the tileset, and for a melee map the start locations and gold
mines placed in war3mapUnits.doo. `render(summary, fmt, template)` writes it
as BBCode, as the Hive Workshop's editor takes it, or as Markdown.

A template is a string.Template: `$name`, `$author`, `$description`,
`$players_recommended`, `$player_count`, `$players`, `$forces`, `$size`,
`$tileset`, `$melee` (the start locations and gold mines, empty for a map
that is not melee) and `$editor_version`. The lists are written in the
template's format.
"""
import math
import os
import re
import string
import tempfile

from wc3files.w3x.imports import find

PLAYER_TYPES = {1: 'User', 2: 'Computer', 3: 'Neutral', 4: 'Rescuable'}
RACES = {0: 'Selectable', 1: 'Human', 2: 'Orc', 3: 'Undead', 4: 'Night Elf'}
GOLD_MINE = 'ngol'
START_LOCATION = 'sloc'
# A gold mine this close to a start location (in game units) is that
# player's main base; the others are expansions.
MAIN_BASE_DISTANCE = 1200.0

BBCODE = """[CENTER][SIZE=6][B]$name[/B][/SIZE]
by $author[/CENTER]

[B]Players:[/B] $player_count ($players_recommended)
[B]Map size:[/B] $size
[B]Tileset:[/B] $tileset

[B]Description[/B]
$description

[B]Players and forces[/B]
$forces
$melee"""

MARKDOWN = """# $name

by $author

| | |
|---|---|
| Players | $player_count ($players_recommended) |
| Map size | $size |
| Tileset | $tileset |

## Description

$description

## Players and forces

$forces
$melee"""

TEMPLATES = {'bbcode': BBCODE, 'markdown': MARKDOWN}

_COLOR = re.compile(r'\|c[0-9A-Fa-f]{2}([0-9A-Fa-f]{6})(.*?)(?:\|r|$)', re.S)


def _read(cls, files, name, suffix):
    key = find(files, name)
    if key is None:
        return None
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(files[key])
        handle.close()
        reader = cls()
        reader.read(handle.name)
        return reader
    finally:
        os.remove(handle.name)


def _strings(files):
    from wc3files.table.wts_file import WtsFile
    key = find(files, 'war3map.wts')
    if key is None:
        return {}
    handler = WtsFile()
    handler.read(files[key])
    return handler.export_data()


def _resolve(text, strings):
    text = text or ''
    return re.sub(r'TRIGSTR_(\d+)', lambda m: strings.get(int(m.group(1)), m.group(0)), text)


def map_summary(files):
    """The data a map description lists, from a map's {name: bytes} members."""
    from wc3files.doo import MapUnitsFile
    from wc3files.w3e.data import MAIN_TILESETS
    from wc3files.w3i import W3iFile
    info = _read(W3iFile, files, 'war3map.w3i', '_war3map.w3i')
    if info is None:
        raise ValueError('the map has no war3map.w3i')
    data = info.get_data()
    strings = _strings(files)
    flags = W3iFile.decode_flags(data.get('flags', 0))
    players = []
    for player in data.get('players', []):
        players.append({
            'id': player.get('id'),
            'name': _resolve(player.get('name'), strings) or f"Player {player.get('id', 0) + 1}",
            'controller': PLAYER_TYPES.get(player.get('type'), f"type {player.get('type')}"),
            'race': RACES.get(player.get('race'), f"race {player.get('race')}"),
            'start': (player.get('start_x'), player.get('start_y')),
        })
    by_id = {p['id']: p for p in players}
    forces = []
    for number, force in enumerate(data.get('forces', []), start=1):
        mask = force.get('player_mask', 0)
        forces.append({'name': _resolve(force.get('name'), strings) or f'Force {number}',
                       'players': [p['name'] for p in players
                                   if p['id'] is not None and mask & (1 << p['id'])]})
    summary = {
        'name': _resolve(data.get('map_name'), strings),
        'author': _resolve(data.get('author_name'), strings),
        'description': _resolve(data.get('map_description'), strings),
        'players_recommended': _resolve(data.get('players_recommended'), strings),
        'players': players,
        'forces': forces,
        'size': (data.get('map_width'), data.get('map_height')),
        'tileset': MAIN_TILESETS.get(data.get('map_main_ground_type'),
                                     data.get('map_main_ground_type') or ''),
        'melee': bool(flags.get('melee_map')),
        'editor_version': data.get('editor_version'),
        'start_locations': [],
        'gold_mines': [],
    }
    units = _read(MapUnitsFile, files, 'war3mapUnits.doo', '_war3mapUnits.doo')
    if units is not None:
        placed = units.get_data().get('units', [])
        starts = [(u['position'][0], u['position'][1], u.get('owner')) for u in placed
                  if u.get('id') == START_LOCATION]
        summary['start_locations'] = [
            {'player': by_id.get(owner, {}).get('name', f'Player {owner + 1}'),
             'x': x, 'y': y} for x, y, owner in starts]
        for unit in placed:
            if unit.get('id') != GOLD_MINE:
                continue
            x, y = unit['position'][0], unit['position'][1]
            near = min((math.hypot(x - sx, y - sy) for sx, sy, _ in starts), default=None)
            summary['gold_mines'].append({
                'gold': unit.get('gold', 0), 'x': x, 'y': y,
                'main': near is not None and near <= MAIN_BASE_DISTANCE})
    # The melee flag is set on few melee maps: of 120 corpus maps, 9 set it
    # and 36 more place gold mines and start locations without it.
    summary['melee'] = summary['melee'] or bool(summary['gold_mines']
                                                and summary['start_locations'])
    return summary


def _text(text, fmt):
    """Warcraft III's color codes and line breaks in `fmt`."""
    text = text.replace('|n', '\n').replace('|N', '\n')
    if fmt == 'bbcode':
        return _COLOR.sub(lambda m: f'[COLOR=#{m.group(1)}]{m.group(2)}[/COLOR]', text)
    return re.sub(r'\|r', '', re.sub(r'\|c[0-9A-Fa-f]{8}', '', text))


def _list(items, fmt):
    if not items:
        return ''
    if fmt == 'bbcode':
        return '[LIST]\n' + ''.join(f'[*]{item}\n' for item in items) + '[/LIST]'
    return ''.join(f'- {item}\n' for item in items).rstrip('\n')


def _melee(summary, fmt):
    if not summary['melee']:
        return ''
    mines = summary['gold_mines']
    main = sum(1 for m in mines if m['main'])
    head = '\n[B]{}[/B]\n' if fmt == 'bbcode' else '\n## {}\n\n'
    lines = [head.format('Melee'),
             _list([f"Start locations: {len(summary['start_locations'])}",
                    f'Gold mines: {len(mines)} ({main} at start locations, '
                    f"{len(mines) - main} expansion{'' if len(mines) - main == 1 else 's'})"] +
                   [f"{m['gold']:,} gold {'at a start location' if m['main'] else 'expansion'}"
                    for m in sorted(mines, key=lambda m: (not m['main'], -m['gold']))], fmt)]
    return ''.join(lines)


def render(summary, fmt='bbcode', template=None):
    """`summary` written with `template`, or the default one of `fmt`
    ('bbcode' or 'markdown')."""
    if fmt not in TEMPLATES:
        raise ValueError(f"the format is 'bbcode' or 'markdown', not {fmt!r}")
    players = [f"{p['name']}: {p['controller']}, {p['race']}" for p in summary['players']]
    forces = [f"{f['name']}: {', '.join(f['players']) or 'no players'}"
              for f in summary['forces']]
    width, height = summary['size']
    values = {
        'name': _text(summary['name'], fmt),
        'author': _text(summary['author'], fmt),
        'description': _text(summary['description'], fmt),
        'players_recommended': _text(summary['players_recommended'], fmt),
        'player_count': str(sum(1 for p in summary['players'] if p['controller'] == 'User')
                            or len(summary['players'])),
        'players': _list([_text(p, fmt) for p in players], fmt),
        'forces': _list([_text(f, fmt) for f in forces], fmt),
        'size': f'{width}x{height} (playable)',
        'tileset': summary['tileset'],
        'melee': _melee(summary, fmt),
        'editor_version': str(summary['editor_version']),
    }
    text = string.Template(template or TEMPLATES[fmt]).safe_substitute(values)
    return text.rstrip('\n') + '\n'
