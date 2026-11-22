"""
Per-format dump (binary -> JSON) / build (JSON -> binary) adapters for the CLI.

Every wc3files file-format class exposes read()/write() plus either
get_data()/`._data =` or export_data()/import_data() to move data in and out as a plain
dict - but the exact pair, and a couple of format-specific quirks (JSON object keys are
always strings, so anything keyed by int needs converting back on build), differ per
class. Each entry below normalizes that into a common (path -> dict) / (dict -> path)
shape the CLI can drive generically.
"""
import base64
import copy
import os

from wc3files.w3i import W3iFile
from wc3files.doo import MapUnitsFile, MapDoodadsFile
from wc3files.wtg import MapTriggersFile, MapCustomTextTriggersFile
from wc3files.imp import ImpFile
from wc3files.w3f import CampaignInfoFile
from wc3files.w3o import WtsFile, SlkFile, CfgFile, WesFile, ObjectFile
from wc3files.w3g import ReplayFile
from wc3files.w3e import TerrainFile
from wc3files.w3c import CameraFile
from wc3files.w3s import SoundsFile
from wc3files.wpm import PathingFile
from wc3files.shd import ShadowMapFile
from wc3files.w3r import RegionFile
from wc3files.w3r.region import Region
from wc3files.mmp import PreviewIconsFile, PreviewIcon


def _get_data_attr(instance):
    return instance.get_data()


def _set_data_attr(instance, data):
    instance._data = data


def _wts_set_data(instance, data):
    # JSON object keys are always strings - war3map.wts entries are keyed by int string id.
    instance.import_data({int(key): value for key, value in data.items()})


def _slk_get_data(instance):
    return {'id_column': instance._id_column, 'rows': instance.export_data()}


def _slk_set_data(instance, data):
    instance.import_data(data['rows'], id_column=data.get('id_column'))


def _slk_write(instance, path, data, validate=False):
    # `validate` is part of the write hook's signature for every format, even
    # where only `obj` acts on it. Omitting it here made `build slk` fail with
    # a TypeError for every input.
    instance.write(path, id_column=data.get('id_column'))


def _wtg_set_data(instance, data):
    # 'item_counts' is keyed by TriggerItemType int values - the only int-keyed dict in this
    # format's data (everything under 'items'/'variables' is already string/list-keyed).
    data = dict(data)
    if isinstance(data.get('item_counts'), dict):
        data['item_counts'] = {int(k): v for k, v in data['item_counts'].items()}
    instance._data = data


def _obj_set_data(instance, data):
    # ObjectTable stores per-level mod values/pointers as {int level: value} - the only
    # int-keyed dicts nested inside this format's data. Everything else (object rawcodes,
    # mod field codes) is already string-keyed.
    for table_set in data.values():
        if not table_set:
            continue
        for table in table_set.values():
            for entry in (table or {}).values():
                for mod_id, mod in entry.items():
                    if mod_id == 'ID':
                        continue
                    for key in ('value', 'ptr'):
                        if isinstance(mod.get(key), dict):
                            mod[key] = {int(level): v for level, v in mod[key].items()}
    instance.import_data(data)


def _obj_write(instance, path, data, validate=False):
    # write() infers the object type (w3o/w3u/w3t/w3a/.../war3mapSkin.*) from path's
    # extension - no separate object_type argument needed on this side. validate=True checks
    # every field against the bundled Blizzard metadata schema before writing and raises
    # instead of writing if anything fails - see object_validator.py for exactly what that
    # does and does not check.
    instance.write(path, validate=validate)


def _b64(raw):
    return base64.b64encode(bytes(raw or b'')).decode('ascii')


def _unb64(text):
    return base64.b64decode(text or '')


def _w3s_get_data(instance):
    # Every field is a string or a number now that the dialogue fields are
    # read as themselves rather than as an opaque trailing blob, so nothing
    # needs encoding.
    return copy.deepcopy(instance.get_data())


def _w3s_set_data(instance, data):
    data = copy.deepcopy(data)
    for sound in data.get('sounds') or ():
        if isinstance(sound.get('cone_orientation'), list):
            sound['cone_orientation'] = tuple(sound['cone_orientation'])
    instance._data = data


def _wpm_get_data(instance):
    # The pathing map is one byte per cell - base64 keeps it compact and exact
    # rather than ballooning into a JSON array of integers.
    data = dict(instance.get_data())
    data['cells'] = _b64(data.get('cells'))
    return data


def _wpm_set_data(instance, data):
    data = dict(data)
    if isinstance(data.get('cells'), str):
        data['cells'] = _unb64(data['cells'])
    instance._data = data


def _shd_get_data(instance):
    data = dict(instance.get_data())
    data['cells'] = _b64(data.get('cells'))
    return data


def _shd_set_data(instance, data):
    data = dict(data)
    if isinstance(data.get('cells'), str):
        data['cells'] = _unb64(data['cells'])
    instance._data = data


def _shd_read(instance, path, options):
    # A shadow map is a bare byte grid with no header, so its dimensions can
    # only come from the map's terrain (war3map.w3e). Refuse rather than
    # guess - a wrong size silently reshapes the whole grid.
    width = (options or {}).get('width')
    height = (options or {}).get('height')
    if not width or not height:
        width, height = _shd_size_from_siblings(path)
    if not width or not height:
        raise ValueError(
            "a war3map.shd carries no header, so its size cannot be read from "
            "the file itself, and there is no war3map.wpm or war3map.w3e next "
            "to it - pass --width and --height (the pathing map's size, which "
            "`dump wpm` reports)")
    instance.read(path, width, height)


def _shd_size_from_siblings(path):
    """The shadow map's size from the map files next to it: the pathing map
    has the same grid, and the terrain one 4x4 block per tile (its width and
    height count tile corners, one more than tiles)."""
    folder = os.path.dirname(os.path.abspath(path))
    names = {name.lower(): name for name in os.listdir(folder)}
    if 'war3map.wpm' in names:
        pathing = PathingFile()
        pathing.read(os.path.join(folder, names['war3map.wpm']))
        return pathing.get_data()['width'], pathing.get_data()['height']
    if 'war3map.w3e' in names:
        terrain = TerrainFile()
        terrain.read(os.path.join(folder, names['war3map.w3e']))
        data = terrain.get_data()
        return 4 * (data['width'] - 1), 4 * (data['height'] - 1)
    return None, None


def _w3r_get_data(instance):
    regions = {}
    for index, region in instance.get_regions().items():
        regions[str(index)] = {
            'left': region.left, 'right': region.right,
            'bottom': region.bottom, 'top': region.top,
            'name': region.name, 'index': region.index,
            # A 4-byte weather rawcode such as b'RAhr'. latin-1 round-trips
            # every byte value exactly, and keeps real rawcodes readable.
            'weather_effect': bytes(region.weather_effect).decode('latin-1'),
            'ambient_sound': region.ambient_sound,
            'color': list(region.color),
        }
    return {'regions': regions}


def _w3r_set_data(instance, data):
    instance.clear()
    for index, entry in (data.get('regions') or {}).items():
        instance.add_region(int(index), Region(
            left=entry['left'], right=entry['right'],
            bottom=entry['bottom'], top=entry['top'],
            name=entry['name'], index=entry['index'],
            weather_effect=entry['weather_effect'].encode('latin-1'),
            ambient_sound=entry['ambient_sound'],
            color=tuple(entry['color']),
        ))


def _mmp_get_data(instance):
    return {'icons': {str(index): {'icon_type': icon.icon_type, 'x': icon.x,
                                   'y': icon.y, 'color': list(icon.color)}
                      for index, icon in instance.get_icons().items()}}


def _mmp_set_data(instance, data):
    instance.clear()
    for index, entry in (data.get('icons') or {}).items():
        instance.add_icon(int(index), PreviewIcon(
            icon_type=entry['icon_type'], x=entry['x'], y=entry['y'],
            color=tuple(entry['color'])))


def _default_read(instance, path, options):
    instance.read(path)


class Format:
    def __init__(self, cls, get_data=_get_data_attr, set_data=_set_data_attr, write=None,
                 can_build=True, notes=None, read=None):
        self.cls = cls
        self.get_data = get_data
        self.set_data = set_data
        self.read = read or _default_read
        self.write = write or (lambda instance, path, data, validate=False: instance.write(path))
        self.can_build = can_build
        self.notes = notes

    def dump(self, input_path, options=None):
        instance = self.cls()
        self.read(instance, input_path, options)
        return self.get_data(instance)

    def build(self, data, output_path, validate=False):
        if not self.can_build:
            raise NotImplementedError(
                f"'{output_path}' was not written: this format has no supported "
                f"build (JSON -> binary) path yet.{(' ' + self.notes) if self.notes else ''}"
            )
        instance = self.cls()
        self.set_data(instance, data)
        self.write(instance, output_path, data, validate=validate)


FORMATS = {
    'w3i': Format(W3iFile),
    'doo': Format(MapUnitsFile,
                  notes='war3mapUnits.doo - preplaced units and items.'),
    'doodads': Format(MapDoodadsFile, get_data=lambda i: i.get_data(),
                      set_data=lambda i, d: i.import_data(d),
                      notes=('war3map.doo - doodads and destructables. A '
                             'different format from war3mapUnits.doo despite '
                             'the shared extension and magic.')),
    'wtg': Format(MapTriggersFile, set_data=_wtg_set_data),
    'wct': Format(MapCustomTextTriggersFile),
    'imp': Format(ImpFile),
    'w3f': Format(CampaignInfoFile),
    'wts': Format(WtsFile, get_data=lambda i: i.export_data(), set_data=_wts_set_data),
    'cfg': Format(CfgFile, get_data=lambda i: i.export_full(),
                  set_data=lambda i, d: i.import_full(d),
                  notes=("The JSON carries the file's layout alongside its values, so a "
                         'dump -> build is byte-for-byte.')),
    'wes': Format(WesFile, get_data=lambda i: i.export_full(),
                  set_data=lambda i, d: i.import_full(d),
                  notes=("The JSON carries the file's layout alongside its values, so a "
                         'dump -> build is byte-for-byte. It used to carry only a flat '
                         '{key: value} dict, which silently kept 53 of UnitEditorData.txt\'s '
                         '404 entries, because real files repeat keys across sections.')),
    'slk': Format(SlkFile, get_data=_slk_get_data, set_data=_slk_set_data, write=_slk_write),
    'obj': Format(ObjectFile, get_data=lambda i: i.export_data(), set_data=_obj_set_data,
                  write=_obj_write),
    'w3g': Format(ReplayFile, get_data=lambda i: i.get_data(),
                  set_data=lambda i, d: i.import_data(d), notes=(
        "A dump -> build is byte-for-byte, verified across 290 real replays. The JSON "
        "carries the stored compressed blocks as well as the decoded stream, because the "
        "game does not compress at zlib level 9 - recompressing identical bytes yields a "
        "valid file 5-7% smaller, which is what this used to do. Editing the decoded data "
        "makes the blocks stale, and write() then recompresses as before."
    )),
    'w3e': Format(TerrainFile),
    'w3c': Format(CameraFile),
    'w3s': Format(SoundsFile, get_data=_w3s_get_data, set_data=_w3s_set_data,
                  notes='Every field is a plain string or number.'),
    'wpm': Format(PathingFile, get_data=_wpm_get_data, set_data=_wpm_set_data,
                  notes='The `cells` grid is base64 in the JSON - one byte per cell.'),
    'shd': Format(ShadowMapFile, get_data=_shd_get_data, set_data=_shd_set_data,
                  read=_shd_read,
                  notes=("A shadow map has no header, so `dump shd` needs --width and "
                         "--height from the map's terrain. `cells` is base64.")),
    'w3r': Format(RegionFile, get_data=_w3r_get_data, set_data=_w3r_set_data),
    'mmp': Format(PreviewIconsFile, get_data=_mmp_get_data, set_data=_mmp_set_data),
}
# `doo` is war3mapUnits.doo and `doodads` war3map.doo, the other way round
# from the file names. Both keep their meaning, so existing commands do not
# change silently; `units` names the units file plainly.
FORMATS['units'] = FORMATS['doo']

# The file each choice reads, for the help.
FORMAT_FILES = {
    'w3i': 'war3map.w3i', 'doo': 'war3mapUnits.doo', 'units': 'war3mapUnits.doo',
    'doodads': 'war3map.doo', 'wtg': 'war3map.wtg', 'wct': 'war3map.wct',
    'imp': 'war3map.imp', 'w3f': 'war3campaign.w3f', 'wts': 'war3map.wts',
    'cfg': '*.cfg', 'wes': 'WorldEditStrings.txt and the like', 'slk': '*.slk',
    'obj': 'war3map.w3u/w3t/w3a/w3b/w3d/w3h/w3q/w3o', 'w3g': '*.w3g replay',
    'w3e': 'war3map.w3e', 'w3c': 'war3map.w3c', 'w3s': 'war3map.w3s',
    'wpm': 'war3map.wpm', 'shd': 'war3map.shd', 'w3r': 'war3map.w3r', 'mmp': 'war3map.mmp',
}

_EXTENSIONS = {
    'w3i': 'w3i', 'wtg': 'wtg', 'wct': 'wct', 'imp': 'imp', 'w3f': 'w3f', 'wts': 'wts',
    'cfg': 'cfg', 'slk': 'slk', 'w3g': 'w3g', 'w3e': 'w3e', 'w3c': 'w3c', 'w3s': 'w3s',
    'wpm': 'wpm', 'shd': 'shd', 'w3r': 'w3r', 'mmp': 'mmp',
    'w3u': 'obj', 'w3t': 'obj', 'w3a': 'obj', 'w3b': 'obj', 'w3d': 'obj', 'w3h': 'obj',
    'w3q': 'obj', 'w3o': 'obj',
}


def format_for_name(path):
    """The format a file's name gives, or None when it does not say: a .doo
    other than war3map.doo and war3mapUnits.doo could be either one."""
    name = os.path.basename(path).lower()
    if name == 'war3mapunits.doo':
        return 'units'
    if name == 'war3map.doo':
        return 'doodads'
    return _EXTENSIONS.get(name.rsplit('.', 1)[-1]) if '.' in name else None
