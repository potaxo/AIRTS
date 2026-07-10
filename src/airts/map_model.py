"""Map data model, loading, and authoritative validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import IO

from airts.geometry import Point


class MapValidationError(ValueError):
    """Raised when map data cannot produce a valid simulation map."""


class Terrain(StrEnum):
    GRASS = "grass"
    ROAD = "road"
    WATER = "water"
    ROCK = "rock"
    BRIDGE = "bridge"

    @property
    def passable(self) -> bool:
        return self not in {Terrain.WATER, Terrain.ROCK}


class EntityKind(StrEnum):
    SCOUT = "scout"
    LIGHT_TANK = "light_tank"
    HEAVY_TANK = "heavy_tank"

    @property
    def movement_speed(self) -> float:
        return {
            EntityKind.SCOUT: 5.0,
            EntityKind.LIGHT_TANK: 3.5,
            EntityKind.HEAVY_TANK: 2.5,
        }[self]


@dataclass(frozen=True, slots=True)
class EntitySpec:
    entity_id: str
    kind: EntityKind
    position: Point


@dataclass(frozen=True, slots=True)
class GameMap:
    map_id: str
    display_name: str
    width: int
    height: int
    terrain: tuple[tuple[Terrain, ...], ...]
    entities: tuple[EntitySpec, ...]

    def contains(self, point: Point) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def terrain_at(self, point: Point) -> Terrain:
        if not self.contains(point):
            raise ValueError(f"point outside map: ({point.x}, {point.y})")
        return self.terrain[int(point.y)][int(point.x)]

    def is_passable(self, point: Point) -> bool:
        return self.contains(point) and self.terrain_at(point).passable


def load_map(path: str | Path) -> GameMap:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return _load_map_stream(stream)
    except json.JSONDecodeError as error:
        raise MapValidationError(f"invalid JSON: {error.msg}") from error


def load_example_map() -> GameMap:
    resource = resources.files("airts").joinpath("data/phase1_map.json")
    try:
        with resource.open(encoding="utf-8") as stream:
            return _load_map_stream(stream)
    except json.JSONDecodeError as error:
        raise MapValidationError(f"invalid bundled map JSON: {error.msg}") from error


def load_map_data(raw_data: object) -> GameMap:
    data = _mapping(raw_data, "map")
    map_id = _string(data.get("id"), "id")
    display_name = _string(data.get("name"), "name")
    width = _integer(data.get("width"), "width")
    height = _integer(data.get("height"), "height")
    if width <= 0 or height <= 0:
        raise MapValidationError("map dimensions must be positive")

    terrain_data = _mapping(data.get("terrain"), "terrain")
    try:
        default_terrain = Terrain(_string(terrain_data.get("default"), "terrain.default"))
    except ValueError as error:
        raise MapValidationError(f"unsupported default terrain: {error}") from error
    rows = [[default_terrain for _ in range(width)] for _ in range(height)]
    for patch_index, raw_patch in enumerate(
        _list(terrain_data.get("rectangles", []), "rectangles")
    ):
        patch = _list(raw_patch, f"terrain.rectangles[{patch_index}]")
        if len(patch) != 5:
            raise MapValidationError("terrain rectangles must be [x, y, width, height, type]")
        x = _integer(patch[0], "terrain rectangle x")
        y = _integer(patch[1], "terrain rectangle y")
        patch_width = _integer(patch[2], "terrain rectangle width")
        patch_height = _integer(patch[3], "terrain rectangle height")
        try:
            terrain = Terrain(_string(patch[4], "terrain rectangle type"))
        except ValueError as error:
            raise MapValidationError(f"unsupported terrain patch: {error}") from error
        if patch_width <= 0 or patch_height <= 0:
            raise MapValidationError("terrain rectangle dimensions must be positive")
        if x < 0 or y < 0 or x + patch_width > width or y + patch_height > height:
            raise MapValidationError("terrain rectangle lies outside the map")
        for row in range(y, y + patch_height):
            for column in range(x, x + patch_width):
                rows[row][column] = terrain

    specs: list[EntitySpec] = []
    known_ids: set[str] = set()
    for entity_index, raw_entity in enumerate(_list(data.get("entities"), "entities")):
        entity = _mapping(raw_entity, f"entities[{entity_index}]")
        entity_id = _string(entity.get("id"), "entity.id")
        if entity_id in known_ids:
            raise MapValidationError(f"duplicate entity ID: {entity_id}")
        known_ids.add(entity_id)
        try:
            kind = EntityKind(_string(entity.get("kind"), "entity.kind"))
        except ValueError as error:
            raise MapValidationError(f"unsupported entity kind: {error}") from error
        raw_position = _list(entity.get("position"), "entity.position")
        if len(raw_position) != 2:
            raise MapValidationError("entity.position must contain exactly two numbers")
        position = Point(
            _number(raw_position[0], "entity.position.x"),
            _number(raw_position[1], "entity.position.y"),
        )
        if not (0 <= position.x < width and 0 <= position.y < height):
            raise MapValidationError(f"entity {entity_id} lies outside the map")
        if not rows[int(position.y)][int(position.x)].passable:
            raise MapValidationError(f"entity {entity_id} starts on impassable terrain")
        specs.append(EntitySpec(entity_id, kind, position))

    if not specs:
        raise MapValidationError("a playable map requires at least one entity")
    return GameMap(
        map_id=map_id,
        display_name=display_name,
        width=width,
        height=height,
        terrain=tuple(tuple(row) for row in rows),
        entities=tuple(specs),
    )


def _load_map_stream(stream: IO[str]) -> GameMap:
    return load_map_data(json.load(stream))


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MapValidationError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise MapValidationError(f"{field} must be a list")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MapValidationError(f"{field} must be a non-empty string")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise MapValidationError(f"{field} must be an integer")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MapValidationError(f"{field} must be a number")
    return float(value)
