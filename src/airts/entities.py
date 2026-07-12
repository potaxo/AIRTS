"""Richer Phase 2 entity state shared by units and inert buildings."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from airts.geometry import Point
from airts.map_model import Cell, EntityCategory, EntityKind


class UnitState(StrEnum):
    IDLE = "idle"
    MOVING = "moving"
    HOLDING = "holding"
    PATROLLING = "patrolling"
    DEFENDING = "defending"
    WAITING = "waiting"
    RETURNING = "returning"
    REPAIRING = "repairing"
    PRODUCING = "producing"
    ATTACKING = "attacking"
    RETREATING = "retreating"


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
    attack_target_id: str | None = None
    pursue_target: bool = False
    attack_cooldown: int = 0
    last_attacker_id: str | None = None
    last_attacked_tick: int | None = None
    progress_target: Point | None = None
    progress_distance: float | None = None
    no_progress_ticks: int = 0
    congestion_stopped: bool = False
    collision_pressure: int = 0
    route_ticks: int = 0

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
            "attack_target_id": self.attack_target_id,
            "pursue_target": self.pursue_target,
            "attack_cooldown": self.attack_cooldown,
            "last_attacker_id": self.last_attacker_id,
            "last_attacked_tick": self.last_attacked_tick,
            "progress_target": (
                None
                if self.progress_target is None
                else [self.progress_target.x, self.progress_target.y]
            ),
            "progress_distance": self.progress_distance,
            "no_progress_ticks": self.no_progress_ticks,
            "congestion_stopped": self.congestion_stopped,
            "collision_pressure": self.collision_pressure,
            "route_ticks": self.route_ticks,
        }


Unit = Entity
