"""wc3files.mdl.gltf (#245): glTF 2.0 to MDX and back, on small glTF files
built here: a static textured triangle and a two-bone skinned strip with an
animation, checked by posing the MDX."""
import base64
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

import _helpers  # noqa: F401
from test_model_extents import walking_cube
from wc3files.img import write_png
from wc3files.mdl import ModelFile
from wc3files.mdl.animation import node_matrices, posed_vertices
from wc3files.mdl.gltf import _load, gltf_to_model, model_to_gltf
from wc3files.mdl.mdx_io import load_mdx, save_mdx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = chr(92)


class Builder:
    def __init__(self):
        self.data = bytearray()
        self.doc = {'asset': {'version': '2.0'}, 'bufferViews': [], 'accessors': []}

    def add(self, values, kind, code='f', component=5126):
        while len(self.data) % 4:
            self.data.append(0)
        start = len(self.data)
        flat = [x for v in values for x in (v if isinstance(v, (list, tuple)) else (v,))]
        self.data += struct.pack('<' + code * len(flat), *flat)
        self.doc['bufferViews'].append({'buffer': 0, 'byteOffset': start,
                                        'byteLength': len(self.data) - start})
        self.doc['accessors'].append({'bufferView': len(self.doc['bufferViews']) - 1,
                                      'componentType': component, 'count': len(values),
                                      'type': kind})
        return len(self.doc['accessors']) - 1

    def gltf(self):
        doc = dict(self.doc)
        doc['buffers'] = [{'byteLength': len(self.data),
                           'uri': 'data:application/octet-stream;base64,'
                                  + base64.b64encode(bytes(self.data)).decode()}]
        return json.dumps(doc).encode()


def textured_triangle():
    b = Builder()
    pos = b.add([(0, 0, 0), (1, 0, 0), (0, 1, 0)], 'VEC3')
    uv = b.add([(0, 0), (1, 0), (0, 1)], 'VEC2')
    idx = b.add([0, 1, 2], 'SCALAR', 'H', 5123)
    png = write_png(2, 2, bytes([255, 0, 0, 255] * 4))
    while len(b.data) % 4:
        b.data.append(0)
    b.doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(b.data), 'byteLength': len(png)})
    b.data += png
    b.doc.update({
        'scene': 0, 'scenes': [{'nodes': [0]}],
        'nodes': [{'name': 'tri', 'mesh': 0, 'translation': [0, 0, 5]}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': pos, 'TEXCOORD_0': uv},
                                    'indices': idx, 'material': 0}]}],
        'materials': [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}},
                       'alphaMode': 'MASK', 'doubleSided': True,
                       'normalTexture': {'index': 0}}],
        'textures': [{'source': 0}],
        'images': [{'name': 'red', 'bufferView': len(b.doc['bufferViews']) - 1,
                    'mimeType': 'image/png'}]})
    return b.gltf()


def skinned_strip():
    """Two joints along glTF +Y; four vertices: two on the first joint,
    one on the second, one shared 50/50. The animation turns the second
    joint 90 degrees about Z in one second."""
    b = Builder()
    pos = b.add([(0, 0, 0), (1, 0, 0), (0, 2, 0), (0, 1, 0)], 'VEC3')
    joints = b.add([(0, 0, 0, 0), (0, 0, 0, 0), (1, 0, 0, 0), (0, 1, 0, 0)], 'VEC4', 'H', 5123)
    weights = b.add([(1, 0, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0), (0.5, 0.5, 0, 0)], 'VEC4')
    idx = b.add([0, 1, 3, 3, 1, 2], 'SCALAR', 'H', 5123)
    ibm = b.add([(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1),
                 (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -1, 0, 1)], 'MAT4')
    times = b.add([0.0, 1.0], 'SCALAR')
    half = math.sqrt(0.5)
    turns = b.add([(0, 0, 0, 1), (0, 0, half, half)], 'VEC4')
    b.doc.update({
        'scene': 0, 'scenes': [{'nodes': [0, 2]}],
        'nodes': [{'name': 'hip', 'children': [1]}, {'name': 'knee', 'translation': [0, 1, 0]},
                  {'name': 'body', 'mesh': 0, 'skin': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': pos, 'JOINTS_0': joints,
                                                   'WEIGHTS_0': weights}, 'indices': idx}]}],
        'skins': [{'joints': [0, 1], 'inverseBindMatrices': ibm}],
        'animations': [{'name': 'Bend', 'samplers': [{'input': times, 'output': turns}],
                        'channels': [{'sampler': 0, 'target': {'node': 1, 'path': 'rotation'}}]}]})
    return b.gltf()


def game(p):
    """A glTF point in the game's axes at scale 1: (x, y, z) -> (z, x, y)."""
    return (p[2], p[0], p[1])


class GltfToModelTest(unittest.TestCase):

    def test_a_static_textured_triangle(self):
        model, report = gltf_to_model(textured_triangle(), texture_dir='war3mapImported')
        geoset = model['geosets'][0]
        # The node's translation is baked in, and the axes turned.
        self.assertEqual([round(v, 6) for v in geoset['vertices'][3:6]], list(game((1, 0, 5))))
        self.assertEqual(model['textures'][0]['path'], 'war3mapImported' + BS + 'red.blp')
        self.assertTrue(report['textures']['war3mapImported' + BS + 'red.blp'].startswith(b'BLP1'))
        layer = model['materials'][0]['layers'][0]
        self.assertEqual((layer['filterMode'], layer['shadingFlags']), (1, 16))
        self.assertIn('normalTexture left out', report['notes'])
        self.assertEqual(report['sequences'], ['Stand'])
        load_mdx(save_mdx(model))

    def test_a_skinned_strip_bends(self):
        model, report = gltf_to_model(skinned_strip())
        self.assertEqual(report['sequences'], ['Bend'])
        a, b = model['sequences'][0]['interval']
        geoset = model['geosets'][0]
        # The shared vertex is on both bones, in equal parts, as glTF has it.
        self.assertEqual(sorted(geoset['matrixGroups']), [1, 1, 2])
        rest = posed_vertices(geoset, node_matrices(model, a, (a, b)))
        bent = posed_vertices(geoset, node_matrices(model, b, (a, b)))
        # At the start the strip is as built; at the end the tip (0, 2, 0) has
        # turned 90 degrees about the knee (0, 1, 0) to (-1, 1, 0).
        for p, q in zip(rest, [(0, 0, 0), (1, 0, 0), (0, 2, 0), (0, 1, 0)]):
            for x, y in zip(p, game(q)):
                self.assertAlmostEqual(x, y, places=5)
        for x, y in zip(bent[2], game((-1, 1, 0))):
            self.assertAlmostEqual(x, y, places=5)
        for x, y in zip(bent[0], game((0, 0, 0))):
            self.assertAlmostEqual(x, y, places=5)

    def test_scale_and_a_glb_with_an_external_buffer(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        doc = json.loads(skinned_strip())
        raw = base64.b64decode(doc['buffers'][0]['uri'].split(',', 1)[1])
        doc['buffers'][0]['uri'] = 'strip.bin'
        with open(os.path.join(tmp, 'strip.bin'), 'wb') as handle:
            handle.write(raw)
        with open(os.path.join(tmp, 'strip.gltf'), 'w') as handle:
            json.dump(doc, handle)
        model, _ = gltf_to_model(os.path.join(tmp, 'strip.gltf'), scale=100.0)
        self.assertAlmostEqual(model['geosets'][0]['vertices'][8], 200.0, places=4)
        with self.assertRaises(ValueError):
            gltf_to_model(json.dumps(doc).encode())


class ModelToGltfTest(unittest.TestCase):

    def test_round_trip_poses_the_same(self):
        model = walking_cube()
        glb, report = model_to_gltf(model)
        self.assertEqual(glb[:4], b'glTF')
        document, _, _ = _load(glb)
        self.assertEqual(len(document['animations']), 1)
        back, _ = gltf_to_model(glb, min_weight=0.0)
        a, b = model['sequences'][1]['interval']
        na, nb = back['sequences'][0]['interval']
        for f in (a, (a + b) // 2, b):
            p = posed_vertices(model['geosets'][0], node_matrices(model, f, (a, b)))
            q = posed_vertices(back['geosets'][0], node_matrices(back, na + f - a, (na, nb)))
            for x, y in zip(p, q):
                for u, v in zip(x, y):
                    self.assertAlmostEqual(u, v, places=4)

    def test_a_global_sequence_is_baked_into_each_animation(self):
        from test_model_extents import track
        model = walking_cube()
        model['globalSequences'] = [400]
        model['bones'][0]['node']['tracks']['KGSC'] = track(
            [(0, [1.0] * 3), (200, [2.0] * 3), (400, [1.0] * 3)], global_sequence=0)
        glb, report = model_to_gltf(model)
        self.assertIn('1 global sequence track(s) baked into each animation', report['notes'][0])
        back, _ = gltf_to_model(glb, min_weight=0.0)
        a, b = model['sequences'][1]['interval']
        names = {s['name']: s for s in back['sequences']}
        na, nb = names['Walk']['interval']
        for f in range(a, b + 1, 100):
            p = posed_vertices(model['geosets'][0], node_matrices(model, f, (a, b)))
            q = posed_vertices(back['geosets'][0], node_matrices(back, na + f - a, (na, nb)))
            for x, y in zip(p, q):
                for u, v in zip(x, y):
                    self.assertAlmostEqual(u, v, places=4)

    def test_cli_both_ways(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        source = os.path.join(tmp, 'tri.glb')
        model, _ = gltf_to_model(textured_triangle())
        ModelFile().import_data(model).write(os.path.join(tmp, 'tri.mdx'))
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src'))
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-gltf',
                                 os.path.join(tmp, 'tri.mdx'), source], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(tmp, 'tri.gltf'), 'wb') as handle:
            handle.write(textured_triangle())
        result = subprocess.run([sys.executable, '-m', 'wc3files.cli', 'model-gltf',
                                 os.path.join(tmp, 'tri.gltf'), os.path.join(tmp, 'out', 'tri.mdx'),
                                 '--texture-dir', 'Textures'], capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(tmp, 'out', 'Textures', 'red.blp')))
        self.assertIn(b'not taken: normalTexture left out', result.stdout)


if __name__ == '__main__':
    unittest.main()
