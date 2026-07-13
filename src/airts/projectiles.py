"""Serializable deterministic projectile and trajectory state."""

from __future__ import annotations

from dataclasses import dataclass, field

from airts.geometry import Point
from airts.map_model import EntityKind


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


def projectile_speed(kind: EntityKind) -> float:
    return {
        EntityKind.SCOUT: 12.0,
        EntityKind.LIGHT_TANK: 10.0,
        EntityKind.HEAVY_TANK: 7.0,
    }.get(kind, 0.0)
