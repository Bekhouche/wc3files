import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.scripts import ScriptFile
from wc3files.w3x.placed_code import placed_code, rect_name


def _unit(rawcode, x=100.0, y=-200.0, owner=0, **fields):
    record = {'id': rawcode, 'position': (x, y, 0.0), 'rotation': math.radians(90),
              'skin': rawcode, 'owner': owner, 'hp': -1, 'mp': -1, 'gold': 12500,
              'target_acquisition': -1.0, 'hero_level': 1, 'hero_stats': (0, 0, 0),
              'inventory': [], 'abilities': [], 'random_mode': 0, 'item_sets': [],
              'item_table_id': -1, 'color': -1, 'waygate': -1}
    record.update(fields)
    return record


def _doodad(rawcode, life=100, flags=2, z=0.0):
    return {'id': rawcode, 'variation': 3, 'position': (64.0, 128.0, z), 'rotation': 0.0,
            'scale': (1.2, 1.2, 1.2), 'skin': rawcode, 'flags': flags, 'life': life,
            'item_table_pointer': -1, 'item_sets': []}


REGION = {'name': 'Spawn 1', 'left': -768.0, 'bottom': 320.0, 'right': -640.0, 'top': 448.0,
          'weather': None, 'index': 7}


class TestPlacedCode(unittest.TestCase):
    def test_editor_statements(self):
        units = [
            _unit('hfoo', owner=3, hp=50, mp=20, target_acquisition=-2.0,
                  inventory=[{'slot': 0, 'item_id': 'ratc'}]),
            _unit('ngol', owner=27, gold=12500),
            _unit('Hpal', hero_level=3, hero_stats=(20, 0, 0),
                  abilities=[{'ability_id': 'AHhb', 'autocast': False, 'level': 2}]),
            _unit('hmpr', abilities=[{'ability_id': 'Ahea', 'autocast': True, 'level': 0}]),
            _unit('nwgt', waygate=7),
            _unit('ratc', x=1.25, y=2.0),            # an item
            _unit('sloc'),                           # a start location
        ]
        code, report = placed_code(units, [_doodad('LTlt'), _doodad('LOtr')], [REGION])
        lines = [l.strip() for l in code.splitlines()]
        for expected in (
                "set u = BlzCreateUnitWithSkin(Player(3), 'hfoo', 100.0, -200.0, 90.000, 'hfoo')",
                "call UnitAddItemToSlotById(u, 'ratc', 0)",
                'call SetUnitState(u, UNIT_STATE_LIFE, 0.50 * life)',
                'call SetUnitState(u, UNIT_STATE_MANA, 20)',
                'call SetUnitAcquireRange(u, 200.0)',
                'call SetResourceAmount(u, 12500)',
                'call SetHeroLevel(u, 3, false)',
                'call SetHeroStr(u, 20, true)',
                "call SelectHeroSkill(u, 'AHhb')",
                'call IssueImmediateOrder(u, "healon")',
                'call WaygateSetDestination(u, GetRectCenterX(gg_rct_Spawn_1), '
                'GetRectCenterY(gg_rct_Spawn_1))',
                "call BlzCreateItemWithSkin('ratc', 1.2, 2.0, 'ratc')",
                "call BlzCreateDestructableWithSkin('LTlt', 64.0, 128.0, 0.000, 1.200, 3, 'LTlt')",
                'set gg_rct_Spawn_1 = Rect(-768.0, 320.0, -640.0, 448.0)'):
            self.assertIn(expected, lines)
        self.assertEqual(lines.count("call SelectHeroSkill(u, 'AHhb')"), 2)
        self.assertEqual((report['units'], report['items'], report['destructables'],
                          report['regions']), (5, 1, 1, 1))
        self.assertEqual(report['not created'], {'start locations': 1,
                                                 'doodads (no native places one)': 1})

    def test_destructable_kinds(self):
        code, _ = placed_code([], [_doodad('LTlt', life=0), _doodad('LTlt', flags=6, z=55.5),
                                   _doodad('LTlt', life=40)])
        self.assertIn("call BlzCreateDeadDestructableWithSkin('LTlt', 64.0, 128.0", code)
        self.assertIn("call BlzCreateDestructableZWithSkin('LTlt', 64.0, 128.0, 55.5, 0.000",
                      code)
        self.assertIn('call SetDestructableLife(d, 0.40 * life)', code)

    def test_classic_and_lua(self):
        units = [_unit('hfoo'), _unit('ratc')]
        classic, _ = placed_code(units, [_doodad('LTlt')], skins=False)
        self.assertIn("set u = CreateUnit(Player(0), 'hfoo', 100.0, -200.0, 90.000)", classic)
        self.assertIn("call CreateItem('ratc', 100.0, -200.0)", classic)
        self.assertNotIn('Blz', classic)
        lua, _ = placed_code(units, [], [REGION], language='lua', function='Build')
        self.assertTrue(lua.startswith('function Build()'))
        self.assertIn("u = BlzCreateUnitWithSkin(Player(0), FourCC('hfoo'), 100.0, -200.0, "
                      "90.000, FourCC('hfoo'))", lua)
        self.assertTrue(lua.rstrip().endswith('end'))

    def test_checked_by_the_script_checkers(self):
        units = [_unit('hfoo', hp=50, inventory=[{'slot': 1, 'item_id': 'ratc'}]),
                 _unit('Hpal', hero_level=2), _unit('ngol'), _unit('ratc')]
        regions = [REGION, dict(REGION, name='Spawn 1', weather='RAhr')]
        for language, suffix, skins in (('jass', '.j', True), ('jass', '.j', False),
                                        ('lua', '.lua', True)):
            code, _ = placed_code(units, [_doodad('LTlt', life=50)], regions,
                                  language=language, skins=skins)
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, 'placed' + suffix)
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write(code)
                script = ScriptFile()
                script.read(path)
                self.assertEqual(script.validate(), [], (language, skins, code))

    def test_split_into_threads(self):
        units = [_unit('hfoo', x=float(i), hp=50) for i in range(10)]
        code, _ = placed_code(units, chunk=6)
        lines = code.splitlines()
        heads = [l for l in lines if l.startswith('function ')]
        self.assertEqual(heads[-1], 'function CreatePlacedObjects takes nothing returns nothing')
        parts = len(heads) - 1
        self.assertGreater(parts, 1)
        for i in range(1, parts + 1):
            self.assertIn(f'    call ExecuteFunc("CreatePlacedObjects_{i}")', lines)
        # Each part starts with a unit, so its life lines stay with it.
        for i, line in enumerate(lines):
            if line.startswith('function CreatePlacedObjects_'):
                self.assertTrue(lines[i + 5].strip().startswith('set u = '))
        self.assertEqual(sum(1 for l in lines if 'BlzCreateUnitWithSkin' in l), 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'placed.j')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(code)
            script = ScriptFile()
            script.read(path)
            self.assertEqual(script.validate(), [])
        short, _ = placed_code(units[:2], chunk=6)
        self.assertNotIn('ExecuteFunc', short)

    def test_rect_names(self):
        self.assertEqual(rect_name('Spawn 1'), 'gg_rct_Spawn_1')
        self.assertEqual(rect_name('Spawn 1', ['gg_rct_Spawn_1']), 'gg_rct_Spawn_1_2')

    def test_cli(self):
        from wc3files.doo import MapUnitsFile
        from wc3files.w3x.modes import _write_map
        units = MapUnitsFile()
        data = units.get_data()
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'in.w3x')
            _write_map({'war3mapUnits.doo': units.import_data(data).to_bytes()}, source)
            out = os.path.join(tmp, 'placed.lua')
            self.assertEqual(main(['placed-code', source, out, '--lua']), 0)
            with open(out, encoding='utf-8') as handle:
                self.assertTrue(handle.read().startswith('function CreatePlacedObjects()'))
            self.assertEqual(main(['placed-code', source, '--parts', 'terrain']), 1)


if __name__ == '__main__':
    unittest.main()
