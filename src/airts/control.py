"""Deterministic authority and automation conflict precedence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ControlAuthority(IntEnum):
    AUTOMATION = 1
    EMERGENCY = 2
    HUMAN = 3


@dataclass(frozen=True, slots=True)
class ControlClaim:
    controller_id: str
    authority: ControlAuthority
    priority: int
    created_tick: int


def claim_precedes(challenger: ControlClaim, incumbent: ControlClaim) -> bool:
    if challenger.authority != incumbent.authority:
        return challenger.authority > incumbent.authority
    if challenger.priority != incumbent.priority:
        return challenger.priority > incumbent.priority
    if challenger.created_tick != incumbent.created_tick:
        return challenger.created_tick >= incumbent.created_tick
    return challenger.controller_id > incumbent.controller_id
