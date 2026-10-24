import base64
import copy
import os

from .data import ModelFileType
from .mdx_io import empty_model, load_mdx, save_mdx
from .mdl_io import load_mdl, save_mdl


class ModelFile:
    """Reader/writer for Warcraft III model files (.mdx / .mdl)."""

    def __init__(self):
        self.clear()

    def clear(self):
        self._data = empty_model()
        self._file_type = None

    def export_data(self):
        data = copy.deepcopy(self._data)
        # JSON-friendly encoding for unknown binary chunks.
        unknowns = []
        for chunk in data.get('unknownChunks') or []:
            unknowns.append({
                'tag': chunk.get('tag'),
                'data': base64.b64encode(chunk.get('data') or b'').decode('ascii'),
            })
        data['unknownChunks'] = unknowns
        return data

    def import_data(self, data):
        model = copy.deepcopy(data)
        unknowns = []
        for chunk in model.get('unknownChunks') or []:
            raw = chunk.get('data') or b''
            if isinstance(raw, str):
                raw = base64.b64decode(raw)
            unknowns.append({'tag': chunk.get('tag'), 'data': raw})
        model['unknownChunks'] = unknowns
        # Ensure all expected keys exist.
        base = empty_model()
        base.update(model)
        self._data = base
        return self

    def read(self, file_path):
        if isinstance(file_path, (bytes, bytearray)):
            data = bytes(file_path)
            if data[:4] == b'MDLX':
                self._data = load_mdx(data)
                self._file_type = ModelFileType.MDX
            else:
                self._data = load_mdl(data.decode('utf-8', errors='replace'))
                self._file_type = ModelFileType.MDL
            return self

        if not isinstance(file_path, str):
            raise TypeError('file_path must be a path string or bytes')

        ext = file_path.rsplit('.', 1)[-1].lower()
        if ext not in (item.value for item in ModelFileType):
            raise ValueError(f'Invalid model file type: {ext}')

        with open(file_path, 'rb') as fh:
            raw = fh.read()

        if ext == ModelFileType.MDX.value or raw[:4] == b'MDLX':
            self._data = load_mdx(raw)
            self._file_type = ModelFileType.MDX
        else:
            self._data = load_mdl(raw.decode('utf-8', errors='replace'))
            self._file_type = ModelFileType.MDL
        return self

    def write(self, file_path):
        ext = file_path.rsplit('.', 1)[-1].lower()
        if ext not in (item.value for item in ModelFileType):
            raise ValueError(f'Invalid model file type: {ext}')

        directory = os.path.dirname(file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if ext == ModelFileType.MDX.value:
            payload = save_mdx(self._data)
            with open(file_path, 'wb') as fh:
                fh.write(payload)
            self._file_type = ModelFileType.MDX
        else:
            text = save_mdl(self._data)
            with open(file_path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
            self._file_type = ModelFileType.MDL
        return self

    def get_file_type(self):
        return self._file_type

    @property
    def name(self):
        return self._data.get('name', '')

    @property
    def version(self):
        return self._data.get('version', 800)

    @property
    def sequences(self):
        return self._data.get('sequences') or []

    @property
    def textures(self):
        return self._data.get('textures') or []

    @property
    def geosets(self):
        return self._data.get('geosets') or []

    @property
    def bones(self):
        return self._data.get('bones') or []
