"""Map authoritative automation assignments to presentation-neutral entity states."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import AutomationKind
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def state_for_assignment(simulation: Simulation, entity_id: str) -> UnitState:
    automation_id = simulation.assignments.get(entity_id)
    if automation_id is None:
        return UnitState.IDLE
    automation = simulation.automations[automation_id]
    return {
        AutomationKind.PATROL: UnitState.PATROLLING,
        AutomationKind.DEFEND: UnitState.DEFENDING,
        AutomationKind.PRODUCTION: UnitState.PRODUCING,
        AutomationKind.CONSTRUCTION: UnitState.BUILDING,
        AutomationKind.REINFORCEMENT: UnitState.WAITING,
        AutomationKind.REPAIR_AND_RETURN: UnitState.REPAIRING,
        AutomationKind.ECONOMY: UnitState.IDLE,
    }[automation.kind]
