"""Automation creation, lifecycle transitions, and control-claim policy."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationParameters,
    AutomationStatus,
)
from airts.contracts.commands import (
    ControlAuthority,
    ControlClaim,
    ValidationFailure,
    ValidationPhase,
    claim_precedes,
)
from airts.contracts.events import EventType

if TYPE_CHECKING:
    from airts.simulation import Simulation


def new_automation(
    simulation: Simulation,
    kind: AutomationKind,
    title: str,
    owner_id: str,
    priority: int,
    original_instruction: str,
    entity_ids: list[str],
    parameters: AutomationParameters,
) -> Automation:
    return Automation(
        automation_id=f"automation_{simulation._next_automation_number:03d}",
        title=title.strip(),
        kind=kind,
        owner_id=owner_id,
        priority=priority,
        created_tick=simulation.tick,
        modified_tick=simulation.tick,
        original_instruction=original_instruction,
        entity_ids=entity_ids,
        parameters=parameters,
    )


def transition(
    simulation: Simulation, automation: Automation, status: AutomationStatus, reason: str
) -> None:
    automation.transition(status, simulation.tick, reason)
    if status.terminal:
        simulation._rebuild_live_automations(simulation._live_automation_candidates)
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_STATE_CHANGED,
        automation.automation_id,
        previous=(
            automation.transition_history[-1].previous.value
            if automation.transition_history[-1].previous is not None
            else None
        ),
        status=status.value,
        reason=reason,
    )


def validate_claims(
    simulation: Simulation,
    automation: Automation,
    entity_ids: tuple[str, ...],
    *,
    authority: ControlAuthority = ControlAuthority.AUTOMATION,
    replace_existing: bool = False,
) -> ValidationFailure | None:
    for entity_id in entity_ids:
        incumbent_id = simulation.assignments.get(entity_id)
        if replace_existing and incumbent_id is not None:
            incumbent = simulation.automations[incumbent_id]
            if incumbent.kind is not AutomationKind.REPAIR_AND_RETURN:
                continue
        if not claim_wins(simulation, automation, entity_id, authority):
            return ValidationFailure(
                ValidationPhase.CONFLICT,
                "CONTROL_CONFLICT",
                "entity_ids",
                {
                    "entity_id": entity_id,
                    "incumbent": simulation.assignments.get(entity_id),
                    "challenger": automation.automation_id,
                },
            )
    return None


def claim_wins(
    simulation: Simulation,
    automation: Automation,
    entity_id: str,
    authority: ControlAuthority = ControlAuthority.AUTOMATION,
) -> bool:
    incumbent_id = simulation.assignments.get(entity_id)
    if incumbent_id is None or incumbent_id == automation.automation_id:
        return True
    incumbent = simulation.automations[incumbent_id]
    incumbent_authority = (
        ControlAuthority.EMERGENCY
        if incumbent.kind is AutomationKind.REPAIR_AND_RETURN
        else ControlAuthority.AUTOMATION
    )
    return claim_precedes(
        ControlClaim(
            automation.automation_id, authority, automation.priority, automation.created_tick
        ),
        ControlClaim(
            incumbent.automation_id,
            incumbent_authority,
            incumbent.priority,
            incumbent.created_tick,
        ),
    )


def owned_automation(
    simulation: Simulation, automation_id: str, owner_id: str
) -> tuple[Automation | None, ValidationFailure | None]:
    automation = simulation.automations.get(automation_id)
    if automation is None:
        return None, ValidationFailure(
            ValidationPhase.REFERENCE, "UNKNOWN_AUTOMATION", "automation_id"
        )
    if automation.owner_id != owner_id:
        return None, ValidationFailure(
            ValidationPhase.OWNERSHIP,
            "AUTOMATION_NOT_OWNED",
            "automation_id",
            {"owner_id": automation.owner_id},
        )
    return automation, None
