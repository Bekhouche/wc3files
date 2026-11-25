"""In-game UI frames (#172): a layout of frames, written as JASS and Lua over
the frame natives, as an .fdf, and as a custom-text trigger in a map."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
from wc3files import MapCustomTextTriggersFile, MapTriggersFile, W3xFile
from wc3files.cli.main import main
from wc3files.scripts.checker import check_program
from wc3files.scripts.jass_parser import parse as parse_jass
from wc3files.scripts.lua_checker import check_chunk
from wc3files.scripts.lua_parser import parse as parse_lua
from wc3files.ui import (Frame, Layout, Point, UiError, add_ui_trigger, from_fdf,
                         init_trigger, to_fdf, to_jass, to_lua)


def sample_layout():
    with open(sample_path('sample_ui.json'), encoding='utf-8') as handle:
        return Layout.from_json(handle.read())


def issues(findings):
    return [f for f in findings if f.get('severity') == 'issue']


class TestLayout(unittest.TestCase):

    def test_json_round_trip(self):
        layout = sample_layout()
        self.assertEqual(Layout.from_json(layout.to_json()).to_dict(), layout.to_dict())
        self.assertEqual([f.name for f, _ in layout.walk()], ['Panel', 'Title', 'Close', 'Hint'])
        self.assertEqual(layout.check(), [])

    def test_what_cannot_be_created_is_refused(self):
        cases = {
            'two frames with one name': [{'name': 'A', 'type': 'TEXT'}, {'name': 'A', 'type': 'TEXT'}],
            'a name that is not an identifier': [{'name': 'my frame', 'type': 'TEXT'}],
            'no type and no template': [{'name': 'A'}],
            'an unknown point': [{'name': 'A', 'type': 'TEXT', 'points': [{'point': 'MIDDLE'}]}],
            'a point against a later frame': [
                {'name': 'A', 'type': 'TEXT', 'points': [{'point': 'TOP', 'relative_to': 'B'}]},
                {'name': 'B', 'type': 'TEXT'}],
            'a top-level frame against its parent': [
                {'name': 'A', 'type': 'TEXT', 'points': [{'point': 'TOP', 'relative_to': 'parent'}]}],
        }
        for label, frames in cases.items():
            with self.subTest(label):
                with self.assertRaises(UiError):
                    Layout.from_dict({'frames': frames})

    def test_a_screen_point_outside_the_area_is_a_warning(self):
        layout = Layout([Frame('A', 'TEXT', points=[Point('CENTER', 0.9, 0.3)])])
        self.assertEqual(len(layout.check()), 1)


class TestCode(unittest.TestCase):

    def test_jass_passes_the_checker(self):
        code = to_jass(sample_layout())
        self.assertEqual(issues(check_program(parse_jass(code))), [])
        self.assertIn('set ui_Close = BlzCreateFrameByType("GLUETEXTBUTTON", "Close", ui_Panel, '
                      '"ScriptDialogButton", 0)', code)
        self.assertIn('call BlzFrameSetAbsPoint(ui_Panel, FRAMEPOINT_CENTER, 0.4, 0.3)', code)
        self.assertIn('call BlzFrameSetText(ui_Title, "Hello \\"hero\\"")', code)
        self.assertIn('"UI\\\\Widgets\\\\EscMenu', code)
        self.assertIn('call BlzFrameSetVisible(ui_Hint, false)', code)

    def test_lua_passes_the_checker(self):
        code = to_lua(sample_layout(), table='MyUI')
        self.assertEqual(issues(check_chunk(parse_lua(code))), [])
        self.assertIn('MyUI.Title = BlzCreateFrameByType("TEXT", "Title", MyUI.Panel, "", 0)', code)

    def test_a_template_and_a_toc(self):
        layout = Layout([Frame('Box', template='EscMenuBackdrop', size=(0.1, 0.1))])
        code = to_jass(layout, toc='war3mapImported\\ui.toc')
        self.assertIn('call BlzLoadTOCFile("war3mapImported\\\\ui.toc")', code)
        self.assertIn('BlzCreateFrame("EscMenuBackdrop", BlzGetOriginFrame(ORIGIN_FRAME_GAME_UI, 0), 0, 0)', code)
        self.assertEqual(issues(check_program(parse_jass(code))), [])

    def test_a_line_break_in_a_text_is_refused(self):
        with self.assertRaises(UiError):
            to_jass(Layout([Frame('A', 'TEXT', text='one\ntwo')]))

    def test_init_triggers_pass_the_checkers(self):
        layout = sample_layout()
        jass = init_trigger(to_jass(layout), 'jass', 'My UI')
        self.assertIn('function InitTrig_My_UI takes nothing returns nothing', jass)
        self.assertEqual(issues(check_program(parse_jass(jass))), [])
        lua = init_trigger(to_lua(layout), 'lua', 'My UI')
        self.assertIn('function InitTrig_My_UI()', lua)
        self.assertEqual(issues(check_chunk(parse_lua(lua))), [])


class TestFdf(unittest.TestCase):

    def test_round_trip_of_what_an_fdf_carries(self):
        layout = sample_layout()
        fdf, warnings = to_fdf(layout)
        self.assertEqual(len(warnings), 3)     # a screen point, a visibility, a quoted text
        back, left_out = from_fdf(fdf.to_text())
        self.assertEqual(left_out, [])
        expected = Layout.from_dict(layout.to_dict())
        panel, title, _close, hint = (f for f, _ in expected.walk())
        panel.points = []
        title.text = None
        hint.visible = True
        self.assertEqual(back.to_dict(), expected.to_dict())

    def test_a_real_fdf_reads(self):
        layout, left_out = from_fdf(sample_path('ResourceBar.fdf'))
        self.assertIsInstance(left_out, list)
        json.dumps(layout.to_dict())


def _reforged_with_root(wtg):
    """The sample map's synthetic Reforged triggers have no root category,
    which every real Reforged file has; add one."""
    for item in wtg['items']:
        if item['parent_id'] == -1:
            item['parent_id'] = 0
    wtg['items'].insert(0, {'id': 0, 'name': 'sample.w3x', 'is_comment': False,
                            'is_expanded': True, 'parent_id': -1, 'item_type': 1})
    return wtg


class TestTriggers(unittest.TestCase):

    def test_reforged_map(self):
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        wtg_file, wct_file = MapTriggersFile(), MapCustomTextTriggersFile()
        wtg_file.read(archive.files['war3map.wtg'])
        wct_file.read(archive.files['war3map.wct'])
        wtg, wct = _reforged_with_root(wtg_file.get_data()), wct_file.get_data()
        summary = add_ui_trigger(wtg, wct, sample_layout())
        self.assertEqual(summary['triggers'], ['UI'])
        archive.files['war3map.wtg'] = wtg_file.import_data(wtg).to_bytes()
        archive.files['war3map.wct'] = wct_file.import_data(wct).to_bytes()

        again_wtg, again_wct = MapTriggersFile(), MapCustomTextTriggersFile()
        again_wtg.read(archive.files['war3map.wtg'])
        again_wct.read(archive.files['war3map.wct'])
        items = again_wtg.get_data()['items']
        ui = [i for i in items if i['name'] == 'UI' and i['item_type'] == 32]
        self.assertEqual(len(ui), 1)
        self.assertTrue(ui[0]['is_custom_text'])
        category = [i for i in items if i['id'] == ui[0]['parent_id']]
        self.assertEqual(category[0]['name'], 'UI')
        text = again_wct.get_data()['triggers'][-1]
        self.assertIn('function InitTrig_UI takes nothing returns nothing', text)
        self.assertEqual(issues(check_program(parse_jass(text))), [])

        # Adding it again replaces the trigger rather than adding a second.
        add_ui_trigger(wtg, wct, sample_layout())
        self.assertEqual(len([i for i in wtg['items'] if i['name'] == 'UI'
                              and i['item_type'] == 32]), 1)

    def test_classic_layout(self):
        wtg = {'categories': [{'id': 0, 'name': 'Initialization', 'is_comment': False}],
               'variables': [],
               'triggers': [{'name': 'Melee Initialization', 'description': '',
                             'is_comment': False, 'is_enabled': True, 'is_custom_text': False,
                             'is_initially_on': True, 'run_on_map_init': False,
                             'parent_id': 0, 'functions': []}]}
        wct = {'triggers': ['']}
        add_ui_trigger(wtg, wct, sample_layout(), language='lua')
        self.assertEqual([t['name'] for t in wtg['triggers']], ['Melee Initialization', 'UI'])
        self.assertEqual([c['name'] for c in wtg['categories']], ['Initialization', 'UI'])
        self.assertEqual(len(wct['triggers']), 2)
        self.assertIn('function InitTrig_UI()', wct['triggers'][1])

    def test_a_maps_own_trigger_of_that_name_is_not_overwritten(self):
        wtg = {'categories': [{'id': 0, 'name': 'Init', 'is_comment': False}], 'variables': [],
               'triggers': [{'name': 'UI', 'description': 'my own', 'is_comment': False,
                             'is_enabled': True, 'is_custom_text': True, 'is_initially_on': True,
                             'run_on_map_init': False, 'parent_id': 0, 'functions': []}]}
        wct = {'triggers': ['// my own code']}
        with self.assertRaises(UiError):
            add_ui_trigger(wtg, wct, sample_layout())
        self.assertEqual(wct['triggers'], ['// my own code'])
        add_ui_trigger(wtg, wct, sample_layout(), trigger_name='UI 2')
        self.assertEqual([t['name'] for t in wtg['triggers']], ['UI', 'UI 2'])


class TestCli(unittest.TestCase):

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_jass_to_stdout_and_files(self):
        status, out, _err = self.run_cli(['ui-build', sample_path('sample_ui.json')])
        self.assertIn(status, (None, 0))
        self.assertIn('function CreateUI takes nothing returns nothing', out)
        tmp = tempfile.mkdtemp()
        paths = {k: os.path.join(tmp, f'ui.{k}') for k in ('j', 'lua', 'fdf', 'json')}
        status, _out, err = self.run_cli(['ui-build', sample_path('sample_ui.json'),
                                          '--jass', paths['j'], '--lua', paths['lua'],
                                          '--fdf', paths['fdf'], '--function', 'MakeUI'])
        self.assertIn(status, (None, 0))
        self.assertTrue(all(os.path.exists(paths[k]) for k in ('j', 'lua', 'fdf')))
        with open(paths['j'], encoding='utf-8') as handle:
            self.assertIn('function MakeUI takes', handle.read())
        self.assertIn('warning:', err)
        status, _out, _err = self.run_cli(['ui-build', paths['fdf'], '--json', paths['json']])
        self.assertIn(status, (None, 0))
        with open(paths['json'], encoding='utf-8') as handle:
            self.assertEqual([f['name'] for f in json.load(handle)['frames']], ['Panel', 'Hint'])

    def test_into_a_map(self):
        tmp = tempfile.mkdtemp()
        archive = W3xFile()
        archive.read(sample_path('sample.w3x'))
        wtg = MapTriggersFile()
        wtg.read(archive.files['war3map.wtg'])
        archive.files['war3map.wtg'] = wtg.import_data(_reforged_with_root(wtg.get_data())).to_bytes()
        source = os.path.join(tmp, 'in.w3x')
        archive.build_from_files(archive.files, source)
        out = os.path.join(tmp, 'out.w3x')
        status, _out, err = self.run_cli(['ui-build', sample_path('sample_ui.json'),
                                          '--map', source, '--out', out])
        self.assertIn(status, (None, 0), err)
        result = W3xFile()
        result.read(out)
        texts = MapCustomTextTriggersFile()
        texts.read(result.files['war3map.wct'])
        self.assertIn('InitTrig_UI', texts.get_data()['triggers'][-1])
        shutil.rmtree(tmp, ignore_errors=True)

    def test_map_needs_out(self):
        status, _out, err = self.run_cli(['ui-build', sample_path('sample_ui.json'),
                                          '--map', sample_path('sample.w3x')])
        self.assertEqual(status, 1)
        self.assertIn('--out', err)


if __name__ == '__main__':
    unittest.main()
