"""Paid, timed entity upgrades independent of the unit's production or combat behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.commands import (
    CancelUpgradeCommand,
    CommandResult,
    UpgradeEntityCommand,
    ValidationFailure,
    ValidationPhase,
)
from airts.contracts.events import EventType
from airts.systems import economy
from airts.systems.validation import validate_entities

if TYPE_CHECKING:
    from airts.simulation import Simulation


def start_upgrade(simulation: Simulation, command: UpgradeEntityCommand) -> CommandResult:
    failure = validate_entities(simulation, (command.entity_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("upgrade_entity", failure)
    entity = simulation.entities[command.entity_id]
    definition = entity.next_upgrade
    if not entity.is_complete:
        failure = ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_UNFINISHED", "entity_id")
    elif entity.upgrade.remaining_ticks:
        failure = ValidationFailure(ValidationPhase.CONFLICT, "UPGRADE_IN_PROGRESS", "entity_id")
    elif definition is None:
        failure = ValidationFailure(ValidationPhase.CAPABILITY, "NO_UPGRADE_AVAILABLE", "entity_id")
    elif simulation.resources.get(command.owner_id, 0) < definition.cost:
        failure = ValidationFailure(ValidationPhase.RESOURCE, "INSUFFICIENT_RESOURCES", "entity_id")
    if failure is not None:
        return simulation._reject_validation("upgrade_entity", failure)
    assert definition is not None
    simulation.resources[command.owner_id] -= definition.cost
    entity.upgrade.remaining_ticks = definition.duration_ticks
    _resource_event(simulation, command.owner_id, -definition.cost, "UPGRADE_COST")
    simulation.events.record(
        simulation.tick,
        EventType.UPGRADE_STARTED,
        entity.entity_id,
        target_tier=entity.upgrade.tier + 1,
        duration_ticks=definition.duration_ticks,
        cost=definition.cost,
    )
    return simulation._accept("upgrade_entity")


def cancel_upgrade(simulation: Simulation, command: CancelUpgradeCommand) -> CommandResult:
    failure = validate_entities(simulation, (command.entity_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("cancel_upgrade", failure)
    entity = simulation.entities[command.entity_id]
    definition = entity.next_upgrade
    if not entity.upgrade.remaining_ticks or definition is None:
        return simulation._reject_validation(
            "cancel_upgrade",
            ValidationFailure(ValidationPhase.CONFLICT, "NO_UPGRADE_IN_PROGRESS", "entity_id"),
        )
    entity.upgrade.remaining_ticks = 0
    simulation.resources[command.owner_id] += definition.cost
    _resource_event(simulation, command.owner_id, definition.cost, "UPGRADE_REFUND")
    simulation.events.record(
        simulation.tick,
        EventType.UPGRADE_CANCELED,
        entity.entity_id,
        target_tier=entity.upgrade.tier + 1,
        refund=definition.cost,
    )
    return simulation._accept("cancel_upgrade")


def advance_upgrades(simulation: Simulation) -> None:
    for entity in simulation.entities.values():
        if not entity.upgrade.remaining_ticks:
            continue
        entity.upgrade.remaining_ticks -= 1
        if entity.upgrade.remaining_ticks == 0:
            previous_income = entity.profile.income_per_second
            entity.upgrade.tier += 1
            economy.change_income(
                simulation, entity.owner_id, entity.profile.income_per_second - previous_income
            )
            simulation.events.record(
                simulation.tick,
                EventType.UPGRADE_COMPLETED,
                entity.entity_id,
                tier=entity.upgrade.tier,
            )


def _resource_event(simulation: Simulation, owner_id: str, amount: int, reason: str) -> None:
    simulation.events.record(
        simulation.tick,
        EventType.RESOURCE_CHANGED,
        owner_id,
        amount=amount,
        balance=simulation.resources[owner_id],
        reason=reason,
    )
