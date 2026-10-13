"""Serializable runtime entities, projectiles, and trajectory traces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from airts.contracts.spatial import Point
from airts.world.map import Cell, footprint_cells
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory


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
    BUILDING = "building"
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
    repair_target_id: str | None = None
    attack_cooldown: int = 0
    last_attacker_id: str | None = None
    last_attacked_tick: int | None = None
    progress_target: Point | None = None
    progress_distance: float | None = None
    no_progress_ticks: int = 0
    collision_pressure: int = 0
    route_ticks: int = 0
    hold_anchor: Point | None = None
    recovery_path: list[Point] = field(default_factory=list)
    recovery_retry_tick: int = 0
    contact_wait_ticks: int = 0
    defend_arrived: bool = False
    recovery_blocked_cells: frozenset[Cell] | None = None
    recovery_request_tick: int | None = None
    recovery_released: bool = False
    construction_progress: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.construction_progress is None

    def clear_recovery_request(self) -> None:
        """End the return phase when its owning intent is replaced or settled."""
        self.recovery_request_tick = None
        self.recovery_released = False

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
        return footprint_cells(self.kind, self.position)

    @property
    def selection_position(self) -> Point:
        profile = self.kind.profile
        if profile.category is EntityCategory.UNIT:
            return self.position
        width, height = profile.footprint
        return Point(self.position.x + width / 2, self.position.y + height / 2)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.entity_id,
            "kind": self.kind.value,
            "owner": self.owner_id,
            "position": [self.position.x, self.position.y],
            "health": self.health,
            "construction_progress": self.construction_progress,
            "state": self.state.value,
            "move_target": (
                None if self.move_target is None else [self.move_target.x, self.move_target.y]
            ),
            "path": [[point.x, point.y] for point in self.path],
            "path_cost": self.path_cost,
            "attack_target_id": self.attack_target_id,
            "pursue_target": self.pursue_target,
            "repair_target_id": self.repair_target_id,
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
            "congestion_stopped": False,  # Retired save/replay field, kept in the wire format.
            "collision_pressure": self.collision_pressure,
            "route_ticks": self.route_ticks,
            "hold_anchor": (
                None if self.hold_anchor is None else [self.hold_anchor.x, self.hold_anchor.y]
            ),
            "recovery_path": [[p.x, p.y] for p in self.recovery_path],
            "recovery_retry_tick": self.recovery_retry_tick,
            "contact_wait_ticks": self.contact_wait_ticks,
            "defend_arrived": self.defend_arrived,
            "recovery_request_tick": self.recovery_request_tick,
            "recovery_released": self.recovery_released,
            "recovery_blocked_cells": (
                None
                if self.recovery_blocked_cells is None
                else [list(cell) for cell in sorted(self.recovery_blocked_cells)]
            ),
        }


Unit = Entity


@dataclass(slots=True)
class Projectile:
    projectile_id: str
    source_entity_id: str
    target_entity_id: str
    owner_id: str
    weapon_kind: EntityKind
    position: Point
    destination: Point
    damage: int
    speed: float
    trajectory: list[Point] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.trajectory:
            self.trajectory.append(self.position)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.projectile_id,
            "source_entity_id": self.source_entity_id,
            "target_entity_id": self.target_entity_id,
            "owner_id": self.owner_id,
            "weapon_kind": self.weapon_kind.value,
            "position": [self.position.x, self.position.y],
            "destination": [self.destination.x, self.destination.y],
            "damage": self.damage,
            "speed": self.speed,
            "trajectory": [[point.x, point.y] for point in self.trajectory],
        }


@dataclass(frozen=True, slots=True)
class ProjectileTrace:
    projectile_id: str
    weapon_kind: EntityKind
    points: tuple[Point, ...]
    expires_tick: int

    def to_dict(self) -> dict[str, object]:
        return {
            "projectile_id": self.projectile_id,
            "weapon_kind": self.weapon_kind.value,
            "points": [[point.x, point.y] for point in self.points],
            "expires_tick": self.expires_tick,
        }
