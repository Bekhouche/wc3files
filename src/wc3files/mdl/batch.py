"""
One edit run over many models (#234).

The model tools are functions on a model: `compute_extents`, and the edits
that follow it. `edit_models` runs one of them over files and folders and
writes each result, so every CLI model command takes the same inputs:

- a model (.mdx or .mdl), or a folder, searched with its subfolders;
- `out_dir` to write the results there, under the path each model had in
  its folder, or `in_place` to overwrite the models.

A model is written back in its own format. One that cannot be read or
edited is reported and left as it was; the others go on.
"""
import os

MODEL_EXTENSIONS = ('.mdx', '.mdl')


def model_data(model):
    """The data dict of a ModelFile, or the dict itself."""
    data = getattr(model, '_data', None)
    return data if data is not None else model


def model_paths(inputs):
    """(path, path relative to its input) of every model under `inputs`."""
    for item in inputs:
        if os.path.isdir(item):
            for root, dirs, files in os.walk(item):
                dirs.sort()
                for name in sorted(files):
                    if name.lower().endswith(MODEL_EXTENSIONS):
                        path = os.path.join(root, name)
                        yield path, os.path.relpath(path, item)
        else:
            yield item, os.path.basename(item)


def edit_models(inputs, edit, out_dir=None, in_place=False):
    """Run `edit(data)` on each model under `inputs` and write it. Yields
    (source, written path or None, edit's result or the exception)."""
    from .model_file import ModelFile
    if not in_place and not out_dir:
        raise ValueError('give an output folder, or edit the models in place')
    for path, relative in model_paths(inputs):
        target = path if in_place else os.path.join(out_dir, relative)
        try:
            model = ModelFile()
            model.read(path)
            result = edit(model._data)
            model.write(target)
        except Exception as error:  # reported, and the run goes on
            yield path, None, error
            continue
        yield path, target, result
