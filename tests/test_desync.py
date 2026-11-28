import io
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from wc3files.cli.main import main
from wc3files.desync import (DesyncLog, DesyncReport, code_name, diff_logs, find_files,
                             summarize_folder)

RULE = '=' * 71


def code(text):
    return struct.unpack('>I', text.encode('ascii'))[0]


def block(category, turn, checksum, lines):
    return '\r\n'.join([RULE, f'[Desync - {code(category)} - Turn({turn:08d}) = {checksum}]']
                       + lines) + '\r\n'


def record(turn, gwar_value=0x10, cust=(7, 8)):
    """One desync record of made-up blocks, newest turn first, in the
    order the game writes them."""
    out = ''
    for category in ('ipse', 'rand', 'cust', 'gwar'):
        for t in (turn, turn - 1, turn - 2):
            if category == 'cust':
                for n, value in enumerate(cust):
                    out += block('cust', t, value + n, [f'\t#{n}#: 0x{value:08X}'])
            elif category == 'gwar':
                out += block('gwar', t, 1000 + t, [
                    f'\t#0#: 0x00000005',
                    f"\t#{code('pnum')}#",
                    f"\t\t#{code('plys')}#",
                    f"\t\t\t#{code('tech')}#: 0x{gwar_value:08X}",
                    f"\t\t\t#{code('teci')}#: 0x48303035",
                    f"\t\t#{code('plyy')}#: 0x00000001"])
            else:
                out += block(category, t, 500 + t, [f'\t#0#: 0x{t:08X}'])
    return out


REPORT = '\r\n'.join([
    '<Application>Warcraft III',
    '<Exception.BuildNumber>24268',
    '',
    '<Exception.Summary:>',
    'Network desync on turn 00102 in game W3-TEST',
    '<:Exception.Summary>',
    '<Exception.Assertion:>',
    'War3 build 7000',
    'War3 game checksum 0000044e',
    'War3 rand checksum 00000001',
    'War3 custom unit checksum 00000007',
    'War3 cheat checksum 00000000',
    '<:Exception.Assertion>',
    '',
    '<Map> <GameDocumentRoot>/Maps/Download/Test.w3x',
    '<PlayerCount> 4',
    '<GameType> Custom',
    'SoundDevices: 1',
    ''])


class TestDesyncLog(unittest.TestCase):
    def test_read(self):
        log = DesyncLog().read(record(102))
        self.assertEqual(len(log.blocks), 3 * 5)
        first = log.blocks[0]
        self.assertEqual((code_name(first['category']), first['turn'], first['checksum']),
                         ('ipse', 102, 602))
        gwar = log.blocks[-1]
        self.assertEqual(gwar['turn'], 100)
        self.assertEqual(gwar['items'][1], {'id': code('pnum'), 'items': [
            {'id': code('plys'), 'items': [{'id': code('tech'), 'value': 0x10},
                                          {'id': code('teci'), 'value': 0x48303035}]},
            {'id': code('plyy'), 'value': 1}]})
        named = log.to_dict()['records'][0]['blocks'][-1]['items'][1]
        self.assertEqual((named['name'], named['items'][0]['items'][0]['value']),
                         ('pnum', '0x00000010'))

    def test_records(self):
        # the file grows: an earlier game's record, then two of this game
        log = DesyncLog().read(record(43775) + record(80) + record(102))
        self.assertEqual([(r['turn'], r['turns'], len(r['blocks'])) for r in log.records()],
                         [(43775, [43773, 43774, 43775], 15), (80, [78, 79, 80], 15),
                          (102, [100, 101, 102], 15)])
        # two desyncs one turn apart are still two records
        self.assertEqual([r['turn'] for r in DesyncLog().read(record(80) + record(81)).records()],
                         [80, 81])

    def test_unknown_line_raises(self):
        with self.assertRaises(ValueError):
            DesyncLog().read(record(10) + 'something else\r\n')
        with self.assertRaises(ValueError):       # a value two levels down
            DesyncLog().read(block('rand', 5, 1, ['\t\t#0#: 0x00000001']))
        with self.assertRaises(ValueError):       # a value before any block
            DesyncLog().read('\t#0#: 0x00000001\n')

    def test_code_name(self):
        self.assertEqual(code_name(1735876978), 'gwar')
        self.assertIsNone(code_name(5))

    def test_diff(self):
        same = diff_logs(DesyncLog().read(record(102)), DesyncLog().read(record(102)))
        self.assertEqual(same, [])
        apart = diff_logs(DesyncLog().read(record(102)),
                          DesyncLog().read(record(102, gwar_value=0x11, cust=(7, 9))))
        self.assertEqual([(d['turn'], code_name(d['category']), d['index']) for d in apart],
                         [(t, c, i) for t in (100, 101, 102)
                          for c, i in (('cust', 1), ('gwar', 0))])
        first = apart[0]
        self.assertEqual((first['checksums'], first['values']), ((9, 10), (8, 9)))
        gwar = apart[1]
        self.assertEqual(gwar['path'], [(code('pnum'), 1), (code('plys'), 0), (code('tech'), 0)])
        self.assertEqual(gwar['values'], (0x10, 0x11))
        # a block only one log has
        only = diff_logs(DesyncLog().read(record(102)), DesyncLog().read(b''))
        self.assertEqual(only[0]['checksums'], (500 + 100, None))


class TestDesyncReport(unittest.TestCase):
    def test_read(self):
        report = DesyncReport().read(REPORT)
        self.assertEqual((report.turn, report.game, report.build), (102, 'W3-TEST', 24268))
        self.assertEqual(report.checksums, {'game': 0x44E, 'rand': 1, 'custom unit': 7,
                                            'cheat': 0})
        self.assertEqual(report.fields['PlayerCount'], '4')
        self.assertEqual(report.other, ['SoundDevices: 1'])

    def test_unclosed_section(self):
        with self.assertRaises(ValueError):
            DesyncReport().read('<Exception.Summary:>\nNetwork desync\n')


class TestFolder(unittest.TestCase):
    def folder(self, tmp, name, log):
        path = os.path.join(tmp, name)
        os.makedirs(path)
        with open(os.path.join(path, 'Desync.txt'), 'w', encoding='utf-8', newline='') as f:
            f.write(REPORT)
        with open(os.path.join(path, 'PC_010126_120000_Desync.log'), 'w', encoding='utf-8',
                  newline='') as f:
            f.write(log)
        return path

    def test_summary_and_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = self.folder(tmp, '2026-01-01 12.05.09 0a1b2c3d', record(80) + record(102))
            two = self.folder(tmp, '2026-01-01 12.05.10 0a1b2c3e',
                              record(80) + record(102, gwar_value=0x11))
            self.assertEqual(find_files(one)['replay'], None)
            summary = summarize_folder(one)
            self.assertEqual((summary['date'], summary['turn'], summary['player_count']),
                             ('2026-01-01 12:05:09', 102, 4))
            self.assertEqual([r['turn'] for r in summary['records']], [80, 102])
            # game is gwar's checksum at turn 102, custom unit the first cust block
            self.assertEqual(summary['checksums_in_log'],
                             {'game': True, 'rand': False, 'custom unit': True})
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['desync-info', one]), 0)
            self.assertIn('log record  : turns 100 to 102, 15 blocks', out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(['desync-diff', one, two]), 0)
            self.assertEqual(out.getvalue().splitlines(),
                             ['turn 100  gwar  0000044c / 0000044c  first at '
                              'pnum[1]/plys[0]/tech[0]: 0x00000010 / 0x00000011'])
            target = os.path.join(tmp, 'diff.json')
            main(['desync-diff', one, two, '--all', '--json', '--out', target])
            with open(target, encoding='utf-8') as handle:
                found = json.load(handle)
            self.assertEqual([d['turn'] for d in found], [100, 101, 102])
            self.assertEqual(found[0]['path'][0], ['pnum', 1])


if __name__ == '__main__':
    unittest.main()
