"""Automation ownership, lifecycle transitions, and control claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationParameters,
    AutomationStatus,
    ProductionParameters,
)
from airts.commands import CommandResult
from airts.control import ControlAuthority, ControlClaim, claim_precedes
from airts.events import EventType
from airts.validation import ValidationFailure, ValidationPhase
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def pause(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = simulation._owned_automation(automation_id, owner_id)
    if failure is not None:
        return simulation._reject_validation("pause_automation", failure)
    assert automation is not None
    if automation.status not in {
        AutomationStatus.ACTIVE,
        AutomationStatus.WAITING,
        AutomationStatus.BLOCKED,
    }:
        return simulation._reject_validation(
            "pause_automation",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "AUTOMATION_NOT_PAUSABLE",
                evidence={"status": automation.status.value},
            ),
        )
    simulation._transition(automation, AutomationStatus.PAUSED, "PLAYER_PAUSED")
    for entity_id in automation.entity_ids:
        if simulation.assignments.get(entity_id) != automation_id:
            continue
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        entity.state = UnitState.IDLE
        simulation._reset_movement_liveness(entity, clear_stop=True)
    return simulation._accept("pause_automation", automation_id)


def resume(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = simulation._owned_automation(automation_id, owner_id)
    if failure is not None:
        return simulation._reject_validation("resume_automation", failure)
    assert automation is not None
    if automation.status is not AutomationStatus.PAUSED:
        return simulation._reject_validation(
            "resume_automation",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "AUTOMATION_NOT_PAUSED",
                evidence={"status": automation.status.value},
            ),
        )
    if automation.kind is AutomationKind.PRODUCTION:
        parameters = production_parameters(automation)
        incumbent_id = simulation.assignments.get(parameters.factory_id)
        if incumbent_id is not None and incumbent_id != automation.automation_id:
            incumbent = simulation.automations.get(incumbent_id)
            if incumbent is not None and incumbent.kind is AutomationKind.PRODUCTION:
                simulation._transition(automation, AutomationStatus.WAITING, "FACTORY_QUEUED")
                return simulation._accept("resume_automation", automation_id)
        failure = simulation._validate_claims(automation, (parameters.factory_id,))
        if failure is not None:
            return simulation._reject_validation("resume_automation", failure)
        if parameters.factory_id not in automation.entity_ids:
            automation.entity_ids.append(parameters.factory_id)
        simulation._assign(parameters.factory_id, automation)
        simulation.entities[parameters.factory_id].state = UnitState.PRODUCING
        simulation._record_production_started(automation)
    elif automation.kind is AutomationKind.CONSTRUCTION and not any(
        simulation.assignments.get(entity_id) == automation.automation_id
        for entity_id in automation.entity_ids
    ):
        simulation._transition(automation, AutomationStatus.WAITING, "BUILDERS_QUEUED")
        simulation._start_next_construction()
        return simulation._accept("resume_automation", automation_id)
    else:
        for entity_id in automation.entity_ids:
            if simulation.assignments.get(entity_id) == automation_id:
                simulation._reset_movement_liveness(simulation.entities[entity_id], clear_stop=True)
    simulation._transition(automation, AutomationStatus.ACTIVE, "PLAYER_RESUMED")
    return simulation._accept("resume_automation", automation_id)


def cancel(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = simulation._owned_automation(automation_id, owner_id)
    if failure is not None:
        return simulation._reject_validation("cancel_automation", failure)
    assert automation is not None
    if automation.status.terminal:
        return simulation._reject_validation(
            "cancel_automation",
            ValidationFailure(
                ValidationPhase.CAPABILITY,
                "AUTOMATION_TERMINAL",
                evidence={"status": automation.status.value},
            ),
        )
    simulation._transition(automation, AutomationStatus.CANCELED, "PLAYER_CANCELED")
    if automation.kind is AutomationKind.REPAIR_AND_RETURN:
        for entity_id in automation.entity_ids:
            if simulation.assignments.get(entity_id) == automation.automation_id:
                simulation._resume_suspended_assignment(automation, entity_id)
    else:
        simulation._release_automation(automation, clear_suspended=True)
    if automation.kind is AutomationKind.PRODUCTION:
        simulation._start_next_production(production_parameters(automation).factory_id)
    elif automation.kind is AutomationKind.CONSTRUCTION:
        simulation._start_next_construction()
    return simulation._accept("cancel_automation", automation_id)


def activate(
    simulation: Simulation,
    automation: Automation,
    entity_ids: tuple[str, ...],
    *,
    authority: ControlAuthority = ControlAuthority.AUTOMATION,
    suspend: bool = False,
    assign_entities: bool = True,
) -> None:
    simulation.automations[automation.automation_id] = automation
    simulation._next_automation_number += 1
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_CREATED,
        automation.automation_id,
        template=automation.kind.value,
        owner_id=automation.owner_id,
        priority=automation.priority,
        entity_ids=list(entity_ids),
    )
    simulation._transition(automation, AutomationStatus.VALIDATING, "VALIDATION_STARTED")
    if not assign_entities:
        pass
    elif suspend:
        for entity_id in entity_ids:
            simulation._assign(entity_id, automation, authority=authority, suspend=True)
    else:
        previous_groups: dict[str, set[str]] = {}
        previous_ids: dict[str, str | None] = {}
        for entity_id in entity_ids:
            previous_id = simulation.assignments.get(entity_id)
            previous_ids[entity_id] = previous_id
            if previous_id is not None and previous_id != automation.automation_id:
                previous_groups.setdefault(previous_id, set()).add(entity_id)
        for previous_id, removed_ids in sorted(previous_groups.items()):
            previous = simulation.automations[previous_id]
            previous.remove_entities(frozenset(removed_ids))
            simulation._refresh_gathering_formation(previous)
            simulation._handle_automation_without_entities(previous)
        for entity_id in entity_ids:
            previous_id = previous_ids[entity_id]
            simulation.suspended_assignments.pop(entity_id, None)
            simulation.assignments[entity_id] = automation.automation_id
            simulation.events.record(
                simulation.tick,
                EventType.ASSIGNMENT_CHANGED,
                entity_id,
                previous_automation_id=previous_id,
                automation_id=automation.automation_id,
                authority=authority.name.lower(),
            )
    simulation._transition(automation, AutomationStatus.ACTIVE, "VALIDATION_SUCCEEDED")
    if assign_entities:
        for entity_id in entity_ids:
            simulation._initialize_runtime_entity(automation, entity_id)


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
    automation_id = f"automation_{simulation._next_automation_number:03d}"
    return Automation(
        automation_id=automation_id,
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
    simulation.events.record(
        simulation.tick,
        EventType.AUTOMATION_STATE_CHANGED,
        automation.automation_id,
        previous=automation.transition_history[-1].previous.value
        if automation.transition_history[-1].previous is not None
        else None,
        status=status.value,
        reason=reason,
    )


def assign(
    simulation: Simulation,
    entity_id: str,
    automation: Automation,
    *,
    authority: ControlAuthority = ControlAuthority.AUTOMATION,
    suspend: bool = False,
) -> None:
    previous_id = simulation.assignments.get(entity_id)
    if previous_id == automation.automation_id:
        return
    if previous_id is not None:
        if suspend:
            previous = simulation.automations[previous_id]
            existing_suspended = simulation.suspended_assignments.get(entity_id)
            simulation.suspended_assignments[entity_id] = existing_suspended or previous_id
            if previous.kind is AutomationKind.REPAIR_AND_RETURN:
                previous.remove_entity(entity_id)
                simulation._handle_automation_without_entities(previous)
        else:
            previous = simulation.automations[previous_id]
            previous.remove_entity(entity_id)
            simulation._refresh_gathering_formation(previous)
            simulation._handle_automation_without_entities(previous)
            simulation.suspended_assignments.pop(entity_id, None)
    simulation.assignments[entity_id] = automation.automation_id
    simulation.events.record(
        simulation.tick,
        EventType.ASSIGNMENT_CHANGED,
        entity_id,
        previous_automation_id=previous_id,
        automation_id=automation.automation_id,
        authority=authority.name.lower(),
    )


def manual_override(simulation: Simulation, entity_id: str) -> None:
    simulation._manual_override_many((entity_id,))


def manual_override_many(simulation: Simulation, entity_ids: tuple[str, ...]) -> None:
    affected: dict[str, set[str]] = {}
    previous: dict[str, tuple[str | None, str | None]] = {}
    for entity_id in entity_ids:
        automation_id = simulation.assignments.pop(entity_id, None)
        suspended_id = simulation.suspended_assignments.pop(entity_id, None)
        previous[entity_id] = (automation_id, suspended_id)
        for affected_id in {item for item in (automation_id, suspended_id) if item is not None}:
            affected.setdefault(affected_id, set()).add(entity_id)
    for affected_id, removed_ids in sorted(affected.items()):
        automation = simulation.automations[affected_id]
        if automation.kind is AutomationKind.PRODUCTION:
            if automation.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}:
                simulation._transition(
                    automation, AutomationStatus.PAUSED, "FACTORY_MANUAL_OVERRIDE"
                )
        else:
            automation.remove_entities(frozenset(removed_ids))
            simulation._refresh_gathering_formation(automation)
            simulation._handle_automation_without_entities(automation)
    for entity_id, (automation_id, suspended_id) in previous.items():
        if automation_id is None:
            continue
        simulation.events.record(
            simulation.tick,
            EventType.MANUAL_OVERRIDE,
            entity_id,
            automation_id=automation_id,
            suspended_automation_id=suspended_id,
        )


def handle_automation_without_entities(simulation: Simulation, automation: Automation) -> None:
    if automation.entity_ids or automation.status.terminal:
        return
    if automation.has_future_source:
        if automation.status is AutomationStatus.ACTIVE:
            simulation._transition(automation, AutomationStatus.WAITING, "NO_ASSIGNED_ENTITIES")
    elif automation.status in {
        AutomationStatus.ACTIVE,
        AutomationStatus.WAITING,
        AutomationStatus.BLOCKED,
        AutomationStatus.PAUSED,
    }:
        if automation.status is AutomationStatus.PAUSED:
            simulation._transition(automation, AutomationStatus.CANCELED, "NO_ASSIGNED_ENTITIES")
        else:
            simulation._transition(automation, AutomationStatus.CANCELED, "NO_ASSIGNED_ENTITIES")


def release_automation(
    simulation: Simulation, automation: Automation, *, clear_suspended: bool = False
) -> None:
    for entity_id in tuple(automation.entity_ids):
        if simulation.assignments.get(entity_id) == automation.automation_id:
            simulation.assignments.pop(entity_id, None)
            entity = simulation.entities[entity_id]
            entity.path.clear()
            entity.move_target = None
            entity.pursue_target = False
            entity.state = UnitState.IDLE
            simulation._reset_movement_liveness(entity, clear_stop=True)
        if clear_suspended:
            suspended_id = simulation.suspended_assignments.pop(entity_id, None)
            if suspended_id is not None and suspended_id in simulation.automations:
                suspended = simulation.automations[suspended_id]
                suspended.remove_entity(entity_id)
                simulation._handle_automation_without_entities(suspended)


def resume_suspended_assignment(
    simulation: Simulation, repair_automation: Automation, entity_id: str
) -> None:
    resume_id = simulation.suspended_assignments.pop(entity_id, None)
    if simulation.assignments.get(entity_id) == repair_automation.automation_id:
        simulation.assignments.pop(entity_id, None)
    entity = simulation.entities[entity_id]
    if resume_id is not None:
        resume = simulation.automations.get(resume_id)
        if resume is not None and not resume.status.terminal and entity_id in resume.entity_ids:
            simulation.assignments[entity_id] = resume_id
            if resume.status in {AutomationStatus.WAITING, AutomationStatus.BLOCKED}:
                simulation._transition(resume, AutomationStatus.ACTIVE, "REPAIRED_UNIT_RETURNED")
            simulation._reset_movement_liveness(entity, clear_stop=True)
            entity.state = simulation._state_for_assignment(entity_id)
            return
    simulation._reset_movement_liveness(entity, clear_stop=True)
    entity.state = UnitState.IDLE


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
        if not simulation._claim_wins(automation, entity_id, authority):
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
    if incumbent_id is None:
        return True
    if incumbent_id == automation.automation_id:
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


def production_parameters(automation: Automation) -> ProductionParameters:
    if not isinstance(automation.parameters, ProductionParameters):
        raise TypeError("automation does not have production parameters")
    return automation.parameters
