"""Player-facing pause, resume, and cancel operations for automations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    production_parameters,
)
from airts.contracts.commands import CommandResult, ValidationFailure, ValidationPhase
from airts.systems import (
    automation_assignments,
    automation_lifecycle,
    construction,
    movement,
    production,
    production_orders,
)
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def pause(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = automation_lifecycle.owned_automation(simulation, automation_id, owner_id)
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
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.PAUSED, "PLAYER_PAUSED"
    )
    for entity_id in automation.entity_ids:
        if simulation.assignments.get(entity_id) != automation_id:
            continue
        entity = simulation.entities[entity_id]
        entity.clear_recovery_request()
        entity.path.clear()
        entity.move_target = None
        if automation.kind in {AutomationKind.PATROL, AutomationKind.DEFEND}:
            entity.attack_target_id = None
            entity.pursue_target = False
            entity.repair_target_id = None
            entity.reclaim_target_id = None
        entity.state = UnitState.IDLE
        movement.reset_movement_liveness(entity)
    return simulation._accept("pause_automation", automation_id)


def resume(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = automation_lifecycle.owned_automation(simulation, automation_id, owner_id)
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
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.WAITING, "FACTORY_QUEUED"
                )
                return simulation._accept("resume_automation", automation_id)
        failure = automation_lifecycle.validate_claims(
            simulation, automation, (parameters.factory_id,)
        )
        if failure is not None:
            return simulation._reject_validation("resume_automation", failure)
        if parameters.factory_id not in automation.entity_ids:
            automation.entity_ids.append(parameters.factory_id)
        automation_assignments.assign(simulation, parameters.factory_id, automation)
        simulation.entities[parameters.factory_id].state = UnitState.PRODUCING
        production.record_production_started(simulation, automation)
    elif automation.kind is AutomationKind.CONSTRUCTION and not any(
        simulation.assignments.get(entity_id) == automation.automation_id
        for entity_id in automation.entity_ids
    ):
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.WAITING, "BUILDERS_QUEUED"
        )
        construction.start_next_construction(simulation)
        return simulation._accept("resume_automation", automation_id)
    else:
        for entity_id in automation.entity_ids:
            if simulation.assignments.get(entity_id) == automation_id:
                simulation.entities[entity_id].clear_recovery_request()
                movement.reset_movement_liveness(simulation.entities[entity_id])
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.ACTIVE, "PLAYER_RESUMED"
    )
    return simulation._accept("resume_automation", automation_id)


def cancel(simulation: Simulation, automation_id: str, owner_id: str) -> CommandResult:
    automation, failure = automation_lifecycle.owned_automation(simulation, automation_id, owner_id)
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
    if (
        isinstance(automation.parameters, DefendParameters)
        and automation.parameters.production_defense
    ):
        production_orders.detach_production_defense(simulation, automation)
    automation_lifecycle.transition(
        simulation, automation, AutomationStatus.CANCELED, "PLAYER_CANCELED"
    )
    if automation.kind is AutomationKind.REPAIR_AND_RETURN:
        for entity_id in automation.entity_ids:
            if simulation.assignments.get(entity_id) == automation.automation_id:
                automation_assignments.resume_suspended_assignment(
                    simulation, automation, entity_id
                )
    else:
        automation_assignments.release_automation(simulation, automation, clear_suspended=True)
    if automation.kind is AutomationKind.PRODUCTION:
        production.start_next_production(simulation, production_parameters(automation).factory_id)
    elif automation.kind is AutomationKind.CONSTRUCTION:
        construction.start_next_construction(simulation)
    return simulation._accept("cancel_automation", automation_id)
