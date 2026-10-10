"""Direct builder repair and single-target automatic repair hubs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import AutomationStatus, RepairParameters
from airts.contracts.commands import (
    CommandResult,
    RepairBuildingCommand,
    ValidationFailure,
    ValidationPhase,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems import automation_control, movement
from airts.systems.command_handlers import validate_entities
from airts.world.entities import Entity, UnitState
from airts.world.map import EntityCategory, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def repair_building(simulation: Simulation, command: RepairBuildingCommand) -> CommandResult:
    failure = validate_entities(simulation, command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("repair_building", failure)
    if any(simulation.entities[key].kind is not EntityKind.BUILDER for key in command.entity_ids):
        return simulation._reject_validation(
            "repair_building",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_NOT_BUILDER", "entity_ids"),
        )
    target = simulation.entities.get(command.target_entity_id)
    if target is None:
        reason = "UNKNOWN_TARGET"
    elif target.owner_id != command.owner_id:
        reason = "TARGET_NOT_OWNED"
    elif target.category is not EntityCategory.BUILDING or not target.is_complete:
        reason = "TARGET_NOT_COMPLETED_BUILDING"
    elif target.health >= target.kind.profile.max_health:
        reason = "TARGET_NOT_DAMAGED"
    else:
        reason = None
    if reason is not None:
        return simulation._reject_validation(
            "repair_building",
            ValidationFailure(ValidationPhase.CAPABILITY, reason, "target_entity_id"),
        )
    automation_control.manual_override_many(simulation, command.entity_ids)
    for key in command.entity_ids:
        builder = simulation.entities[key]
        builder.path.clear()
        builder.move_target = None
        builder.attack_target_id = None
        builder.pursue_target = False
        builder.repair_target_id = command.target_entity_id
        builder.state = UnitState.REPAIRING
        movement.reset_movement_liveness(builder)
    return simulation._accept("repair_building")


def building_distance(point: Point, building: Entity) -> float:
    width, height = building.kind.profile.footprint
    nearest = Point(
        min(max(point.x, building.position.x), building.position.x + width),
        min(max(point.y, building.position.y), building.position.y + height),
    )
    return point.distance_to(nearest)


def drive_builders(simulation: Simulation) -> None:
    builders = [
        entity
        for entity in simulation.entities.values()
        if entity.kind is EntityKind.BUILDER and entity.repair_target_id is not None
    ]
    if not builders:
        return
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for builder in sorted(builders, key=lambda entity: entity.entity_id):
        target = simulation.entities.get(builder.repair_target_id or "")
        if target is None or target.health >= target.kind.profile.max_health:
            builder.repair_target_id = None
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.IDLE
            movement.reset_movement_liveness(builder)
            continue
        if building_distance(builder.position, target) <= builder.kind.profile.repair_range:
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.REPAIRING
            movement.reset_movement_liveness(builder)
            _heal(simulation, builder, target, builder.kind.profile.repair_rate)
        elif not builder.path and allowance.claim():
            try:
                point, path = simulation._routes.shared_path_to_any(
                    builder.position,
                    simulation._interaction_points(target),
                    simulation._building_cells(),
                )
            except PathfindingError as error:
                builder.state = UnitState.WAITING
                if builder.route_ticks == 0:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        builder.entity_id,
                        operation="repair_building",
                        reason=str(error),
                    )
                builder.route_ticks += 1
                continue
            movement.start_path(
                simulation, builder, point, path, "repair_building", UnitState.REPAIRING
            )


def drive_hubs(simulation: Simulation) -> None:
    hubs = [
        entity
        for entity in simulation.entities.values()
        if entity.kind is EntityKind.REPAIR_HUB and entity.is_complete
    ]
    if not hubs:
        return
    wounded = {
        key: entity.position
        for key, entity in simulation.entities.items()
        if entity.is_movable and entity.health < entity.kind.profile.max_health
    }
    index = SpatialIndex(wounded)
    rates = {}
    for automation in simulation.live_automations:
        parameters = automation.parameters
        if (
            isinstance(parameters, RepairParameters)
            and automation.status is AutomationStatus.ACTIVE
        ):
            for key, destination in parameters.destinations.items():
                if simulation.assignments.get(key) == automation.automation_id:
                    rates[destination, key] = parameters.repair_rate
    for hub in sorted(hubs, key=lambda entity: entity.entity_id):
        target = simulation.entities.get(hub.repair_target_id or "")
        radius = hub.kind.profile.repair_range
        center = hub.selection_position
        if (
            target is None
            or target.entity_id not in wounded
            or target.health >= target.kind.profile.max_health
            or target.owner_id != hub.owner_id
            or center.distance_to(target.position) > radius
        ):
            candidates = [
                simulation.entities[key]
                for key in index.nearby(center, radius)
                if simulation.entities[key].owner_id == hub.owner_id
                and simulation.entities[key].health
                < simulation.entities[key].kind.profile.max_health
            ]
            target = min(
                candidates,
                key=lambda entity: (center.distance_to(entity.position), entity.entity_id),
                default=None,
            )
            hub.repair_target_id = None if target is None else target.entity_id
            if target is not None:
                simulation.events.record(
                    simulation.tick,
                    EventType.REPAIR_STARTED,
                    target.entity_id,
                    destination_id=hub.entity_id,
                )
        if target is not None:
            _heal(
                simulation,
                hub,
                target,
                rates.get((hub.entity_id, target.entity_id), hub.kind.profile.repair_rate),
            )
            if target.health == target.kind.profile.max_health:
                hub.repair_target_id = None


def _heal(simulation: Simulation, source: Entity, target: Entity, rate: int) -> None:
    target.health = min(target.kind.profile.max_health, target.health + rate)
    if target.health == target.kind.profile.max_health:
        simulation.events.record(
            simulation.tick,
            EventType.REPAIR_COMPLETED,
            target.entity_id,
            destination_id=source.entity_id,
        )
