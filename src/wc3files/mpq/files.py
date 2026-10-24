import os
import re
import struct
from tqdm import tqdm
from io import BytesIO
from ..data import BinaryDataType
from .data import *
from .cipher import MpqCipher
from .header import MpqHeader
from .hash_table import MpqHashTable
from .block_table import MpqBlockTable
from wc3files.w3o.object_file import ObjectFile
from wc3files.mdl.model_file import ModelFile

# Real asset extensions worth searching for when mining a text-bearing file
# for further file references - not just the original .mdl/.mdx/.blp/.dds/
# .toc/.tga set, which missed real, extremely common sound-file references,
# custom melee AI scripts (.ai - StartMeleeAI("something.ai") is a real,
# common JASS pattern - 744 real .ai entries in the bundled reference lists,
# not a rare edge case), and custom UI frame definitions (.fdf). Chosen from
# an actual frequency count of the bundled reference lists, filtered down to
# extensions WC3 itself (not WoW/Diablo/StarCraft - those lists are a
# cross-game corpus, e.g. .m2/.wmo/.adt/.cof/.ds1/.scm/.pud are all other
# games' formats) really uses for assets referenced by path from a map's own
# scripts/object data/text files - not added speculatively.
_ASSET_REFERENCE_EXTENSIONS = ('.mdl', '.mdx', '.blp', '.dds', '.toc', '.tga', '.wav', '.mp3',
                               '.ai', '.fdf')

# Real object-editor data extensions (matches w3x_file.py's own
# OBJECT_FILE_EXTENSIONS convention) - these get parsed structurally via
# ObjectFile, not text-regex-scanned, since their references live inside a
# real field structure, not free text.
_OBJECT_DATA_EXTENSIONS = ('w3o', 'w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q')

# A real model file (.mdx/.mdl) embeds further real file references
# structurally too - not just its own texture list (Textures{Image "path"})
# but also attachment-point override models and particle-emitter textures/
# models (this package's own ModelFile parser already exposes all of these as
# plain strings nested somewhere in its parsed dict, at whatever field each
# actually lives under - reusing find_val_from_dict's generic recursive
# search, exactly like the object-data case above, needs no per-field-name
# knowledge of the model format to find them).
_MODEL_DATA_EXTENSIONS = ('mdx', 'mdl')

# Base + Reforged "Skin" override object-data filenames worth checking
# explicitly (see w3x_file.py's own OBJECT_FILES list) - war3mapSkin.* isn't
# in the bundled reference filename lists, so without this a Reforged map's
# HD-skin object overrides would never be discovered at all when no real
# (listfile) exists.
_OBJECT_DATA_CANDIDATE_NAMES = (
     ['war3map.' + ext for ext in _OBJECT_DATA_EXTENSIONS]
     + ['war3mapSkin.' + ext for ext in _OBJECT_DATA_EXTENSIONS if ext != 'w3o']
)

# Real, plain-text file types worth mining for further references, beyond
# scripts and object data - a real map's own custom .txt import lists or .slk
# tables can name further real assets by path just as readily as a script can.
_TEXT_SCAN_EXTENSIONS = ('.j', '.lua', '.txt', '.slk')

# What the regex below searches for: terminal assets *and* further text-scan
# sources. A real, load-bearing distinction - a script referencing a custom
# .txt import list by path is itself a text-scan candidate for the *next*
# iteration (see _is_scan_candidate/the deep-scan loop in read()), so if this
# pattern only matched terminal asset extensions, that .txt file would never
# even be recognized as a reference to look up in the first place, breaking
# the whole point of scanning it further one hop later.
_SEARCHABLE_EXTENSIONS = tuple(dict.fromkeys(_ASSET_REFERENCE_EXTENSIONS + _TEXT_SCAN_EXTENSIONS))

# Deliberately unquoted (unlike an earlier version that only matched inside
# `"..."` for JASS specifically) - SLK/TXT content doesn't use JASS's quoting
# convention at all, and a permissive match here is harmless: a spurious
# match is filtered out for free the moment it fails check_file()'s real hash
# lookup, so there's no real downside to casting a slightly wider net once
# real verification gates every candidate anyway.
_REFERENCE_PATTERN = re.compile(
     r'[A-Za-z0-9_ .\\/\-]+\.(?:' + '|'.join(ext[1:] for ext in _SEARCHABLE_EXTENSIONS) + r')',
     re.IGNORECASE,
)


class MpqFiles:
    """
    Class for Block Table
    """

    def __init__(self):
        """Initialize by clearing any stored MPQ data."""
        self.clear()
        self.mpq_cipher = MpqCipher()
        self.default_files = self.get_filelist()
        self.mpq_hash_table = MpqHashTable()
        self.mpq_block_table = MpqBlockTable()
        self.mpq_archive_header = MpqHeader()
        self.data_hash_table = {}
        self.data_block_table = {}
        self.data_archive_header = {}
        self._listfile = {}

    def clear(self):
        """Clear any previously stored MPQ data."""
        self._data = {}

    def set_archive_header(self, mpq_archive_header):
        self.mpq_archive_header = mpq_archive_header
        self.data_archive_header = self.mpq_archive_header.get_data()

    def set_hash_table(self, mpq_hash_table):
        self.mpq_hash_table = mpq_hash_table
        self.data_hash_table = self.mpq_hash_table.get_data()

    def set_block_table(self, mpq_block_table):
        self.mpq_block_table = mpq_block_table
        self.data_block_table = self.mpq_block_table.get_data()

    def read(self, reader):
        self._data = {}
        self._listfile = {}

        # 1. Check (listfile), (attributes), (signature)
        file_data = self.read_file(reader, '(listfile)')
        if file_data:
            self._listfile['(listfile)'] = file_data[1]
            try:
                check_list = [file.strip() for file in file_data[0].decode(errors='replace').split('\n')
                              if file.strip()]
            except Exception:
                check_list = []

            if '(attributes)' not in self._listfile:
                file_data = self.check_file('(attributes)')
                if file_data:
                    self._listfile['(attributes)'] = file_data

            if '(signature)' not in self._listfile:
                file_data = self.check_file('(signature)')
                if file_data:
                    self._listfile['(signature)'] = file_data

            for file in tqdm(check_list):
                if file not in self._listfile:
                    file_data = self.check_file(file)
                    if file_data:
                        self._listfile[file] = file_data

        # 2. No (complete) listfile - seed from the bundled reference filename
        # list, then deep-scan: any text-bearing file (script, object data,
        # SLK, TXT) can itself reference further real files by path, and a
        # newly-discovered text file can reference still more - keep scanning
        # newly-found files until a pass turns up nothing new, not just once
        # (a single script/object-data pass, as this used to be, misses
        # anything named only inside a second-hop file - e.g. a custom .txt
        # import list a script references by name, which itself names more
        # real assets).
        if len(self.data_block_table) > len(self._listfile):
            for file in tqdm(self.default_files):
                file_data = self.check_file(file)
                if file_data:
                    self._listfile[file] = file_data

            for file in _OBJECT_DATA_CANDIDATE_NAMES:
                if file not in self._listfile:
                    file_data = self.check_file(file)
                    if file_data:
                        self._listfile[file] = file_data

            scanned = set()
            while True:
                pending = [name for name in list(self._listfile.keys())
                           if name not in scanned and self._is_scan_candidate(name)]
                if not pending:
                    break

                new_candidates = set()
                for file in tqdm(pending):
                    scanned.add(file)
                    file_data = self.read_file(reader, file)
                    if file_data:
                        new_candidates |= self._extract_references(file, file_data[0])

                found_new = False
                for candidate in new_candidates:
                    if candidate not in self._listfile:
                        file_data = self.check_file(candidate)
                        if file_data:
                            self._listfile[candidate] = file_data
                            found_new = True
                if not found_new:
                    break

        for file in self._listfile.keys():
            try:
                file_data = self.read_file(reader, file)
                if file_data:
                    self._data[file] = file_data[0]
            except Exception:
                pass

        self._data['(listfile)'] = '\r\n'.join(sorted(self._listfile.keys())).encode()

    @staticmethod
    def _is_scan_candidate(name):
        return is_scan_candidate(name)

    @staticmethod
    def _extract_references(name, raw_bytes):
        return extract_references(name, raw_bytes)

    def check_file(self, filename):
        hash_entry = self.mpq_hash_table.get_hash_table_entry(filename)
        if hash_entry is None:
            return None
        else:
            return hash_entry

    def read_file(self, reader, filename):
        
        force_decompress = False
        hash_entry = self.mpq_hash_table.get_hash_table_entry(filename)
        if hash_entry is None:
            return None
        
        block_entry = self.data_block_table[hash_entry['FileBlockIndex']]
        

        if block_entry['Flags'] & MPQFileDataType.EXISTS.value:
            if block_entry['BlockSize'] == 0:
                return None

            offset = block_entry['BlockOffset'] + self.data_archive_header['ARCHIVE_OFFSET']

            if block_entry['Flags'] & MPQFileDataType.ENCRYPTED.value:
                key = self.mpq_cipher.hash(os.path.basename(filename), MPQHashType.MPQ_HASH_FILE_KEY.value)
                if block_entry['Flags'] & MPQFileDataType.FIX_KEY.value:
                        key = (key + block_entry['BlockOffset']) ^ block_entry['FileSize']

            if not block_entry['Flags'] & MPQFileDataType.SINGLE_UNIT.value:
                sector_size = 512 << self.data_archive_header['SectorSizeShift']
                sectors = block_entry['FileSize'] // sector_size + 1
                if block_entry['Flags'] & MPQFileDataType.SECTOR_CRC.value:
                        crc = True
                        sectors += 1
                else:
                        crc = False
                
                result = BytesIO()
                sector_bytes_left = block_entry['FileSize']
                # 'sectors' (FileSize // sector_size + 1) already spans every data sector
                # including a trailing partial/empty one - positions[-1] always lands on
                # the block's true compressed end.
                block_count = sectors

                reader.seek(offset)
                sectors_data = reader.read(BinaryDataType.BYTES, num=4*(sectors+1))
                if block_entry['Flags'] & MPQFileDataType.ENCRYPTED.value:
                        sectors_data = self.mpq_cipher.decrypt(sectors_data, key - 1)
                positions = struct.unpack('<%dI' % (sectors + 1), sectors_data)

                current_offset = 0
                if block_entry['Flags'] & MPQFileDataType.COMPRESS.value :
                        reader.seek(offset + positions[0])
                        raw_bytes_to_read = positions[block_count] - positions[0]
                else:
                        raw_bytes_to_read = block_entry['FileSize']

                file_data = reader.read(BinaryDataType.BYTES, num=raw_bytes_to_read)

                for i in range(block_count):
                    expected_sector_size = min(sector_size, sector_bytes_left)
                    this_sector_size = expected_sector_size
                    if block_entry['Flags'] & MPQFileDataType.COMPRESS.value:
                        this_sector_size = positions[i+1] - positions[i]

                    sector = file_data[current_offset:current_offset + this_sector_size]
                    if block_entry['Flags'] & MPQFileDataType.ENCRYPTED.value:
                        sector = self.mpq_cipher.decrypt(sector, key + i)

                    if (block_entry['Flags'] & MPQFileDataType.COMPRESS.value and
                    (force_decompress or len(sector) < expected_sector_size)):
                        sector = self.mpq_cipher.decompress(sector)

                    sector_bytes_left -= len(sector)
                    result.write(sector)

                    current_offset += this_sector_size
                file_data = result.getvalue()
                
            else:
                reader.seek(offset)
                file_data = reader.read(BinaryDataType.BYTES, num=block_entry['BlockSize'])

                if (block_entry['Flags'] & MPQFileDataType.COMPRESS.value and
                        (force_decompress or block_entry['FileSize'] > block_entry['BlockSize'])):
                        file_data = self.mpq_cipher.decompress(file_data)

            return file_data, hash_entry


    def get_data(self):
        """Return the parsed MPQ data."""
        return self._data

    @staticmethod
    def get_filelist():
        files = []
        reference_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'reference')
        for file_path in ['Warcraft III.txt', 'Warcraft III Maps.txt']:
            with open(os.path.join(reference_path, file_path), mode='r', encoding='utf8') as file:
                files += [file.strip() for file in file.readlines()]
        files = sorted(list(set(files)))
        return files
    
    @staticmethod
    def find_val_from_dict(data, extensions):
        return find_val_from_dict(data, extensions)

def find_val_from_dict(data, extensions):
    """Recursively walks any dict/list/str-shaped structure looking for
    string values ending in one of `extensions`. Genuinely recursive at
    every level, not just one level of dict-of-dicts or list-of-strings -
    a real fix, not just a style cleanup: model data (ModelFile._data)
    has *lists of dicts* (e.g. `textures: [{'path': ...}, ...]`), which
    the previous version couldn't handle - `item.endswith(...)` on a
    dict raises AttributeError, silently swallowed by this function's own
    caller's try/except, so it would have looked like "no references
    found" instead of "crashed" without ever surfacing the real bug."""
    results = []

    def visit(node):
        if isinstance(node, dict):
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, str):
            if any(node.endswith(ext) for ext in extensions):
                results.append(node)

    visit(data)
    return results


def is_scan_candidate(name):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext in _OBJECT_DATA_EXTENSIONS or ext in _MODEL_DATA_EXTENSIONS:
        return True
    return ('.' + ext) in _TEXT_SCAN_EXTENSIONS

def extract_references(name, raw_bytes):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    matches = set()

    if ext in _OBJECT_DATA_EXTENSIONS:
        try:
            obj = ObjectFile()
            obj.read(raw_bytes, ext)
            for value in find_val_from_dict(obj._data, _SEARCHABLE_EXTENSIONS):
                matches.add(value.replace('\\\\', '\\'))
        except Exception:
            pass
        return matches

    if ext in _MODEL_DATA_EXTENSIONS:
        try:
            model = ModelFile()
            model.read(raw_bytes)
            for value in find_val_from_dict(model._data, _SEARCHABLE_EXTENSIONS):
                matches.add(value.replace('\\\\', '\\'))
        except Exception:
            pass
        return matches

    try:
        text = raw_bytes.decode('utf-8', errors='replace')
    except Exception:
        return matches
    for match in _REFERENCE_PATTERN.finditer(text):
        matches.add(match.group().strip().replace('\\\\', '\\'))
    return matches


def to_hex(value):
    value = str(hex(value)).replace('0x', '').zfill(8).upper()
    return value

def bin_to_hex(binary_data, limit=16):
    if limit is not None:
        binary_data = binary_data[:limit]
    hex_string = ' '.join(f'{byte:02X}' for byte in binary_data)
    if limit:
         hex_string += ' ...'
    return hex_string