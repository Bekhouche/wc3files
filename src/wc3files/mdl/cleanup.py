"""
Unused materials, textures and texture animations removed from a model
(#232). It is lossless: nothing that is drawn changes.

What uses what:

- a material is used by a geoset (`materialId`) or a ribbon emitter
  (`materialId`);
- a texture by a layer of a used material: its `textureId`, the values of
  its `KMTF` (flipbook) track, and from version 1100 on its texture list
  and their `KMTF` tracks (#301); or by a particle emitter (`textureId`);
- a texture animation by a layer of a used material (`textureAnimationId`).

Materials go first, since a texture used only by an unused material is
unused too. The references to what is kept are renumbered. In version 1100
and later, the `textureId` field before the texture list is not always the
layer's texture (#301), but it counts as a use all the same: a texture may
then stay that is not needed, never go that is.
"""
from .batch import model_data

NONE = 0xFFFFFFFF


def _layers(materials):
    for material in materials:
        yield from material.get('layers') or []


def _layer_textures(layer):
    """The texture ids a layer uses, with a setter for each: (id, set)."""
    refs = []
    if 'textureId' in layer:
        refs.append((layer['textureId'], lambda v, l=layer: l.__setitem__('textureId', v)))
    groups = [layer.get('tracks') or {}]
    for entry in layer.get('textures') or []:
        refs.append((entry.get('textureId', 0),
                     lambda v, e=entry: e.__setitem__('textureId', v)))
        groups.append(entry.get('tracks') or {})
    for tracks in groups:
        for key in (tracks.get('KMTF') or {}).get('keys') or []:
            refs.append((key['value'], lambda v, k=key: k.__setitem__('value', v)))
    return refs


def _renumber(count, used):
    """old index -> new index of the kept ones."""
    mapping, new = {}, 0
    for old in range(count):
        if old in used:
            mapping[old] = new
            new += 1
    return mapping


def remove_unused(model):
    """Remove the unused materials, textures and texture animations of
    `model` (a ModelFile or its data). Returns how many of each went."""
    model = model_data(model)
    materials = model.get('materials') or []
    textures = model.get('textures') or []
    animations = model.get('textureAnimations') or []

    material_users = [(g, 'materialId') for g in model.get('geosets') or []]
    material_users += [(r, 'materialId') for r in model.get('ribbonEmitters') or []]
    used = {user[key] for user, key in material_users if user.get(key) is not None}
    keep = _renumber(len(materials), used)
    for user, key in material_users:
        if user.get(key) in keep:
            user[key] = keep[user[key]]
    model['materials'] = [m for i, m in enumerate(materials) if i in keep]

    emitters = model.get('particleEmitters2') or []
    refs = [ref for layer in _layers(model['materials']) for ref in _layer_textures(layer)]
    refs += [(e.get('textureId', 0), lambda v, e=e: e.__setitem__('textureId', v))
             for e in emitters if 'textureId' in e]
    keep_textures = _renumber(len(textures), {value for value, _ in refs})
    for value, set_value in refs:
        if value in keep_textures:
            set_value(keep_textures[value])
    model['textures'] = [t for i, t in enumerate(textures) if i in keep_textures]

    layers = list(_layers(model['materials']))
    keep_animations = _renumber(len(animations), {l.get('textureAnimationId') for l in layers})
    for layer in layers:
        if layer.get('textureAnimationId') in keep_animations:
            layer['textureAnimationId'] = keep_animations[layer['textureAnimationId']]
    model['textureAnimations'] = [a for i, a in enumerate(animations) if i in keep_animations]

    return {'materials': len(materials) - len(model['materials']),
            'textures': len(textures) - len(model['textures']),
            'textureAnimations': len(animations) - len(model['textureAnimations'])}
