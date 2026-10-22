"""Automation-to-entity assignment mutation and runtime cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import Automation, AutomationKind, AutomationStatus
from airts.contracts.commands import ControlAuthority
from airts.contracts.events import EventType
from airts.systems import (
    automation_lifecycle,
    automation_runtime,
    construction_membership,
    movement,
)
from airts.systems.assignment_state import state_for_assignment
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def activate(
    simulation: Simulation,
    automation: Automation,
    entity_ids: tuple[str, ...],
    *,
    authority: ControlAuthority = ControlAuthority.AUTOMATION,
    suspend: bool = False,
    assign_entities: bool = True,
) -> None:
    if assign_entities and not suspend:
        construction_membership.cancel_queued_construction(simulation, entity_ids)
    simulation.automations[automation.automation_id] = automation
    simulation._rebuild_live_automations((*simulation._live_automation_candidates, automation))
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
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.VALIDATING, "VALIDATION_STARTED"
    )
    if not assign_entities:
        pass
    elif suspend:
        for entity_id in entity_ids:
            assign(simulation, entity_id, automation, authority=authority, suspend=True)
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
            automation_runtime.refresh_gathering_formation(simulation, previous)
            handle_automation_without_entities(simulation, previous)
        for entity_id in entity_ids:
            previous_id = previous_ids[entity_id]
            simulation.suspended_assignments.pop(entity_id, None)
            simulation.assignments[entity_id] = automation.automation_id
            simulation.entities[entity_id].clear_recovery_request()
            simulation.events.record(
                simulation.tick,
                EventType.ASSIGNMENT_CHANGED,
                entity_id,
                previous_automation_id=previous_id,
                automation_id=automation.automation_id,
                authority=authority.name.lower(),
            )
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.ACTIVE, "VALIDATION_SUCCEEDED"
    )
    if assign_entities:
        for entity_id in entity_ids:
            automation_runtime.initialize_runtime_entity(simulation, automation, entity_id)


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
    simulation.entities[entity_id].clear_recovery_request()
    if previous_id is not None:
        if suspend:
            previous = simulation.automations[previous_id]
            existing_suspended = simulation.suspended_assignments.get(entity_id)
            simulation.suspended_assignments[entity_id] = existing_suspended or previous_id
            if previous.kind is AutomationKind.REPAIR_AND_RETURN:
                previous.remove_entity(entity_id)
                handle_automation_without_entities(simulation, previous)
        else:
            previous = simulation.automations[previous_id]
            previous.remove_entity(entity_id)
            automation_runtime.refresh_gathering_formation(simulation, previous)
            handle_automation_without_entities(simulation, previous)
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


def manual_override_many(simulation: Simulation, entity_ids: tuple[str, ...]) -> None:
    construction_membership.cancel_queued_construction(simulation, entity_ids)
    affected: dict[str, set[str]] = {}
    previous: dict[str, tuple[str | None, str | None]] = {}
    for entity_id in entity_ids:
        automation_id = simulation.assignments.pop(entity_id, None)
        entity = simulation.entities[entity_id]
        entity.order_queue.clear()
        entity.formation_goal = None
        entity.formation_leg = None
        entity.attack_gather_point = None
        entity.attack_target_id = None
        entity.pursue_target = False
        if (entity.profile.movable and entity.profile.can_repair) or entity.profile.can_reclaim:
            entity.repair_target_id = None
            entity.reclaim_target_id = None
        entity.clear_recovery_request()
        entity.hold_anchor = None
        entity.defend_arrived = False
        entity.recovery_path.clear()
        entity.recovery_blocked_cells = None
        entity.recovery_retry_tick = entity.contact_wait_ticks = 0
        suspended_id = simulation.suspended_assignments.pop(entity_id, None)
        previous[entity_id] = (automation_id, suspended_id)
        for affected_id in {item for item in (automation_id, suspended_id) if item is not None}:
            affected.setdefault(affected_id, set()).add(entity_id)
    for affected_id, removed_ids in sorted(affected.items()):
        automation = simulation.automations[affected_id]
        if automation.kind is AutomationKind.PRODUCTION:
            if automation.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.PAUSED, "FACTORY_MANUAL_OVERRIDE"
                )
        else:
            automation.remove_entities(frozenset(removed_ids))
            automation_runtime.refresh_gathering_formation(simulation, automation)
            handle_automation_without_entities(simulation, automation)
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
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.WAITING, "NO_ASSIGNED_ENTITIES"
            )
    elif automation.status in {
        AutomationStatus.ACTIVE,
        AutomationStatus.WAITING,
        AutomationStatus.BLOCKED,
        AutomationStatus.PAUSED,
    }:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.CANCELED, "NO_ASSIGNED_ENTITIES"
        )


def release_automation(
    simulation: Simulation, automation: Automation, *, clear_suspended: bool = False
) -> None:
    for entity_id in tuple(automation.entity_ids):
        if simulation.assignments.get(entity_id) == automation.automation_id:
            simulation.assignments.pop(entity_id, None)
            entity = simulation.entities[entity_id]
            entity.clear_recovery_request()
            entity.path.clear()
            entity.move_target = None
            entity.pursue_target = False
            if automation.kind is AutomationKind.PATROL:
                entity.attack_target_id = None
                entity.repair_target_id = None
            entity.reclaim_target_id = None
            entity.state = UnitState.IDLE
            movement.reset_movement_liveness(entity)
        if clear_suspended:
            suspended_id = simulation.suspended_assignments.pop(entity_id, None)
            if suspended_id is not None and suspended_id in simulation.automations:
                suspended = simulation.automations[suspended_id]
                suspended.remove_entity(entity_id)
                handle_automation_without_entities(simulation, suspended)


def resume_suspended_assignment(
    simulation: Simulation, repair_automation: Automation, entity_id: str
) -> None:
    resume_id = simulation.suspended_assignments.pop(entity_id, None)
    if simulation.assignments.get(entity_id) == repair_automation.automation_id:
        simulation.assignments.pop(entity_id, None)
    entity = simulation.entities[entity_id]
    entity.clear_recovery_request()
    if resume_id is not None:
        resume = simulation.automations.get(resume_id)
        if resume is not None and not resume.status.terminal and entity_id in resume.entity_ids:
            simulation.assignments[entity_id] = resume_id
            if resume.status in {AutomationStatus.WAITING, AutomationStatus.BLOCKED}:
                automation_lifecycle.transition(
                    simulation, resume, AutomationStatus.ACTIVE, "REPAIRED_UNIT_RETURNED"
                )
            movement.reset_movement_liveness(entity)
            entity.state = state_for_assignment(simulation, entity_id)
            return
    movement.reset_movement_liveness(entity)
    entity.state = UnitState.IDLE
