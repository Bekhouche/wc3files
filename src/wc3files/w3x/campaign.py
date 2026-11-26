"""
Splitting a campaign into standalone maps (#249).

`split_campaign(members)` turns the members of a .w3n campaign into one map
per mission, as Xetanth87's Campaign Splitter does, so the missions can be
played on their own or in co-op. Each map gets the campaign's custom object
data merged under its own, the campaign's gameplay constants and interface
when it has none of its own, the campaign's imported files, and the
campaign's strings those bring.

A campaign keeps its data in war3campaign.* members beside the maps it
holds. They are read here as a map's war3map.* files and copied in with
wc3files.w3x.transfer, which numbers the campaign's strings into each map's
war3map.wts. The map's own object data, constants, interface and imported
files win over the campaign's, as in the game, where a map's object data is
applied over the campaign's.
"""
import os
import re
import tempfile

from wc3files.w3x.imports import find, member_name
from wc3files.w3x.transfer import transfer

CAMPAIGN = 'war3campaign'
MAP_EXTENSIONS = ('.w3x', '.w3m')
# war3campaign.<ext> is read as war3map.<ext>; the Skin and Misc text files
# keep their suffix.
_RENAMED = {'w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q', 'wts', 'imp'}
_TEXT = {'war3campaignskin.txt': 'war3mapSkin.txt', 'war3campaignmisc.txt': 'war3mapMisc.txt'}
PARTS = ('objects', 'interface', 'constants', 'imports')
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _read_info(members):
    from wc3files.w3f import CampaignInfoFile
    key = find(members, 'war3campaign.w3f')
    if key is None:
        raise ValueError('not a campaign: there is no war3campaign.w3f')
    handle = tempfile.NamedTemporaryFile(suffix='.w3f', delete=False)
    try:
        handle.write(members[key])
        handle.close()
        info = CampaignInfoFile()
        info.read(handle.name)
        return info.get_data()
    finally:
        os.remove(handle.name)


def campaign_as_map(members):
    """The campaign's own data as the members of a map: war3campaign.w3u as
    war3map.w3u and so on, and its imported files as they are. The maps the
    campaign holds are left out."""
    out = {}
    for name, data in members.items():
        lower = member_name(name).lower()
        if lower.endswith(MAP_EXTENSIONS) or lower.startswith('('):
            continue
        if lower in _TEXT:
            out[_TEXT[lower]] = data
        elif '\\' not in lower and lower.startswith(CAMPAIGN + '.'):
            ext = lower.rsplit('.', 1)[-1]
            if ext in _RENAMED:
                out['war3map.' + ext] = data
        elif '\\' not in lower and lower.startswith(CAMPAIGN + 'skin.'):
            out['war3mapSkin.' + lower.rsplit('.', 1)[-1]] = data
        elif '\\' in lower or not lower.startswith(CAMPAIGN):
            out[name] = data
    return out


def _read_map(data, extension):
    from wc3files.w3x.modes import _read_files
    handle = tempfile.NamedTemporaryFile(suffix=extension, delete=False)
    try:
        handle.write(data)
        handle.close()
        return _read_files(handle.name)
    finally:
        os.remove(handle.name)


def _resolve(text, strings):
    match = re.fullmatch(r'TRIGSTR_(\d+)', text or '')
    return strings.get(int(match.group(1)), text) if match else (text or '')


def _file_name(index, chapter, title, fallback, extension):
    parts = [part for part in (chapter, title) if part]
    label = ' - '.join(parts) if parts else fallback
    label = re.sub(r'\|c[0-9A-Fa-f]{8}|\|r', '', label)
    label = _UNSAFE.sub(' ', label).strip(' .') or fallback
    return f'{index:02d} {label}{extension}'


def _set_map_name(files, name):
    """Write `name` as the map's name in its war3map.w3i, as a new string of
    its war3map.wts; False when the map info does not read."""
    from wc3files.table.wts_file import WtsFile
    from wc3files.w3i import W3iFile
    key = find(files, 'war3map.w3i')
    if key is None:
        return False
    handle = tempfile.NamedTemporaryFile(suffix='.w3i', delete=False)
    try:
        handle.write(files[key])
        handle.close()
        info = W3iFile()
        info.read(handle.name)
        data = info.get_data()
        wts_key = find(files, 'war3map.wts') or 'war3map.wts'
        strings = WtsFile()
        if wts_key in files:
            strings.read(files[wts_key])
        table = dict(strings.export_data())
        number = max(list(table) + [-1]) + 1
        table[number] = name
        data['map_name'] = f'TRIGSTR_{number:03d}'
        info.import_data(data)
        files[key] = info.to_bytes()
        files[wts_key] = strings.import_data(table).to_bytes()
        return True
    except Exception:
        return False
    finally:
        os.remove(handle.name)


def split_campaign(members, parts=PARTS, rename=True):
    """[(file name, members, report)], one per map of the campaign, in the
    order of war3campaign.w3f's map list.

    `members` is the {name: bytes} table of a .w3n (wc3files.w3x.modes
    archive_files). The file name is the mission index, then the chapter and
    title of the map's campaign button (`03 Chapter Two - The Siege.w3x`),
    or the map's own name when no button names it. With `rename`, that title
    also becomes the map's name in its war3map.w3i.

    Each report is transfer's, with `title`, `member` (the map's name in the
    campaign) and `renamed` (whether war3map.w3i took the title) added."""
    from wc3files.table.wts_file import WtsFile
    info = _read_info(members)
    campaign = campaign_as_map(members)
    strings = WtsFile()
    if find(members, 'war3campaign.wts'):
        strings.read(members[find(members, 'war3campaign.wts')])
    strings = strings.export_data()
    buttons = {member_name(b.get('map_file_path', '')).lower(): b
               for b in info.get('map_buttons', [])}
    listed = [member_name(m.get('map_file_path', '')) for m in info.get('maps', [])]
    listed += [member_name(b.get('map_file_path', '')) for b in info.get('map_buttons', [])
               if member_name(b.get('map_file_path', '')).lower()
               not in {m.lower() for m in listed}]
    results = []
    for index, path in enumerate(listed, start=1):
        key = find(members, path)
        if key is None:
            raise ValueError(f'war3campaign.w3f lists {path}, which the campaign does not hold')
        extension = os.path.splitext(key)[1].lower()
        if extension not in MAP_EXTENSIONS:
            extension = '.w3x'
        files = _read_map(members[key], extension)
        files, report = transfer(campaign, files, parts=parts, prefer='target')
        button = buttons.get(path.lower(), {})
        chapter = _resolve(button.get('chapter', ''), strings)
        title = _resolve(button.get('title', ''), strings)
        stem = os.path.splitext(key.replace('\\', '/').rsplit('/', 1)[-1])[0]
        name = _file_name(index, chapter, title, stem, extension)
        report['title'] = ' - '.join(p for p in (chapter, title) if p) or stem
        report['member'] = key
        report['renamed'] = _set_map_name(files, report['title']) if rename else False
        results.append((name, files, report))
    return results
