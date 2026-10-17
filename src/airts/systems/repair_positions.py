"""Repair service geometry and local free-space checks, independent of controllers."""

from __future__ import annotations

from collections.abc import Iterator
from math import ceil, floor, inf
from typing import TYPE_CHECKING

from airts.contracts.automations import RepairParameters, RepairPhase
from airts.contracts.spatial import Point
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.pathfinding import PathfindingError, PathResult
from airts.world.entities import Entity
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation

_MAX_MOBILE_RADIUS = max(collision_radius(kind) for kind in EntityKind if kind.profile.movable)


def in_service_range(simulation: Simulation, entity: Entity, building: Entity) -> bool:
    if building.kind is EntityKind.REPAIR_HUB:
        return (
            entity.position.distance_to(building.selection_position)
            <= building.profile.repair_range
        )
    return simulation.game_map.cell_for(entity.position) in {
        simulation.game_map.cell_for(point) for point in simulation._interaction_points(building)
    }


def repair_position_clear(simulation: Simulation, entity: Entity, point: Point) -> bool:
    """Use local occupancy as broadphase; never scan every unit for each candidate."""
    radius = collision_radius(entity.kind)
    reach = radius + _MAX_MOBILE_RADIUS
    nearby: set[str] = set()
    for y in range(floor(point.y - reach), floor(point.y + reach) + 1):
        for x in range(floor(point.x - reach), floor(point.x + reach) + 1):
            if simulation.game_map.contains_cell((x, y)):
                nearby.update(simulation.occupancy.occupants((x, y)))
    for key in sorted(nearby):
        other = simulation.entities[key]
        if key == entity.entity_id or not other.is_movable:
            continue
        simulation._collision_pair_check_count += 1
        if point.distance_to(other.position) < radius + collision_radius(other.kind) - 1e-6:
            return False
    return True


def service_area(simulation: Simulation, building: Entity) -> tuple[Point, ...]:
    """Static goals preserve the shared routing field across an entire repair group."""
    candidates: Iterator[Point]
    if building.kind is EntityKind.REPAIR_HUB:
        center = building.selection_position
        radius = building.profile.repair_range
        candidates = (
            Point(x + 0.5, y + 0.5)
            for y in range(floor(center.y - radius), ceil(center.y + radius))
            for x in range(floor(center.x - radius), ceil(center.x + radius))
            if Point(x + 0.5, y + 0.5).distance_to(center) <= radius
        )
    else:
        candidates = iter(simulation._interaction_points(building))
    blocked = simulation._building_cells()
    return tuple(
        point
        for point in candidates
        if simulation.game_map.is_passable(point)
        and simulation.game_map.cell_for(point) not in blocked
    )


def service_points(simulation: Simulation, entity: Entity, building: Entity) -> tuple[Point, ...]:
    """Free in-range cells and transient travel destinations form repair capacity."""
    reserved = [
        (other.move_target, collision_radius(other.kind))
        for automation in simulation.live_automations
        if isinstance(automation.parameters, RepairParameters)
        for key, phase in automation.parameters.phases.items()
        if phase is RepairPhase.TRAVELING
        and key != entity.entity_id
        and (other := simulation.entities.get(key)) is not None
        and other.move_target is not None
        and simulation.assignments.get(key) == automation.automation_id
    ]
    points = tuple(
        point
        for point in service_area(simulation, building)
        if repair_position_clear(simulation, entity, point)
        and all(
            point.distance_to(target) >= collision_radius(entity.kind) + radius
            for target, radius in reserved
        )
    )
    if in_service_range(simulation, entity, building) and repair_position_clear(
        simulation, entity, entity.position
    ):
        return (entity.position, *points)
    return points


def service_route(
    simulation: Simulation, entity: Entity, building: Entity
) -> tuple[Point, PathResult]:
    if in_service_range(simulation, entity, building) and repair_position_clear(
        simulation, entity, entity.position
    ):
        return entity.position, PathResult((simulation.game_map.cell_for(entity.position),), (), 0)
    free = service_points(simulation, entity, building)
    if not free:
        raise PathfindingError("NO_REPAIR_SPACE")
    goal, path = simulation._routes.shared_path_to_any(
        entity.position, service_area(simulation, building), simulation._building_cells()
    )
    if goal in free:
        return goal, path
    # These short certified segments stay local to the service area; their goals do
    # not create one full-map navigation field per reserved slot.
    for point in sorted(free, key=lambda point: (point.distance_to(goal), point.y, point.x)):
        extension = simulation._routes.segment_path(
            goal, point, simulation._building_cells(), maximum_cost=inf
        )
        if extension is not None:
            return point, PathResult(
                path.cells + extension.cells[1:],
                path.waypoints + extension.waypoints,
                path.cost + extension.cost,
            )
    raise PathfindingError("NO_REPAIR_SPACE")
