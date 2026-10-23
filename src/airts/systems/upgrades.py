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
from airts.world.entities import Entity

if TYPE_CHECKING:
    from airts.simulation import Simulation


def start_upgrade(simulation: Simulation, command: UpgradeEntityCommand) -> CommandResult:
    failure = validate_entities(simulation, (command.entity_id,), command.owner_id)
    if failure is None and not isinstance(command.queued, bool):
        failure = ValidationFailure(ValidationPhase.SCHEMA, "QUEUED_NOT_BOOLEAN", "queued")
    if failure is not None:
        return simulation._reject_validation("upgrade_entity", failure)
    entity = simulation.entities[command.entity_id]
    definition = entity.next_upgrade_to_queue
    if entity.upgrade.remaining_ticks and not command.queued:
        failure = ValidationFailure(ValidationPhase.CONFLICT, "UPGRADE_IN_PROGRESS", "entity_id")
    elif entity.upgrade.queued_count and not command.queued:
        failure = ValidationFailure(ValidationPhase.CONFLICT, "UPGRADE_QUEUED", "entity_id")
    elif definition is None:
        failure = ValidationFailure(ValidationPhase.CAPABILITY, "NO_UPGRADE_AVAILABLE", "entity_id")
    elif simulation.resources.get(command.owner_id, 0) < definition.cost:
        failure = ValidationFailure(ValidationPhase.RESOURCE, "INSUFFICIENT_RESOURCES", "entity_id")
    if failure is not None:
        return simulation._reject_validation("upgrade_entity", failure)
    assert definition is not None
    simulation.resources[command.owner_id] -= definition.cost
    _resource_event(simulation, command.owner_id, -definition.cost, "UPGRADE_COST")
    if not entity.is_complete or entity.upgrade.remaining_ticks or entity.upgrade.queued_count:
        entity.upgrade.queued_count += 1
        simulation.events.record(
            simulation.tick,
            EventType.UPGRADE_QUEUED,
            entity.entity_id,
            target_tier=(
                entity.upgrade.tier
                + bool(entity.upgrade.remaining_ticks)
                + entity.upgrade.queued_count
            ),
            cost=definition.cost,
            queued_count=entity.upgrade.queued_count,
        )
    else:
        _begin_upgrade(simulation, entity)
    return simulation._accept("upgrade_entity")


def _begin_upgrade(simulation: Simulation, entity: Entity) -> None:
    definition = entity.next_upgrade
    assert definition is not None and entity.is_complete
    entity.upgrade.remaining_ticks = definition.duration_ticks
    simulation.events.record(
        simulation.tick,
        EventType.UPGRADE_STARTED,
        entity.entity_id,
        target_tier=entity.upgrade.tier + 1,
        duration_ticks=definition.duration_ticks,
        cost=definition.cost,
    )


def cancel_upgrade(simulation: Simulation, command: CancelUpgradeCommand) -> CommandResult:
    failure = validate_entities(simulation, (command.entity_id,), command.owner_id)
    if failure is not None:
        return simulation._reject_validation("cancel_upgrade", failure)
    entity = simulation.entities[command.entity_id]
    reserved = bool(entity.upgrade.remaining_ticks) + entity.upgrade.queued_count
    if not reserved:
        return simulation._reject_validation(
            "cancel_upgrade",
            ValidationFailure(ValidationPhase.CONFLICT, "NO_UPGRADE_IN_PROGRESS", "entity_id"),
        )
    start = entity.upgrade.tier - 1
    refund = sum(definition.cost for definition in entity.kind.upgrades[start : start + reserved])
    entity.upgrade.remaining_ticks = 0
    entity.upgrade.queued_count = 0
    simulation.resources[command.owner_id] += refund
    _resource_event(simulation, command.owner_id, refund, "UPGRADE_REFUND")
    simulation.events.record(
        simulation.tick,
        EventType.UPGRADE_CANCELED,
        entity.entity_id,
        target_tier=entity.upgrade.tier + 1,
        refund=refund,
    )
    return simulation._accept("cancel_upgrade")


def advance_upgrades(simulation: Simulation) -> None:
    for entity in simulation.entities.values():
        if not entity.upgrade.remaining_ticks and not entity.upgrade.queued_count:
            continue
        if not entity.is_complete:
            continue
        if entity.upgrade.remaining_ticks:
            entity.upgrade.remaining_ticks -= 1
            if entity.upgrade.remaining_ticks == 0:
                _complete_upgrade(simulation, entity)
        if not entity.upgrade.remaining_ticks and entity.upgrade.queued_count:
            entity.upgrade.queued_count -= 1
            _begin_upgrade(simulation, entity)


def _complete_upgrade(simulation: Simulation, entity: Entity) -> None:
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
