"""wc3files.w3x.description and `map-description` (#253) on a melee map
built from the checked-in samples: the map info's name, author and
description as war3map.wts strings (one with a color code), two players and
one force, and war3mapUnits.doo with two start locations, a gold mine at
each and one expansion."""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers
from wc3files.doo import MapUnitsFile
from wc3files.table.wts_file import WtsFile
from wc3files.w3i import W3iFile
from wc3files.w3x.description import map_summary, render
from wc3files.w3x.modes import _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _info():
    info = W3iFile()
    info.read(_helpers.sample_path('sample.w3i'))
    data = info.get_data()
    data.update({'map_name': 'TRIGSTR_001', 'author_name': 'TRIGSTR_002',
                 'map_description': 'TRIGSTR_003', 'players_recommended': '1v1',
                 'map_main_ground_type': 'L'})
    return info.import_data(data).to_bytes()


def _units():
    units = MapUnitsFile()
    units.read(_helpers.sample_path('sample.doo'))
    data = units.get_data()
    template = data['units'][0]
    placed = []
    for unit_id, x, owner, gold in (('sloc', -1500.0, 0, 0), ('sloc', 1500.0, 1, 0),
                                    ('ngol', -1100.0, 15, 12500), ('ngol', 1100.0, 15, 12500),
                                    ('ngol', 0.0, 15, 8000)):
        unit = copy.deepcopy(template)
        unit.update({'id': unit_id, 'skin': unit_id, 'position': (x, 0.0, 0.0),
                     'owner': owner, 'gold': gold, 'inventory': [], 'abilities': []})
        placed.append(unit)
    data['units'] = placed
    return units.import_data(data).to_bytes()


def _files(melee=True):
    files = {
        'war3map.w3i': _info(),
        'war3map.wts': WtsFile().import_data({
            1: 'Twin |cffffcc00Rivers|r', 2: 'Someone', 3: 'Two bases.|nCross the river.'})
            .to_bytes(),
    }
    if melee:
        files['war3mapUnits.doo'] = _units()
    return files


class MapDescriptionTest(unittest.TestCase):

    def test_the_summary_has_the_maps_data(self):
        summary = map_summary(_files())
        self.assertEqual(summary['name'], 'Twin |cffffcc00Rivers|r')
        self.assertEqual(summary['author'], 'Someone')
        self.assertEqual(summary['tileset'], 'Lordaeron Summer')
        self.assertEqual(summary['size'], (64, 64))
        self.assertEqual([(p['name'], p['controller'], p['race']) for p in summary['players']],
                         [('Player 1', 'User', 'Human'), ('Player 2', 'User', 'Orc')])
        self.assertEqual(summary['forces'], [{'name': 'Force 1',
                                              'players': ['Player 1', 'Player 2']}])
        self.assertTrue(summary['melee'])
        self.assertEqual(len(summary['start_locations']), 2)
        self.assertEqual([(m['gold'], m['main']) for m in summary['gold_mines']],
                         [(12500, True), (12500, True), (8000, False)])

    def test_bbcode(self):
        text = render(map_summary(_files()))
        self.assertIn('[B]Twin [COLOR=#ffcc00]Rivers[/COLOR][/B]', text)
        self.assertIn('Two bases.\nCross the river.', text)
        self.assertIn('[B]Players:[/B] 2 (1v1)', text)
        self.assertIn('[*]Force 1: Player 1, Player 2', text)
        self.assertIn('[*]Gold mines: 3 (2 at start locations, 1 expansion)', text)
        self.assertIn('[*]8,000 gold expansion', text)
        self.assertNotIn('$', text)

    def test_markdown_drops_the_color_codes(self):
        text = render(map_summary(_files()), 'markdown')
        self.assertTrue(text.startswith('# Twin Rivers\n'))
        self.assertIn('| Tileset | Lordaeron Summer |', text)
        self.assertIn('- Start locations: 2', text)

    def test_a_map_with_no_placed_units_has_no_melee_part(self):
        text = render(map_summary(_files(melee=False)))
        self.assertNotIn('Melee', text)

    def test_a_template_of_ones_own(self):
        text = render(map_summary(_files()), template='$name by $author on $tileset, $nothing')
        self.assertEqual(text, 'Twin [COLOR=#ffcc00]Rivers[/COLOR] by Someone on '
                               'Lordaeron Summer, $nothing\n')

    def test_a_wrong_format_is_refused(self):
        with self.assertRaises(ValueError):
            render(map_summary(_files()), 'html')

    def test_cli(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, 'map.w3x')
        _write_map(_files(), path)
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))
        out = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'map-description', path,
                              '--format', 'markdown'], capture_output=True, check=True,
                             env=env).stdout.decode('utf-8')
        self.assertTrue(out.startswith('# Twin Rivers'))
        template = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'map-description',
                                   '--print-template'], capture_output=True, check=True,
                                  env=env).stdout.decode('utf-8')
        self.assertIn('$melee', template)


if __name__ == '__main__':
    unittest.main()
