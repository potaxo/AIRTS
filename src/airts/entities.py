"""Simple Phase 1 entity state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from airts.geometry import Point
from airts.map_model import EntityKind


class UnitState(StrEnum):
    IDLE = "idle"
    MOVING = "moving"
    PATROLLING = "patrolling"


@dataclass(slots=True)
class Unit:
    entity_id: str
    kind: EntityKind
    position: Point
    speed: float
    state: UnitState = UnitState.IDLE
    move_target: Point | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.entity_id,
            "kind": self.kind.value,
            "position": [self.position.x, self.position.y],
            "speed": self.speed,
            "state": self.state.value,
            "move_target": (
                None if self.move_target is None else [self.move_target.x, self.move_target.y]
            ),
        }
