"""
The textures that models use, for many models or for a map (#242), as
MDX_Texturelist does.

`list_textures(models)` gathers the texture paths of models, each with the
models that use it. `map_textures(files, game_names)` does it for the
models of a map and compares them with the map's own files:

- `unused`: imported images that no model uses and that no other file of
  the map names either (object data, map info, triggers, scripts, interface
  files): these are the imports that can go;
- `named_elsewhere`: imported images no model uses, but that another file
  names, such as an icon in the object data. A file counts as named by its
  path with any extension or none, since maps name an imported `.blp` as
  `.dds` or `.tga`, and scripts name icons with no extension;
- `missing`: textures that models use but that are not in the map, and,
  with `game_names` (from `game_file_names` on an installed game), not in
  the game's files either.

Paths are compared as the game reads them: without regard to case or to
`\\` against `/`. Against the game's files, an image counts under any image
extension, since the game's models name `.blp` files that Reforged stores as
`.dds` (all 932 textures of 240 sampled game models that the game has, it
has with another extension), and a `_hd.w3mod:` or other prefix before a
colon is dropped. Against the map's files, only the path given counts:
whether the game takes a map's `X.dds` for a model's `X.blp` was not
checked, so such pairs are listed apart, as `other_extension`, and stay in
`missing` and `unused`.
"""
from .batch import model_data

IMAGE_EXTENSIONS = ('blp', 'dds', 'tga', 'tif', 'tiff', 'png', 'bmp', 'jpg', 'jpeg')
MODEL_EXTENSIONS = ('mdx', 'mdl')
BS = chr(92)


def canonical(path):
    return path.replace('/', BS).lower()


def _extension(name):
    base = canonical(name).rsplit(BS, 1)[-1]
    return base.rsplit('.', 1)[-1] if '.' in base else ''


def _game_key(path):
    """A path as looked up in the game: no prefix, no image extension."""
    path = canonical(path).rsplit(':', 1)[-1]
    if _extension(path) in IMAGE_EXTENSIONS:
        path = path.rsplit('.', 1)[0]
    return path


def texture_paths(model):
    """The texture paths of `model` (a ModelFile or its data), each once,
    in order; replaceable textures have none."""
    seen, out = set(), []
    for texture in model_data(model).get('textures') or []:
        path = texture.get('path') or ''
        if path and canonical(path) not in seen:
            seen.add(canonical(path))
            out.append(path)
    return out


def list_textures(models):
    """{canonical path: {'path': first spelling, 'models': [names]}} for
    `models`, an iterable of (name, ModelFile or data)."""
    found = {}
    for name, model in models:
        for path in texture_paths(model):
            entry = found.setdefault(canonical(path), {'path': path, 'models': []})
            entry['models'].append(name)
    return found


def game_file_names(storage):
    """The keys the game's files are found by, from a CascStorage."""
    return {_game_key(path) for path in storage.root.paths()}


def _named_elsewhere(files, candidates):
    """{member: [files naming it]} for the candidate members, from the map's
    other files: text, object data, map info, sounds and triggers."""
    import re

    from wc3files.w3x import imports
    if not candidates:
        return {}
    # The custom-text triggers too: their code may name a file the
    # generated script does not.
    kinds = (imports.TEXT_EXTENSIONS | imports.ZSTRING_EXTENSIONS
             | imports.ESCAPED_EXTENSIONS | {'wct'})
    # A file is named by its path with any extension or none: maps name an
    # imported .blp as .dds or .tga, and scripts name icons with no
    # extension at all.
    by_stem = {}
    for name in candidates:
        by_stem.setdefault(_game_key(name), []).append(canonical(name))
    separator = rb'(?:\\\\|\\|/)'
    alternatives = b'|'.join(
        separator.join(re.escape(part.encode('utf-8')) for part in stem.split(BS))
        for stem in sorted(by_stem, key=len, reverse=True))
    regex = re.compile(imports._BEFORE + b'(?:' + alternatives + rb')(?:\.[A-Za-z0-9]{2,4})?'
                       + imports._AFTER, re.IGNORECASE)
    named = {}
    for member, data in files.items():
        if _extension(member) not in kinds or _extension(member) in MODEL_EXTENSIONS:
            continue
        for match in regex.finditer(data):
            found = match.group(0).decode('utf-8', errors='replace').replace(BS + BS, BS)
            for key in by_stem.get(_game_key(found), []):
                named.setdefault(key, set()).add(member)
    return {key: sorted(members) for key, members in named.items()}


def map_textures(files, game_names=None):
    """The texture report of a map's {member: bytes}."""
    from .model_file import ModelFile
    models, unreadable = [], []
    for name in sorted(files):
        if _extension(name) in MODEL_EXTENSIONS:
            try:
                models.append((name, ModelFile().read(files[name])))
            except Exception as error:  # reported, and the others go on
                unreadable.append((name, str(error)))
    used = list_textures(models)
    members = {canonical(name): name for name in files}
    # The map's own images (war3mapMap.blp, war3mapPreview.tga and the
    # like) are loaded by name, not imported.
    from wc3files.w3x.imports import is_map_data
    images = {key: name for key, name in members.items()
              if _extension(key) in IMAGE_EXTENSIONS and not is_map_data(name)}
    not_by_models = [name for key, name in images.items() if key not in used]
    elsewhere = _named_elsewhere(files, not_by_models)
    stems = {}
    for key, name in images.items():
        stems.setdefault(_game_key(key), []).append(name)
    missing, other_extension = [], []
    for key, entry in sorted(used.items()):
        if key in members:
            continue
        for name in stems.get(_game_key(key), []):
            other_extension.append((entry['path'], name))
        if game_names is not None and _game_key(key) in game_names:
            continue
        missing.append(entry)
    return {
        'models': len(models),
        'unreadable': unreadable,
        'textures': used,
        'imported_images': len(images),
        'unused': sorted(name for name in not_by_models if canonical(name) not in elsewhere),
        'named_elsewhere': {members[key]: names for key, names in sorted(elsewhere.items())},
        'missing': missing,
        'other_extension': other_extension,
        'checked_game': game_names is not None,
    }
