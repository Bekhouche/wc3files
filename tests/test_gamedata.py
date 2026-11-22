"""Tests for wc3files.gamedata: game-data profiles from an install.

A classic install is built here from MpqWriter archives (war3.mpq, and a
war3patch.mpq that overrides one table, as real patches do); the extracted
profile must take the patched copy, list what the version lacks, and, once
in use, be what the object metadata reads.
"""

import json
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401

from wc3files import gamedata
from wc3files.mpq.writer import MpqWriter
from wc3files.w3o.object_metadata import ObjectMetaData
from wc3files.w3o.data import ObjectFileType


def _bundled(relative):
     with open(gamedata.bundled(relative), 'rb') as handle:
          return handle.read()


class TestClassicProfile(unittest.TestCase):

     def setUp(self):
          self.work = tempfile.mkdtemp()
          self.install = os.path.join(self.work, 'Warcraft III 1.27')
          os.makedirs(self.install)
          base = {}
          for relative, casc_path in gamedata.FILES.items():
               if 'Skin' in relative or 'skin' in relative:
                    continue                    # a classic version has no skin tables
               base[gamedata.classic_path(casc_path.format(locale='enUS'))] = _bundled(relative)
          MpqWriter().write(base, os.path.join(self.install, 'war3.mpq'))
          # the patch changes one row of UnitMetaData: a field renamed
          meta = _bundled('w3o/gamedata/UnitMetaData.slk').replace(b'"Name"', b'"NamePatched"', 1)
          self.assertNotEqual(meta, _bundled('w3o/gamedata/UnitMetaData.slk'))
          MpqWriter().write({'Units\\UnitMetaData.slk': meta},
                            os.path.join(self.install, 'War3Patch.mpq'))
          self.profile = os.path.join(self.work, 'enUS-1.27')

     def tearDown(self):
          gamedata.use(None)
          shutil.rmtree(self.work, ignore_errors=True)

     def test_extract_takes_the_patch_and_lists_what_is_missing(self):
          profile = gamedata.extract(self.install, self.profile)
          self.assertEqual(profile['refused'], {})
          self.assertTrue(profile['files']['w3o/gamedata/UnitMetaData.slk']['from']
                          .startswith('War3Patch.mpq:'))
          self.assertTrue(profile['files']['w3o/gamedata/UnitData.slk']['from']
                          .startswith('war3.mpq:'))
          self.assertIn('w3o/gamedata/SkinMetaData.slk', profile['missing'])
          with open(os.path.join(self.profile, 'profile.json'), encoding='utf-8') as handle:
               self.assertEqual(json.load(handle)['source'],
                                'classic (war3.mpq, War3Patch.mpq)')

     def test_use_switches_what_is_read(self):
          gamedata.extract(self.install, self.profile)
          fields = lambda: {row['field'] for row in
                            ObjectMetaData.default().fields(ObjectFileType.Units).values()}
          self.assertIn('Name', fields())
          gamedata.use(self.profile)
          self.assertIn('NamePatched', fields())
          # a table the profile lacks still loads, from the bundled copy
          self.assertTrue(ObjectMetaData.default().fields(ObjectFileType.Abilities))
          gamedata.use(None)
          self.assertIn('Name', fields())

     def test_not_a_profile(self):
          with self.assertRaises(ValueError):
               gamedata.use(self.work)


if __name__ == '__main__':
     unittest.main()
