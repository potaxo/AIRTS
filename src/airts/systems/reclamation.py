"""Builder-controlled building removal and a single authoritative refund."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.commands import (
    CommandResult,
    ReclaimBuildingCommand,
    RemoveEntityCommand,
    ValidationFailure,
    ValidationPhase,
)
from airts.contracts.events import EventType
from airts.navigation.pathfinding import PathfindingError
from airts.systems import automation_control, movement
from airts.systems.command_handlers import validate_entities
from airts.systems.repair import building_distance
from airts.world.entities import UnitState
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory

if TYPE_CHECKING:
    from airts.simulation import Simulation


def reclaim_building(simulation: Simulation, command: ReclaimBuildingCommand) -> CommandResult:
    failure = validate_entities(simulation, command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("reclaim_building", failure)
    if any(simulation.entities[key].kind is not EntityKind.BUILDER for key in command.entity_ids):
        reason = "ENTITY_NOT_BUILDER"
    else:
        target = simulation.entities.get(command.target_entity_id)
        if target is None:
            reason = "UNKNOWN_TARGET"
        elif target.owner_id != command.owner_id:
            reason = "TARGET_NOT_OWNED"
        elif target.category is not EntityCategory.BUILDING:
            reason = "TARGET_NOT_BUILDING"
        else:
            reason = None
    if reason is not None:
        return simulation._reject_validation(
            "reclaim_building",
            ValidationFailure(ValidationPhase.CAPABILITY, reason, "target_entity_id"),
        )
    automation_control.manual_override_many(simulation, command.entity_ids)
    for key in command.entity_ids:
        builder = simulation.entities[key]
        builder.path.clear()
        builder.move_target = None
        builder.attack_target_id = None
        builder.pursue_target = False
        builder.reclaim_target_id = command.target_entity_id
        builder.state = UnitState.RECLAIMING
        movement.reset_movement_liveness(builder)
    return simulation._accept("reclaim_building")


def drive_reclamation(simulation: Simulation) -> None:
    builders = sorted(
        (entity for entity in simulation.entities.values() if entity.reclaim_target_id is not None),
        key=lambda entity: entity.entity_id,
    )
    if not builders:
        return
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for builder in builders:
        target = simulation.entities.get(builder.reclaim_target_id or "")
        if target is None:
            continue  # Removal clears the other builders' targets and paths immediately.
        if building_distance(builder.position, target) <= builder.kind.profile.build_range:
            refund = target.kind.profile.construction_cost * 70 // 100
            owner = target.owner_id
            result = simulation._remove_entity(
                RemoveEntityCommand(target.entity_id, "BUILDING_RECLAIMED")
            )
            assert result.accepted
            balance = simulation.resources.get(owner, 0)
            simulation.resources[owner] = balance + refund
            simulation.events.record(
                simulation.tick,
                EventType.RESOURCE_CHANGED,
                target.entity_id,
                owner_id=owner,
                reason="BUILDING_RECLAIMED",
                amount=refund,
                balance=balance + refund,
                builder_id=builder.entity_id,
            )
        elif not builder.path and allowance.claim():
            try:
                point, path = simulation._routes.shared_path_to_any(
                    builder.position,
                    simulation._interaction_points(target),
                    simulation._building_cells(),
                )
            except PathfindingError as error:
                if builder.state is not UnitState.WAITING:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        builder.entity_id,
                        operation="reclaim_building",
                        reason=str(error),
                    )
                builder.state = UnitState.WAITING
                continue
            movement.start_path(
                simulation, builder, point, path, "reclaim_building", UnitState.RECLAIMING
            )
