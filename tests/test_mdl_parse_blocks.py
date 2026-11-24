"""The MDL readers that walk the token list directly (#177) read what the
peek()/next() versions read: vectors, flat and nested float lists, and track
blocks in both layouts the Art Tools and other writers use."""
import unittest

import _helpers  # noqa: F401
from wc3files.mdl.mdl_io import (MdlTokenizer, _parse_float_list_block,
                                 _parse_track_block, _parse_vector_list_block,
                                 _read_vector)


def tok(text):
    return MdlTokenizer(text)


class TestVectors(unittest.TestCase):

    def test_vector(self):
        tokens = tok('{ 1, -0, 2.5e1 } next')
        values = _read_vector(tokens)
        self.assertEqual(values, [1.0, -0.0, 25.0])
        self.assertEqual(str(values[1]), '-0.0')
        self.assertEqual(tokens.next(), 'next')

    def test_special_floats(self):
        values = _read_vector(tok('{ nan, inf, -inf }'))
        self.assertNotEqual(values[0], values[0])
        self.assertEqual(values[1:], [float('inf'), float('-inf')])

    def test_vector_list_nested_and_flat(self):
        self.assertEqual(_parse_vector_list_block(tok('{ { 1, 2 }, { 3, 4 }, }'), 2),
                         [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(_parse_vector_list_block(tok('{ 1, 2, 3 }'), 1), [1.0, 2.0, 3.0])

    def test_float_list(self):
        self.assertEqual(_parse_float_list_block(tok('{ 0.5, 1, }')), [0.5, 1.0])
        self.assertEqual(_parse_float_list_block(tok('{ { 1, 2 }, }')), [1.0, 2.0])

    def test_comments_are_dropped_but_not_inside_strings(self):
        self.assertEqual(tok('// c\n"a//b" { 1 } // d\n')._tokens, ['"a//b"', '{', '1', '}'])

    def test_input_that_ends_early_raises_value_error(self):
        for reader, text in ((_read_vector, '{ 1, 2'), (_read_vector, ''),
                             (lambda t: _parse_vector_list_block(t, 3), '{ { 1, 2, 3 },'),
                             (_parse_float_list_block, '{ 1,'),
                             (lambda t: _parse_track_block(t, 'f32'), '{ 0: 1,')):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    reader(tok(text))


class TestTrackBlocks(unittest.TestCase):

    def test_flags_inside_the_block(self):
        track = _parse_track_block(tok(
            '{ Hermite, GlobalSeqId 3, 0: { 1, 2, 3 }, InTan { 0, 0, 0 }, '
            'OutTan { 1, 1, 1 }, 10: { 4, 5, 6 }, InTan { 2, 2, 2 }, OutTan { 3, 3, 3 }, }'),
            'f32x3')
        self.assertEqual(track['interpolationType'], 2)
        self.assertEqual(track['globalSequenceId'], 3)
        self.assertEqual(track['keys'], [
            {'frame': 0, 'value': [1.0, 2.0, 3.0], 'inTan': [0.0] * 3, 'outTan': [1.0] * 3},
            {'frame': 10, 'value': [4.0, 5.0, 6.0], 'inTan': [2.0] * 3, 'outTan': [3.0] * 3},
        ])

    def test_flags_before_the_block_and_spaced_colons(self):
        track = _parse_track_block(tok('Linear GlobalSeqId 1 { 5 : 0.25, 7: -0, }'), 'f32')
        self.assertEqual(track['interpolationType'], 1)
        self.assertEqual(track['globalSequenceId'], 1)
        self.assertEqual(track['keys'], [{'frame': 5, 'value': 0.25}, {'frame': 7, 'value': -0.0}])
        self.assertEqual(str(track['keys'][1]['value']), '-0.0')

    def test_integer_values_and_tangents_raise_interpolation(self):
        track = _parse_track_block(tok('{ DontInterp, 0: 2, InTan 1, OutTan 3, }'), 'u32')
        self.assertEqual(track['keys'], [{'frame': 0, 'value': 2, 'inTan': 1, 'outTan': 3}])
        self.assertIsInstance(track['keys'][0]['value'], int)
        # A key with tangents and no Hermite or Bezier flag is read as Hermite.
        self.assertEqual(track['interpolationType'], 2)


if __name__ == '__main__':
    unittest.main()
