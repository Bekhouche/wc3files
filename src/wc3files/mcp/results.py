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
