"""
A map's placed units, items, destructables and regions as JASS or Lua that
creates them at run time (#251), for example to build one of several
layouts in one map.

The statements are those the World Editor writes into war3map.j for the same
objects (CreateAllUnits, CreateAllItems, CreateAllDestructables,
CreateRegions), checked against the scripts of corpus maps:

- a unit: BlzCreateUnitWithSkin(player, id, x, y, facing, skin), then its
  items, colour, gold, life and mana, acquisition range, hero level, hero
  attributes and hero skills, and waygate; an item: BlzCreateItemWithSkin;
  a destructable: BlzCreateDestructableWithSkin, or the dead one, and its
  life. With `skins=False`, the classic natives (CreateUnit, CreateItem,
  CreateDestructable) are used instead, for patches before 1.32;
- coordinates with one decimal and facings with three, as the editor writes.

Not created, and reported: start locations (the map info defines them),
doodads (the game has no native that places one), random units and items,
and item drop tables (the editor makes a trigger for each). Terrain is not
written: SetTerrainType changes one point's texture, but there is no native
that sets a height, a cliff level or water, so a terrain cannot be rebuilt
at run time, and one call per tile corner is far past a script's limits.
"""
import math

START_LOCATION = 'sloc'


def rect_name(name, taken=()):
    """The editor's variable for a region: gg_rct_ and its name with each
    character that is not a letter, digit or underscore made an underscore;
    a name already `taken` gets a number."""
    import re
    base = 'gg_rct_' + re.sub(r'[^A-Za-z0-9_]', '_', name or 'Region')
    taken = {n for n, _ in taken} if taken and isinstance(next(iter(taken)), tuple) \
        else set(taken)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f'{base}_{n}', n + 1
    return candidate


def region_fields(region):
    """{name, left, bottom, right, top, weather} of a w3r Region or a dict;
    weather is a rawcode, or None for none."""
    if isinstance(region, dict):
        fields = dict(region)
    else:
        fields = {k: getattr(region, k) for k in ('name', 'left', 'bottom', 'right', 'top')}
        fields['weather'] = getattr(region, 'weather_effect', None)
        fields['index'] = getattr(region, 'index', None)
    weather = fields.get('weather')
    if isinstance(weather, (bytes, bytearray)):
        weather = weather.decode('latin-1')
    fields['weather'] = weather if weather and weather.strip('\0') else None
    return fields


def _num(value, places):
    return f'{value:.{places}f}'


def _code(rawcode):
    return f"'{rawcode}'"


class _Writer:
    def __init__(self, language):
        self.lua = language == 'lua'
        self.lines = []

    def call(self, name, *args):
        text = f'{name}({", ".join(args)})'
        self.lines.append(('    ' + text) if self.lua else f'    call {text}')

    def set(self, var, name, *args):
        text = f'{name}({", ".join(args)})'
        self.lines.append(f'    {var} = {text}' if self.lua else f'    set {var} = {text}')

    def raw(self, line):
        self.lines.append(line)

    def code(self, rawcode):
        return f"FourCC('{rawcode}')" if self.lua else _code(rawcode)

    def bool(self, value):
        return 'true' if value else 'false'


def _item_ids():
    from wc3files import gamedata
    from wc3files.table.slk_file import SlkFile
    table = SlkFile()
    table.read(gamedata.path('w3o/gamedata/ItemData.slk'))
    return set(table.export_data())


def _hero_ids():
    from wc3files import gamedata
    from wc3files.table.slk_file import SlkFile
    table = SlkFile()
    table.read(gamedata.path('w3o/gamedata/UnitBalance.slk'))
    # Heroes have a primary attribute; other units have none.
    return {key for key, row in table.export_data().items()
            if str(row.get('Primary') or '').strip() not in ('', '_', '-')}


def _autocast_orders():
    """{ability id: (order on, order off)}, bundled from the game's
    *AbilityFunc.txt files (66 abilities)."""
    import json
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'autocast_orders.json')
    with open(path, encoding='utf-8') as handle:
        return {k: tuple(v) for k, v in json.load(handle)['orders'].items()}


def _gold_mines():
    """The units whose default abilities hold a gold mine's (Agld, and the
    entangled and haunted ones)."""
    from wc3files import gamedata
    from wc3files.table.slk_file import SlkFile
    table = SlkFile()
    table.read(gamedata.path('w3o/gamedata/UnitAbilities.slk'))
    mine_abilities = {'Agld', 'Aegm', 'Abgm'}
    out = set()
    for key, row in table.export_data().items():
        abilities = {a.strip() for a in str(row.get('abilList') or '').split(',')}
        if abilities & mine_abilities:
            out.add(key)
    return out


def _destructable_ids():
    from wc3files import gamedata
    from wc3files.table.slk_file import SlkFile
    table = SlkFile()
    table.read(gamedata.path('w3o/gamedata/DestructableData.slk'))
    return set(table.export_data())


def placed_code(units=(), doodads=(), regions=(), language='jass',
                function='CreatePlacedObjects', skins=True, custom_bases=None,
                chunk=2000):
    """(code, report): a function named `function` that creates the units and
    items (`units`: MapUnitsFile records), the destructables among `doodads`
    (MapDoodadsFile records; the doodads are reported, not created) and the
    regions (`regions`: w3r Region objects, or dicts of name, left, bottom,
    right, top and weather). `custom_bases` is {custom id: base id} from the
    map's object data, to tell custom items, heroes and destructables apart.
    The report counts what was created and lists what was not."""
    if language not in ('jass', 'lua'):
        raise ValueError(f"language is 'jass' or 'lua', not {language!r}")
    w = _Writer(language)
    items, heroes, destructable_ids = _item_ids(), _hero_ids(), _destructable_ids()
    bases = custom_bases or {}

    def base(rawcode):
        return bases.get(rawcode, rawcode)

    report = {'units': 0, 'items': 0, 'destructables': 0, 'regions': 0,
              'not created': {}}

    def skip(what):
        report['not created'][what] = report['not created'].get(what, 0) + 1

    orders, mines = _autocast_orders(), _gold_mines()
    names, by_index = [], {}
    for region in regions:
        fields = region_fields(region)
        name = rect_name(fields['name'], names)
        names.append((name, fields.get))
        if fields.get('index') is not None:
            by_index[fields['index']] = name
    if names and not w.lua:
        # A region is a rect the map's triggers refer to by its variable,
        # gg_rct_ and its name as the editor makes it.
        w.raw('globals')
        for name, _ in names:
            w.raw(f'    rect {name} = null')
        w.raw('endglobals')
        w.raw('')
    if w.lua:
        w.raw(f'function {function}()')
        w.raw('    local u, d, life, we')
    else:
        w.raw(f'function {function} takes nothing returns nothing')
        w.raw('    local unit u')
        w.raw('    local destructable d')
        w.raw('    local real life')
        w.raw('    local weathereffect we')
    for name, get in names:
        w.set(name, 'Rect', *[_num(get(k), 1) for k in ('left', 'bottom', 'right', 'top')])
        weather = get('weather')
        if weather:
            w.set('we', 'AddWeatherEffect', name, w.code(weather))
            w.call('EnableWeatherEffect', 'we', 'true')
        report['regions'] += 1
    for unit in units:
        rawcode = unit['id']
        x, y = unit['position'][0], unit['position'][1]
        skin = unit.get('skin') or rawcode
        if rawcode == START_LOCATION:
            skip('start locations')
            continue
        if unit.get('random_mode') not in (None, 0) or rawcode in ('uDNR', 'bDNR', 'iDNR') \
                or rawcode.startswith(('YY', 'YI', 'yy')):
            skip('random units and items')
            continue
        if unit.get('item_sets') or unit.get('item_table_id', -1) not in (-1, None):
            skip('item drop tables (left off the unit)')
        if base(rawcode) in items:
            if skins:
                w.call('BlzCreateItemWithSkin', w.code(rawcode), _num(x, 1), _num(y, 1),
                       w.code(skin))
            else:
                w.call('CreateItem', w.code(rawcode), _num(x, 1), _num(y, 1))
            report['items'] += 1
            continue
        facing = _num(math.degrees(unit.get('rotation', 0.0)) % 360, 3)
        # A few records carry a high bit over the player (65544 for 8).
        player = f'Player({unit.get("owner", 0) & 0xFFFF})'
        if skins:
            w.set('u', 'BlzCreateUnitWithSkin', player, w.code(rawcode), _num(x, 1),
                  _num(y, 1), facing, w.code(skin))
        else:
            w.set('u', 'CreateUnit', player, w.code(rawcode), _num(x, 1), _num(y, 1), facing)
        report['units'] += 1
        for slot in unit.get('inventory') or ():
            w.call('UnitAddItemToSlotById', 'u', w.code(slot['item_id']), str(slot['slot']))
        if unit.get('color', -1) not in (-1, None):
            w.call('SetUnitColor', 'u', f'ConvertPlayerColor({unit["color"]})')
        if base(rawcode) in mines:
            # The editor writes every gold mine's gold, 12500 included.
            w.call('SetResourceAmount', 'u', str(unit.get('gold', 12500)))
        hp, mp = unit.get('hp', -1), unit.get('mp', -1)
        if hp not in (-1, None):
            w.set('life', 'GetUnitState', 'u', 'UNIT_STATE_LIFE')
            w.call('SetUnitState', 'u', 'UNIT_STATE_LIFE', f'{hp / 100:.2f} * life')
        if mp not in (-1, None):
            w.call('SetUnitState', 'u', 'UNIT_STATE_MANA', str(mp))
        acquisition = unit.get('target_acquisition', -1)
        if acquisition not in (-1, -1.0, None):
            if acquisition == -2:
                w.call('SetUnitAcquireRange', 'u', '200.0')
            else:
                w.call('SetUnitAcquireRange', 'u', _num(acquisition, 1))
        if base(rawcode) in heroes:
            level = unit.get('hero_level', 1)
            if level and level > 1:
                w.call('SetHeroLevel', 'u', str(level), 'false')
            stats = unit.get('hero_stats') or (0, 0, 0)
            for name, value in zip(('SetHeroStr', 'SetHeroAgi', 'SetHeroInt'), stats):
                if value:
                    w.call(name, 'u', str(value), 'true')
            for ability in unit.get('abilities') or ():
                for _ in range(ability.get('level', 0)):
                    w.call('SelectHeroSkill', 'u', w.code(ability['ability_id']))
        for ability in unit.get('abilities') or ():
            pair = orders.get(base(ability['ability_id']))
            if pair:
                order = pair[0] if ability.get('autocast') else pair[1]
                w.call('IssueImmediateOrder', 'u', f'"{order}"')
            elif ability.get('autocast'):
                skip('autocast of an ability with no known order')
        waygate = unit.get('waygate', -1)
        if waygate not in (-1, None):
            target = by_index.get(waygate)
            if target is None:
                skip('waygate destinations (their region was not given)')
            else:
                w.call('WaygateSetDestination', 'u', f'GetRectCenterX({target})',
                       f'GetRectCenterY({target})')
                w.call('WaygateActivate', 'u', 'true')
    for record in doodads:
        rawcode = record['id']
        if base(rawcode) not in destructable_ids:
            skip('doodads (no native places one)')
            continue
        x, y, z = record['position']
        skin = record.get('skin') or rawcode
        facing = _num(math.degrees(record.get('rotation', 0.0)) % 360, 3)
        scale = _num(record['scale'][0], 3)
        variation = str(record.get('variation', 0))
        life = record.get('life', 100)
        dead = life == 0
        # Flag 4 keeps the destructable at its stored height, and the
        # editor then creates it with the Z native (all 8 such in the corpus).
        fixed_z = bool(record.get('flags', 0) & 4)
        where = (_num(x, 1), _num(y, 1)) + ((_num(z, 1),) if fixed_z else ())
        name = ('CreateDeadDestructable' if dead else 'CreateDestructable') + \
            ('Z' if fixed_z else '')
        if skins:
            name = 'Blz' + name + 'WithSkin'
            args = (w.code(rawcode),) + where + (facing, scale, variation, w.code(skin))
        else:
            args = (w.code(rawcode),) + where + (facing, scale, variation)
        if not dead and life not in (100, None):
            w.set('d', name, *args)
            w.set('life', 'GetDestructableLife', 'd')
            w.call('SetDestructableLife', 'd', f'{life / 100:.2f} * life')
        else:
            w.call(name, *args)
        report['destructables'] += 1
        if record.get('item_sets') or record.get('item_table_pointer', -1) not in (-1, None):
            skip('item drop tables (left off the destructable)')
    w.raw('end' if w.lua else 'endfunction')
    if not w.lua:
        w.lines = _split(w.lines, function, chunk)
    return '\n'.join(w.lines) + '\n', report


_LOCALS = ('    local unit u', '    local destructable d', '    local real life',
           '    local weathereffect we')
# A line that starts an object: a split never parts an object from its calls.
_STARTS = ('set u = ', 'set d = ', 'set gg_rct_', 'call BlzCreate', 'call Create')


def _split(lines, function, chunk):
    """JASS: the body cut into functions of at most about `chunk`
    statements, which the named function runs one after another with
    ExecuteFunc. Each runs as a thread of its own, so it has its own budget
    of operations, which one function of thousands of statements would run
    past and stop partway."""
    header = lines.index(f'function {function} takes nothing returns nothing')
    body = lines[header + 1 + len(_LOCALS):-1]
    if len(body) <= chunk:
        return lines
    parts, current = [], []
    for line in body:
        if len(current) >= chunk and line.strip().startswith(_STARTS):
            parts.append(current)
            current = []
        current.append(line)
    parts.append(current)
    out = lines[:header]
    for i, part in enumerate(parts, 1):
        out += [f'function {function}_{i} takes nothing returns nothing', *_LOCALS, *part,
                'endfunction', '']
    out.append(f'function {function} takes nothing returns nothing')
    out += [f'    call ExecuteFunc("{function}_{i}")' for i in range(1, len(parts) + 1)]
    out.append('endfunction')
    return out
