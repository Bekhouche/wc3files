"""wc3files.doo.merge and `doodads-merge` (#219): the two checked-in
war3map.doo samples (formats 8 and 13) merged, read back, and checked for
editor ids, references the script makes, item tables and special
doodads."""
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers
from wc3files.doo import MapDoodadsFile
from wc3files.doo.merge import merge_doodads, referenced_destructables
from wc3files.w3x.modes import _read_files, _write_map

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sample(name):
    handler = MapDoodadsFile()
    handler.read(_helpers.sample_path(name))
    return handler.get_data()


def _read_back(handler):
    again = MapDoodadsFile()
    again.read(handler.to_bytes())
    return again.get_data()


class MergeDoodadsTest(unittest.TestCase):

    def setUp(self):
        self.v8 = _sample('sample_doodads_v8_11.doo')
        self.v13 = _sample('sample_doodads_v13_11.doo')

    def test_every_record_is_kept_in_order_and_reads_back(self):
        merged, report = merge_doodads([self.v8, self.v13])
        data = _read_back(merged)
        self.assertEqual(len(data['doodads']), len(self.v8['doodads']) + len(self.v13['doodads']))
        self.assertEqual([r['id'] for r in data['doodads']],
                         [r['id'] for r in self.v8['doodads'] + self.v13['doodads']])
        # The newest format is written, so the format 13 records keep their fields.
        self.assertEqual(report['version'], (self.v13['version'], self.v13['subversion']))
        tail = data['doodads'][len(self.v8['doodads']):]
        self.assertEqual([r.get('roll') for r in tail], [r.get('roll') for r in self.v13['doodads']])

    def test_the_first_keeps_its_ids_and_the_others_follow(self):
        merged, report = merge_doodads([self.v8, self.v13])
        ids = [r['editor_id'] for r in merged.get_data()['doodads']]
        first = [r['editor_id'] for r in self.v8['doodads']]
        self.assertEqual(ids[:len(first)], first)
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids[len(first)], max(first) + 1)
        self.assertEqual(report['renumbered'][0], {})
        self.assertEqual(len(report['renumbered'][1]),
                         sum(1 for r, n in zip(self.v13['doodads'], ids[len(first):])
                             if r['editor_id'] != n))

    def test_renumber_all_counts_from_zero(self):
        merged, _ = merge_doodads([self.v13, self.v13], renumber_all=True)
        ids = [r['editor_id'] for r in merged.get_data()['doodads']]
        self.assertEqual(ids, list(range(len(ids))))

    def test_ids_the_first_source_repeats_are_reported(self):
        first = copy.deepcopy(self.v8)
        first['doodads'][1]['editor_id'] = first['doodads'][0]['editor_id']
        _, report = merge_doodads([first, self.v13])
        self.assertEqual(report['duplicate_ids'], [first['doodads'][0]['editor_id']])
        _, report = merge_doodads([first, self.v13], renumber_all=True)
        self.assertEqual(report['duplicate_ids'], [])

    def test_the_sources_are_not_changed(self):
        before = copy.deepcopy(self.v13)
        merge_doodads([self.v8, self.v13], renumber_all=True)
        self.assertEqual(self.v13, before)

    def test_a_renumbered_destructable_the_script_names_is_reported(self):
        record = self.v13['doodads'][0]
        name = f"gg_dest_{record['id']}_{record['editor_id']:04d}"
        script = f'function main takes nothing returns nothing\n' \
                 f'    call KillDestructable({name})\nendfunction\n'
        refs = referenced_destructables(script.encode())
        self.assertEqual(refs, {(record['id'], record['editor_id'])})
        _, report = merge_doodads([self.v8, self.v13], references=[set(), refs])
        new = report['renumbered'][1].get(record['editor_id'])
        expected = [] if new is None else [{'source': 1, 'id': record['id'],
                                            'old': record['editor_id'], 'new': new}]
        self.assertEqual(report['broken_references'], expected)
        # The first source keeps its numbers, so its references never break.
        _, report = merge_doodads([self.v13, self.v8], references=[refs, set()])
        self.assertEqual(report['broken_references'], [])

    def test_item_tables_of_the_other_sources_are_reported(self):
        second = copy.deepcopy(self.v8)
        second['doodads'][2]['item_table_pointer'] = 3
        first = copy.deepcopy(self.v8)
        first['doodads'][1]['item_table_pointer'] = 1
        _, report = merge_doodads([first, second])
        self.assertEqual(report['item_tables'], [{'source': 1, 'index': 2,
                                                  'id': second['doodads'][2]['id'],
                                                  'pointer': 3}])

    def test_duplicate_special_doodads_are_left_out(self):
        first = copy.deepcopy(self.v8)
        first['specials'] = [{'id': 'D000', 'variation': 0, 'x': 1, 'y': 2}]
        second = copy.deepcopy(self.v8)
        second['specials'] = [{'id': 'D000', 'variation': 0, 'x': 1, 'y': 2},
                              {'id': 'D000', 'variation': 0, 'x': 5, 'y': 2}]
        merged, report = merge_doodads([first, second])
        self.assertEqual(report['specials_dropped'], 1)
        self.assertEqual([(s['x'], s['y']) for s in _read_back(merged)['specials']],
                         [(1, 2), (5, 2)])

    def test_cli_merges_maps_and_reports_a_broken_variable(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        with open(_helpers.sample_path('sample_doodads_v8_11.doo'), 'rb') as handle:
            first_doo = handle.read()
        with open(_helpers.sample_path('sample_doodads_v13_11.doo'), 'rb') as handle:
            second_doo = handle.read()
        record = self.v13['doodads'][-1]
        name = f"gg_dest_{record['id']}_{record['editor_id']:04d}"
        first_map = os.path.join(tmp, 'first.w3x')
        second_map = os.path.join(tmp, 'second.w3x')
        _write_map({'war3map.doo': first_doo, 'war3map.w3i': b'info'}, first_map)
        _write_map({'war3map.doo': second_doo,
                    'war3map.j': f'globals\n    destructable {name} = null\nendglobals\n'.encode()},
                   second_map)
        output = os.path.join(tmp, 'out.w3x')
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'doodads-merge',
                                 output, first_map, second_map], capture_output=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        files = _read_files(output)
        self.assertEqual(files['war3map.w3i'], b'info')
        data = MapDoodadsFile()
        data.read(files['war3map.doo'])
        self.assertEqual(len(data.get_data()['doodads']),
                         len(self.v8['doodads']) + len(self.v13['doodads']))
        renumbered = record['editor_id'] != max(r['editor_id'] for r in self.v8['doodads']) \
            + len(self.v13['doodads'])
        self.assertEqual(result.returncode, 1 if renumbered else 0)
        if renumbered:
            self.assertIn(f'breaks: {name}'.encode(), result.stdout)


if __name__ == '__main__':
    unittest.main()
