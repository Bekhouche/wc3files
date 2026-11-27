"""The texture list of version 1100+ MDX layers (#301): a flag, a count,
then per texture its id, slot and optional KMTF track, then the layer's own
tracks. Built here byte by byte and read back."""
import struct
import unittest

import _helpers  # noqa: F401
from wc3files.mdl.mdx_io import empty_model, load_mdx, save_mdx


def track(tag, frames_values):
    out = tag.encode() + struct.pack('<III', len(frames_values), 0, 0xFFFFFFFF)
    for frame, value in frames_values:
        out += struct.pack('<i', frame) + (struct.pack('<I', value) if tag == 'KMTF'
                                          else struct.pack('<f', value))
    return out


def layer_bytes(textures, tail):
    body = struct.pack('<IIIIIf', 0, 0, 0, 0xFFFFFFFF, 0, 1.0)
    body += struct.pack('<f3fff', 1.0, 1.0, 1.0, 1.0, 0.0, 0.0)
    body += struct.pack('<II', 1, len(textures))
    for texture_id, slot, extra in textures:
        body += struct.pack('<II', texture_id, slot) + extra
    body += tail
    return struct.pack('<I', len(body) + 4) + body


def model_bytes(layer):
    def chunk(tag, payload):
        return tag + struct.pack('<I', len(payload)) + payload
    material = struct.pack('<II', 0, 0) + b'LAYS' + struct.pack('<I', 1) + layer
    material = struct.pack('<I', len(material) + 4) + material
    textures = b''.join(struct.pack('<I', 0) + name.ljust(260, b'\0') + struct.pack('<I', 0)
                        for name in (b'a.blp', b'b.blp', b'c.blp'))
    return (b'MDLX' + chunk(b'VERS', struct.pack('<I', 1100))
            + chunk(b'TEXS', textures) + chunk(b'MTLS', material))


def materials_chunk(raw):
    pos = 4
    while pos < len(raw):
        tag, size = raw[pos:pos + 4], struct.unpack_from('<I', raw, pos + 4)[0]
        if tag == b'MTLS':
            return raw[pos:pos + 8 + size]
        pos += 8 + size
    return None


class LayerTexturesTest(unittest.TestCase):

    def test_two_textures_a_flipbook_and_layer_tracks(self):
        layer = layer_bytes([(2, 0, track('KMTF', [(0, 1), (100, 2)])), (1, 1, b'')],
                            track('KMTA', [(0, 0.5)]))
        raw = model_bytes(layer)
        model = load_mdx(raw)
        read = model['materials'][0]['layers'][0]
        self.assertEqual(read['hd'], 1)
        self.assertEqual([(t['textureId'], t['slot']) for t in read['textures']], [(2, 0), (1, 1)])
        self.assertEqual([k['value'] for k in read['textures'][0]['tracks']['KMTF']['keys']], [1, 2])
        self.assertEqual(read['textures'][1]['tracks'], {})
        self.assertEqual(read['tracks']['KMTA']['keys'][0]['value'], 0.5)
        self.assertNotIn('trailing', read)
        # The writer adds the chunks this file leaves out; the materials
        # come back byte for byte.
        self.assertEqual(materials_chunk(save_mdx(model)), materials_chunk(raw))

    def test_edited_ids_are_written(self):
        model = load_mdx(model_bytes(layer_bytes([(2, 0, b''), (1, 1, b'')], b'')))
        model['materials'][0]['layers'][0]['textures'][0]['textureId'] = 0
        again = load_mdx(save_mdx(model))
        self.assertEqual(again['materials'][0]['layers'][0]['textures'][0]['textureId'], 0)

    def test_data_from_before_still_writes(self):
        # A layer as read before #301: the first 16 bytes kept raw.
        model = empty_model()
        model['version'] = 1100
        model['materials'] = [{'priorityPlane': 0, 'flags': 0, 'layers': [{
            'filterMode': 0, 'shadingFlags': 0, 'textureId': 0,
            'textureAnimationId': 0xFFFFFFFF, 'coordId': 0, 'alpha': 1.0,
            'unknown_v1100': list(struct.pack('<IIII', 0, 1, 0, 0)), 'tracks': {}}]}]
        read = load_mdx(save_mdx(model))['materials'][0]['layers'][0]
        self.assertEqual(read['textures'], [{'textureId': 0, 'slot': 0, 'tracks': {}}])


if __name__ == '__main__':
    unittest.main()
