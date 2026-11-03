"""Tests for wc3files.wtg.ydwe_ui: GUI trigger definitions as YDWE's ui
folder and as the World Editor's TriggerData.txt / TriggerStrings.txt.

Both directions are checked on the bundled Reforged files (every function,
type and category, the stray `_..._Defaults` line Blizzard's file has
included); a plugin merge and a language folder on small hand-written
definitions.
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401

from wc3files.cli.main import main as cli_main
from wc3files.wtg import TriggerData, ydwe_ui
from wc3files import gamedata


def _bundled():
     with open(gamedata.bundled('wtg/gamedata/TriggerData.txt'), encoding='utf-8',
               errors='replace') as h:
          data = h.read()
     with open(gamedata.bundled('wtg/gamedata/TriggerStrings.txt'), encoding='utf-8',
               errors='replace') as h:
          strings = h.read()
     return data, strings


PLUGIN = '''[TriggerActions]
YDWEDamage=0,unit,real
_YDWEDamage_Defaults=GetTriggerUnit,100
_YDWEDamage_Limits=_,_,0,_
_YDWEDamage_Category=TC_UNIT

[TriggerActionStrings]
YDWEDamage="Damage"
YDWEDamage="Damage ",~Unit," by ",~Amount
YDWEDamageHint="Deals damage."
'''


class TestConversions(unittest.TestCase):

     def setUp(self):
          self.work = tempfile.mkdtemp()

     def tearDown(self):
          shutil.rmtree(self.work, ignore_errors=True)

     def test_bundled_both_ways(self):
          data, strings = _bundled()
          ui = ydwe_ui.read_trigger_data(data, strings)
          self.assertGreater(len(ui.functions['action']), 500)
          ydwe_ui.write_ui(ui, self.work)
          self.assertEqual(ydwe_ui.read_ui(self.work), ui)
          data2, strings2 = ydwe_ui.write_trigger_data(ui)
          self.assertEqual(ydwe_ui.read_trigger_data(data2, strings2), ui)
          path = os.path.join(self.work, 'TriggerData.txt')
          with open(path, 'w', encoding='utf-8') as h:
               h.write(data2)
          original, rewritten = TriggerData(), TriggerData()
          original.read(gamedata.bundled('wtg/gamedata/TriggerData.txt'))
          rewritten.read(path)
          self.assertEqual(original._arg_counts, rewritten._arg_counts)

     def test_function_fields(self):
          entry = ydwe_ui.read_trigger_data(PLUGIN, PLUGIN).functions['action']['YDWEDamage']
          self.assertEqual(entry['description'], 'Damage ${Unit} by ${Amount}')
          self.assertEqual(entry['comment'], 'Deals damage.')
          self.assertEqual(entry['args'], [{'type': 'unit', 'default': 'GetTriggerUnit'},
                                           {'type': 'real', 'default': '100', 'min': '0'}])

     def test_merge_and_language(self):
          base = ydwe_ui.read_trigger_data(*_bundled())
          plugin = ydwe_ui.read_trigger_data(PLUGIN, PLUGIN)
          plugin.functions['action']['KillUnit'] = {'name': 'KillUnit', 'version': '0',
                                                    'args': [], 'title': 'Replaced'}
          merged = ydwe_ui.merge(base, plugin)
          self.assertIn('YDWEDamage', merged.functions['action'])
          self.assertEqual(merged.functions['action']['KillUnit']['title'], 'Replaced')
          folder = os.path.join(self.work, 'ui')
          ydwe_ui.write_ui(plugin, folder)
          os.makedirs(os.path.join(folder, 'zhCN'))
          with open(os.path.join(folder, 'zhCN', 'action.txt'), 'w', encoding='utf-8') as h:
               h.write('[YDWEDamage]\ntitle = "伤害"\n')
          self.assertEqual(ydwe_ui.read_ui(folder, 'zhCN').functions['action']['YDWEDamage']
                           ['title'], '伤害')

     def test_cli_both_ways(self):
          source = os.path.join(self.work, 'TriggerData.txt')
          with open(source, 'w', encoding='utf-8') as h:
               h.write(PLUGIN)
          folder = os.path.join(self.work, 'ui')
          out = os.path.join(self.work, 'out')
          with contextlib.redirect_stdout(io.StringIO()):
               self.assertEqual(cli_main(['trigger-ui', source, source, folder]), 0)
               self.assertEqual(cli_main(['trigger-ui', folder, out]), 0)
          with open(os.path.join(out, 'TriggerStrings.txt'), encoding='utf-8') as h:
               self.assertIn('YDWEDamage="Damage ",~Unit," by ",~Amount', h.read())


if __name__ == '__main__':
     unittest.main()
