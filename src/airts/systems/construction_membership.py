"""Construction queue membership updates shared with direct-control assignment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import AutomationKind, AutomationStatus
from airts.systems import automation_lifecycle

if TYPE_CHECKING:
    from airts.simulation import Simulation


def cancel_queued_construction(simulation: Simulation, builder_ids: tuple[str, ...]) -> None:
    selected = frozenset(builder_ids)
    if not selected:
        return
    for automation in simulation.live_automations:
        if (
            automation.kind is AutomationKind.CONSTRUCTION
            and automation.status is AutomationStatus.WAITING
            and automation.reason_code == "BUILDERS_QUEUED"
            and selected.intersection(automation.entity_ids)
        ):
            automation.remove_entities(selected)
            if not automation.entity_ids:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.CANCELED, "QUEUE_REPLACED"
                )
