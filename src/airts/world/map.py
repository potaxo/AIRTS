"""Map cells, terrain and entity profiles, loading, and dynamic occupancy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import IO

from airts.contracts.spatial import Point

Cell = tuple[int, int]


class MapValidationError(ValueError):
    """Raised when map data cannot produce a valid simulation map."""


class Terrain(StrEnum):
    GRASS = "grass"
    ROAD = "road"
    FOREST = "forest"
    WATER = "water"
    ROCK = "rock"
    BRIDGE = "bridge"

    @property
    def passable(self) -> bool:
        return self is not Terrain.WATER and self is not Terrain.ROCK

    @property
    def movement_cost(self) -> float:
        return _MOVEMENT_COSTS[self]


_MOVEMENT_COSTS = {
    Terrain.ROAD: 0.75,
    Terrain.BRIDGE: 0.8,
    Terrain.GRASS: 1.0,
    Terrain.FOREST: 1.5,
    Terrain.WATER: float("inf"),
    Terrain.ROCK: float("inf"),
}


class EntityCategory(StrEnum):
    UNIT = "unit"
    BUILDING = "building"


@dataclass(frozen=True, slots=True)
class EntityProfile:
    category: EntityCategory
    max_health: int
    vision_range: float
    footprint: tuple[int, int]
    movement_speed: float | None = None
    attack_damage: int = 0
    attack_range: float = 0.0
    production_cost: int = 0
    construction_cost: int = 0
    build_speed: float = 0.0
    build_range: float = 0.0

    @property
    def movable(self) -> bool:
        return self.movement_speed is not None


class EntityKind(StrEnum):
    SCOUT = "scout"
    LIGHT_TANK = "light_tank"
    HEAVY_TANK = "heavy_tank"
    BUILDER = "builder"
    FACTORY = "factory"
    REPAIR_HUB = "repair_hub"
    COMMAND_CENTER = "command_center"
    RESOURCE_GENERATOR = "resource_generator"
    RESOURCE_EXTRACTOR = "resource_generator"  # Preserve existing map/save/command tags.

    @property
    def profile(self) -> EntityProfile:
        return _ENTITY_PROFILES[self]

    @property
    def production_kinds(self) -> tuple[EntityKind, ...]:
        if self is EntityKind.FACTORY:
            return (
                EntityKind.SCOUT,
                EntityKind.LIGHT_TANK,
                EntityKind.HEAVY_TANK,
                EntityKind.BUILDER,
            )
        if self is EntityKind.COMMAND_CENTER:
            return (EntityKind.BUILDER,)
        return ()

    @property
    def movement_speed(self) -> float:
        speed = self.profile.movement_speed
        if speed is None:
            raise ValueError(f"{self.value} is not movable")
        return speed


_ENTITY_PROFILES = {
    EntityKind.SCOUT: EntityProfile(EntityCategory.UNIT, 60, 7.0, (1, 1), 5.0, 5, 5.0, 50),
    EntityKind.LIGHT_TANK: EntityProfile(EntityCategory.UNIT, 100, 5.0, (1, 1), 3.5, 12, 6.0, 100),
    EntityKind.HEAVY_TANK: EntityProfile(EntityCategory.UNIT, 160, 4.0, (1, 1), 2.5, 20, 7.0, 175),
    EntityKind.BUILDER: EntityProfile(
        EntityCategory.UNIT,
        80,
        5.0,
        (1, 1),
        3.0,
        0,
        0.0,
        75,
        build_speed=5.0,
        build_range=2.5,
    ),
    EntityKind.FACTORY: EntityProfile(
        EntityCategory.BUILDING, 500, 5.0, (4, 4), construction_cost=400
    ),
    EntityKind.REPAIR_HUB: EntityProfile(
        EntityCategory.BUILDING, 350, 4.0, (3, 3), construction_cost=250
    ),
    EntityKind.COMMAND_CENTER: EntityProfile(EntityCategory.BUILDING, 700, 7.0, (5, 5)),
    EntityKind.RESOURCE_GENERATOR: EntityProfile(
        EntityCategory.BUILDING, 250, 3.0, (2, 2), construction_cost=200
    ),
}


@dataclass(frozen=True, slots=True)
class EntitySpec:
    entity_id: str
    kind: EntityKind
    owner_id: str
    position: Point

    @property
    def occupied_cells(self) -> frozenset[Cell]:
        return footprint_cells(self.kind, self.position)


@dataclass(frozen=True, slots=True)
class MatchSlot:
    """An immutable starting owner, controller and alliance."""

    owner_id: str
    controller: str
    team: int

    def to_dict(self) -> dict[str, object]:
        return {"owner": self.owner_id, "controller": self.controller, "team": self.team}


def footprint_cells(kind: EntityKind, position: Point) -> frozenset[Cell]:
    """Return the profile's occupied grid cells at a continuous position."""

    width, height = kind.profile.footprint
    origin_x, origin_y = int(position.x), int(position.y)
    if width == height == 1:
        return frozenset(((origin_x, origin_y),))
    return frozenset(
        (x, y)
        for y in range(origin_y, origin_y + height)
        for x in range(origin_x, origin_x + width)
    )


@dataclass(frozen=True, slots=True)
class GameMap:
    map_id: str
    map_version: int
    display_name: str
    width: int
    height: int
    terrain: tuple[tuple[Terrain, ...], ...]
    entities: tuple[EntitySpec, ...]
    resource_sites: tuple[Point, ...] | None = None
    match_slots: tuple[MatchSlot, ...] = ()

    def contains(self, point: Point) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def contains_cell(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def cell_for(self, point: Point) -> Cell:
        if not self.contains(point):
            raise ValueError(f"point outside map: ({point.x}, {point.y})")
        return int(point.x), int(point.y)

    def terrain_at(self, point: Point) -> Terrain:
        return self.terrain_at_cell(self.cell_for(point))

    def terrain_at_cell(self, cell: Cell) -> Terrain:
        if not self.contains_cell(cell):
            raise ValueError(f"cell outside map: {cell}")
        return self.terrain[cell[1]][cell[0]]

    def is_passable(self, point: Point) -> bool:
        return self.contains(point) and self.terrain_at(point).passable

    def is_cell_passable(self, cell: Cell) -> bool:
        return self.contains_cell(cell) and self.terrain_at_cell(cell).passable

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "id": self.map_id,
            "version": self.map_version,
            "name": self.display_name,
            "width": self.width,
            "height": self.height,
            "terrain": {"rows": [[terrain.value for terrain in row] for row in self.terrain]},
            "entities": [
                {
                    "id": spec.entity_id,
                    "kind": spec.kind.value,
                    "owner": spec.owner_id,
                    "position": [spec.position.x, spec.position.y],
                }
                for spec in self.entities
            ],
        }
        if self.resource_sites is not None:
            result["resource_sites"] = [[point.x, point.y] for point in self.resource_sites]
        if self.match_slots:
            result["match_slots"] = [slot.to_dict() for slot in self.match_slots]
        return result


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
    map_version = _integer(data.get("version", 1), "version")
    display_name = _string(data.get("name"), "name")
    width = _integer(data.get("width"), "width")
    height = _integer(data.get("height"), "height")
    if map_version <= 0:
        raise MapValidationError("map version must be positive")
    if width <= 0 or height <= 0:
        raise MapValidationError("map dimensions must be positive")

    rows = _load_terrain(_mapping(data.get("terrain"), "terrain"), width, height)
    specs: list[EntitySpec] = []
    known_ids: set[str] = set()
    occupied: dict[Cell, str] = {}
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
        owner_id = _string(entity.get("owner", "player"), "entity.owner")
        raw_position = _list(entity.get("position"), "entity.position")
        if len(raw_position) != 2:
            raise MapValidationError("entity.position must contain exactly two numbers")
        position = Point(
            _number(raw_position[0], "entity.position.x"),
            _number(raw_position[1], "entity.position.y"),
        )
        if not (0 <= position.x < width and 0 <= position.y < height):
            raise MapValidationError(f"entity {entity_id} lies outside the map")
        if kind.profile.category is EntityCategory.BUILDING and (
            not position.x.is_integer() or not position.y.is_integer()
        ):
            raise MapValidationError(f"building {entity_id} position must be grid-aligned")
        spec = EntitySpec(entity_id, kind, owner_id, position)
        for cell in spec.occupied_cells:
            if not (0 <= cell[0] < width and 0 <= cell[1] < height):
                raise MapValidationError(f"entity {entity_id} footprint lies outside the map")
            if not rows[cell[1]][cell[0]].passable:
                raise MapValidationError(f"entity {entity_id} starts on impassable terrain")
            if previous := occupied.get(cell):
                raise MapValidationError(f"entity {entity_id} overlaps {previous} at cell {cell}")
            occupied[cell] = entity_id
        specs.append(spec)

    if not specs:
        raise MapValidationError("a playable map requires at least one entity")
    sites: list[Point] | None = None
    if "resource_sites" in data:
        sites = []
        site_cells: set[Cell] = set()
        for raw_site in _list(data["resource_sites"], "resource_sites"):
            coordinates = _list(raw_site, "resource site")
            if len(coordinates) != 2:
                raise MapValidationError("resource site requires two coordinates")
            point = Point(*(_number(value, "resource coordinate") for value in coordinates))
            if not point.x.is_integer() or not point.y.is_integer():
                raise MapValidationError("resource sites must be grid-aligned")
            cells = footprint_cells(EntityKind.RESOURCE_GENERATOR, point)
            if (
                any(
                    not (0 <= x < width and 0 <= y < height) or not rows[y][x].passable
                    for x, y in cells
                )
                or cells & site_cells
            ):
                raise MapValidationError("resource sites must be passable, disjoint and in bounds")
            site_cells.update(cells)
            sites.append(point)
        if any(
            spec.kind is EntityKind.RESOURCE_GENERATOR and spec.position not in sites
            for spec in specs
        ):
            raise MapValidationError("extractor must occupy a resource site")
    slots: list[MatchSlot] = []
    for raw_slot in _list(data.get("match_slots", []), "match_slots"):
        slot = _mapping(raw_slot, "match slot")
        owner = _string(slot.get("owner"), "slot.owner")
        controller = _string(slot.get("controller"), "slot.controller")
        team = _integer(slot.get("team"), "slot.team")
        if controller not in {"player", "ai"} or team not in range(1, 5):
            raise MapValidationError("invalid match controller or team")
        if owner in {item.owner_id for item in slots}:
            raise MapValidationError("duplicate match owner")
        if (controller == "player") != (owner == "player"):
            raise MapValidationError("the human controller must own player")
        slots.append(MatchSlot(owner, controller, team))
    if slots and (
        sum(slot.controller == "player" for slot in slots) != 1
        or len({slot.team for slot in slots}) < 2
        or {spec.owner_id for spec in specs} != {slot.owner_id for slot in slots}
    ):
        raise MapValidationError("match requires one human, opposing teams and matching owners")
    return GameMap(
        map_id=map_id,
        map_version=map_version,
        display_name=display_name,
        width=width,
        height=height,
        terrain=tuple(tuple(row) for row in rows),
        entities=tuple(specs),
        resource_sites=None if sites is None else tuple(sites),
        match_slots=tuple(slots),
    )


def _load_terrain(data: dict[str, object], width: int, height: int) -> list[list[Terrain]]:
    if "rows" in data:
        raw_rows = _list(data["rows"], "terrain.rows")
        if len(raw_rows) != height:
            raise MapValidationError("terrain row count must equal map height")
        result: list[list[Terrain]] = []
        for row_index, raw_row in enumerate(raw_rows):
            row = _list(raw_row, f"terrain.rows[{row_index}]")
            if len(row) != width:
                raise MapValidationError("terrain row width must equal map width")
            try:
                result.append([Terrain(_string(value, "terrain cell")) for value in row])
            except ValueError as error:
                raise MapValidationError(f"unsupported terrain cell: {error}") from error
        return result

    try:
        default_terrain = Terrain(_string(data.get("default"), "terrain.default"))
    except ValueError as error:
        raise MapValidationError(f"unsupported default terrain: {error}") from error
    rows = [[default_terrain for _ in range(width)] for _ in range(height)]
    for patch_index, raw_patch in enumerate(_list(data.get("rectangles", []), "rectangles")):
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
        for row_number in range(y, y + patch_height):
            for column in range(x, x + patch_width):
                rows[row_number][column] = terrain
    return rows


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


class OccupancyError(ValueError):
    """Raised when an entity cannot atomically occupy requested cells."""


class OccupancyGrid:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._cells: dict[Cell, set[str]] = {}
        self._entity_cells: dict[str, frozenset[Cell]] = {}

    def place(
        self,
        entity_id: str,
        cells: frozenset[Cell],
        allowed_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        if entity_id in self._entity_cells:
            raise OccupancyError(f"entity already placed: {entity_id}")
        self._validate_available(entity_id, cells, allowed_conflicts)
        self._set_cells(entity_id, cells)

    def move(
        self,
        entity_id: str,
        cells: frozenset[Cell],
        allowed_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        if entity_id not in self._entity_cells:
            raise OccupancyError(f"entity is not placed: {entity_id}")
        if self._entity_cells[entity_id] == cells:
            return
        self._validate_available(entity_id, cells, allowed_conflicts)
        self.remove(entity_id)
        self._set_cells(entity_id, cells)

    def remove(self, entity_id: str) -> None:
        cells = self._entity_cells.pop(entity_id, frozenset())
        for cell in cells:
            occupants = self._cells[cell]
            occupants.remove(entity_id)
            if not occupants:
                del self._cells[cell]

    def occupants(self, cell: Cell) -> frozenset[str]:
        return frozenset(self._cells.get(cell, set()))

    def cells_for(self, entity_id: str) -> frozenset[Cell]:
        return self._entity_cells.get(entity_id, frozenset())

    def blocked_cells(self, excluding: frozenset[str] = frozenset()) -> frozenset[Cell]:
        return frozenset(
            cell for cell, occupants in self._cells.items() if occupants.difference(excluding)
        )

    def snapshot(self) -> dict[str, list[list[int]]]:
        return {
            entity_id: [[x, y] for x, y in sorted(cells, key=lambda cell: (cell[1], cell[0]))]
            for entity_id, cells in sorted(self._entity_cells.items())
        }

    def _validate_available(
        self,
        entity_id: str,
        cells: frozenset[Cell],
        allowed_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        if not cells:
            raise OccupancyError("an entity must occupy at least one cell")
        for cell in cells:
            if not (0 <= cell[0] < self.width and 0 <= cell[1] < self.height):
                raise OccupancyError(f"cell outside occupancy grid: {cell}")
            conflicts = tuple(
                occupant_id
                for occupant_id in self._cells.get(cell, ())
                if occupant_id != entity_id and occupant_id not in allowed_conflicts
            )
            if conflicts:
                conflict = min(conflicts)
                raise OccupancyError(f"cell {cell} is occupied by {conflict}")

    def _set_cells(self, entity_id: str, cells: frozenset[Cell]) -> None:
        self._entity_cells[entity_id] = cells
        for cell in cells:
            self._cells.setdefault(cell, set()).add(entity_id)
