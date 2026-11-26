"""
Team glow removed from a model (#240), as Easy MDX Teamglow Remover does.

Team glow is the replaceable texture 2 (team color is 1). A layer shows it
when its texture, a frame of its flipbook (`KMTF`) or, from version 1100
on, an entry of its texture list is a team glow texture. In the models
measured it is nearly always a material of one layer on a geoset of its
own: 178 of 181 layers, over 34 of the game's SD models and 137 models
imported into corpus maps. None of 211 HD game models has one.

Two ways, `mode`:

- 'transparent' (default): the layer stays, with alpha 0 and no alpha track;
- 'remove': the layer goes. A material left with no layer goes with the
  geosets that use it, and the references to those geosets are renumbered;
  a ribbon emitter that uses it gets back one transparent layer instead.
  Then `remove_unused` drops what nothing uses any more, such as the team
  glow texture.
"""
from .batch import model_data
from .cleanup import remove_unused

NONE = 0xFFFFFFFF
TEAM_GLOW = 2


def _uses(layer, glow):
    ids = [layer.get('textureId')]
    ids += [e.get('textureId') for e in layer.get('textures') or []]
    groups = [layer.get('tracks') or {}] + [e.get('tracks') or {} for e in layer.get('textures') or []]
    for tracks in groups:
        ids += [k['value'] for k in (tracks.get('KMTF') or {}).get('keys') or []]
    if layer.get('textures'):
        # From version 1100 the list is the layer's textures (#301).
        ids = ids[1:]
    return bool(glow & set(ids))


def _transparent(layer):
    layer['alpha'] = 0.0
    (layer.get('tracks') or {}).pop('KMTA', None)


def _remove_geosets(model, gone):
    """Remove the geosets whose indices are in `gone`, renumbering what
    points at geosets and at geoset animations."""
    geosets = model.get('geosets') or []
    keep = {}
    for old in range(len(geosets)):
        if old not in gone:
            keep[old] = len(keep)
    model['geosets'] = [g for i, g in enumerate(geosets) if i in keep]
    animations = model.get('geosetAnimations') or []
    keep_anim = {}
    kept_anims = []
    for old, anim in enumerate(animations):
        if anim.get('geosetId') in gone:
            continue
        if anim.get('geosetId') in keep:
            anim['geosetId'] = keep[anim['geosetId']]
        keep_anim[old] = len(kept_anims)
        kept_anims.append(anim)
    model['geosetAnimations'] = kept_anims
    for bone in model.get('bones') or []:
        gid = bone.get('geosetId', NONE)
        if gid in gone:
            bone['geosetId'] = NONE
        elif gid in keep:
            bone['geosetId'] = keep[gid]
        aid = bone.get('geosetAnimationId', NONE)
        if aid in keep_anim:
            bone['geosetAnimationId'] = keep_anim[aid]
        elif aid != NONE and aid < len(animations):
            bone['geosetAnimationId'] = NONE


def remove_team_glow(model, mode='transparent'):
    """Make the team glow of `model` (a ModelFile or its data) transparent,
    or remove it. Returns what was changed."""
    if mode not in ('transparent', 'remove'):
        raise ValueError(f"mode is 'transparent' or 'remove', not {mode!r}")
    model = model_data(model)
    glow = {i for i, t in enumerate(model.get('textures') or [])
            if t.get('replaceableId') == TEAM_GLOW}
    report = {'layers': 0, 'geosets': 0, 'materials': 0, 'textures': 0}
    if not glow:
        return report
    emptied = set()
    for index, material in enumerate(model.get('materials') or []):
        layers = material.get('layers') or []
        hits = [layer for layer in layers if _uses(layer, glow)]
        report['layers'] += len(hits)
        if not hits:
            continue
        if mode == 'transparent':
            for layer in hits:
                _transparent(layer)
            continue
        material['layers'] = [layer for layer in layers if layer not in hits]
        if not material['layers']:
            emptied.add(index)
            # Kept for any ribbon that uses it; dropped below if none does.
            _transparent(hits[0])
            material['layers'] = [hits[0]]
    if mode == 'remove':
        gone = {i for i, g in enumerate(model.get('geosets') or [])
                if g.get('materialId') in emptied}
        _remove_geosets(model, gone)
        report['geosets'] = len(gone)
        removed = remove_unused(model)
        report['materials'] = removed['materials']
        report['textures'] = removed['textures']
    return report
