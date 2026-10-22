"""Direct builder repair and single-target automatic repair hubs."""

from __future__ import annotations

from math import ceil, floor
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
from airts.systems import automation_assignments, construction, movement, unit_orders
from airts.systems.validation import validate_entities
from airts.world.entities import Entity, OrderKind, UnitOrder, UnitState
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory

if TYPE_CHECKING:
    from airts.simulation import Simulation


def repair_building(simulation: Simulation, command: RepairBuildingCommand) -> CommandResult:
    failure = validate_entities(simulation, command.entity_ids, command.owner_id)
    if failure is not None:
        return simulation._reject_validation("repair_building", failure)
    builders = tuple(
        key
        for key in command.entity_ids
        if simulation.entities[key].is_complete and simulation.entities[key].profile.can_repair
    )
    if not builders:
        return simulation._reject_validation(
            "repair_building",
            ValidationFailure(ValidationPhase.CAPABILITY, "ENTITY_NOT_BUILDER", "entity_ids"),
        )
    target = simulation.entities.get(command.target_entity_id)
    if target is None:
        reason = "UNKNOWN_TARGET"
    elif target.owner_id != command.owner_id:
        reason = "TARGET_NOT_OWNED"
    elif target.category is not EntityCategory.BUILDING:
        reason = "TARGET_NOT_COMPLETED_BUILDING"
    elif target.is_complete and target.health >= target.kind.profile.max_health:
        reason = "TARGET_NOT_DAMAGED"
    elif any(
        not simulation.entities[key].is_movable
        and (
            simulation.entities[key] is target
            or simulation.entities[key].selection_position.distance_to(target.selection_position)
            > simulation.entities[key].profile.repair_range
        )
        for key in builders
    ):
        reason = "TARGET_OUT_OF_REPAIR_RANGE"
    elif not target.is_complete and any(
        simulation.entities[key].is_movable and not simulation.entities[key].profile.can_build
        for key in builders
    ):
        reason = "ENTITY_CANNOT_FINISH_CONSTRUCTION"
    else:
        reason = None
    if reason is not None:
        return simulation._reject_validation(
            "repair_building",
            ValidationFailure(ValidationPhase.CAPABILITY, reason, "target_entity_id"),
        )
    if command.queued:
        assert target is not None
        unit_orders.append_target_order(
            simulation,
            builders,
            UnitOrder(OrderKind.REPAIR, target.selection_position, target.entity_id),
        )
        return simulation._accept("repair_building")
    automation_assignments.manual_override_many(simulation, builders)
    for key in builders:
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


def builder_repair_points(
    simulation: Simulation, builder: Entity, building: Entity
) -> tuple[Point, ...]:
    radius = builder.profile.repair_range
    width, height = building.kind.profile.footprint
    blocked = simulation._building_cells()
    return tuple(
        point
        for y in range(
            floor(building.position.y - radius), ceil(building.position.y + height + radius)
        )
        for x in range(
            floor(building.position.x - radius), ceil(building.position.x + width + radius)
        )
        if (x, y) not in blocked
        and simulation.game_map.is_cell_passable((x, y))
        and building_distance(point := Point(x + 0.5, y + 0.5), building) <= radius
    )


def drive_builders(simulation: Simulation) -> None:
    builders = [
        entity
        for entity in simulation.entities.values()
        if entity.profile.movable
        and entity.profile.can_repair
        and entity.repair_target_id is not None
    ]
    if not builders:
        return
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for builder in sorted(builders, key=lambda entity: entity.entity_id):
        target = simulation.entities.get(builder.repair_target_id or "")
        if target is None or (
            target.is_complete and target.health >= target.kind.profile.max_health
        ):
            builder.repair_target_id = None
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.IDLE
            movement.reset_movement_liveness(builder)
            continue
        if building_distance(builder.position, target) <= builder.profile.repair_range:
            builder.path.clear()
            builder.move_target = None
            builder.state = UnitState.REPAIRING
            movement.reset_movement_liveness(builder)
            _heal(simulation, builder, target, builder.profile.repair_rate)
        elif not builder.path and allowance.claim():
            try:
                point, path = simulation._routes.shared_path_to_any(
                    builder.position,
                    builder_repair_points(simulation, builder, target),
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
    reclaiming = {
        entity.reclaim_target_id
        for entity in simulation.entities.values()
        if entity.reclaim_target_id is not None
    }
    wounded = {
        key: entity.selection_position
        for key, entity in simulation.entities.items()
        if (not entity.is_complete or entity.health < entity.kind.profile.max_health)
        and key not in reclaiming
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
        radius = hub.profile.repair_range
        center = hub.selection_position
        if (
            target is None
            or target is hub
            or target.entity_id not in wounded
            or (target.is_complete and target.health >= target.kind.profile.max_health)
            or target.owner_id != hub.owner_id
            or center.distance_to(target.selection_position) > radius
        ):
            candidates = [
                simulation.entities[key]
                for key in index.nearby(center, radius)
                if key != hub.entity_id
                and simulation.entities[key].owner_id == hub.owner_id
                and (
                    not simulation.entities[key].is_complete
                    or simulation.entities[key].health
                    < simulation.entities[key].kind.profile.max_health
                )
            ]
            target = min(
                candidates,
                key=lambda entity: (
                    center.distance_to(entity.selection_position),
                    entity.entity_id,
                ),
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
                rates.get((hub.entity_id, target.entity_id), hub.profile.repair_rate),
            )
            if target.is_complete and target.health == target.kind.profile.max_health:
                hub.repair_target_id = None


def _heal(simulation: Simulation, source: Entity, target: Entity, rate: int) -> None:
    if not target.is_complete:
        work = (
            source.profile.build_speed
            if source.is_movable and source.profile.can_build
            else rate * simulation.CONSTRUCTION_REQUIRED_VALUE / target.kind.profile.max_health
        )
        construction.apply_construction_work(simulation, target, work)
        return
    target.health = min(target.kind.profile.max_health, target.health + rate)
    if target.health == target.kind.profile.max_health:
        simulation.events.record(
            simulation.tick,
            EventType.REPAIR_COMPLETED,
            target.entity_id,
            destination_id=source.entity_id,
        )
