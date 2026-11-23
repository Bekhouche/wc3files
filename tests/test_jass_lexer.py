"""Token positions from the JASS lexer. Whitespace is skipped inside the next
match and columns come from the line start (#138); these pin the positions
it must keep."""
import unittest

from wc3files.scripts.jass_lexer import tokenize, JassLexError


def positions(source, keep_comments=False):
    return [(t.kind, t.value, t.line, t.col) for t in tokenize(source, keep_comments)]


class TestTokenPositions(unittest.TestCase):

    def test_columns_after_spaces_and_tabs(self):
        self.assertEqual(positions('  set\tx =  1\r\n'), [
            ('KEYWORD', 'set', 1, 3), ('IDENT', 'x', 1, 7), ('ASSIGN', '=', 1, 9),
            ('INT', '1', 1, 12), ('EOF', '', 2, 1)])

    def test_string_over_two_lines(self):
        self.assertEqual(positions('call f("a\nbc") x'), [
            ('KEYWORD', 'call', 1, 1), ('IDENT', 'f', 1, 6), ('LPAREN', '(', 1, 7),
            ('STRING', 'a\nbc', 1, 8), ('RPAREN', ')', 2, 4), ('IDENT', 'x', 2, 6),
            ('EOF', '', 2, 7)])

    def test_eof_after_trailing_whitespace(self):
        self.assertEqual(positions('x  \t'), [('IDENT', 'x', 1, 1), ('EOF', '', 1, 5)])
        self.assertEqual(positions(''), [('EOF', '', 1, 1)])

    def test_comments(self):
        self.assertEqual(positions('x // note\ny'), [
            ('IDENT', 'x', 1, 1), ('IDENT', 'y', 2, 1), ('EOF', '', 2, 2)])
        self.assertEqual(positions('x // note\ny', keep_comments=True), [
            ('IDENT', 'x', 1, 1), ('COMMENT', 'note', 1, 3), ('IDENT', 'y', 2, 1),
            ('EOF', '', 2, 2)])

    def test_unexpected_character(self):
        with self.assertRaises(JassLexError) as caught:
            tokenize('x\n  y # z')
        self.assertEqual((caught.exception.line, caught.exception.col), (2, 5))
        self.assertIn("Unexpected character '#'", str(caught.exception))

    def test_rawcode_and_numbers(self):
        self.assertEqual(positions("'hfoo' $FF 0x1 1.5 .5 7"), [
            ('RAWCODE', 'hfoo', 1, 1), ('HEXINT', '$FF', 1, 8), ('HEXINT', '0x1', 1, 12),
            ('REAL', '1.5', 1, 16), ('REAL', '.5', 1, 20), ('INT', '7', 1, 23),
            ('EOF', '', 1, 24)])


if __name__ == '__main__':
    unittest.main()
