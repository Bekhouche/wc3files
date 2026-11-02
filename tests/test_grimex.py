"""Tests for wc3files.grimex: running a map's external tool calls
(ObjectMerger, FileImporter, FileExporter, ConstantMerger).

What ObjectMerger does was read from Grim Extension Pack 1.0's own binary
(names, argument rules, messages, the id generator); the cases below pin that
behaviour down. Over the nestharus/JASS corpus, every external of the lua/
folder runs, and the failures left in jass/ are the scripts' own (a function
the shipped library version does not define, a missing include).
"""
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401
from loguru import logger

from wc3files.grimex import ExternalRunner, ObjectData, split_arguments
from wc3files.vjass import VJassCompiler
from wc3files.w3o import ObjectFile

MAIN = '\nfunction main takes nothing returns nothing\nendfunction\n'


def block(*lines, tool='ObjectMerger', extension='lua'):
    body = '\n'.join(f'    //! i {line}' for line in lines)
    return (f'//! externalblock extension={extension} {tool} $FILENAME$\n{body}\n'
            f'//! endexternalblock\n')


def externals_of(source):
    return VJassCompiler().compile(source + MAIN, 'map.j').externals


def run(source, files=None, **options):
    options.setdefault('stop_on_error', False)
    with ExternalRunner(files, **options) as runner:
        return runner.run(externals_of(source))


def table(result, ext):
    obj = ObjectFile()
    obj.read(result.files[f'war3map.{ext}'], ext)
    return obj.export_data()[ext]


class TestObjectMergerLua(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def test_create_and_change_objects(self):
        result = run(block('setobjecttype("abilities")',
                           'createobject("AHbz", "A000")',
                           'makechange(current, "anam", "Snowfall")',
                           'makechange(current, "Hbz1", "2", "40")',
                           'makechange(current, "alev", 3)',
                           'setobjecttype("units")',
                           'createobject("hfoo", "h000")',
                           'makechange(current, "unam", "Captain")',
                           'makechange(current, "uhpm", "900")'))
        self.assertEqual(result.errors, [])
        self.assertEqual(result.changed, ['war3map.w3a', 'war3map.w3u'])
        ability = table(result, 'w3a')['custom']['A000']
        self.assertEqual(ability['ID'], 'AHbz')
        self.assertEqual(ability['anam']['value'][0], 'Snowfall')
        self.assertEqual(ability['Hbz1']['value'][2], 40)
        self.assertEqual(ability['Hbz1']['ptr'][2], 1)      # the field's data column
        unit = table(result, 'w3u')['custom']['h000']
        self.assertEqual((unit['ID'], unit['unam']['value'], unit['uhpm']['value']),
                         ('hfoo', 'Captain', 900))

    def test_queries(self):
        result = run(block('setobjecttype("w3a")',
                           'createobject("AHbz", "A000")',
                           'print(currentobject(), currentobjecttype(), objectexists("A000"),'
                           ' objectexists("AHbz"), objectexists("Zzzz"))',
                           'modifyobject("AHtb")',
                           'print(currentobject())'))
        self.assertEqual(result.output, 'A000\tw3a\ttrue\ttrue\tfalse\nAHtb\n')

    def test_generateid_is_objectmergers_counter(self):
        # A counter starting at u~~~, the base id's first character, the last
        # three stepping through punctuation; existing ids are not skipped.
        result = run(block('setobjecttype("abilities")',
                           'local t = {}',
                           'for i = 1, 4 do t[i] = generateid("AHbz") end',
                           't[5] = generateid("hfoo")',
                           'print(table.concat(t, " "))'))
        self.assertEqual(result.output, 'A!!! A!!" A!!# A!!$ h!!%\n')

    def test_counter_skips_letters_digits_and_quotes(self):
        objects = ObjectData()
        ids = [objects.generate_id('w3a', 'AHbz') for _ in range(94)]
        characters = ''.join(sorted({i[3] for i in ids}))
        self.assertEqual(characters, '!"#$%&()*+,-./:;<=>?@[]^_{|}~')
        self.assertEqual(len(ids), len(set(ids)))

    def test_groups(self):
        result = run(block('setobjecttype("units")',
                           'createobject("hfoo", "h000")',
                           'createobject("hpea", "h001")',
                           'makechange(custom, "uhpm", "5")',
                           'modifyobject("hkni")',
                           'makechange(current, "unam", "Knight!")',
                           'makechange(modded, "uhpm", "7")'))
        self.assertEqual(result.errors, [])
        units = table(result, 'w3u')
        self.assertEqual(units['custom']['h000']['uhpm']['value'], 5)
        self.assertEqual(units['custom']['h001']['uhpm']['value'], 5)
        self.assertEqual(set(units['original']), {'hkni'})
        self.assertEqual(units['original']['hkni']['uhpm']['value'], 7)

    def test_reset(self):
        result = run(block('setobjecttype("units")',
                           'createobject("hfoo", "h000")',
                           'makechange(current, "unam", "A")',
                           'makechange(current, "uhpm", "5")',
                           'resetchange(current, "unam")',
                           'createobject("hfoo", "h001")',
                           'makechange(current, "uhpm", "6")',
                           'resetobject(current)'))
        units = table(result, 'w3u')['custom']
        self.assertEqual(set(units['h000']) - {'ID'}, {'uhpm'})
        self.assertEqual(set(units['h001']) - {'ID'}, set())

    def test_errors_point_at_the_source_line(self):
        source = block('setobjecttype("abilities")',
                       'createobject("AHbz", "A000")',
                       'makechange(current, "anaam", "x")')
        result = run(source)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(str(result.errors[0]), 'map.j:4: Invalid object field anaam')
        # A failed external leaves the map as it was.
        self.assertEqual(result.changed, [])
        self.assertNotIn('war3map.w3a', result.files)

    def test_argument_rules(self):
        cases = {
            'makechange(current, "Hbz1", "40")':
                'Invalid number of arguments, expected 4 arguments including level',
            'makechange(current, "anam", "1", "x")':
                'Invalid number of arguments, expected 3 arguments excluding level',
            'makechange(current, "Hbz1", "0", "40")':
                'The level/variation value 0 must not be zero or negative',
            'makechange(current, "Hbz1", "x", "40")': 'The level argument must be numeric',
            'makechange(current, "Hbz1", "1", "lots")': 'The value lots is not a valid integer',
            'createobject("Zzzz", "A001")': "The base object Zzzz doesn't exist",
            'createobject("AHbz", "A0")': 'Invalid object id A0',
            'setobjecttype("spells")':
                'Unsupported object type spells, w3u, w3t, w3b, w3d, w3a, w3h or w3q expected',
        }
        for line, message in cases.items():
            with self.subTest(line=line):
                result = run(block('setobjecttype("abilities")',
                                   'createobject("AHbz", "A000")', line))
                self.assertEqual(len(result.errors), 1)
                self.assertTrue(str(result.errors[0]).endswith(message),
                                str(result.errors[0]))

    def test_type_must_be_set_first(self):
        result = run(block('createobject("hfoo", "h000")'))
        self.assertIn("use 'setobjectype' to do so", str(result.errors[0]))

    def test_changes_apply_to_an_existing_map(self):
        first = run(block('setobjecttype("units")', 'createobject("hfoo", "h000")'))
        second = run(block('setobjecttype("units")', 'modifyobject("h000")',
                           'makechange(current, "unam", "Renamed")'), files=first.files)
        self.assertEqual(table(second, 'w3u')['custom']['h000']['unam']['value'], 'Renamed')


class TestCommandLines(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def test_split_arguments(self):
        self.assertEqual(split_arguments('w3a AHbz A000 anam "Two words" x'),
                         ['w3a', 'AHbz', 'A000', 'anam', 'Two words', 'x'])
        self.assertEqual(split_arguments(r'a "b \"c\"" d\e'), ['a', 'b "c"', r'd\e'])

    def test_objectmerger_generation(self):
        result = run('//! external ObjectMerger w3a AHbz A000 anam "Big Blizzard" '
                     'Hbz1 1 60 alev 2\n')
        self.assertEqual(result.errors, [])
        ability = table(result, 'w3a')['custom']['A000']
        self.assertEqual(ability['anam']['value'][0], 'Big Blizzard')
        self.assertEqual(ability['Hbz1']['value'][1], 60)
        self.assertEqual(ability['alev']['value'][0], 2)

    def test_objectmerger_merge_file(self):
        work = tempfile.mkdtemp()
        try:
            made = run('//! external ObjectMerger w3u hfoo h000 unam "From file"\n')
            path = os.path.join(work, 'units.w3u')
            with open(path, 'wb') as fh:
                fh.write(made.files['war3map.w3u'])
            merged = run('//! external ObjectMerger m units.w3u\n', lookup_folders=[work])
            self.assertEqual(merged.errors, [])
            self.assertEqual(table(merged, 'w3u')['custom']['h000']['unam']['value'],
                             'From file')
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_unknown_and_unsupported_tools(self):
        result = run('//! external Frobnicator x\n//! external TriggerMerger x\n')
        self.assertIn('External not found in config file: "Frobnicator"',
                      str(result.errors[0]))
        self.assertIn('TriggerMerger tool is not supported', str(result.errors[1]))

    def test_stop_on_error(self):
        source = ('//! external Frobnicator x\n'
                  '//! external ObjectMerger w3u hfoo h000 unam x\n')
        result = run(source, stop_on_error=True)
        self.assertEqual([status for _, status in result.ran], ['error', 'skipped'])


class TestOtherTools(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.folder, 'art'))
        with open(os.path.join(self.folder, 'art', 'icon.blp'), 'wb') as fh:
            fh.write(b'BLP1 data')
        with open(os.path.join(self.folder, 'war3mapMisc.txt'), 'w') as fh:
            fh.write('[Misc]\nMaxLevel=20\n')

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_file_importer(self):
        result = run(block('importfile("art\\\\icon.blp", "war3mapImported\\\\icon.blp")',
                           tool='FileImporter'), lookup_folders=[self.folder])
        self.assertEqual(result.errors, [])
        self.assertEqual(result.files['war3mapImported\\icon.blp'], b'BLP1 data')
        self.assertIn('war3map.imp', result.changed)
        from wc3files.grimex.tools import _reader
        from wc3files.imp.imp_file import ImpFile
        imp = ImpFile()
        listed = imp._read(_reader(result.files['war3map.imp']))['files']
        self.assertEqual([entry['path'] for entry in listed], ['war3mapImported\\icon.blp'])

    def test_file_exporter_writes_into_the_workspace(self):
        workspace = tempfile.mkdtemp()
        try:
            files = {'war3map.j': b'// script'}
            result = run(block('exportfile("war3map.j", "out\\\\script.j")',
                               tool='FileExporter'), files=files, workspace=workspace)
            self.assertEqual(result.errors, [])
            path = os.path.join(workspace, 'jassnewgenpack', 'out', 'script.j')
            with open(path, 'rb') as fh:
                self.assertEqual(fh.read(), b'// script')
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_constant_merger(self):
        result = run(block('mergefile("war3mapMisc.txt")', 'setvalue("Misc", "GoldMax", "9")',
                           tool='ConstantMerger'),
                     files={'war3mapMisc.txt': b'[Misc]\r\nMaxLevel=10\r\n'},
                     lookup_folders=[self.folder])
        self.assertEqual(result.errors, [])
        text = result.files['war3mapMisc.txt'].decode()
        self.assertIn('MaxLevel=20', text)
        self.assertIn('GoldMax=9', text)


class TestWorkspace(unittest.TestCase):
    """Scripts written for JassNewGenPack find their files from package.path
    and share them between blocks through the disk."""

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def test_layout_and_persistence(self):
        workspace = tempfile.mkdtemp()
        try:
            source = (block(
                'local root = string.sub(package.path, string.find(package.path, '
                '".:.-Warcraft III")) .. "\\\\"',
                'local f = io.open(root .. "jassnewgenpack\\\\jass\\\\gen.j", "w")',
                'f:write("globals\\nendglobals\\n") f:close()',
                'f = io.open(root .. "jassnewgenpack\\\\grimext\\\\lib.lua", "w")',
                'f:write("answer = 42") f:close()', tool='FileExporter')
                + block('dofile("grimext\\\\lib.lua")', 'print(answer)'))
            result = run(source, workspace=workspace)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.output, '42\n')
            with open(os.path.join(workspace, 'jassnewgenpack', 'jass', 'gen.j'), 'rb') as fh:
                self.assertEqual(fh.read(), b'globals\r\nendglobals\r\n')
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def test_nothing_outside_the_workspace(self):
        outside = os.path.join(tempfile.gettempdir(), 'wc3files_grimex_escape.txt')
        source = block(f'local f = io.open({outside!r}, "w")', 'print(f == nil)',
                       'print(os.execute("del C:\\\\Windows\\\\win.ini"))',
                       'print(os.execute("format c:"))', tool='FileExporter')
        result = run(source)
        self.assertEqual(result.output, 'true\n1\n1\n')
        self.assertFalse(os.path.exists(outside))
        self.assertIn(('execute', 'format c:'), result.denied)


class TestCompilerIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def test_compile_map_with_externals(self):
        folder = tempfile.mkdtemp()
        try:
            with open(os.path.join(folder, 'war3map.j'), 'w') as fh:
                fh.write(block('setobjecttype("units")', 'createobject("hfoo", "h000")')
                         + MAIN)
            result = VJassCompiler().compile_map(folder, externals=True)
            self.assertEqual(result.externals_result.changed, ['war3map.w3u'])
            self.assertTrue(os.path.isfile(os.path.join(folder, 'war3map.w3u')))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_failed_external_writes_nothing(self):
        from wc3files.vjass import VJassError
        folder = tempfile.mkdtemp()
        try:
            source = block('setobjecttype("units")', 'oops()') + MAIN
            with open(os.path.join(folder, 'war3map.j'), 'w') as fh:
                fh.write(source)
            with self.assertRaises(VJassError) as caught:
                VJassCompiler().compile_map(folder, externals=True)
            self.assertIn("attempt to call global 'oops'", str(caught.exception))
            with open(os.path.join(folder, 'war3map.j')) as fh:
                self.assertEqual(fh.read(), source)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_compile_result_runs_its_externals(self):
        result = VJassCompiler().compile(block('setobjecttype("items")',
                                               'createobject("ratf", "I000")') + MAIN)
        done = result.run_externals()
        self.assertEqual(done.changed, ['war3map.w3t'])

    def test_block_lines_keep_their_text(self):
        externals = externals_of('//! externalblock extension=lua ObjectMerger $FILENAME$\n'
                                 '//! i   indented   \n//! endexternalblock\n')
        self.assertEqual(externals[0].lines, ['  indented   '])
        self.assertEqual(externals[0].extension, 'lua')
        self.assertEqual(externals[0].tool, 'ObjectMerger')
        self.assertEqual(str(externals[0].positions[0]), 'map.j:2')
        location, tool, lines = externals[0]
        self.assertEqual((tool, lines), ('ObjectMerger', ['  indented   ']))


class TestCli(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        logger.disable('wc3files')

    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_vjass_externals_run(self):
        import contextlib
        import io
        from wc3files.cli.main import main
        source = os.path.join(self.folder, 'script.j')
        with open(source, 'w') as fh:
            fh.write(block('setobjecttype("units")', 'createobject("hfoo", "h000")') + MAIN)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = main(['vjass', source, '-o', os.path.join(self.folder, 'out.j'),
                         '--externals', 'run'])
        self.assertIn(code, (0, None))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, 'war3map.w3u')))

    def test_lua_command(self):
        import contextlib
        import io
        from wc3files.cli.main import main
        script = os.path.join(self.folder, 'make.lua')
        with open(script, 'w') as fh:
            fh.write('setobjecttype("units")\ncreateobject("hpea", "h000")\n'
                     'makechange(current, "unam", "Worker")\n')
        out = os.path.join(self.folder, 'objects')
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            main(['lua', script, '--tool', 'ObjectMerger', '-o', out])
        self.assertTrue(os.path.isfile(os.path.join(out, 'war3map.w3u')))


if __name__ == '__main__':
    unittest.main()
