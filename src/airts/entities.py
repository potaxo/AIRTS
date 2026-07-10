"""Richer Phase 2 entity state shared by units and inert buildings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from airts.geometry import Point
from airts.map_model import Cell, EntityCategory, EntityKind


class UnitState(StrEnum):
    IDLE = "idle"
    MOVING = "moving"
    PATROLLING = "patrolling"


@dataclass(slots=True)
class Entity:
    entity_id: str
    kind: EntityKind
    owner_id: str
    position: Point
    health: int
    state: UnitState = UnitState.IDLE
    move_target: Point | None = None
    path: list[Point] = field(default_factory=list)
    path_cost: float = 0.0

    @property
    def category(self) -> EntityCategory:
        return self.kind.profile.category

    @property
    def is_movable(self) -> bool:
        return self.kind.profile.movable

    @property
    def speed(self) -> float:
        return self.kind.movement_speed

    @property
    def vision_range(self) -> float:
        return self.kind.profile.vision_range

    @property
    def occupied_cells(self) -> frozenset[Cell]:
        width, height = self.kind.profile.footprint
        origin_x = int(self.position.x)
        origin_y = int(self.position.y)
        return frozenset(
            (x, y)
            for y in range(origin_y, origin_y + height)
            for x in range(origin_x, origin_x + width)
        )

    @property
    def selection_position(self) -> Point:
        width, height = self.kind.profile.footprint
        if self.category is EntityCategory.UNIT:
            return self.position
        return Point(self.position.x + width / 2, self.position.y + height / 2)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.entity_id,
            "kind": self.kind.value,
            "owner": self.owner_id,
            "position": [self.position.x, self.position.y],
            "health": self.health,
            "state": self.state.value,
            "move_target": (
                None if self.move_target is None else [self.move_target.x, self.move_target.y]
            ),
            "path": [[point.x, point.y] for point in self.path],
            "path_cost": self.path_cost,
        }


Unit = Entity
