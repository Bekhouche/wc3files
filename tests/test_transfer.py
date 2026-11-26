"""wc3files.w3x.transfer and `transfer` (#218) on maps built here.

The source is shaped as the World Editor writes a custom map: its JASS script
places a footman and a region, a trigger kills the footman and shows a
string, and the map has a custom unit named in war3map.wts and one import.
The targets are melee maps, one JASS and one Lua (written without indentation,
as the World Editor writes Lua), each with its own region, start location
and map name."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.imp.imp_file import ImpFile
from wc3files.table.wts_file import WtsFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.modes import _read_files, _write_map
from wc3files.w3x.transfer import merge_scripts, transfer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SOURCE_J = '''globals
    rect gg_rct_Spawn = null
    unit gg_unit_hfoo_0001 = null
    trigger gg_trg_Start = null
    integer udg_Count = 0
endglobals

function InitGlobals takes nothing returns nothing
    set udg_Count = 0
endfunction

function InitSounds takes nothing returns nothing
endfunction

function CreateRegions takes nothing returns nothing
    set gg_rct_Spawn = Rect(0.0, 0.0, 128.0, 128.0)
endfunction

function CreateAllUnits takes nothing returns nothing
    set gg_unit_hfoo_0001 = CreateUnit(Player(0), 'hfoo', 0.0, 0.0, 0.0)
endfunction

function Trig_Start_Actions takes nothing returns nothing
    call KillUnit(gg_unit_hfoo_0001)
    call DisplayTextToForce(GetPlayersAll(), "TRIGSTR_002")
    set udg_Count = udg_Count + 1
endfunction

function InitTrig_Start takes nothing returns nothing
    set gg_trg_Start = CreateTrigger()
    call TriggerAddAction(gg_trg_Start, function Trig_Start_Actions)
endfunction

function InitCustomTriggers takes nothing returns nothing
    call InitTrig_Start()
endfunction

function RunInitializationTriggers takes nothing returns nothing
    call ConditionalTriggerExecute(gg_trg_Start)
endfunction

function InitCustomPlayerSlots takes nothing returns nothing
    call SetPlayerStartLocation(Player(0), 0)
endfunction

function main takes nothing returns nothing
    call SetCameraBounds(-1024.0, -1024.0, 1024.0, 1024.0, -1024.0, 1024.0, 1024.0, -1024.0)
    call InitSounds()
    call CreateRegions()
    call CreateAllUnits()
    call InitBlizzard()
    call InitGlobals()
    call InitCustomTriggers()
    call RunInitializationTriggers()
endfunction

function config takes nothing returns nothing
    call SetMapName("TRIGSTR_001")
    call SetPlayers(1)
    call SetTeams(1)
    call DefineStartLocation(0, 0.0, 0.0)
    call InitCustomPlayerSlots()
endfunction
'''

TARGET_J = '''globals
    rect gg_rct_Base = null
    trigger gg_trg_Melee_Initialization = null
endglobals

function InitGlobals takes nothing returns nothing
endfunction

function CreateRegions takes nothing returns nothing
    set gg_rct_Base = Rect(-64.0, -64.0, 64.0, 64.0)
endfunction

function CreateAllUnits takes nothing returns nothing
    call CreateUnit(Player(0), 'hpea', 0.0, 0.0, 0.0)
endfunction

function Trig_Melee_Initialization_Actions takes nothing returns nothing
    call MeleeStartingVisibility()
endfunction

function InitTrig_Melee_Initialization takes nothing returns nothing
    set gg_trg_Melee_Initialization = CreateTrigger()
    call TriggerAddAction(gg_trg_Melee_Initialization, function Trig_Melee_Initialization_Actions)
endfunction

function InitCustomTriggers takes nothing returns nothing
    call InitTrig_Melee_Initialization()
endfunction

function RunInitializationTriggers takes nothing returns nothing
    call ConditionalTriggerExecute(gg_trg_Melee_Initialization)
endfunction

function InitCustomPlayerSlots takes nothing returns nothing
    call SetPlayerStartLocation(Player(0), 0)
    call SetPlayerStartLocation(Player(1), 1)
endfunction

function main takes nothing returns nothing
    call SetCameraBounds(-4096.0, -4096.0, 4096.0, 4096.0, -4096.0, 4096.0, 4096.0, -4096.0)
    call CreateRegions()
    call CreateAllUnits()
    call InitBlizzard()
    call InitGlobals()
    call InitCustomTriggers()
    call RunInitializationTriggers()
endfunction

function config takes nothing returns nothing
    call SetMapName("TRIGSTR_001")
    call SetPlayers(2)
    call SetTeams(2)
    call DefineStartLocation(0, -2048.0, 0.0)
    call DefineStartLocation(1, 2048.0, 0.0)
    call InitCustomPlayerSlots()
endfunction
'''

TARGET_LUA = '''gg_rct_Base = nil
gg_trg_Melee_Initialization = nil
function InitGlobals()
end

function CreateRegions()
gg_rct_Base = Rect(-64.0, -64.0, 64.0, 64.0)
end

function CreateAllUnits()
local u
u = CreateUnit(Player(0), FourCC("hpea"), 0.0, 0.0, 0.0)
end

function Trig_Melee_Initialization_Actions()
if (true) then
MeleeStartingVisibility()
end
end

function InitTrig_Melee_Initialization()
gg_trg_Melee_Initialization = CreateTrigger()
TriggerAddAction(gg_trg_Melee_Initialization, Trig_Melee_Initialization_Actions)
end

function InitCustomTriggers()
InitTrig_Melee_Initialization()
end

function RunInitializationTriggers()
ConditionalTriggerExecute(gg_trg_Melee_Initialization)
end

function InitCustomPlayerSlots()
SetPlayerStartLocation(Player(0), 0)
end

function main()
SetCameraBounds(-4096.0, -4096.0, 4096.0, 4096.0, -4096.0, 4096.0, 4096.0, -4096.0)
CreateRegions()
CreateAllUnits()
InitBlizzard()
InitGlobals()
InitCustomTriggers()
RunInitializationTriggers()
end

function config()
SetMapName("TRIGSTR_001")
SetPlayers(1)
SetTeams(1)
DefineStartLocation(0, 0.0, 0.0)
InitCustomPlayerSlots()
end
'''


def _wts(strings):
    return WtsFile().import_data(strings).to_bytes()


def _units(custom):
    handler = ObjectFile()
    handler.import_data({'w3u': {'original': {}, 'custom': custom}})
    return handler.to_bytes('w3u')


def _unit_names(data):
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, 'war3map.w3u')
        with open(path, 'wb') as handle:
            handle.write(data)
        handler = ObjectFile()
        handler.read(path)
    finally:
        shutil.rmtree(folder)
    return {object_id: fields['unam']['value']
            for object_id, fields in handler.export_data()['w3u']['custom'].items()}


def _source():
    return {
        'war3map.j': SOURCE_J.encode(),
        'war3map.w3i': b'source info',
        'war3map.w3e': b'source terrain',
        'war3map.wts': _wts({1: 'Source Map', 2: 'Hello', 3: 'Swordsman'}),
        'war3map.w3u': _units({'h000': {'ID': 'hfoo', 'unam': {'value': 'TRIGSTR_003',
                                                               'type': 3}}}),
        'war3map.wtg': b'source triggers',
        'war3mapMisc.txt': b'[Misc]\r\nGoldTextHeight=0.03\r\n',
        'war3mapImported\\Icon.blp': b'icon',
        'war3map.imp': ImpFile().import_data({'version': 1, 'files': [
            {'flags': 13, 'path': 'war3mapImported\\Icon.blp'}]}).to_bytes(),
    }


def _target(lua=False):
    files = {
        'war3map.lua' if lua else 'war3map.j': (TARGET_LUA if lua else TARGET_J).encode(),
        'war3map.w3i': b'target info',
        'war3map.w3e': b'target terrain',
        'war3map.doo': b'target doodads',
        'war3map.wts': _wts({1: 'Target Map', 2: 'Target Description'}),
    }
    return files


class MergeScriptsTest(unittest.TestCase):

    def test_the_target_keeps_its_placement_and_the_source_brings_its_triggers(self):
        text, report = merge_scripts(SOURCE_J, TARGET_J, lua=False)
        self.assertIn('set gg_rct_Base = Rect(-64.0', text)
        self.assertIn("call CreateUnit(Player(0), 'hpea'", text)
        self.assertNotIn("'hfoo'", text)
        self.assertNotIn('gg_rct_Spawn', text)
        self.assertIn('function Trig_Start_Actions', text)
        self.assertNotIn('Trig_Melee_Initialization', text)
        self.assertIn('call SetPlayers(2)', text)
        self.assertEqual(text.count('function main takes'), 1)
        self.assertEqual(text.count('function config takes'), 1)
        # main: the target's bounds and placement, then the source's start.
        main = text[text.index('function main takes'):text.index('function config takes')]
        self.assertIn('-4096.0', main)
        self.assertLess(main.index('call InitSounds()'), main.index('call CreateRegions()'))
        self.assertLess(main.index('call InitBlizzard()'), main.index('call InitCustomTriggers()'))
        # The footman the trigger kills is not in the target: declared null.
        self.assertEqual(report['missing_placement'], ['gg_unit_hfoo_0001'])
        self.assertIn('unit gg_unit_hfoo_0001 = null', text)

    def test_init_sounds_stays_with_the_target_when_sounds_are_not_copied(self):
        text, _ = merge_scripts(SOURCE_J, TARGET_J, lua=False, copy_sounds=False)
        self.assertNotIn('InitSounds', text)

    def test_a_lua_target_without_indentation_splits_by_blocks(self):
        from wc3files.scripts.jass_to_lua import jass_to_lua
        from wc3files.scripts.script_file import parse_jass
        source = jass_to_lua(parse_jass(SOURCE_J))
        text, report = merge_scripts(source, TARGET_LUA, lua=True)
        self.assertIn('gg_rct_Base = Rect(-64.0', text)
        self.assertNotIn('MeleeStartingVisibility', text)
        self.assertIn('function Trig_Start_Actions', text)
        self.assertEqual(report['missing_placement'], ['gg_unit_hfoo_0001'])


class TransferTest(unittest.TestCase):

    def test_the_target_keeps_its_terrain_and_map_info(self):
        files, report = transfer(_source(), _target())
        for name in ('war3map.w3i', 'war3map.w3e', 'war3map.doo'):
            self.assertEqual(files[name], _target()[name])
        self.assertEqual(files['war3map.wtg'], b'source triggers')
        self.assertEqual(files['war3mapMisc.txt'], _source()['war3mapMisc.txt'])
        self.assertEqual(files['war3mapImported\\Icon.blp'], b'icon')
        imports = ImpFile()
        imports.read(files['war3map.imp'])
        self.assertEqual([e['path'] for e in imports.get_data()['files']],
                         ['war3mapImported\\Icon.blp'])

    def test_strings_are_added_and_renumbered_where_taken(self):
        files, report = transfer(_source(), _target())
        # 2 is taken in the target, 3 is free; the source's map name stays out.
        self.assertEqual(report['strings'], {2: 4, 3: 3})
        strings = WtsFile()
        strings.read(files['war3map.wts'])
        self.assertEqual(strings.export_data(), {1: 'Target Map', 2: 'Target Description',
                                                 3: 'Swordsman', 4: 'Hello'})
        script = files['war3map.j'].decode()
        self.assertIn('"TRIGSTR_004"', script)
        self.assertIn('call SetMapName("TRIGSTR_001")', script)
        self.assertEqual(_unit_names(files['war3map.w3u']), {'h000': 'TRIGSTR_003'})

    def test_source_objects_replace_the_targets_with_the_same_rawcode(self):
        target = _target()
        target['war3map.w3u'] = _units({
            'h000': {'ID': 'hpea', 'unam': {'value': 'Target Unit', 'type': 3}},
            'h001': {'ID': 'hpea', 'unam': {'value': 'Kept', 'type': 3}}})
        files, report = transfer(_source(), target)
        self.assertEqual(_unit_names(files['war3map.w3u']), {'h000': 'TRIGSTR_003',
                                                            'h001': 'Kept'})
        self.assertEqual(report['objects']['war3map.w3u'], ['h000'])

    def test_parts_choose_what_is_copied(self):
        files, report = transfer(_source(), _target(), parts=('constants',))
        self.assertEqual(files['war3mapMisc.txt'], _source()['war3mapMisc.txt'])
        self.assertEqual(files['war3map.j'], _target()['war3map.j'])
        self.assertNotIn('war3map.w3u', files)
        self.assertNotIn('war3mapImported\\Icon.blp', files)
        with self.assertRaises(ValueError):
            transfer(_source(), _target(), parts=('terrain',))

    def test_a_jass_source_is_translated_for_a_lua_target(self):
        files, report = transfer(_source(), _target(lua=True))
        self.assertTrue(report['script']['translated'])
        self.assertNotIn('war3map.j', files)
        script = files['war3map.lua'].decode()
        self.assertIn('function Trig_Start_Actions', script)
        self.assertTrue(any('translated' in note for note in report['notes']))

    def test_a_lua_source_cannot_go_into_a_jass_target(self):
        from wc3files.scripts.jass_to_lua import jass_to_lua
        from wc3files.scripts.script_file import parse_jass
        source = _source()
        source['war3map.lua'] = jass_to_lua(parse_jass(source.pop('war3map.j').decode())).encode()
        with self.assertRaises(ValueError):
            transfer(source, _target())

    def test_cli_writes_one_map_per_target(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source = os.path.join(tmp, 'Mod.w3x')
        _write_map(_source(), source)
        targets = []
        for name, lua in (('(2)One.w3m', False), ('(2)Two.w3x', True)):
            path = os.path.join(tmp, name)
            _write_map(_target(lua), path)
            targets.append(path)
        out = os.path.join(tmp, 'out')
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'transfer', source, out,
                                 *targets], capture_output=True, check=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertIn(b'not in the target: gg_unit_hfoo_0001', result.stdout)
        one = _read_files(os.path.join(out, '(2)One.w3m'))
        two = _read_files(os.path.join(out, '(2)Two.w3x'))
        self.assertEqual(one['war3map.w3e'], b'target terrain')
        self.assertIn(b'Trig_Start_Actions', one['war3map.j'])
        self.assertIn(b'Trig_Start_Actions', two['war3map.lua'])


if __name__ == '__main__':
    unittest.main()
