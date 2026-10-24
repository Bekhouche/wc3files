"""`CascStorage` - open an installed Blizzard game and read files out of it.

This is the layer the rest of the world uses; everything under it
(`config`, `index`, `archive`, `blte`, `encoding`, `root`) exists to make
this work. The whole chain for one file is:

    name -> root -> CKey -> encoding -> EKey -> local index -> archive blob
         -> strip block header -> BLTE decode -> file bytes

Each step is verifiable, and `read()` checks the last one: the MD5 of what
comes out must equal the CKey the root asked for. A wrong offset or a
missed compression mode cannot survive that check.

Reading is lazy and local-only. Opening a storage parses the configs, the
index and the root - a few seconds and a couple hundred MB of archives
untouched - and nothing is read out of a `data.NNN` until a file is asked
for. Nothing here contacts Blizzard's CDN: a file whose archive is not
installed raises rather than being downloaded.
"""
import fnmatch
import hashlib
import os

from . import archive as _archive
from . import config as _config
from .encoding import EncodingTable
from .index import CascIndex
from .root import CascRoot


class CascStorageError(Exception):
    pass


class CascStorage(object):
    def __init__(self):
        self.install_path = None
        self.data_path = None
        self.build = None
        self.build_config = {}
        self.index = None
        self.encoding = None
        self.root = None

    # ---- opening -------------------------------------------------------

    def open(self, install_path):
        """Open an installed game directory (the one holding `.build.info`)."""
        install_path = os.path.abspath(install_path)
        build_info = os.path.join(install_path, '.build.info')
        if not os.path.exists(build_info):
            raise CascStorageError(
                f'{install_path} is not a CASC install: no .build.info found'
            )

        self.install_path = install_path
        self.data_path = os.path.join(install_path, 'Data')
        data_dir = os.path.join(self.data_path, 'data')
        if not os.path.isdir(data_dir):
            raise CascStorageError(f'no Data/data directory under {install_path}')

        self.build = _config.active_build(build_info)
        build_key = self.build.get('Build Key')
        if not build_key:
            raise CascStorageError('.build.info has no Build Key for the active build')
        self.build_config = _config.parse_config(
            _config.config_path(self.data_path, build_key))

        self.index = CascIndex().read_directory(data_dir)

        encoding_keys = self.build_config.get('encoding')
        if not encoding_keys or len(encoding_keys) < 2:
            raise CascStorageError('build config has no encoding content/encoded key pair')
        self.encoding = EncodingTable().read(self._read_by_ekey(encoding_keys[1]))

        root_keys = self.build_config.get('root')
        if not root_keys:
            raise CascStorageError('build config has no root key')
        self.root = CascRoot().read(self.read_by_ckey(root_keys[0]))

        return self

    @property
    def version(self):
        return (self.build or {}).get('Version')

    @property
    def product(self):
        return (self.build or {}).get('Product')

    # ---- listing and searching -----------------------------------------

    def __len__(self):
        return len(self.root) if self.root else 0

    def files(self):
        """Every entry, in root order (a path repeats once per locale)."""
        self._require_root()
        return list(self.root.entries)

    def search(self, pattern, locale=None, limit=None):
        """Find entries by path.

        A pattern containing a wildcard (`*` or `?`) is matched as a glob
        against the whole path; anything else is treated as a case-insensitive
        substring, which is what you usually want when hunting for a file by a
        fragment of its name. Matching is case-insensitive either way, since
        Blizzard's own paths are inconsistently cased.
        """
        self._require_root()
        needle = pattern.lower()
        is_glob = any(char in pattern for char in '*?[')

        results = []
        for entry in self.root.entries:
            if locale is not None and entry.locale != locale:
                continue
            path = entry.path.lower()
            if fnmatch.fnmatch(path, needle) if is_glob else (needle in path):
                results.append(entry)
                if limit is not None and len(results) >= limit:
                    break
        return results

    def info(self, path_or_entry, locale=None):
        """Where a file lives and how big it is, without reading it."""
        entry = self._resolve(path_or_entry, locale)
        content_key = bytes.fromhex(entry.content_key)
        encoded_key = self.encoding.find(content_key)
        located = self.index.find(encoded_key) if encoded_key else None
        return {
            'path': entry.path,
            'locale': entry.locale,
            'content_key': entry.content_key,
            'encoded_key': encoded_key.hex() if encoded_key else None,
            'size': self.encoding.size_of(content_key),
            'archive': located.archive if located else None,
            'offset': located.offset if located else None,
            'stored_size': located.size if located else None,
            'installed': located is not None,
        }

    # ---- reading --------------------------------------------------------

    def read(self, path_or_entry, locale=None, verify=True):
        """Read one file's decoded bytes.

        With `verify` (the default) the result's MD5 is checked against the
        content key the root gave - the name *is* the hash in CASC, so this
        is a complete integrity check, not a heuristic.
        """
        entry = self._resolve(path_or_entry, locale)
        data = self.read_by_ckey(entry.content_key, verify=verify)
        return data

    def read_by_ckey(self, content_key, verify=True):
        self._require_keys()
        if isinstance(content_key, bytes):
            content_key = content_key.hex()
        encoded_key = self.encoding.find(content_key)
        if encoded_key is None:
            raise CascStorageError(
                f'content key {content_key} is not in the encoding table'
            )
        data = self._read_by_ekey(encoded_key)
        if verify:
            actual = hashlib.md5(data).hexdigest()
            if actual != content_key.lower():
                raise CascStorageError(
                    f'decoded data hashes to {actual}, but was stored under '
                    f'content key {content_key}'
                )
        return data

    def extract(self, path_or_entry, dest_path, locale=None):
        data = self.read(path_or_entry, locale=locale)
        parent = os.path.dirname(os.path.abspath(dest_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest_path, 'wb') as handle:
            handle.write(data)
        return len(data)

    # ---- internals ------------------------------------------------------

    def _read_by_ekey(self, encoded_key):
        located = self.index.find(encoded_key)
        if located is None:
            key = encoded_key.hex() if isinstance(encoded_key, bytes) else encoded_key
            raise CascStorageError(
                f'encoded key {key} is not in this install\'s local index - the '
                f'archive holding it is not downloaded (this reader is local-only '
                f'and never contacts the CDN)'
            )
        return _archive.read_block(os.path.join(self.data_path, 'data'), located)

    def _resolve(self, path_or_entry, locale):
        self._require_root()
        if hasattr(path_or_entry, 'content_key'):
            return path_or_entry

        candidates = self.root.find(path_or_entry)
        if not candidates:
            # Fall back to a case-insensitive exact match before giving up;
            # Blizzard's own paths are not consistently cased.
            wanted = path_or_entry.lower()
            candidates = [e for e in self.root.entries if e.path.lower() == wanted]
        if not candidates:
            raise CascStorageError(f'{path_or_entry!r} is not in this storage')

        if locale is not None:
            for entry in candidates:
                if entry.locale == locale:
                    return entry
            available = sorted({e.locale for e in candidates})
            raise CascStorageError(
                f'{path_or_entry!r} has no {locale!r} entry (have: {available})'
            )
        return candidates[0]

    def _require_keys(self):
        """Enough of the storage to resolve a key. Deliberately weaker than
        _require_root: open() itself reads the root *through* these layers, so
        requiring the root here would make the storage impossible to open."""
        if self.index is None or self.encoding is None:
            raise CascStorageError('no storage is open; call open() first')

    def _require_root(self):
        if self.root is None:
            raise CascStorageError('no storage is open; call open() first')
