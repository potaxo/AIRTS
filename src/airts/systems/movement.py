"""Deterministic unit movement, collision, and blocked-unit recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.events import EventType
from airts.geometry import Point
from airts.navigation.movement import (
    NEIGHBOR_RADIUS,
    collision_radius,
    steering_candidates,
    unit_mass,
)
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.world.entities import Entity
from airts.world.map_model import Cell, EntityKind
from airts.world.occupancy import OccupancyError

if TYPE_CHECKING:
    from airts.simulation import Simulation


type LocalCollider = tuple[str, Point, float, bool]


def move_entities(simulation: Simulation) -> None:
    movable_entities = {
        entity_id: entity for entity_id, entity in simulation.entities.items() if entity.is_movable
    }
    movable_ids = frozenset(movable_entities)
    collision_radii = {
        entity_id: collision_radius(entity.kind) for entity_id, entity in movable_entities.items()
    }
    static_occupant_cells = simulation._building_cells()
    unit_index = SpatialIndex(
        {entity_id: entity.position for entity_id, entity in movable_entities.items()}
    )
    for entity_id in sorted(simulation.entities):
        entity = simulation.entities[entity_id]
        if not entity.path:
            continue
        if entity.congestion_stopped:
            retry_phase = sum(ord(character) for character in entity_id) % (
                simulation.CONGESTION_RETRY_TICKS
            )
            if simulation.tick % simulation.CONGESTION_RETRY_TICKS != retry_phase:
                continue
            entity.congestion_stopped = False
            entity.no_progress_ticks = simulation.NO_PROGRESS_YIELD_TICKS - 1
            entity.progress_distance = entity.position.distance_to(entity.path[0])
        simulation._movement_step_attempt_count += 1
        simulation._consume_reached_intermediate_waypoints(entity)
        simulation._skip_crowded_waypoints(entity)
        target = entity.path[0]
        maximum_step = entity.speed * simulation.TICK_SECONDS
        entity_radius = collision_radii[entity_id]
        local_query_radius = max(
            NEIGHBOR_RADIUS,
            maximum_step + entity_radius + 0.5,
        )
        local_neighbor_ids = unit_index.nearby(entity.position, local_query_radius)
        local_colliders: tuple[LocalCollider, ...] = tuple(
            (
                other_id,
                other.position,
                collision_radii[other_id],
                not other.path,
            )
            for other_id in local_neighbor_ids
            for other in (simulation.entities[other_id],)
        )
        neighbors = (
            tuple(position for other_id, position, _, _ in local_colliders if other_id != entity_id)
            if local_query_radius == NEIGHBOR_RADIUS
            else tuple(
                position
                for other_id, position, _, _ in local_colliders
                if other_id != entity_id
                and _squared_distance(entity.position, position)
                <= NEIGHBOR_RADIUS * NEIGHBOR_RADIUS
            )
        )
        next_position: Point | None
        direct_distance = entity.position.distance_to(target)
        desired_direct_position = (
            target
            if direct_distance <= maximum_step
            else Point(
                entity.position.x + (target.x - entity.position.x) * maximum_step / direct_distance,
                entity.position.y + (target.y - entity.position.y) * maximum_step / direct_distance,
            )
        )
        direct_position = simulation._clamp_to_collider_contact(
            entity,
            desired_direct_position,
            entity_radius,
            local_colliders,
        )
        direct_was_clamped = _squared_distance(direct_position, desired_direct_position) > 1e-18
        push_stationary_blocker = direct_was_clamped and simulation._contact_has_stationary_blocker(
            entity,
            desired_direct_position,
            entity_radius,
            local_colliders,
        )
        if (
            not direct_was_clamped or push_stationary_blocker
        ) and simulation._local_move_is_available(
            entity,
            direct_position,
            entity_radius,
            local_colliders,
            static_occupant_cells,
        ):
            next_position = direct_position
        else:
            if (
                push_stationary_blocker
                and len(entity.path) == 1
                and simulation._replan_contested_final_approach(entity, target)
            ):
                continue
            next_position = None
            for raw_candidate in steering_candidates(
                entity.position,
                target,
                maximum_step,
                neighbors,
                candidate_limit=4 if len(neighbors) >= 8 else None,
            ):
                if (
                    direct_was_clamped
                    and not push_stationary_blocker
                    and _squared_distance(raw_candidate, desired_direct_position) <= 1e-18
                ):
                    continue
                candidate = simulation._clamp_to_collider_contact(
                    entity,
                    raw_candidate,
                    entity_radius,
                    local_colliders,
                )
                if simulation._local_move_is_available(
                    entity,
                    candidate,
                    entity_radius,
                    local_colliders,
                    static_occupant_cells,
                ):
                    next_position = candidate
                    break
        if next_position is None:
            simulation._record_movement_blocked(entity, "NO_SAFE_LOCAL_VELOCITY")
            continue
        try:
            simulation.occupancy.move(
                entity_id,
                simulation._cells_at(entity, next_position),
                movable_ids,
            )
        except OccupancyError as error:
            simulation._record_movement_blocked(entity, str(error))
            continue
        simulation._movement_blocked.discard(entity_id)
        simulation._blocked_ticks.pop(entity_id, None)
        entity.position = next_position
        unit_index.move(entity_id, next_position)
        arrived = _squared_distance(entity.position, target) <= 1e-18
        if arrived:
            entity.path.pop(0)
            if not entity.path:
                entity.move_target = None
                entity.state = simulation._state_for_assignment(entity_id)
                simulation.events.record(
                    simulation.tick,
                    EventType.MOVEMENT_COMPLETED,
                    entity_id,
                    position=[entity.position.x, entity.position.y],
                    assignment=simulation.assignments.get(entity_id),
                )
    simulation._resolve_unit_collisions(unit_index)
    simulation._track_movement_progress()


def track_movement_progress(simulation: Simulation) -> None:
    """Temporarily yield orders that are not getting closer to their waypoint."""

    for entity_id in sorted(simulation.entities):
        entity = simulation.entities[entity_id]
        if not entity.path:
            simulation._reset_movement_liveness(entity)
            continue
        entity.route_ticks += 1
        repath_phase = sum(map(ord, entity_id)) % simulation.DESTINATION_REPATH_TICKS
        if (
            entity.route_ticks >= simulation.DESTINATION_REPATH_TICKS
            and (
                entity.kind is EntityKind.BUILDER
                or (entity.route_ticks - simulation.DESTINATION_REPATH_TICKS)
                % simulation.DESTINATION_REPATH_TICKS
                == repath_phase
            )
            and (
                (entity.kind is EntityKind.BUILDER and entity.collision_pressure > 0)
                or simulation._remaining_path_crosses_military_units(entity)
            )
            and simulation._repath_stalled_entity(entity, reason="DESTINATION_DELAY_REPATH")
        ):
            continue
        target = entity.path[0]
        distance = entity.position.distance_to(target)
        if entity.progress_target != target or entity.progress_distance is None:
            entity.progress_target = target
            entity.progress_distance = distance
            entity.no_progress_ticks = 0
            entity.congestion_stopped = False
            continue
        if entity.congestion_stopped:
            if distance <= entity.progress_distance - simulation.MIN_PROGRESS_DISTANCE:
                entity.progress_distance = distance
                entity.no_progress_ticks = 0
                entity.congestion_stopped = False
            continue
        if distance <= entity.progress_distance - simulation.MIN_PROGRESS_DISTANCE:
            entity.progress_distance = distance
            entity.no_progress_ticks = 0
            continue
        entity.no_progress_ticks += 1
        if entity.no_progress_ticks < simulation.NO_PROGRESS_YIELD_TICKS:
            continue
        if (
            entity.kind is EntityKind.BUILDER
            or simulation._remaining_path_crosses_military_units(entity)
        ) and simulation._repath_stalled_entity(entity):
            continue
        entity.congestion_stopped = True
        simulation._movement_blocked.discard(entity_id)
        simulation._blocked_ticks.pop(entity_id, None)
        simulation.events.record(
            simulation.tick,
            EventType.MOVEMENT_YIELDED,
            entity_id,
            reason="NO_PROGRESS_YIELD",
            timeout_ticks=simulation.NO_PROGRESS_YIELD_TICKS,
            retry_ticks=simulation.CONGESTION_RETRY_TICKS,
            destination=(
                None if entity.move_target is None else [entity.move_target.x, entity.move_target.y]
            ),
            position=[entity.position.x, entity.position.y],
        )


def reset_movement_liveness(entity: Entity, *, clear_stop: bool = False) -> None:
    entity.progress_target = None
    entity.progress_distance = None
    entity.no_progress_ticks = 0
    entity.route_ticks = 0
    if clear_stop:
        entity.congestion_stopped = False


def repath_stalled_entity(
    simulation: Simulation, entity: Entity, *, reason: str = "NO_PROGRESS_REPATH"
) -> bool:
    destination = entity.move_target
    if (
        destination is None
        or simulation._stalled_repaths_this_tick >= simulation.STALLED_REPATH_BUDGET
    ):
        return False
    try:
        path = simulation._routes.dynamic_path(
            entity.position,
            destination,
            simulation._building_cells(),
            cell_penalties=simulation._military_cell_penalties(entity.entity_id),
        )
    except PathfindingError:
        return False
    if path.waypoints == tuple(entity.path):
        entity.route_ticks = 0
        return False
    simulation._stalled_repaths_this_tick += 1
    entity.path = list(path.waypoints)
    entity.path_cost = path.cost
    simulation._reset_movement_liveness(entity, clear_stop=True)
    simulation._movement_blocked.discard(entity.entity_id)
    simulation._blocked_ticks.pop(entity.entity_id, None)
    simulation.events.record(
        simulation.tick,
        EventType.PATH_COMPUTED,
        entity.entity_id,
        reason=reason,
        obstacle_penalty=simulation.MILITARY_OBSTACLE_PATH_PENALTY,
        target=[destination.x, destination.y],
    )
    return True


def remaining_path_crosses_military_units(simulation: Simulation, entity: Entity) -> bool:
    """Return whether a route crosses a settled unit that local steering cannot carry along."""

    route_cells = {simulation.game_map.cell_for(point) for point in entity.path}
    return any(
        occupant_id != entity.entity_id
        and simulation.entities[occupant_id].is_movable
        and not simulation.entities[occupant_id].path
        for cell in route_cells
        for occupant_id in simulation.occupancy.occupants(cell)
    )


def military_cell_penalties(simulation: Simulation, excluding_id: str) -> dict[Cell, float]:
    return {
        cell: simulation.MILITARY_OBSTACLE_PATH_PENALTY
        for other_id, other in simulation.entities.items()
        if other_id != excluding_id and other.is_movable
        for cell in other.occupied_cells
    }


def replan_contested_final_approach(
    simulation: Simulation, entity: Entity, destination: Point
) -> bool:
    """Route around units that have already settled between this unit and its slot."""

    if simulation._stalled_repaths_this_tick >= simulation.STALLED_REPATH_BUDGET:
        return False
    try:
        path = simulation._routes.dynamic_path(
            entity.position,
            destination,
            simulation._building_cells(),
            cell_penalties=simulation._military_cell_penalties(entity.entity_id),
        )
    except PathfindingError:
        return False
    if len(path.waypoints) <= 1:
        return False
    simulation._stalled_repaths_this_tick += 1
    entity.path = list(path.waypoints)
    entity.path_cost = path.cost
    simulation._reset_movement_liveness(entity, clear_stop=True)
    simulation.events.record(
        simulation.tick,
        EventType.PATH_COMPUTED,
        entity.entity_id,
        reason="SETTLED_UNIT_REROUTE",
        target=[destination.x, destination.y],
    )
    return True


def consume_reached_intermediate_waypoints(entity: Entity) -> None:
    """Do not orbit an A* cell center after safely entering its local neighborhood."""

    while len(entity.path) > 1 and entity.position.distance_to(entity.path[0]) <= 0.35:
        entity.path.pop(0)


def skip_crowded_waypoints(simulation: Simulation, entity: Entity) -> None:
    """Use path lookahead so agents pass a contested cell instead of orbiting its center."""

    while len(entity.path) > 1:
        waypoint = entity.path[0]
        if not any(
            occupant_id != entity.entity_id and simulation.entities[occupant_id].is_movable
            for occupant_id in simulation.occupancy.occupants(
                simulation.game_map.cell_for(waypoint)
            )
        ):
            return
        if not _waypoint_lookahead_preserves_axis(simulation, entity, entity.path[1]):
            return
        if not simulation._waypoint_has_lateral_clearance(entity, waypoint):
            return
        entity.path.pop(0)


def _waypoint_lookahead_preserves_axis(
    simulation: Simulation, entity: Entity, destination: Point
) -> bool:
    """Only look past occupied waypoints along one passable four-direction corridor."""

    start = simulation.game_map.cell_for(entity.position)
    end = simulation.game_map.cell_for(destination)
    if start[0] != end[0] and start[1] != end[1]:
        return False
    first, second = sorted((start, end))
    cache_key = (first, second)
    cached = simulation._waypoint_corridor_cache.get(cache_key)
    if cached is not None:
        return cached
    if start[0] == end[0]:
        cells = ((start[0], y) for y in range(min(start[1], end[1]), max(start[1], end[1]) + 1))
    else:
        cells = ((x, start[1]) for x in range(min(start[0], end[0]), max(start[0], end[0]) + 1))
    blocked = simulation._building_cells()
    corridor_is_clear = all(
        simulation.game_map.is_cell_passable(cell) and cell not in blocked for cell in cells
    )
    simulation._waypoint_corridor_cache[cache_key] = corridor_is_clear
    return corridor_is_clear


def waypoint_has_lateral_clearance(simulation: Simulation, entity: Entity, waypoint: Point) -> bool:
    cell = simulation.game_map.cell_for(waypoint)
    offset_x = waypoint.x - entity.position.x
    offset_y = waypoint.y - entity.position.y
    lateral_cells = (
        ((cell[0], cell[1] - 1), (cell[0], cell[1] + 1))
        if abs(offset_x) >= abs(offset_y)
        else ((cell[0] - 1, cell[1]), (cell[0] + 1, cell[1]))
    )
    return any(simulation.game_map.is_cell_passable(candidate) for candidate in lateral_cells)


def local_move_is_available(
    simulation: Simulation,
    entity: Entity,
    candidate: Point,
    entity_radius: float,
    local_colliders: tuple[LocalCollider, ...],
    static_occupant_cells: frozenset[Cell],
) -> bool:
    if not simulation.game_map.is_passable(candidate):
        return False
    if static_occupant_cells and not simulation._cells_at(entity, candidate).isdisjoint(
        static_occupant_cells
    ):
        return False
    return all(
        other_id == entity.entity_id
        or _squared_distance(candidate, position) >= (entity_radius + other_radius - 1e-6) ** 2
        for other_id, position, other_radius, _ in local_colliders
    )


def clamp_to_collider_contact(
    simulation: Simulation,
    entity: Entity,
    candidate: Point,
    entity_radius: float,
    local_colliders: tuple[LocalCollider, ...],
) -> Point:
    direction_x = candidate.x - entity.position.x
    direction_y = candidate.y - entity.position.y
    squared_length = direction_x * direction_x + direction_y * direction_y
    if squared_length <= 1e-12:
        return candidate
    maximum_fraction = 1.0
    for other_id, other_position, other_radius, _ in local_colliders:
        if other_id == entity.entity_id:
            continue
        radius = entity_radius + other_radius
        radius_squared = radius * radius
        candidate_distance_squared = _squared_distance(candidate, other_position)
        if candidate_distance_squared >= radius_squared:
            continue
        current_distance_squared = _squared_distance(entity.position, other_position)
        if current_distance_squared <= radius_squared:
            current_distance = current_distance_squared**0.5
            if candidate_distance_squared**0.5 < current_distance - 1e-9:
                return entity.position
            continue
        offset_x = entity.position.x - other_position.x
        offset_y = entity.position.y - other_position.y
        linear = 2 * (offset_x * direction_x + offset_y * direction_y)
        constant = offset_x * offset_x + offset_y * offset_y - radius * radius
        discriminant = linear * linear - 4 * squared_length * constant
        if discriminant < 0:
            continue
        fraction = (-linear - discriminant**0.5) / (2 * squared_length)
        if 0 <= fraction <= maximum_fraction:
            maximum_fraction = max(0.0, fraction - 1e-6)
    return Point(
        entity.position.x + direction_x * maximum_fraction,
        entity.position.y + direction_y * maximum_fraction,
    )


def contact_has_stationary_blocker(
    simulation: Simulation,
    entity: Entity,
    candidate: Point,
    entity_radius: float,
    local_colliders: tuple[LocalCollider, ...],
) -> bool:
    return any(
        other_id != entity.entity_id
        and stationary
        and _squared_distance(candidate, position) < (entity_radius + other_radius) ** 2
        for other_id, position, other_radius, stationary in local_colliders
    )


def resolve_unit_collisions(simulation: Simulation, unit_index: SpatialIndex) -> None:
    unit_ids = tuple(
        entity_id for entity_id, entity in sorted(simulation.entities.items()) if entity.is_movable
    )
    active_ids = tuple(
        entity_id
        for entity_id in unit_ids
        if simulation.entities[entity_id].path
        and not simulation.entities[entity_id].congestion_stopped
    )
    for entity_id in unit_ids:
        simulation.entities[entity_id].collision_pressure = 0
    forces = {
        entity_id: simulation._unit_drive_force(simulation.entities[entity_id])
        for entity_id in unit_ids
    }
    force_pairs = unit_index.candidate_pairs_for(active_ids, 0.93)
    force_passes = (
        1
        if unit_ids
        and all(simulation.entities[entity_id].kind is EntityKind.SCOUT for entity_id in unit_ids)
        else 2
    )
    for _ in range(force_passes):
        for first_id, second_id in force_pairs:
            simulation._collision_pair_check_count += 1
            first = simulation.entities[first_id]
            second = simulation.entities[second_id]
            offset_x = second.position.x - first.position.x
            offset_y = second.position.y - first.position.y
            distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
            contact_distance = collision_radius(first.kind) + collision_radius(second.kind)
            if distance > contact_distance + 0.03:
                continue
            first.collision_pressure += 1
            second.collision_pressure += 1
            if distance <= 1e-9:
                normal_x = 1.0 if first_id < second_id else -1.0
                normal_y = 0.0
            else:
                normal_x = offset_x / distance
                normal_y = offset_y / distance
            first_force = forces[first_id]
            second_force = forces[second_id]
            first_pressure = max(0.0, first_force[0] * normal_x + first_force[1] * normal_y)
            second_pressure = max(0.0, -(second_force[0] * normal_x + second_force[1] * normal_y))
            if first_pressure > 0:
                forces[second_id] = (
                    second_force[0] + first_pressure * normal_x,
                    second_force[1] + first_pressure * normal_y,
                )
            if second_pressure > 0:
                forces[first_id] = (
                    first_force[0] - second_pressure * normal_x,
                    first_force[1] - second_pressure * normal_y,
                )
            net_pressure = first_pressure - second_pressure
            if net_pressure > 1e-9:
                simulation._apply_physical_push(
                    second,
                    normal_x,
                    normal_y,
                    net_pressure,
                    first_id,
                    unit_index,
                )
            elif net_pressure < -1e-9:
                simulation._apply_physical_push(
                    first,
                    -normal_x,
                    -normal_y,
                    -net_pressure,
                    second_id,
                    unit_index,
                )
            corrected_offset_x = second.position.x - first.position.x
            corrected_offset_y = second.position.y - first.position.y
            corrected_distance = (
                corrected_offset_x * corrected_offset_x + corrected_offset_y * corrected_offset_y
            ) ** 0.5
            if corrected_distance < contact_distance:
                overlap = contact_distance - corrected_distance
                if corrected_distance > 1e-9:
                    correction_x = corrected_offset_x / corrected_distance
                    correction_y = corrected_offset_y / corrected_distance
                else:
                    correction_x = normal_x
                    correction_y = normal_y
                total_inverse_mass = 1 / unit_mass(first.kind) + 1 / unit_mass(second.kind)
                first_share = (1 / unit_mass(first.kind)) / total_inverse_mass
                second_share = (1 / unit_mass(second.kind)) / total_inverse_mass
                simulation._apply_physical_push(
                    first,
                    -correction_x,
                    -correction_y,
                    overlap * first_share,
                    second_id,
                    unit_index,
                    correction=True,
                )
                simulation._apply_physical_push(
                    second,
                    correction_x,
                    correction_y,
                    overlap * second_share,
                    first_id,
                    unit_index,
                    correction=True,
                )
    active_id_set = frozenset(active_ids)
    collision_ids = tuple(
        entity_id
        for entity_id in unit_ids
        if entity_id in active_id_set or simulation.entities[entity_id].collision_pressure > 0
    )
    simulation._separate_overlapping_colliders(collision_ids, unit_index)


def separate_overlapping_colliders(
    simulation: Simulation,
    collision_ids: tuple[str, ...],
    unit_index: SpatialIndex,
) -> None:
    # A third bounded relaxation pass prevents dense moving fronts from preserving a deeply
    # overlapped heavy or mixed pair after pressure propagates through neighboring units. Scouts
    # can separate their full radius in one correction, so two passes avoid redundant work in the
    # 1,000-scout convergence case without weakening the mixed-mass contract.
    relaxation_passes = (
        2
        if collision_ids
        and all(
            simulation.entities[entity_id].kind is EntityKind.SCOUT for entity_id in collision_ids
        )
        else 3
    )
    for _ in range(relaxation_passes):
        changed = False
        for first_id, second_id in unit_index.candidate_pairs_for(collision_ids, 0.9):
            simulation._collision_pair_check_count += 1
            first = simulation.entities[first_id]
            second = simulation.entities[second_id]
            offset_x = second.position.x - first.position.x
            offset_y = second.position.y - first.position.y
            distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
            required = collision_radius(first.kind) + collision_radius(second.kind)
            if distance >= required - 1e-6:
                continue
            if distance <= 1e-9:
                normal_x = 1.0 if first_id < second_id else -1.0
                normal_y = 0.0
            else:
                normal_x = offset_x / distance
                normal_y = offset_y / distance
            overlap = required - distance
            total_inverse_mass = 1 / unit_mass(first.kind) + 1 / unit_mass(second.kind)
            first_share = (1 / unit_mass(first.kind)) / total_inverse_mass
            second_share = (1 / unit_mass(second.kind)) / total_inverse_mass
            changed = (
                simulation._apply_physical_push(
                    first,
                    -normal_x,
                    -normal_y,
                    overlap * first_share,
                    second_id,
                    unit_index,
                    correction=True,
                )
                or changed
            )
            changed = (
                simulation._apply_physical_push(
                    second,
                    normal_x,
                    normal_y,
                    overlap * second_share,
                    first_id,
                    unit_index,
                    correction=True,
                )
                or changed
            )
        if not changed:
            return


def unit_drive_force(simulation: Simulation, entity: Entity) -> tuple[float, float]:
    if not entity.path or entity.congestion_stopped:
        return 0.0, 0.0
    target = entity.path[0]
    offset_x = target.x - entity.position.x
    offset_y = target.y - entity.position.y
    distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
    if distance <= 1e-9:
        return 0.0, 0.0
    step = min(entity.speed * simulation.TICK_SECONDS, distance)
    force = step * unit_mass(entity.kind)
    return offset_x / distance * force, offset_y / distance * force


def apply_physical_push(
    simulation: Simulation,
    entity: Entity,
    normal_x: float,
    normal_y: float,
    pressure: float,
    pusher_id: str,
    unit_index: SpatialIndex,
    *,
    correction: bool = False,
) -> bool:
    stationary = not entity.path
    scale = 1.0 if correction else (0.35 if stationary else 0.2) / unit_mass(entity.kind)
    maximum_amount = 0.3 if correction else (0.18 if stationary else 0.12)
    amount = min(maximum_amount, pressure * scale)
    if amount <= 1e-9:
        return False
    directions = [(normal_x, normal_y)]
    if stationary and not correction:
        preferred_sign = 1 if sum(map(ord, entity.entity_id + pusher_id)) % 2 else -1
        directions.extend(
            [
                (-normal_y * preferred_sign, normal_x * preferred_sign),
                (normal_y * preferred_sign, -normal_x * preferred_sign),
            ]
        )
    position: Point | None = None
    yielded_laterally = False
    for index, (direction_x, direction_y) in enumerate(directions):
        candidate = Point(
            entity.position.x + direction_x * amount,
            entity.position.y + direction_y * amount,
        )
        if not simulation.game_map.is_passable(candidate):
            continue
        try:
            cells = simulation._cells_at(entity, candidate)
            allowed_conflicts = frozenset(
                occupant_id
                for cell in cells
                for occupant_id in simulation.occupancy.occupants(cell)
                if simulation.entities[occupant_id].is_movable
            )
            simulation.occupancy.move(
                entity.entity_id,
                cells,
                allowed_conflicts,
            )
        except OccupancyError:
            continue
        position = candidate
        yielded_laterally = index > 0
        break
    if position is None:
        return False
    previous = entity.position
    entity.position = position
    unit_index.move(entity.entity_id, position)
    if entity.entity_id not in simulation._push_events_this_tick:
        simulation._push_events_this_tick.add(entity.entity_id)
        simulation.events.record(
            simulation.tick,
            EventType.UNIT_PUSHED,
            entity.entity_id,
            pusher_id=pusher_id,
            previous_position=[previous.x, previous.y],
            position=[position.x, position.y],
            amount=amount,
            mass=unit_mass(entity.kind),
            correction=correction,
            yielded_laterally=yielded_laterally,
            pushed_was_moving=bool(entity.path),
        )
    return True


def record_movement_blocked(simulation: Simulation, entity: Entity, evidence: str) -> None:
    entity_id = entity.entity_id
    simulation._blocked_ticks[entity_id] = simulation._blocked_ticks.get(entity_id, 0) + 1
    if entity_id not in simulation._movement_blocked:
        simulation.events.record(
            simulation.tick,
            EventType.MOVEMENT_BLOCKED,
            entity_id,
            reason="LOCAL_AVOIDANCE_BLOCKED",
            evidence=evidence,
        )
        simulation._movement_blocked.add(entity_id)
    if simulation._blocked_ticks[entity_id] < simulation.TICKS_PER_SECOND:
        return
    if simulation._blocked_recoveries_this_tick >= simulation.BLOCKED_RECOVERY_BUDGET:
        return
    retry_phase = sum(map(ord, entity_id)) % simulation.CONGESTION_RETRY_TICKS
    if simulation.tick % simulation.CONGESTION_RETRY_TICKS != retry_phase:
        return
    if (
        entity.kind is not EntityKind.BUILDER
        and not simulation._final_destination_is_contested(entity)
        and not simulation._remaining_path_crosses_military_units(entity)
    ):
        return
    simulation._blocked_recoveries_this_tick += 1
    simulation._recover_blocked_entity(entity)


def final_destination_is_contested(simulation: Simulation, entity: Entity) -> bool:
    if len(entity.path) != 1:
        return False
    destination = entity.path[0]
    destination_cell = simulation.game_map.cell_for(destination)
    nearby_cells = (destination_cell, *simulation._neighbor_cells(destination_cell))
    nearby_ids = {
        occupant_id for cell in nearby_cells for occupant_id in simulation.occupancy.occupants(cell)
    }
    return any(
        other_id != entity.entity_id
        and other.is_movable
        and not other.path
        and (
            destination_cell in other.occupied_cells
            or destination.distance_to(other.position) < 0.62
        )
        for other_id in nearby_ids
        for other in (simulation.entities[other_id],)
    )


def recover_blocked_entity(simulation: Simulation, entity: Entity) -> None:
    """Choose a deterministic free sidestep, then replan to the original target."""

    destination = entity.move_target
    if destination is None:
        return
    replacement = simulation._nearest_unreserved_destination(entity, destination)
    if replacement is not None and replacement != destination:
        try:
            path = simulation._routes.dynamic_path(
                entity.position,
                replacement,
                simulation._building_cells(),
                cell_penalties=simulation._military_cell_penalties(entity.entity_id),
            )
        except PathfindingError:
            pass
        else:
            entity.move_target = replacement
            entity.path = list(path.waypoints)
            entity.path_cost = path.cost
            simulation._blocked_ticks[entity.entity_id] = 0
            simulation.events.record(
                simulation.tick,
                EventType.PATH_COMPUTED,
                entity.entity_id,
                reason="CROWDED_DESTINATION_REALLOCATED",
                target=[replacement.x, replacement.y],
            )
            return
    origin_x, origin_y = int(entity.position.x), int(entity.position.y)
    candidates: list[Point] = []
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1)):
        point = Point(origin_x + dx + 0.5, origin_y + dy + 0.5)
        if not simulation.game_map.is_passable(point):
            continue
        cells = simulation._cells_at(entity, point)
        if any(simulation.occupancy.occupants(cell) - {entity.entity_id} for cell in cells):
            continue
        candidates.append(point)
    if not candidates:
        return
    clockwise = sum(ord(character) for character in entity.entity_id) % 2
    candidates.sort(
        key=lambda point: (
            point.distance_to(destination),
            point.y if clockwise else -point.y,
            point.x if clockwise else -point.x,
        )
    )
    sidestep = candidates[0]
    try:
        path = simulation._routes.dynamic_path(
            sidestep,
            destination,
            simulation._building_cells(),
            cell_penalties=simulation._military_cell_penalties(entity.entity_id),
        )
    except PathfindingError:
        return
    entity.path = [sidestep, *path.waypoints]
    entity.path_cost = entity.position.distance_to(sidestep) + path.cost
    simulation._blocked_ticks[entity.entity_id] = 0
    simulation.events.record(
        simulation.tick,
        EventType.PATH_COMPUTED,
        entity.entity_id,
        reason="STUCK_REPLAN",
        target=[destination.x, destination.y],
    )


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y
