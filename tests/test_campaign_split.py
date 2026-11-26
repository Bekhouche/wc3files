"""wc3files.w3x.campaign and `campaign-split` (#249) on a small campaign
built here: two missions, the first with a unit of its own that shares a
rawcode with a campaign unit and with its own gameplay constants; the
campaign with a custom unit and ability named in war3campaign.wts, gameplay
constants, one import, and buttons naming the missions."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers
from wc3files.table.wts_file import WtsFile
from wc3files.w3f import CampaignInfoFile
from wc3files.w3i import W3iFile
from wc3files.w3o.object_file import ObjectFile
from wc3files.w3x.campaign import campaign_as_map, split_campaign
from wc3files.w3x.modes import _read_files, _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    return {i: f['unam']['value'] for i, f in handler.export_data()['w3u']['custom'].items()}


def _info(name):
    info = W3iFile()
    info.read(_helpers.sample_path('sample.w3i'))
    data = info.get_data()
    data['map_name'] = name
    return info.import_data(data).to_bytes()


def _map_bytes(members):
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, 'map.w3x')
        _write_map(members, path)
        with open(path, 'rb') as handle:
            return handle.read()
    finally:
        shutil.rmtree(folder)


def _campaign():
    first = {
        'war3map.w3i': _info('TRIGSTR_001'),
        'war3map.wts': WtsFile().import_data({1: 'First Mission'}).to_bytes(),
        'war3map.w3u': _units({'h000': {'ID': 'hfoo', 'unam': {'value': 'Mission Guard',
                                                               'type': 3}}}),
        'war3mapMisc.txt': b'[Misc]\r\nGoldTextHeight=0.05\r\n',
    }
    second = {
        'war3map.w3i': _info('TRIGSTR_001'),
        'war3map.wts': WtsFile().import_data({1: 'Second Mission', 2: 'Taken'}).to_bytes(),
    }
    info = CampaignInfoFile()
    data = info.get_data()
    data.update({'campaign_name': 'Test Campaign', 'map_buttons': [
        {'is_visible_initially': 1, 'chapter': 'TRIGSTR_010', 'title': 'TRIGSTR_011',
         'map_file_path': 'Maps\\First.w3x'},
        {'is_visible_initially': 1, 'chapter': '', 'title': 'The |cffff0000Last|r Stand?',
         'map_file_path': 'Maps\\Second.w3m'}],
        'maps': [{'unknown': '', 'map_file_path': 'Maps\\First.w3x'},
                 {'unknown': '', 'map_file_path': 'Maps\\Second.w3m'}]})
    return {
        'war3campaign.w3f': info.import_data(data).to_bytes(),
        'war3campaign.wts': WtsFile().import_data(
            {2: 'Campaign Knight', 10: 'Chapter One', 11: 'The Gate'}).to_bytes(),
        'war3campaign.w3u': _units({
            'h000': {'ID': 'hkni', 'unam': {'value': 'Campaign Knight', 'type': 3}},
            'h001': {'ID': 'hkni', 'unam': {'value': 'TRIGSTR_002', 'type': 3}}}),
        'war3campaignMisc.txt': b'[Misc]\r\nGoldTextHeight=0.02\r\n',
        'war3campaignImported\\Banner.blp': b'banner',
        'Maps\\First.w3x': _map_bytes(first),
        'Maps\\Second.w3m': _map_bytes(second),
    }


def _strings(files):
    handler = WtsFile()
    handler.read(files['war3map.wts'])
    return handler.export_data()


def _map_name(files):
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, 'war3map.w3i')
        with open(path, 'wb') as handle:
            handle.write(files['war3map.w3i'])
        info = W3iFile()
        info.read(path)
    finally:
        shutil.rmtree(folder)
    number = int(info.get_data()['map_name'].split('_')[1])
    return _strings(files)[number]


class CampaignSplitTest(unittest.TestCase):

    def setUp(self):
        self.maps = split_campaign(_campaign())

    def test_one_map_per_mission_named_after_its_button(self):
        self.assertEqual([name for name, _, _ in self.maps],
                         ['01 Chapter One - The Gate.w3x', '02 The Last Stand.w3m'])
        self.assertEqual(_map_name(self.maps[0][1]), 'Chapter One - The Gate')
        self.assertEqual(_map_name(self.maps[1][1]), 'The |cffff0000Last|r Stand?')

    def test_the_maps_object_data_wins_over_the_campaigns(self):
        first, second = self.maps[0][1], self.maps[1][1]
        names = _unit_names(first['war3map.w3u'])
        self.assertEqual(names['h000'], 'Mission Guard')
        self.assertEqual(self.maps[0][2]['objects']['war3map.w3u'], ['h000'])
        self.assertEqual(_strings(first)[int(names['h001'].split('_')[1])], 'Campaign Knight')
        self.assertEqual(_unit_names(second['war3map.w3u'])['h000'], 'Campaign Knight')

    def test_strings_are_renumbered_where_the_map_uses_the_number(self):
        second = self.maps[1][1]
        number = int(_unit_names(second['war3map.w3u'])['h001'].split('_')[1])
        self.assertNotEqual(number, 2)
        self.assertEqual(_strings(second)[number], 'Campaign Knight')
        self.assertEqual(_strings(second)[2], 'Taken')

    def test_constants_and_imports(self):
        first, second = self.maps[0][1], self.maps[1][1]
        self.assertEqual(first['war3mapMisc.txt'], b'[Misc]\r\nGoldTextHeight=0.05\r\n')
        self.assertEqual(second['war3mapMisc.txt'], b'[Misc]\r\nGoldTextHeight=0.02\r\n')
        for files in (first, second):
            self.assertEqual(files['war3campaignImported\\Banner.blp'], b'banner')
            self.assertFalse(any(k.lower().endswith(('.w3x', '.w3m')) for k in files))

    def test_campaign_as_map_names_the_campaign_files_as_a_maps(self):
        data = campaign_as_map(_campaign())
        self.assertEqual(sorted(data), ['war3campaignImported\\Banner.blp', 'war3map.w3u',
                                        'war3map.wts', 'war3mapMisc.txt'])

    def test_keep_names(self):
        maps = split_campaign(_campaign(), rename=False)
        self.assertEqual(_map_name(maps[0][1]), 'First Mission')

    def test_a_listed_map_the_campaign_lacks_is_refused(self):
        campaign = _campaign()
        del campaign['Maps\\Second.w3m']
        with self.assertRaises(ValueError):
            split_campaign(campaign)

    def test_cli_writes_the_maps(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source = os.path.join(tmp, 'Test.w3n')
        _write_map(_campaign(), source)
        out = os.path.join(tmp, 'out')
        subprocess.run([sys.executable, '-m', 'wc3files.cli', 'campaign-split', source, out],
                       capture_output=True, check=True,
                       env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertEqual(sorted(os.listdir(out)),
                         ['01 Chapter One - The Gate.w3x', '02 The Last Stand.w3m'])
        files = _read_files(os.path.join(out, '01 Chapter One - The Gate.w3x'))
        self.assertEqual(_unit_names(files['war3map.w3u'])['h000'], 'Mission Guard')


if __name__ == '__main__':
    unittest.main()
