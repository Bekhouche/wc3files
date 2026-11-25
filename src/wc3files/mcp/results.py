"""The shape of each MCP tool's result, published as the tool's output schema
(#268), so a client knows what comes back without the description listing
it. The tool functions in tools.py return plain dicts of exactly this shape;
tests/test_mcp_server.py checks that the server's structured result equals
what the function returns, so no field is dropped or changed on the way.

Imported by server.py only, which needs the mcp extra; typing_extensions
comes with it (pydantic needs typing_extensions.TypedDict before 3.12).
"""
from typing import Annotated, Any, Literal

from pydantic import Field
from typing_extensions import NotRequired, TypedDict

ObjectType = Literal['', 'w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q']


class FileEntry(TypedDict):
    path: Annotated[str, Field(description='Full path, accepted as it is by the other tools.')]
    size: Annotated[int, Field(description='Size in bytes.')]


class ListFilesResult(TypedDict):
    files: list[FileEntry]
    truncated: Annotated[bool, Field(description='True when more than 1000 files matched.')]


class MapPlayer(TypedDict):
    id: int | None
    name: str | None
    type: Annotated[str | int | None, Field(description='user, computer, neutral or rescuable.')]
    race: Annotated[str | int | None, Field(description='human, orc, undead, night elf or selectable.')]


class MapForce(TypedDict):
    name: str | None
    players: Annotated[list[int], Field(description='Ids of the players in the force.')]


class MapInfoResult(TypedDict):
    path: str
    name: str | None
    author: str | None
    description: str | None
    suggested_players: str | None
    width: Annotated[int | None, Field(description='Playable width, in terrain tiles.')]
    height: Annotated[int | None, Field(description='Playable height, in terrain tiles.')]
    script_language: Annotated[str | None, Field(description='jass or lua; null in old maps.')]
    game_version: Annotated[list[int] | None, Field(
        description='Game version the map was saved with, as [major, minor, patch, build]; '
                    'null in old maps.')]
    editor_version: int | None
    w3i_version: Annotated[int | None, Field(description='Format version of war3map.w3i.')]
    players: list[MapPlayer]
    forces: list[MapForce]
    members: Annotated[int, Field(description='Number of files the map holds.')]


class Member(TypedDict):
    name: str
    size: Annotated[int | None, Field(description='Unpacked size in bytes.')]
    stored: Annotated[int | None, Field(description='Size in the archive, in bytes.')]


class ListMembersResult(TypedDict):
    path: str
    members: list[Member]


class ReadMemberResult(TypedDict):
    path: str
    member: str
    size: Annotated[int, Field(description='Size of the member in bytes.')]
    format: NotRequired[Annotated[str, Field(
        description='The wc3files reader used, such as w3i, w3e or wtg, when data is given.')]]
    data: NotRequired[Annotated[Any, Field(
        description='The parsed member, as `wc3files dump` gives it for that format.')]]
    text: NotRequired[Annotated[str, Field(description='The text of a script or text member.')]]
    note: NotRequired[Annotated[str, Field(description='Why neither data nor text is given.')]]


class ObjectDataResult(TypedDict):
    path: str
    objects: Annotated[dict[str, dict[str, dict[str, dict[str, Any]]]], Field(
        description='Object type (w3u, w3a, ...) to table to object id to fields. The original '
                    'table holds standard objects the map changes, the custom table new objects '
                    'with their template_id. Fields use the game\'s names, such as goldcost.')]


class TriggerLines(TypedDict):
    name: str | None
    lines: Annotated[list[str], Field(
        description='Events, conditions and actions, one per line, as the World Editor shows them.')]


class TriggerTextResult(TypedDict):
    path: str
    names: NotRequired[Annotated[list[str | None], Field(description='Given with names_only.')]]
    triggers: NotRequired[Annotated[list[TriggerLines], Field(
        description='Given without names_only.')]]


class Finding(TypedDict):
    severity: Annotated[str, Field(description='issue (likely an error in game) or note.')]
    message: str
    line: Annotated[int | None, Field(description='Line in the script, when known.')]


class CheckScriptResult(TypedDict):
    path: str
    script: Annotated[str, Field(description='The script checked, such as war3map.j.')]
    language: Annotated[str, Field(description='jass or lua.')]
    counts: Annotated[dict[str, int], Field(description='Number of findings by severity.')]
    findings: list[Finding]


class ModelSequence(TypedDict):
    name: str | None
    start: Annotated[int | None, Field(description='First frame, in milliseconds.')]
    end: Annotated[int | None, Field(description='Last frame, in milliseconds.')]


class ModelTexture(TypedDict):
    path: Annotated[str, Field(description='Texture file; empty for a replaceable texture.')]
    replaceable_id: Annotated[int, Field(
        description='0 for a texture file; otherwise the replaceable texture, such as 1 for '
                    'team colour or 2 for team glow.')]


class ModelInfoResult(TypedDict):
    path: str
    member: Annotated[str | None, Field(description='The model inside the map, or null.')]
    name: str | None
    format: Annotated[str, Field(description='mdx or mdl.')]
    version: int | None
    geosets: Annotated[int, Field(description='Number of geosets (meshes).')]
    sequences: list[ModelSequence]
    textures: list[ModelTexture]


class ReplayPlayer(TypedDict):
    id: int | None
    name: str | None
    race: str | None
    host: bool | None


class W3mmdResult(TypedDict):
    players: Annotated[dict[str, str], Field(description='Player id to name, as W3MMD records it.')]
    winners: list[int]
    losers: list[int]


class ReplayInfoResult(TypedDict):
    path: str
    game_name: str | None
    map: Annotated[str | None, Field(description='Path of the map the game was played on.')]
    host: str | None
    length_ms: Annotated[int | None, Field(description='Length of the game in milliseconds.')]
    version: Annotated[int | None, Field(description='Game version number, such as 10100 or 10200 for Reforged replays.')]
    build: int | None
    reforged: bool | None
    players: list[ReplayPlayer]
    w3mmd: NotRequired[Annotated[W3mmdResult, Field(
        description='Given only when the map records W3MMD results.')]]


PlacedKind = Literal['units', 'doodads']
RecordKind = Literal['regions', 'cameras', 'sounds', 'imports', 'strings', 'terrain']


class OverviewCounts(TypedDict):
    units: Annotated[int | None, Field(description='Units and items placed on the map.')]
    doodads: Annotated[int | None, Field(description='Doodads and destructables placed on the map.')]
    regions: int | None
    cameras: int | None
    sounds: int | None
    imports: Annotated[int | None, Field(description='Files listed in war3map.imp.')]
    strings: Annotated[int | None, Field(description='Entries of war3map.wts.')]
    triggers: Annotated[int | None, Field(description='GUI triggers in war3map.wtg.')]


class OverviewTerrain(TypedDict):
    main_tileset: str | None
    width: int | None
    height: int | None


class MapOverviewResult(TypedDict):
    path: str
    name: str | None
    author: str | None
    suggested_players: str | None
    players: int
    forces: int
    script: Annotated[str | None, Field(description='The script member, such as war3map.j.')]
    script_language: str | None
    game_version: list[int] | None
    members: Annotated[int, Field(description='Number of files the map holds.')]
    terrain: Annotated[OverviewTerrain | None, Field(description='Null without war3map.w3e.')]
    counts: Annotated[OverviewCounts, Field(
        description='How many of each the map holds; null when that file cannot be read.')]
    objects: Annotated[dict[str, dict[str, int] | None], Field(
        description='Object type (w3u, w3a, ...) to the number of original (changed standard) '
                    'and custom objects; only the types the map has.')]


class PlacedObject(TypedDict):
    index: Annotated[int, Field(description='Position in the file.')]
    id: Annotated[str, Field(description='Four-character type id, such as hfoo or LTlt.')]
    x: float
    y: float
    z: float
    facing: Annotated[float | None, Field(description='Facing in degrees, as the World Editor shows it.')]
    scale: Annotated[list[float], Field(description='Scale on x, y and z.')]
    variation: int | None
    owner: NotRequired[Annotated[int | None, Field(description='Units: player number, from 0.')]]
    hp: NotRequired[Annotated[int | None, Field(description='Units: hit points; -1 is the default.')]]
    mana: NotRequired[Annotated[int | None, Field(description='Units: mana; -1 is the default.')]]
    creation_number: NotRequired[Annotated[int | None, Field(
        description='Units: the number the World Editor names the unit by in triggers.')]]
    life: NotRequired[Annotated[int | None, Field(description='Doodads: life in percent.')]]
    editor_id: NotRequired[Annotated[int | None, Field(
        description='Doodads: the number the World Editor names the doodad by in triggers.')]]


class PlacedObjectsResult(TypedDict):
    path: str
    kind: PlacedKind
    total: Annotated[int, Field(description='How many match, before limit.')]
    objects: list[PlacedObject]
    truncated: Annotated[bool, Field(description='True when limit left some out.')]


class TerrainSummary(TypedDict):
    version: int | None
    main_tileset: str | None
    custom_tileset: bool | None
    ground_tilesets: Annotated[list[str], Field(description='Ground texture ids, such as Ldrt.')]
    cliff_tilesets: list[str]
    width: Annotated[int | None, Field(description='Width in tile corners.')]
    height: Annotated[int | None, Field(description='Height in tile corners.')]
    offset_x: float | None
    offset_y: float | None
    corners: int
    water_corners: int
    ramp_corners: int
    blight_corners: int
    cliff_levels: Annotated[list[int] | None, Field(description='Lowest and highest cliff level.')]
    ground_texture_use: Annotated[dict[str, int], Field(
        description='Ground texture id to the number of corners that use it, most used first.')]


class MapRecordsResult(TypedDict):
    path: str
    kind: RecordKind
    member: Annotated[str, Field(description='The map file read, such as war3map.w3r.')]
    records: NotRequired[Annotated[list[dict[str, Any]], Field(
        description='One record per region, camera, sound, import or string, with the fields '
                    '`wc3files dump` gives; strings are {id, text}. Not given for terrain.')]]
    terrain: NotRequired[Annotated[TerrainSummary, Field(
        description='Given for kind terrain when the map has war3map.w3e.')]]


EditObjectType = Literal['w3u', 'w3t', 'w3b', 'w3d', 'w3a', 'w3h', 'w3q']


class ObjectChange(TypedDict):
    id: Annotated[str, Field(description=(
        'The four-character object id: a standard id such as hfoo, a custom id of the map '
        'such as h000, or a new unused id when base is given.'), min_length=4, max_length=4)]
    base: NotRequired[Annotated[str, Field(description=(
        'Create id as a new custom object based on this standard id, such as hfoo.'),
        min_length=4, max_length=4)]]
    set: NotRequired[Annotated[dict[str, Any], Field(description=(
        'Field to value. A field is named as object_data shows it (goldcost, Name) or by its '
        'four-character id (ugol). A field with levels takes {"1": value, "2": value}.'))]]
    reset: NotRequired[Annotated[list[str], Field(description=(
        'Fields to take back to the default of the base object.'))]]
    delete: NotRequired[Annotated[bool, Field(description=(
        'Remove the object from the map: a custom object, or the changes to a standard one.'))]]


class EditResult(TypedDict):
    path: Annotated[str, Field(description='The map written.')]
    source: Annotated[str, Field(description='The map read.')]
    overwrote_source: bool
    changed_members: Annotated[list[str], Field(description='The map files that were rewritten.')]


class EditObjectsResult(EditResult):
    object_type: str
    created: list[str]
    changed: list[str]
    deleted: list[str]
    warnings: Annotated[list[str], Field(description=(
        'Set values past the limits of the World Editor (minimum, maximum, allowed values). '
        'The edit is written anyway, as the game takes such values.'))]


class PlacedValues(TypedDict, total=False):
    id: Annotated[str, Field(description='Type id, such as hfoo for a unit or LTlt for a tree.',
                             min_length=4, max_length=4)]
    x: float
    y: float
    z: Annotated[float, Field(description='Height; the World Editor sets it from the terrain.')]
    facing: Annotated[float, Field(description='Facing in degrees, 0 east, 90 north. New: 270.')]
    scale: Annotated[float | list[float], Field(description='One number, or [x, y, z]. New: 1.')]
    variation: int
    owner: Annotated[int, Field(description='Units: player number from 0. New: 0.')]
    hp: Annotated[int, Field(description='Units: hit points; -1 is the type default.')]
    mana: Annotated[int, Field(description='Units: mana; -1 is the type default.')]
    life: Annotated[int, Field(description='Doodads: life in percent, 0 to 255. New: 100.')]


class PlacedSelector(TypedDict, total=False):
    index: Annotated[int, Field(description='Position in the file, as placed_objects gives it.')]
    id: Annotated[str, Field(description='Every record of this type id.')]
    creation_number: Annotated[int, Field(description='Units: the editor number of one unit.')]
    editor_id: Annotated[int, Field(description='Doodads: the editor number of one doodad.')]


class PlacedChange(TypedDict):
    select: Annotated[PlacedSelector, Field(description='Exactly one of index, id, creation_number '
                                                        'or editor_id.')]
    set: PlacedValues


class PlacedAdded(TypedDict, total=False):
    index: int
    id: str
    creation_number: int
    editor_id: int


class EditPlacedResult(EditResult):
    kind: PlacedKind
    added: Annotated[list[PlacedAdded], Field(description='New records with their editor numbers.')]
    changed: Annotated[list[int], Field(description='Indices changed, in the file before the edit.')]
    deleted: Annotated[list[int], Field(description='Indices deleted, in the file before the edit.')]
    total: Annotated[int, Field(description='Records in the file after the edit.')]


EditRecordKind = Literal['regions', 'cameras', 'strings']


class RecordValues(TypedDict, total=False):
    name: Annotated[str, Field(description='Regions and cameras: the name; the script global is '
                                           'gg_rct_ or gg_cam_ and the name with _ for spaces.')]
    left: float
    right: float
    bottom: float
    top: float
    weather: Annotated[str, Field(description='Regions: a weather id such as RAhr, or empty.')]
    ambient_sound: Annotated[str, Field(description='Regions: a sound name, or empty.')]
    color: Annotated[list[int], Field(description='Regions: [red, green, blue], 0 to 255.')]
    target_x: float
    target_y: float
    z_offset: float
    rotation: Annotated[float, Field(description='Cameras: degrees. New: 90.')]
    angle_of_attack: Annotated[float, Field(description='Cameras: degrees. New: 304.')]
    distance: Annotated[float, Field(description='Cameras: new: 1650.')]
    roll: float
    field_of_view: Annotated[float, Field(description='Cameras: degrees. New: 70.')]
    far_clipping_plane: Annotated[float, Field(description='Cameras: new: 5000.')]
    near_clipping_plane: Annotated[float, Field(description='Cameras: new: 16.')]
    id: Annotated[int, Field(description='Strings: the string id (TRIGSTR_ number) to change.')]
    text: Annotated[str, Field(description='Strings: the text.')]


class RecordSelector(TypedDict, total=False):
    index: Annotated[int, Field(description='Position, as map_records gives it.')]
    name: Annotated[str, Field(description='Every record of this name.')]


class RecordChange(TypedDict, total=False):
    select: Annotated[RecordSelector, Field(description='Regions and cameras: index or name.')]
    set: RecordValues
    id: Annotated[int, Field(description='Strings: the id of the string to change.')]
    text: Annotated[str, Field(description='Strings: its new text.')]


class EditRecordsResult(EditResult):
    kind: EditRecordKind
    added: Annotated[list[dict[str, Any]], Field(description=(
        'New records: {index, name} for regions and cameras, {id, reference} for strings, '
        'where reference is the TRIGSTR_ text other files use.'))]
    changed: Annotated[list[int], Field(description='Indices (or string ids) changed.')]
    deleted: Annotated[list[int], Field(description='Indices deleted.')]


class EditScriptResult(EditResult):
    script: Annotated[str, Field(description='The script member, such as war3map.j.')]
    language: Annotated[str, Field(description='jass or lua.')]
    replaced: bool
    init_function: str | None
    issues_before: Annotated[int, Field(description='Issue findings of the script checker before.')]
    issues_after: Annotated[int, Field(description='Issue findings after; never a new one.')]


class ImportFileResult(EditResult):
    member: Annotated[str, Field(description='The file inside the map.')]
    size: Annotated[int, Field(description='Its size in bytes.')]
    replaced: Annotated[bool, Field(description='True when the map had a file of that name.')]
    listed: Annotated[bool, Field(description='True when it was added to war3map.imp.')]
