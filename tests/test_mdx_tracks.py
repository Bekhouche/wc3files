"""MDX keyframe tracks are read in one pass per track (#176): one read of the
whole track and one unpack per key, instead of a read per frame, value and
tangent. The keys must stay exactly as they were."""
import struct
import unittest

import _helpers  # noqa: F401
from wc3files.files.binary_file import TruncatedDataError
from wc3files.mdl.stream import BinaryStream
from wc3files.mdl.tracks import read_tracks, write_tracks


def _f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


TRACKS = {
    # u32, no tangents (linear)
    'KMTF': {'interpolationType': 1, 'globalSequenceId': 0xFFFFFFFF,
             'keys': [{'frame': 0, 'value': 3}, {'frame': 100, 'value': 0xFFFFFFFE}]},
    # f32 with tangents (hermite)
    'KMTA': {'interpolationType': 2, 'globalSequenceId': 4,
             'keys': [{'frame': -5, 'value': _f32(0.25), 'inTan': _f32(-0.0), 'outTan': _f32(1.5)}]},
    # f32x3 with tangents (bezier)
    'KGTR': {'interpolationType': 3, 'globalSequenceId': 0xFFFFFFFF,
             'keys': [{'frame': 10, 'value': [_f32(1.1), 2.0, 3.0],
                       'inTan': [0.0, -1.0, 0.5], 'outTan': [4.0, 5.0, 6.0]},
                      {'frame': 20, 'value': [7.0, 8.0, 9.0],
                       'inTan': [1.0, 1.0, 1.0], 'outTan': [2.0, 2.0, 2.0]}]},
    # f32x4, no tangents
    'KGRT': {'interpolationType': 0, 'globalSequenceId': 0xFFFFFFFF,
             'keys': [{'frame': 0, 'value': [0.0, 0.0, 0.0, 1.0]}]},
    # no keys at all
    'KATV': {'interpolationType': 0, 'globalSequenceId': 0xFFFFFFFF, 'keys': []},
}


def _encoded(tracks):
    out = BinaryStream(mode='write')
    write_tracks(out, tracks)
    return out.getvalue()


class TestTracks(unittest.TestCase):

    def test_every_value_kind_reads_back_the_same(self):
        data = _encoded(TRACKS)
        stream = BinaryStream(data)
        self.assertEqual(read_tracks(stream), TRACKS)
        self.assertEqual(stream.remaining(), 0)

    def test_value_types(self):
        tracks = read_tracks(BinaryStream(_encoded(TRACKS)))
        self.assertIsInstance(tracks['KMTF']['keys'][0]['value'], int)
        self.assertIsInstance(tracks['KMTA']['keys'][0]['value'], float)
        self.assertIsInstance(tracks['KGTR']['keys'][0]['value'], list)
        self.assertIsInstance(tracks['KGTR']['keys'][0]['outTan'], list)
        self.assertEqual(str(tracks['KMTA']['keys'][0]['inTan']), '-0.0')

    def test_a_track_cut_short_raises_truncated_data_error(self):
        data = _encoded({'KGTR': TRACKS['KGTR']})
        for cut in (17, 20, len(data) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(TruncatedDataError):
                    read_tracks(BinaryStream(data[:cut]))


if __name__ == '__main__':
    unittest.main()
