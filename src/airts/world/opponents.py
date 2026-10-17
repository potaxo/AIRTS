"""Authoritative, serializable memory for deterministic skirmish opponents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from airts.contracts.spatial import Point


class SquadPhase(StrEnum):
    GATHERING = "gathering"
    ADVANCING = "advancing"
    ATTACKING = "attacking"
    RETREATING = "retreating"
    DEFENDING = "defending"


@dataclass(slots=True)
class Squad:
    entity_ids: list[str] = field(default_factory=list)
    phase: SquadPhase = SquadPhase.GATHERING
    phase_tick: int = 0
    target_id: str | None = None
    destination: Point | None = None
    last_order_tick: int = 0
    retry_tick: int = 0
    last_failure: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "entity_ids": list(self.entity_ids),
            "phase": self.phase.value,
            "phase_tick": self.phase_tick,
            "target_id": self.target_id,
            "destination": (
                None if self.destination is None else [self.destination.x, self.destination.y]
            ),
            "last_order_tick": self.last_order_tick,
            "retry_tick": self.retry_tick,
            "last_failure": self.last_failure,
        }


@dataclass(slots=True)
class OpponentState:
    reserve: Squad = field(default_factory=Squad)
    squads: list[Squad] = field(default_factory=list)
    budget_paused_ids: list[str] = field(default_factory=list)
    growth_credit: float = 0.0
    budget_tick: int = 0

    def remove_entity(self, entity_id: str) -> None:
        for squad in (self.reserve, *self.squads):
            if entity_id in squad.entity_ids:
                squad.entity_ids.remove(entity_id)
            if squad.target_id == entity_id:
                squad.target_id = None

    def to_dict(self) -> dict[str, object]:
        return {
            "reserve": self.reserve.to_dict(),
            "squads": [squad.to_dict() for squad in self.squads],
            "budget_paused_ids": list(self.budget_paused_ids),
            "growth_credit": self.growth_credit,
            "budget_tick": self.budget_tick,
        }
