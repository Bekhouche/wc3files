"""Real tests for wc3files.w3o: the ObjectFile binary container plus the
metadata-driven schema/base-data/enum/string pipeline. The metadata-side
assertions are pinned to facts already verified once (and documented in
TODO.md) against a real Reforged install - not guesses."""
import os
import tempfile
import unittest

import _helpers
from wc3files import ObjectFile
from wc3files.data import BinaryDataType, ObjectFileType
from wc3files.w3o.object_metadata import ObjectMetaData
from wc3files.w3o.object_base_data import BaseObjectData
from wc3files.w3o.object_editor_enums import ObjectEditorEnums
from wc3files.w3o.world_edit_strings import WorldEditStrings
from wc3files.w3o.json_file import JsonFile
from wc3files.w3o.wes_file import WesFile


class TestObjectFileRoundTrip(unittest.TestCase):
    def setUp(self):
        self.obj = ObjectFile()
        self.obj.read(_helpers.sample_path('sample.w3u'))

    def test_known_fields(self):
        data = self.obj.export_data()
        self.assertIn('hfoo', data['w3u']['original'])
        self.assertEqual(data['w3u']['original']['hfoo']['utst']['value'], 999)
        self.assertIn('h000', data['w3u']['custom'])
        self.assertEqual(data['w3u']['custom']['h000']['ID'], 'hfoo')

    def test_round_trip_is_byte_identical(self):
        original = _helpers.read_bytes(_helpers.sample_path('sample.w3u'))
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'roundtrip.w3u')
            self.obj.write(out)
            self.assertEqual(_helpers.read_bytes(out), original)


class TestObjectMetadataPipeline(unittest.TestCase):
    """Real facts checked once against a real Reforged install (see TODO.md's
    metadata-driven-schema and base-object-data entries) - re-verified here so
    a regression shows up as a failing test, not a rediscovery."""

    def test_object_metadata_knows_real_unit_fields(self):
        fields = ObjectMetaData.default().fields(ObjectFileType.Units)
        self.assertGreater(len(fields), 200)
        self.assertIn('unam', fields)  # unit Name field, cited directly in TODO.md

    def test_base_object_data_has_real_footman_stats(self):
        row = BaseObjectData.default().get(ObjectFileType.Units, 'hfoo')
        self.assertIsNotNone(row)
        # TODO.md: "a spot-check for hfoo's HP came back None from [UnitData/UnitUI/
        # UnitWeapons] ... UnitBalance.slk ... has HP: 420".
        self.assertEqual(row['HP'], 420)

    def test_object_editor_enums_has_real_defense_type_codes(self):
        codes = ObjectEditorEnums.default().valid_codes('defenseType')
        self.assertIn('fort', codes)  # TODO.md's own cited example

    def test_world_edit_strings_resolves_a_real_key(self):
        resolved = WorldEditStrings.default().resolve('WESTRING_UEVAL_IABI')
        self.assertEqual(resolved, 'Abilities')

    def test_world_edit_strings_passes_through_unknown_values(self):
        wes = WorldEditStrings.default()
        self.assertEqual(wes.resolve('not a westring key'), 'not a westring key')
        self.assertEqual(wes.resolve(42), 42)


class TestJsonFile(unittest.TestCase):
    """Regression coverage for two real bugs found during a capability audit
    (see TODO.md): JsonFile() crashed on construction (referenced a
    nonexistent ObjectFileType.W3O member), and import_data() called bare
    eval() on a plain WESTRING_* key string, which is not code."""

    def test_construction_does_not_crash(self):
        JsonFile()  # used to raise AttributeError

    def test_import_data_resolves_display_names(self):
        jf = JsonFile()
        meta = ObjectMetaData.default()
        wes = WorldEditStrings.default()
        jf.load_slk(meta.fields(ObjectFileType.Units), 'w3u')
        jf.load_wes(wes._data)
        jf.import_data({'w3u': {'original': {'hfoo': {'urac': {'value': 'human'}}}}})
        row = jf.export_data()['w3u']['original']['hfoo']['urac']
        self.assertEqual(row['value'], 'human')
        self.assertEqual(row['description'], 'Race')  # resolved WESTRING_UEVAL_URAC


class TestWesFile(unittest.TestCase):
    """Regression coverage for a real bug found during a capability audit
    (see TODO.md): WesFile.read() never stripped surrounding quotes, so
    quoted values in the real bundled WorldEditStrings.txt (~23% of its
    entries) came back with the literal quote characters still attached."""

    def test_synthetic_quoted_value_is_unquoted(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          encoding='utf-8') as fh:
            fh.write('WESTRING_TEST="quoted value"\nWESTRING_PLAIN=plain value\n')
            path = fh.name
        try:
            wes = WesFile()
            wes.read(path)
            data = wes.export_data()
            self.assertEqual(data['WESTRING_TEST'], 'quoted value')
            self.assertEqual(data['WESTRING_PLAIN'], 'plain value')
        finally:
            os.remove(path)

    def test_real_bundled_file_has_no_balanced_quotes_left(self):
        """Quoting is stripped only when it is actually quoting - i.e. balanced.

        This used to assert that NO value keeps a quote character, which
        encoded the old `.strip('"')` behavior as if it were correct. That
        call strips either end independently, so it also silently deleted a
        stray quote - and Blizzard's own file has exactly two entries with
        genuinely unbalanced quotes (an opening quote whose closing one was
        never typed, and the reverse). Deleting one character of real content
        to tidy up a typo is a guess, and it made the value impossible to
        write back unchanged, so those two are now preserved verbatim.
        """
        import wc3files
        gamedata = os.path.join(os.path.dirname(wc3files.__file__), 'w3o', 'gamedata')
        wes = WesFile()
        wes.read(os.path.join(gamedata, 'WorldEditStrings.txt'))
        data = wes.export_data()
        self.assertEqual(data['WESTRING_FILESEPARATOR_CAMPAIGNMAP'], '--')

        # No value is left *wrapped* in quotes.
        self.assertFalse(any(len(v) >= 2 and v.startswith('"') and v.endswith('"')
                             for v in data.values()))

        # The real unbalanced cases, kept exactly as Blizzard wrote them.
        # The count is data, not a contract: refreshing the bundled tables
        # from a 3.0.0.24248 install took the file from 9,488 entries to
        # 10,201 and added a third such entry.
        self.assertEqual(data['WESTRING_AIE_TARGET_CREEPS_LEVEL_WITHFLYERS'],
                         '" (%d-%d, flyers)')
        self.assertEqual(data['WESTRING_HEROSKILL_TRANSMUTE'],
                         'Neutral Alchemist - Transmute"')
        self.assertEqual(data['WESTRING_CAMPAIGN_INDEX_RU'],
                         '"Forsaken Kingdom Undead Campaign')
        self.assertEqual(sum(1 for v in data.values()
                             if v.startswith('"') or v.endswith('"')), 3)


class TestWesFileWrite(unittest.TestCase):
    """WesFile.write() - this format was dump-only (no write() at all) until
    now. Faithfulness is the whole point: a real World Edit strings file is
    mostly *not* data (the bundled WorldEditStrings.txt is 10465 lines, of
    which 502 are `//` comments, 460 blank, 1 a `[Section]` header) and 2132
    of its values are quoted, none of which a flat {key: value} dict holds.
    write() replays the original layout, so an unmodified read -> write is
    byte-identical."""

    GAMEDATA = None

    @classmethod
    def setUpClass(cls):
        import wc3files
        cls.GAMEDATA = os.path.join(os.path.dirname(wc3files.__file__), 'w3o', 'gamedata')

    def _roundtrip(self, name):
        src = os.path.join(self.GAMEDATA, name)
        wes = WesFile()
        wes.read(src)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, name)
            wes.write(out)
            with open(src, 'rb') as fh:
                original = fh.read()
            with open(out, 'rb') as fh:
                written = fh.read()
        return original, written, wes

    def test_real_world_edit_strings_round_trips_byte_for_byte(self):
        # 9488 entries across 10465 lines, including 12 duplicated keys.
        original, written, _ = self._roundtrip('WorldEditStrings.txt')
        self.assertEqual(written, original)

    def test_real_world_edit_game_strings_round_trips_byte_for_byte(self):
        original, written, _ = self._roundtrip('WorldEditGameStrings.txt')
        self.assertEqual(written, original)

    def test_a_values_meaningful_leading_space_is_preserved(self):
        # A real bug this work found, not just a round-trip detail: read() used
        # to strip values, but 20 values in the real file are suffixes carrying
        # their own separating space, so stripping made the editor render
        # 'Footman(Dead)' instead of 'Footman (Dead)'.
        wes = WesFile()
        wes.read(os.path.join(self.GAMEDATA, 'WorldEditStrings.txt'))
        data = wes.export_data()
        self.assertEqual(data['WESTRING_GENVAR_SUFFIX'], ' <gen>')
        self.assertEqual(data['WESTRING_DOODDEADSUFFIX'], ' (Dead)')
        # ...while quoting is still stripped, as before.
        self.assertEqual(data['WESTRING_FILESEPARATOR_CAMPAIGNMAP'], '--')

    def test_an_edited_value_is_written_back_in_place(self):
        src = os.path.join(self.GAMEDATA, 'WorldEditGameStrings.txt')
        wes = WesFile()
        wes.read(src)
        key = next(iter(wes.export_data()))
        wes.export_data()[key] = 'CHANGED VALUE'
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'edited.txt')
            wes.write(out)
            reread = WesFile()
            reread.read(out)
        self.assertEqual(reread.export_data()[key], 'CHANGED VALUE')
        # The rest of the file must survive the edit untouched.
        self.assertEqual(len(reread.export_data()), len(wes.export_data()))

    def test_hand_built_data_writes_without_any_layout(self):
        wes = WesFile()
        wes.import_data({'WESTRING_A': 'first', 'WESTRING_B': 'second'})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'built.txt')
            wes.write(out)
            reread = WesFile()
            reread.read(out)
        self.assertEqual(reread.export_data(), {'WESTRING_A': 'first', 'WESTRING_B': 'second'})

    def test_a_value_that_looks_quoted_survives_the_round_trip(self):
        # The one case that genuinely must be re-quoted on write, or read()
        # would strip the value's own quote characters as this format's quoting.
        wes = WesFile()
        wes.import_data({'WESTRING_Q': '"literally quoted"'})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'quoted.txt')
            wes.write(out)
            reread = WesFile()
            reread.read(out)
        self.assertEqual(reread.export_data()['WESTRING_Q'], '"literally quoted"')

    def test_a_deleted_key_drops_its_line(self):
        src = os.path.join(self.GAMEDATA, 'WorldEditGameStrings.txt')
        wes = WesFile()
        wes.read(src)
        victim = next(iter(wes.export_data()))
        del wes.export_data()[victim]
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'trimmed.txt')
            wes.write(out)
            reread = WesFile()
            reread.read(out)
        self.assertNotIn(victim, reread.export_data())


class TestObjectFileMutationHooks(unittest.TestCase):
    """The wc3files-native equivalent of w3x2lni's "plugin architecture for
    scripted object-data mutation" - see TODO.md for why it's scoped this way
    (generic Python callbacks, not a Lua-plugin-loading mechanism tied to a
    protection pass this package doesn't have)."""

    def test_read_hook_can_mutate_in_place(self):
        renamed = []

        def rename_on_read(instance, data):
            if 'hfoo' in data['w3u']['original']:
                data['w3u']['original']['hpea'] = data['w3u']['original'].pop('hfoo')
                renamed.append(True)

        obj = ObjectFile()
        obj.register_hook('read', rename_on_read)
        obj.read(_helpers.sample_path('sample.w3u'))

        self.assertEqual(renamed, [True])
        self.assertIn('hpea', obj.export_data()['w3u']['original'])
        self.assertNotIn('hfoo', obj.export_data()['w3u']['original'])

    def test_write_hook_mutation_is_persisted(self):
        obj = ObjectFile()
        obj.read(_helpers.sample_path('sample.w3u'))

        def bump_value_on_write(instance, data):
            data['w3u']['original']['hfoo']['utst']['value'] = 111

        obj.register_hook('write', bump_value_on_write)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'hooked.w3u')
            obj.write(out)
            reread = ObjectFile()
            reread.read(out)
            self.assertEqual(reread.export_data()['w3u']['original']['hfoo']['utst']['value'], 111)

    def test_hook_returning_replacement_dict_is_used(self):
        obj = ObjectFile()
        obj.read(_helpers.sample_path('sample.w3u'))

        def replace_on_read(instance, data):
            return {'w3u': {'original': {}, 'custom': {}}, 'w3t': None, 'w3b': None,
                    'w3d': None, 'w3a': None, 'w3h': None, 'w3q': None}

        obj2 = ObjectFile()
        obj2.register_hook('read', replace_on_read)
        obj2.read(_helpers.sample_path('sample.w3u'))
        self.assertEqual(obj2.export_data()['w3u']['original'], {})

    def test_hooks_do_not_leak_between_instances(self):
        calls = []
        obj1 = ObjectFile()
        obj1.register_hook('read', lambda instance, data: calls.append('obj1'))
        obj2 = ObjectFile()
        obj2.read(_helpers.sample_path('sample.w3u'))
        self.assertEqual(calls, [])  # obj1's hook never fired for obj2's read

    def test_unregister_and_clear_hooks(self):
        obj = ObjectFile()
        callback = lambda instance, data: None
        obj.register_hook('read', callback)
        obj.unregister_hook('read', callback)
        self.assertEqual(obj._hooks['read'], [])

        obj.register_hook('read', callback)
        obj.register_hook('write', callback)
        obj.clear_hooks()
        self.assertEqual(obj._hooks, {'read': [], 'write': []})

    def test_unknown_event_raises(self):
        obj = ObjectFile()
        with self.assertRaises(ValueError):
            obj.register_hook('bogus', lambda instance, data: None)
        with self.assertRaises(ValueError):
            obj.clear_hooks('bogus')


if __name__ == '__main__':
    unittest.main()
