"""wc3files.w3o.merge and `objects-merge` (#217) on two small sets of
object data built here. Both have a custom ability A000 and a custom unit
h000, and both edit the default footman; the second also has a unit that
lists its A000, a TRIGSTR_ name, and an edit of the knight that the first
does not have."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.w3o.merge import merge_objects, rewrite_ids
from wc3files.w3o.object_file import ObjectFile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _text(value):
    return {'value': value, 'type': 3}


def _ability(name):
    return {'ID': 'AHtb', 'anam': {'value': {0: name}, 'type': 3, 'ptr': {0: 0}}}


def _first():
    return {
        'w3a': {'original': {}, 'custom': {'A000': _ability('Hammer')}},
        'w3u': {'original': {'hfoo': {'unam': _text('First Footman')}},
                'custom': {'h000': {'ID': 'hpea', 'unam': _text('Worker'),
                                    'uabi': _text('A000')}}},
    }


def _second():
    return {
        'w3a': {'original': {}, 'custom': {'A000': _ability('Storm')}},
        'w3u': {'original': {'hfoo': {'unam': _text('Second Footman')},
                             'hkni': {'unam': _text('Knight')}},
                'custom': {'h000': {'ID': 'hrif', 'unam': _text('Rifle')},
                           'h001': {'ID': 'hpea', 'unam': _text('TRIGSTR_007'),
                                    'uabi': _text('A000,AHbz'), 'ubui': _text('h000,hhou')}}},
    }


def _name(objects, ext, table, object_id):
    field = objects[ext][table][object_id]['anam' if ext == 'w3a' else 'unam']['value']
    return field[0] if isinstance(field, dict) else field


class MergeObjectsTest(unittest.TestCase):

    def test_remap_gives_free_rawcodes_and_rewrites_the_lists(self):
        merged, report = merge_objects(_first(), _second())
        table = report['table']
        self.assertEqual(sorted(table), ['A000', 'h000', 'hfoo'])
        # A free rawcode keeps the old first letter and is no default id.
        self.assertEqual(table['A000'][0], 'A')
        self.assertNotIn(table['A000'], ('A000',))
        self.assertEqual(_name(merged, 'w3a', 'custom', 'A000'), 'Hammer')
        self.assertEqual(_name(merged, 'w3a', 'custom', table['A000']), 'Storm')
        self.assertEqual(merged['w3u']['custom']['h001']['uabi']['value'],
                         table['A000'] + ',AHbz')
        self.assertEqual(merged['w3u']['custom']['h001']['ubui']['value'],
                         table['h000'] + ',hhou')
        # The first side's own list still names its own A000.
        self.assertEqual(merged['w3u']['custom']['h000']['uabi']['value'], 'A000')
        self.assertEqual(report['references_rewritten'], 2)

    def test_remap_turns_a_conflicting_edit_into_a_new_object(self):
        merged, report = merge_objects(_first(), _second())
        new = report['table']['hfoo']
        self.assertEqual(new[0], 'h')
        self.assertEqual(merged['w3u']['custom'][new]['ID'], 'hfoo')
        self.assertEqual(_name(merged, 'w3u', 'custom', new), 'Second Footman')
        self.assertEqual(_name(merged, 'w3u', 'original', 'hfoo'), 'First Footman')
        # An edit only the second has goes in as it is.
        self.assertEqual(_name(merged, 'w3u', 'original', 'hkni'), 'Knight')
        self.assertEqual(report['added'], 2)

    def test_first_keeps_the_first_side(self):
        merged, report = merge_objects(_first(), _second(), on_conflict='first',
                                       on_edit_conflict='first')
        self.assertEqual(report['table'], {})
        self.assertEqual(_name(merged, 'w3a', 'custom', 'A000'), 'Hammer')
        self.assertEqual(_name(merged, 'w3u', 'original', 'hfoo'), 'First Footman')
        self.assertEqual(sorted(report['kept_first']),
                         [('w3a', 'A000'), ('w3u', 'h000'), ('w3u', 'hfoo')])

    def test_second_takes_the_second_side(self):
        merged, report = merge_objects(_first(), _second(), on_conflict='second',
                                       on_edit_conflict='second')
        self.assertEqual(_name(merged, 'w3a', 'custom', 'A000'), 'Storm')
        self.assertEqual(_name(merged, 'w3u', 'custom', 'h000'), 'Rifle')
        self.assertEqual(_name(merged, 'w3u', 'original', 'hfoo'), 'Second Footman')
        self.assertEqual(len(report['kept_second']), 3)

    def test_policies_mix_and_taken_ids_are_avoided(self):
        first = _first()
        merged, report = merge_objects(first, _second(), on_conflict='remap',
                                       on_edit_conflict='first', taken={'A001', 'h001', 'h002'})
        self.assertEqual(sorted(report['table']), ['A000', 'h000'])
        self.assertNotIn(report['table']['A000'], {'A000', 'A001'})
        self.assertNotIn(report['table']['h000'], {'h000', 'h001', 'h002'})
        self.assertEqual(report['kept_first'], [('w3u', 'hfoo')])
        # The sides are not changed.
        self.assertEqual(first, _first())

    def test_strings_of_the_second_side_are_reported(self):
        _, report = merge_objects(_first(), _second())
        self.assertEqual(report['strings'], ['TRIGSTR_007'])

    def test_a_wrong_policy_is_refused(self):
        with self.assertRaises(ValueError):
            merge_objects(_first(), _second(), on_conflict='keep')

    def test_rewrite_ids_changes_whole_items_only(self):
        objects = {'w3u': {'original': {}, 'custom': {
            'h009': {'ID': 'h000', 'uabi': _text('A000,A0001,xA000')}}}}
        out, count = rewrite_ids(objects, {'A000': 'A00Z', 'h000': 'h00Z'})
        self.assertEqual(out['w3u']['custom']['h009']['uabi']['value'], 'A00Z,A0001,xA000')
        self.assertEqual(out['w3u']['custom']['h009']['ID'], 'h000')
        self.assertEqual(count, 1)

    def test_the_merged_objects_write_and_read_back(self):
        merged, report = merge_objects(_first(), _second())
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for ext in ('w3a', 'w3u'):
            path = os.path.join(tmp, 'war3map.' + ext)
            handler = ObjectFile()
            handler.import_data({ext: merged[ext]})
            handler.write(path)
            again = ObjectFile()
            again.read(path)
            back = again.export_data()[ext]
            self.assertEqual(sorted(back['custom']), sorted(merged[ext]['custom']))
            self.assertEqual(sorted(back['original']), sorted(merged[ext]['original']))

    def test_cli_merges_two_files(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        paths = []
        for name, side in (('a.w3u', _first()), ('b.w3u', _second())):
            path = os.path.join(tmp, name)
            handler = ObjectFile()
            handler.import_data({'w3u': side['w3u']})
            handler.write(path)
            paths.append(path)
        output = os.path.join(tmp, 'out.w3u')
        table = os.path.join(tmp, 'table.json')
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'objects-merge', output,
                                 *paths, '--table', table], capture_output=True, check=True,
                                env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        self.assertIn(b'remapped: h000 ->', result.stdout)
        handler = ObjectFile()
        handler.read(output)
        self.assertEqual(len(handler.export_data()['w3u']['custom']), 4)
        self.assertTrue(os.path.exists(table))


if __name__ == '__main__':
    unittest.main()
