"""Tests for the frame-definition (`.fdf`) and table-of-contents (`.toc`) readers.

Verified against every `.fdf` the installed game ships - 114 readable files,
2,920 blocks and 15,512 properties - all of which parse and re-read to an
equal structure, with no statement misparsed (see TODO.md). The fixtures
here are two of those files plus focused cases for the parts of the grammar
that are easy to get wrong.
"""
import unittest

import _helpers  # noqa: F401  (sets up sys.path + quiet logging)
from _helpers import sample_path

from wc3files.fdf import FdfError, FdfFile, TocFile
from wc3files.fdf.fdf_file import Number, Word


class TestRealFile(unittest.TestCase):
    def setUp(self):
        self.fdf = FdfFile().read(sample_path('ResourceBar.fdf'))

    def test_every_block_keyword_is_recognised(self):
        kinds = [block['keyword'] for block in self.fdf.blocks()]
        self.assertIn('Texture', kinds)
        self.assertIn('String', kinds)
        self.assertIn('Frame', kinds)

    def test_a_frame_carries_both_a_type_and_a_name(self):
        frame = self.fdf.frames()[0]
        self.assertEqual(frame['type'], 'SIMPLEFRAME')
        self.assertEqual(frame['name'], 'ResourceBarFrame')

    def test_a_texture_block_may_be_anonymous_and_only_inherit(self):
        # `Texture INHERITS "ResourceBarIconTemplate" {` has no name at all;
        # reading the template as the name would be wrong.
        anonymous = [b for b in self.fdf.blocks('Texture') if b['name'] is None]
        self.assertTrue(anonymous)
        self.assertEqual(anonymous[0]['inherits'], 'ResourceBarIconTemplate')

    def test_a_named_block_can_also_inherit(self):
        named = [b for b in self.fdf.blocks('String')
                 if b['name'] == 'ResourceBarGoldText']
        self.assertEqual(len(named), 1)
        self.assertEqual(named[0]['inherits'], 'ResourceBarTextTemplate')

    def test_writing_and_rereading_gives_an_equal_structure(self):
        again = FdfFile().read(self.fdf.to_text())
        self.assertEqual(again.to_text(), self.fdf.to_text())
        self.assertEqual(len(again.blocks()), len(self.fdf.blocks()))


class TestStatementBoundaries(unittest.TestCase):
    """A comma both separates values and ends a statement; the line does the
    deciding. These are the cases that distinction exists for."""

    def test_commas_inside_one_statement_are_separators(self):
        fdf = FdfFile().read('Frame "FRAME" "F" {\n'
                             '    Anchor TOPLEFT, 0.0, -0.003125,\n'
                             '    File "GoldIcon",\n'
                             '}\n')
        items = fdf.frames()[0]['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['name'], 'Anchor')
        self.assertEqual(items[0]['values'], ['TOPLEFT', 0.0, -0.003125])
        self.assertEqual(items[1]['values'], ['GoldIcon'])

    def test_a_property_may_carry_no_values_at_all(self):
        fdf = FdfFile().read('Frame "FRAME" "F" {\n    SetAllPoints,\n}\n')
        item = fdf.frames()[0]['items'][0]
        self.assertEqual(item['name'], 'SetAllPoints')
        self.assertEqual(item['values'], [])

    def test_a_block_header_may_sit_on_its_own_line(self):
        # StatDetailsPanel.fdf puts `{` on the line after the header.
        fdf = FdfFile().read('Frame "SIMPLEFRAME" "Split"\n{\n    Width 0.1,\n}\n')
        self.assertEqual(fdf.frames()[0]['name'], 'Split')

    def test_a_string_may_span_lines(self):
        # GlobalStrings.fdf's TAIWAN_LEGAL entry really does contain newlines.
        fdf = FdfFile().read('StringList {\n    LEGAL "first\nsecond",\n    OK "Ok",\n}\n')
        self.assertEqual(fdf.strings(), {'LEGAL': 'first\nsecond', 'OK': 'Ok'})


class TestLexicalDetail(unittest.TestCase):
    def test_a_double_slash_inside_a_string_is_not_a_comment(self):
        fdf = FdfFile().read('StringList {\n'
                             '    SITE "https://example.com/x",\n}\n')
        self.assertEqual(fdf.strings()['SITE'], 'https://example.com/x')

    def test_both_comment_styles_are_dropped(self):
        fdf = FdfFile().read('// leading\n/* block\n   spanning */\n'
                             'Frame "FRAME" "F" {\n    Width 0.1, // trailing\n}\n')
        self.assertEqual(len(fdf.frames()), 1)
        self.assertEqual(len(fdf.frames()[0]['items']), 1)

    def test_a_numeric_suffix_survives_the_round_trip(self):
        fdf = FdfFile().read('Frame "FRAME" "F" {\n    Width 0.384f,\n}\n')
        value = fdf.frames()[0]['items'][0]['values'][0]
        self.assertAlmostEqual(value, 0.384)
        self.assertIn('0.384f', fdf.to_text())

    def test_an_integer_is_not_rewritten_as_a_float(self):
        fdf = FdfFile().read('String "S" {\n    TextLength 8,\n}\n')
        self.assertIn('TextLength 8,', fdf.to_text())

    def test_a_bare_keyword_stays_bare_and_a_string_stays_quoted(self):
        fdf = FdfFile().read('Frame "FRAME" "F" {\n'
                             '    FontJustificationH JUSTIFYRIGHT,\n'
                             '    Text "KEY",\n}\n')
        items = fdf.frames()[0]['items']
        self.assertIsInstance(items[0]['values'][0], Word)
        self.assertNotIsInstance(items[1]['values'][0], Word)
        text = fdf.to_text()
        self.assertIn('FontJustificationH JUSTIFYRIGHT,', text)
        self.assertIn('Text "KEY",', text)

    def test_a_number_compares_as_a_number(self):
        self.assertEqual(Number('0.5f'), 0.5)
        self.assertEqual(Number('8'), 8)


class TestInheritance(unittest.TestCase):
    def test_withchildren_is_recorded_and_written_back(self):
        fdf = FdfFile().read('Frame "FRAME" "F" INHERITS WITHCHILDREN "T" {\n}\n')
        frame = fdf.frames()[0]
        self.assertTrue(frame['with_children'])
        self.assertEqual(frame['inherits'], 'T')
        self.assertIn('INHERITS WITHCHILDREN "T"', fdf.to_text())

    def test_inheriting_without_a_template_is_refused(self):
        with self.assertRaises(FdfError):
            FdfFile().read('Frame "FRAME" "F" INHERITS {\n}\n')


class TestIncludesAndStrings(unittest.TestCase):
    def test_includes_are_listed_in_order(self):
        fdf = FdfFile().read('IncludeFile "A.fdf",\nIncludeFile "B.fdf",\n')
        self.assertEqual(fdf.includes(), ['A.fdf', 'B.fdf'])

    def test_a_backslash_path_survives_intact(self):
        path = r'UI\FrameDef\UI\EscMenuTemplates.fdf'
        fdf = FdfFile().read(f'IncludeFile "{path}",\n')
        self.assertEqual(fdf.includes(), [path])
        self.assertEqual(FdfFile().read(fdf.to_text()).includes(), [path])

    def test_an_include_without_a_path_is_refused(self):
        with self.assertRaises(FdfError):
            FdfFile().read('IncludeFile,\n')


class TestMalformedInput(unittest.TestCase):
    def test_an_unclosed_block_is_refused(self):
        with self.assertRaises(FdfError):
            FdfFile().read('Frame "FRAME" "F" {\n    Width 0.1,\n')

    def test_a_stray_closing_brace_is_refused(self):
        with self.assertRaises(FdfError):
            FdfFile().read('Width 0.1,\n}\n')

    def test_a_block_keyword_used_as_a_property_still_reads(self):
        # Nothing guarantees `String` always opens a block, so a `String`
        # with no `{` must fall back to being an ordinary property rather
        # than failing.
        fdf = FdfFile().read('Frame "FRAME" "F" {\n    String "x",\n}\n')
        item = fdf.frames()[0]['items'][0]
        self.assertEqual(item['kind'], 'property')
        self.assertEqual(item['values'], ['x'])


class TestToc(unittest.TestCase):
    def test_a_real_toc_lists_fdf_paths(self):
        toc = TocFile().read(sample_path('FrameDef.toc'))
        self.assertGreater(len(toc.entries), 50)
        self.assertTrue(all(entry.lower().endswith('.fdf') for entry in toc.entries))
        self.assertIn(r'UI\FrameDef\GlobalStrings.fdf', toc.entries)

    def test_blank_lines_and_comments_are_skipped(self):
        toc = TocFile().read('A.fdf\n\n// note\n  B.fdf  \n')
        self.assertEqual(toc.entries, ['A.fdf', 'B.fdf'])

    def test_import_data_round_trips(self):
        toc = TocFile().import_data(['X.fdf'])
        self.assertEqual(toc.get_data(), ['X.fdf'])


if __name__ == '__main__':
    unittest.main()
