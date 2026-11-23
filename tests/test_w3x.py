"""Tests for wc3files.w3x.W3xFile's own no-(listfile) file discovery.

W3xFile carries its own MPQ primitives (crypt table, hash table, read_file)
rather than inheriting MpqFile's, and it used to carry its own *discovery
logic* too - which is exactly how that copy silently fell behind the one in
wc3files.mpq.files: it ran a fixed two manifest passes instead of iterating
to a fixed point, and never scanned .mdl/.mdx models structurally at all.
Measured against a real protected map (238 files, no usable listfile), that
cost it a real file the other implementation found.

W3xFile now calls the same `is_scan_candidate`/`extract_references` helpers
MpqFiles does. These tests pin that: the same reference chains must be
discovered through W3xFile's own read path, so the two can't drift again
without something here failing. There was previously no test_w3x.py at all,
which is the other half of why the drift went unnoticed.
"""
import os
import tempfile
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)

from wc3files import W3xFile, W3xReadMode
from wc3files.mpq.data import MpqProtectionLevel
from wc3files.mpq.writer import MpqWriter
from wc3files.mdl.model_file import ModelFile
from wc3files.mdl.mdx_io import empty_model


class TestW3xDeepFileDiscovery(unittest.TestCase):
    """Mirrors TestMpqDeepFileDiscovery (tests/test_mpq.py) through W3xFile's
    own read path: a multi-hop reference chain where each file is named only
    inside the previous one, so nothing but iterate-to-a-fixed-point finds
    the far end."""

    FILES = {
        'war3map.j': b'call Foo("Imports\\MyList.txt")\nendfunction\n',
        'Imports\\MyList.txt': b'Imports\\MyTable.slk\nSounds\\Direct.wav\n',
        'Imports\\MyTable.slk': b'ID;X1;K"Textures\\Deep.blp"\n',
        'Sounds\\Direct.wav': b'not-really-a-wav-just-content',
        'Textures\\Deep.blp': b'not-really-a-blp-just-content',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.archive_path = os.path.join(self.tmpdir.name, 'test.w3x')

    def test_a_three_hop_reference_chain_is_fully_discovered_with_no_listfile(self):
        builder = W3xFile()
        builder.build_from_files(self.FILES, self.archive_path,
                                 protection=MpqProtectionLevel.SAFE)

        w3x = W3xFile()
        w3x.read(self.archive_path, mode=W3xReadMode.DEEP)
        # Confirms there is no real listfile to fall back on, so this genuinely
        # exercises the discovery path rather than a plain name lookup.
        self.assertFalse(w3x.files.get('(listfile)'))

        for name, data in self.FILES.items():
            self.assertEqual(w3x.files.get(name), data,
                             f'not discovered/misread: {name!r}')

    def test_chain_members_are_not_bundled_reference_names(self):
        # Without this, the test above would pass even with no scanning at all
        # if these names happened to be in the bundled reference filename list.
        reference_names = {n.lower() for n in W3xFile.get_filelist()}
        for name in self.FILES:
            if name == 'war3map.j':
                continue  # war3map.j itself is expected to be a bundled name
            self.assertNotIn(name.lower(), reference_names,
                             f'{name!r} unexpectedly already bundled')


class TestW3xDiscoveryFindsModelEmbeddedTextures(unittest.TestCase):
    """The capability W3xFile's own copy of the discovery logic never had:
    pulling a texture path out of an .mdx structurally (via ModelFile), not
    from any plain-text reference. Built with this package's own model writer,
    matching the fixture convention in tests/test_mpq.py."""

    CUSTOM_TEXTURE_PATH = 'Textures\\MyW3xOnlyModelTexture.blp'

    @classmethod
    def setUpClass(cls):
        model = empty_model()
        model['textures'] = [{'replaceableId': 0, 'path': cls.CUSTOM_TEXTURE_PATH,
                              'flags': 0}]
        mf = ModelFile()
        mf.import_data(model)

        with tempfile.TemporaryDirectory() as tmp:
            mdx_path = os.path.join(tmp, 'model.mdx')
            mf.write(mdx_path)
            with open(mdx_path, 'rb') as fh:
                cls.mdx_bytes = fh.read()

    def test_a_texture_named_only_inside_an_mdx_is_discovered(self):
        files = {
            # The script must name the model, or with no listfile there is no
            # chain to start from and the model itself is never found.
            'war3map.j': b'call CreateSomething("MyModel.mdx")\nendfunction\n',
            'MyModel.mdx': self.mdx_bytes,
            self.CUSTOM_TEXTURE_PATH: b'not-really-a-blp',
        }
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'model.w3x')
            W3xFile().build_from_files(files, archive_path,
                                      protection=MpqProtectionLevel.SAFE)

            w3x = W3xFile()
            w3x.read(archive_path, mode=W3xReadMode.DEEP)
            self.assertEqual(w3x.files.get(self.CUSTOM_TEXTURE_PATH),
                             b'not-really-a-blp')


class TestW3xReadMode(unittest.TestCase):
    FILES = {
        '(listfile)': b'war3map.w3i\r\nwar3map.j\r\n',
        'war3map.w3i': b'\x00' * 64,
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, 'map.w3x')

    def test_simple_stops_at_the_listfile(self):
        W3xFile().build_from_files(self.FILES, self.path)
        w3x = W3xFile()
        w3x.read(self.path, mode=W3xReadMode.SIMPLE)
        self.assertFalse(w3x.protected)
        self.assertTrue(w3x.files.get('(listfile)'))
        self.assertIsNone(w3x.files.get('war3map.j'))

    def test_listfile_extracts_named_members(self):
        W3xFile().build_from_files(self.FILES, self.path)
        w3x = W3xFile()
        w3x.read(self.path)  # default LISTFILE
        self.assertFalse(w3x.protected)
        self.assertEqual(w3x.files.get('war3map.j'), self.FILES['war3map.j'])

    def test_missing_listfile_is_protected_and_not_scanned(self):
        W3xFile().build_from_files(self.FILES, self.path,
                                   protection=MpqProtectionLevel.SAFE)
        w3x = W3xFile()
        w3x.read(self.path)
        self.assertTrue(w3x.protected)
        self.assertFalse(w3x.files.get('(listfile)'))
        self.assertIsNone(w3x.files.get('war3map.j'))


class _ListfileWriter(MpqWriter):
    """Writes the (listfile) it is given, instead of generating one."""
    GENERATED = ()


class TestIncompleteListfile(unittest.TestCase):
    """Some protectors replace (listfile) with a decoy that names only itself.
    Any non-empty list used to count as the real one: the map read as empty,
    with protected False, and DEEP never ran. 54 corpus maps do this."""

    MEMBERS = {
        'war3map.w3i': b'\x00' * 64,
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
    }

    def build(self, listfile):
        path = os.path.join(self.tmpdir.name, 'map.w3x')
        files = dict(self.MEMBERS, **{'(listfile)': listfile})
        _ListfileWriter(protection=MpqProtectionLevel.SAFE).write(files, path)
        return path

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_a_decoy_listfile_is_protected(self):
        path = self.build(b'(listfile)\r\n \r\n \r\n \r\n')
        w3x = W3xFile()
        w3x.read(path)
        self.assertTrue(w3x.protected)
        self.assertEqual(w3x.unlisted_blocks, 2)

    def test_deep_recovers_what_a_decoy_hides(self):
        path = self.build(b'(listfile)\r\n \r\n \r\n \r\n')
        w3x = W3xFile()
        w3x.read(path, mode=W3xReadMode.DEEP)
        self.assertEqual(w3x.files.get('war3map.j'), self.MEMBERS['war3map.j'])
        self.assertEqual(w3x.files.get('war3map.w3i'), self.MEMBERS['war3map.w3i'])

    def test_a_partial_listfile_keeps_what_it_names(self):
        path = self.build(b'(listfile)\r\nwar3map.w3i\r\n')
        w3x = W3xFile()
        w3x.read(path)
        self.assertTrue(w3x.protected)
        self.assertEqual(w3x.unlisted_blocks, 1)
        self.assertEqual(w3x.files.get('war3map.w3i'), self.MEMBERS['war3map.w3i'])
        self.assertIsNone(w3x.files.get('war3map.j'))
        deep = W3xFile()
        deep.read(path, mode=W3xReadMode.DEEP)
        self.assertEqual(deep.files.get('war3map.j'), self.MEMBERS['war3map.j'])

    def test_a_complete_listfile_is_not_protected(self):
        path = self.build(b'(listfile)\r\nwar3map.w3i\r\nwar3map.j\r\n')
        w3x = W3xFile()
        w3x.read(path)
        self.assertFalse(w3x.protected)
        self.assertEqual(w3x.unlisted_blocks, 0)




class TestReferenceNamesAreLazy(unittest.TestCase):
    """W3xFile() and MpqFiles() read the 332,532 reference names only when a
    DEEP read needs them, and once per process (#135)."""

    def setUp(self):
        import wc3files.mpq.files as files
        self.files = files
        self.saved = files._REFERENCE_NAMES
        files._REFERENCE_NAMES = None

    def tearDown(self):
        self.files._REFERENCE_NAMES = self.saved

    def test_construction_does_not_load_them(self):
        from wc3files import W3xFile
        from wc3files.mpq.files import MpqFiles
        W3xFile()
        MpqFiles()
        self.assertIsNone(self.files._REFERENCE_NAMES)

    def test_loaded_once_and_sorted(self):
        from wc3files import W3xFile
        first = W3xFile().default_files
        loaded = self.files._REFERENCE_NAMES
        self.assertIsNotNone(loaded)
        W3xFile().default_files
        self.assertIs(self.files._REFERENCE_NAMES, loaded)
        self.assertEqual(first, sorted(first))
        self.assertIn('war3map.j', first)

    def test_default_files_can_still_be_set(self):
        from wc3files import W3xFile
        archive = W3xFile()
        archive.default_files = ['war3map.j']
        self.assertEqual(archive.default_files, ['war3map.j'])
        self.assertIsNone(self.files._REFERENCE_NAMES)

class TestReadMembers(unittest.TestCase):
    """read(members=...) reads only those files, so a command that needs
    the script of a large map does not decompress the rest (#139)."""

    FILES = {
        'war3map.j': b'function main takes nothing returns nothing\nendfunction\n',
        'war3map.w3i': b'\x01' * 64,
        'war3map.w3u': b'\x02' * 64,
        'Units\\big.txt': b'x' * 5000,
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, 'map.w3x')

    def _reads(self, archive):
        names = []
        original = archive.read_file

        def counting(reader, name):
            names.append(name)
            return original(reader, name)
        archive.read_file = counting
        return names

    def test_only_the_members_asked_for(self):
        W3xFile().build_from_files(self.FILES, self.path)
        w3x = W3xFile()
        reads = self._reads(w3x)
        w3x.read(self.path, members=['WAR3MAP.J', 'war3map.w3u', 'war3map.missing'])
        self.assertFalse(w3x.protected)
        self.assertEqual(w3x.files['war3map.j'], self.FILES['war3map.j'])
        self.assertEqual(w3x.files['war3map.w3u'], self.FILES['war3map.w3u'])
        self.assertNotIn('war3map.w3i', w3x.files)
        self.assertNotIn('Units\\big.txt', w3x.files)
        self.assertEqual(sorted(reads), ['(listfile)', 'war3map.j', 'war3map.w3u'])

    def test_protected_map_is_still_refused(self):
        from wc3files.w3x import read_map, ProtectedMapError
        W3xFile().build_from_files(self.FILES, self.path,
                                   protection=MpqProtectionLevel.SAFE)
        with self.assertRaises(ProtectedMapError):
            read_map(self.path, members=['war3map.j'])

    def test_deep_looks_members_up_by_name(self):
        W3xFile().build_from_files(self.FILES, self.path,
                                   protection=MpqProtectionLevel.SAFE)
        w3x = W3xFile()
        reads = self._reads(w3x)
        w3x.read(self.path, mode=W3xReadMode.DEEP, members=['war3map.j'])
        self.assertTrue(w3x.protected)
        self.assertEqual(w3x.files['war3map.j'], self.FILES['war3map.j'])
        self.assertNotIn('war3map.w3i', w3x.files)
        self.assertEqual(sorted(reads), ['(listfile)', 'war3map.j'])


if __name__ == '__main__':
    unittest.main()
