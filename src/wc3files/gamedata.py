"""
Game data for a chosen Warcraft III version.

The package bundles one version's tables: Reforged's object-data SLKs and
strings, common.j/Blizzard.j, TriggerData.txt. A map made for 1.24 or 1.27
has object fields, natives and GUI functions that version had, and is best
read against that version's tables. A *profile* is a folder holding the same
files, taken from a real game install:

    wc3files gamedata-extract "C:/Games/Warcraft III 1.27" profiles/enUS-1.27.1
    wc3files --game-data profiles/enUS-1.27.1 obj-to-slk ...

    from wc3files import gamedata
    gamedata.extract('C:/Games/Warcraft III 1.27', 'profiles/enUS-1.27.1')
    gamedata.use('profiles/enUS-1.27.1')     # object data, checkers, triggers
    gamedata.use(None)                       # back to the bundled tables

`extract` reads either kind of install: a classic one (1.31 and older:
war3.mpq, war3x.mpq, their locale MPQs and war3patch.mpq, later archives
overriding earlier ones) or a CASC one (1.32 and later). Each file is parsed
with this package's own reader before it is written, as
tools/refresh_gamedata.py does for the bundled copies, and a profile records
where every file came from in `profile.json`.

A file a version does not have (the skin tables are Reforged-only) is left
out of its profile; the bundled copy is used for it, and `profile.json`
lists what was missing.
"""

import json
import os

_PACKAGE = os.path.dirname(os.path.abspath(__file__))

# bundled relative path -> path inside a CASC install (1.32+); the classic
# MPQ path is the same without the "War3.w3mod:" and locale prefixes
FILES = {
     'scripts/gamedata/blizzard.j': 'War3.w3mod:Scripts/Blizzard.j',
     'scripts/gamedata/common.j': 'War3.w3mod:Scripts/common.j',
     'scripts/gamedata/common.ai': 'War3.w3mod:Scripts/common.ai',
     'wtg/gamedata/TriggerData.txt': 'War3.w3mod:UI/TriggerData.txt',
     'wtg/gamedata/TriggerStrings.txt': 'War3.w3mod:_Locales/{locale}.w3mod:UI/TriggerStrings.txt',
     'w3o/gamedata/UnitEditorData.txt': 'War3.w3mod:UI/UnitEditorData.txt',
     'w3o/gamedata/WorldEditGameStrings.txt':
          'War3.w3mod:_Locales/{locale}.w3mod:UI/WorldEditGameStrings.txt',
     'w3o/gamedata/WorldEditStrings.txt':
          'War3.w3mod:_Locales/{locale}.w3mod:UI/WorldEditStrings.txt',
     'w3o/gamedata/SkinMetaData.slk': 'War3.w3mod:UI/SkinMetaData.slk',
     'w3o/gamedata/DoodadMetaData.slk': 'War3.w3mod:Doodads/DoodadMetaData.slk',
     'w3o/gamedata/Doodads.slk': 'War3.w3mod:Doodads/Doodads.slk',
}
for _name in ('AbilityBuffData', 'AbilityBuffMetaData', 'AbilityData', 'AbilityMetaData',
              'DestructableData', 'DestructableMetaData', 'ItemData', 'MiscMetaData',
              'UnitAbilities', 'UnitBalance', 'UnitData', 'UnitMetaData', 'UnitUI',
              'UnitWeapons', 'UpgradeData', 'UpgradeEffectMetaData', 'UpgradeMetaData'):
     FILES[f'w3o/gamedata/{_name}.slk'] = f'War3.w3mod:Units/{_name}.slk'
for _name in (
          'abilityskinstrings', 'campaignabilitystrings', 'campaignunitstrings',
          'campaignupgradestrings', 'commonabilitystrings', 'destructableskinstrings',
          'humanabilitystrings', 'humanunitstrings', 'humanupgradestrings',
          'itemabilitystrings', 'itemskinstrings', 'itemstrings', 'neutralabilitystrings',
          'neutralunitstrings', 'neutralupgradestrings', 'nightelfabilitystrings',
          'nightelfunitstrings', 'nightelfupgradestrings', 'orcabilitystrings',
          'orcunitstrings', 'orcupgradestrings', 'undeadabilitystrings',
          'undeadunitstrings', 'undeadupgradestrings', 'unitglobalstrings',
          'unitskinstrings', 'upgradeskinstrings'):
     FILES[f'w3o/gamedata/strings/{_name}.txt'] = (
          f'War3.w3mod:_Locales/{{locale}}.w3mod:Units/{_name}.txt')

# classic installs: later archives override earlier ones
_CLASSIC_ARCHIVES = ('war3.mpq', 'war3local.mpq', 'war3x.mpq', 'war3xlocal.mpq',
                     'war3patch.mpq')


def classic_path(casc_path):
     """The path of a CASC file in a classic install's MPQs."""
     path = casc_path.split(':')[-1]
     return path.replace('/', '\\')


# -- using a profile -------------------------------------------------------------

_active = None


def active():
     """The folder of the profile in use, or None for the bundled tables."""
     return _active


def bundled(relative):
     return os.path.join(_PACKAGE, *relative.split('/'))


def path(relative):
     """Where to read one of the tables (a bundled relative path) from."""
     if _active is not None:
          candidate = os.path.join(_active, *relative.split('/'))
          if os.path.isfile(candidate):
               return candidate
     return bundled(relative)


def use(profile):
     """Read game data from `profile` (a folder made by extract), or from the
     bundled tables again with None. Every cached table is reloaded."""
     global _active
     if profile is not None:
          profile = os.path.abspath(profile)
          if not os.path.isfile(os.path.join(profile, 'profile.json')):
               raise ValueError(f'{profile} is not a game-data profile (no profile.json)')
     _active = profile
     _rebind()


def _rebind():
     """Point every module's data directory at the active profile and drop
     what they cached."""
     import importlib
     modules = (
          ('wc3files.w3o.object_metadata', '_METADATA_DIR', 'w3o/gamedata'),
          ('wc3files.w3o.object_base_data', '_METADATA_DIR', 'w3o/gamedata'),
          ('wc3files.w3o.object_base_strings', '_METADATA_DIR', 'w3o/gamedata/strings'),
          ('wc3files.w3o.object_editor_enums', '_METADATA_DIR', 'w3o/gamedata'),
          ('wc3files.w3o.object_override', '_METADATA_DIR', 'w3o/gamedata'),
          ('wc3files.w3o.world_edit_strings', '_METADATA_DIR', 'w3o/gamedata'),
          ('wc3files.scripts.checker', '_GAMEDATA_DIR', 'scripts/gamedata'),
          ('wc3files.scripts.jass_vm', '_GAMEDATA', 'scripts/gamedata'),
          ('wc3files.scripts.wc3_runtime', '_GAMEDATA', 'scripts/gamedata'),
          ('wc3files.vjass.gamedata', '_GAMEDATA', 'scripts/gamedata'),
          ('wc3files.wtg.trigger_text', '_GAMEDATA', 'wtg/gamedata'),
     )
     for module_name, attribute, relative_dir in modules:
          module = importlib.import_module(module_name)
          setattr(module, attribute, _merged_dir(relative_dir))
          for value in vars(module).values():
               if isinstance(value, type) and '_default_instance' in vars(value):
                    value._default_instance = None
          cache = getattr(module, '_cache', None)
          if isinstance(cache, dict):
               cache.clear()
     trigger_text = importlib.import_module('wc3files.wtg.trigger_text')
     trigger_text._TRIGGER_DATA_PATH = path('wtg/gamedata/TriggerData.txt')
     trigger_text._TRIGGER_STRINGS_PATH = path('wtg/gamedata/TriggerStrings.txt')
     trigger_data = importlib.import_module('wc3files.wtg.trigger_data')
     trigger_data._DEFAULT_PATH = path('wtg/gamedata/TriggerData.txt')
     trigger_data.TriggerData._default_instance = None
     importlib.import_module('wc3files.scripts.lua_checker')._COMMON_AI = None
     for module_name in ('wc3files.scripts.wc3_runtime',):
          module = importlib.import_module(module_name)
          for cache_name in ('_PROGRAMS', '_BLIZZARD_LUA'):
               getattr(module, cache_name).clear()


def _merged_dir(relative_dir):
     """A directory to read tables from: the profile's own when it holds all
     of that directory's files, else a merged copy (profile files over the
     bundled ones) in a cache folder, so that modules joining a file name to
     the directory always find each file."""
     base = bundled(relative_dir)
     if _active is None:
          return base
     own = os.path.join(_active, *relative_dir.split('/'))
     if not os.path.isdir(own):
          return base
     names = {n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n))}
     if all(os.path.isfile(os.path.join(own, n)) for n in names):
          return own
     merged = os.path.join(_active, '.merged', *relative_dir.split('/'))
     os.makedirs(merged, exist_ok=True)
     import shutil
     for n in names:
          source = os.path.join(own, n) if os.path.isfile(os.path.join(own, n)) \
               else os.path.join(base, n)
          target = os.path.join(merged, n)
          if not os.path.isfile(target) or os.path.getmtime(target) < os.path.getmtime(source):
               shutil.copyfile(source, target)
     return merged


# -- extracting a profile --------------------------------------------------------

class _ClassicInstall:
     """Read files from a classic install's MPQ archives, later ones winning."""

     def __init__(self, install):
          from wc3files.mpq.mpq import MpqFile
          self.archives = []
          names = {n.lower(): n for n in os.listdir(install)}
          for name in reversed(_CLASSIC_ARCHIVES):
               if name in names:
                    archive = MpqFile()
                    archive.read(os.path.join(install, names[name]))
                    self.archives.append((names[name], archive))
          if not self.archives:
               raise ValueError(f'{install} has no war3.mpq / war3x.mpq / war3patch.mpq')
          self.version = 'classic (' + ', '.join(n for n, _ in reversed(self.archives)) + ')'

     def read(self, casc_path, locale):
          from wc3files.files import BinaryFile
          name = classic_path(casc_path.format(locale=locale))
          for archive_name, archive in self.archives:
               files = archive.mpq_files
               files.set_archive_header(archive.mpq_header)
               files.set_hash_table(archive.mpq_hash_table)
               files.set_block_table(archive.mpq_block_table)
               with BinaryFile(archive.current_path, 'read') as reader:
                    found = files.read_file(reader, name)
               if found is not None and found[0] is not None:
                    return found[0], f'{archive_name}:{name}'
          return None, None


class _CascInstall:
     def __init__(self, install):
          from wc3files.casc import CascStorage
          self.storage = CascStorage().open(install)
          self.version = f'CASC {self.storage.version}'

     def read(self, casc_path, locale):
          path = casc_path.format(locale=locale)
          try:
               return self.storage.read(path), path
          except Exception:
               return None, None


def _check(relative, blob, scratch):
     """Parse `blob` with this package's reader; raises if it does not parse."""
     target = os.path.join(scratch, os.path.basename(relative))
     with open(target, 'wb') as handle:
          handle.write(blob)
     suffix = os.path.splitext(relative)[1].lower()
     if suffix == '.slk':
          from wc3files.table.slk_file import SlkFile
          table = SlkFile()
          table.read(target)
          return f'{len(table.export_data() or [])} rows'
     if suffix in ('.j', '.ai'):
          from wc3files.scripts.jass_parser import parse
          with open(target, encoding='utf-8', errors='replace') as handle:
               program = parse(handle.read())
          return f'{len(program.functions)} functions, {len(program.natives)} natives'
     if os.path.basename(relative).lower() == 'triggerdata.txt':
          from wc3files.wtg import TriggerData
          TriggerData().read(target)
          return 'trigger data parsed'
     return f'{len(blob)} bytes'


def extract(install, out_dir, locale='enUS', name=None):
     """Write a profile of `install`'s tables to `out_dir`; returns the
     profile description (also written as profile.json)."""
     import tempfile
     if os.path.isfile(os.path.join(install, '.build.info')):
          source = _CascInstall(install)
     else:
          source = _ClassicInstall(install)
     os.makedirs(out_dir, exist_ok=True)
     files, missing, refused = {}, [], {}
     with tempfile.TemporaryDirectory() as scratch:
          for relative, casc_path in sorted(FILES.items()):
               blob, origin = source.read(casc_path, locale)
               if blob is None:
                    missing.append(relative)
                    continue
               try:
                    note = _check(relative, blob, scratch)
               except Exception as error:
                    refused[relative] = f'{type(error).__name__}: {error}'
                    continue
               target = os.path.join(out_dir, *relative.split('/'))
               os.makedirs(os.path.dirname(target), exist_ok=True)
               with open(target, 'wb') as handle:
                    handle.write(blob)
               files[relative] = {'from': origin, 'check': note}
     profile = {'name': name or os.path.basename(os.path.abspath(out_dir)),
                'install': os.path.abspath(install), 'source': source.version,
                'locale': locale, 'files': files, 'missing': missing, 'refused': refused}
     with open(os.path.join(out_dir, 'profile.json'), 'w', encoding='utf-8') as handle:
          json.dump(profile, handle, indent=1)
     return profile
