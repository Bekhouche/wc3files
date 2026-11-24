"""MDL keeps the sign of a zero float (#168). The writer prints -0.0 as -0,
and the reader took it for the integer 0, so MDX -> MDL -> MDX changed the
float's bits and MDL -> MDL was not a fixed point."""
import math
import os
import tempfile
import unittest

import _helpers  # noqa: F401
from _helpers import sample_path
from wc3files import ModelFile


class TestNegativeZero(unittest.TestCase):

    def test_negative_zero_survives_mdx_to_mdl_to_mdx(self):
        tmp = tempfile.mkdtemp()
        model = ModelFile()
        model.read(sample_path('sample.mdx'))
        data = model.get_data()
        data['extent']['maximum'] = [1.5, -0.0, 2.0]
        data['extent']['boundsRadius'] = -0.0
        model.import_data(data)
        mdx = os.path.join(tmp, 'a.mdx')
        mdl = os.path.join(tmp, 'a.mdl')
        again = os.path.join(tmp, 'b.mdx')
        model.write(mdx)
        model.write(mdl)
        with open(mdl, encoding='utf-8') as handle:
            self.assertIn('-0', handle.read())

        text = ModelFile()
        text.read(mdl)
        extent = text.get_data()['extent']
        self.assertEqual(math.copysign(1.0, extent['maximum'][1]), -1.0)
        self.assertEqual(math.copysign(1.0, extent['boundsRadius']), -1.0)
        text.write(again)
        with open(mdx, 'rb') as a, open(again, 'rb') as b:
            self.assertEqual(a.read(), b.read())

    def test_an_integer_field_still_reads_as_an_integer(self):
        from wc3files.mdl.mdl_io import _parse_number
        self.assertEqual(int(_parse_number('-0')), 0)
        self.assertIsInstance(_parse_number('0'), int)
        self.assertEqual(_parse_number('-3'), -3)


if __name__ == '__main__':
    unittest.main()
