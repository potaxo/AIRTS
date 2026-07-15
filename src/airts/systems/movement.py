"""Deterministic unit movement, collision, and blocked-unit recovery."""

from __future__ import annotations

from math import ceil, floor
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
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell, EntityKind
from airts.world.occupancy import OccupancyError

if TYPE_CHECKING:
    from airts.simulation import Simulation


type LocalCollider = tuple[str, Point, float, bool]


def move_entities(simulation: Simulation) -> None:
    movable_entities: dict[str, Entity] = {}
    collision_radii: dict[str, float] = {}
    radius_by_kind: dict[EntityKind, float] = {}
    maximum_radius = 0.0
    for entity_id, entity in simulation.entities.items():
        if not entity.is_movable:
            continue
        movable_entities[entity_id] = entity
        entity.collision_pressure = 0
        radius = radius_by_kind.get(entity.kind)
        if radius is None:
            radius = collision_radius(entity.kind)
            radius_by_kind[entity.kind] = radius
        collision_radii[entity_id] = radius
        maximum_radius = max(maximum_radius, radius)
    movable_ids = frozenset(movable_entities)
    static_occupant_cells = simulation._building_cells()
    active_count = sum(
        bool(entity.path) or entity.congestion_stopped for entity in movable_entities.values()
    )
    continuous_topology_force = (
        len(movable_entities) > 128
        and not simulation._all_terrain_passable
        and not simulation.assignments
    )
    if (
        len(movable_entities) > 128
        and (len(movable_entities) > 160 or active_count > 128)
        and (simulation._all_terrain_passable or simulation.assignments)
    ):
        _move_large_force(
            simulation,
            movable_entities,
            movable_ids,
            static_occupant_cells,
            collision_radii,
            maximum_radius,
        )
        track_movement_progress(simulation, movable_entities)
        return
    simulation._open_force_slots = None
    if continuous_topology_force:
        collision_radii = {
            entity_id: radius * 0.91 for entity_id, radius in collision_radii.items()
        }
    active_ids = tuple(
        entity_id
        for entity_id, entity in movable_entities.items()
        if entity.path or entity.congestion_stopped
    )
    ordered_active_ids = tuple(sorted(active_ids))
    unit_index = SpatialIndex(
        {entity_id: entity.position for entity_id, entity in movable_entities.items()},
        bucket_size=1.5,
    )
    contact_ids: set[str] = set()
    for entity_id in ordered_active_ids:
        entity = movable_entities[entity_id]
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
        local_query_radius = maximum_step + entity_radius + 0.46
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
        preferred_position = desired_direct_position
        direct_position = simulation._clamp_to_collider_contact(
            entity,
            preferred_position,
            entity_radius,
            local_colliders,
        )
        direct_was_clamped = _squared_distance(direct_position, preferred_position) > 1e-18
        push_stationary_blocker = direct_was_clamped and simulation._contact_has_stationary_blocker(
            entity,
            preferred_position,
            entity_radius,
            local_colliders,
        )
        if direct_was_clamped:
            simulation._collision_pair_check_count += 1
            contact_ids.add(entity_id)
            contact_ids.update(
                other_id
                for other_id, position, other_radius, _ in local_colliders
                if other_id != entity_id
                and (
                    _squared_distance(preferred_position, position)
                    < (entity_radius + other_radius) ** 2
                    or _squared_distance(entity.position, position)
                    <= (entity_radius + other_radius + 0.03) ** 2
                )
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
            local_neighbor_ids = unit_index.nearby(entity.position, NEIGHBOR_RADIUS)
            local_colliders = tuple(
                (
                    other_id,
                    other.position,
                    collision_radii[other_id],
                    not other.path,
                )
                for other_id in local_neighbor_ids
                for other in (simulation.entities[other_id],)
            )
            neighbors = tuple(
                position for other_id, position, _, _ in local_colliders if other_id != entity_id
            )
            raw_candidates = steering_candidates(
                entity.position,
                target,
                maximum_step,
                neighbors,
            )
            for raw_candidate in raw_candidates:
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
            contact_ids.add(entity_id)
            contact_ids.update(
                other_id
                for other_id, position, other_radius, _ in local_colliders
                if other_id != entity_id
                and _squared_distance(entity.position, position)
                <= (entity_radius + other_radius + maximum_step) ** 2
            )
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
    if contact_ids and not continuous_topology_force:
        simulation._resolve_unit_collisions(unit_index, tuple(sorted(contact_ids)))
    simulation._track_movement_progress()


def _move_large_force(
    simulation: Simulation,
    movable_entities: dict[str, Entity],
    movable_ids: frozenset[str],
    static_occupant_cells: frozenset[Cell],
    collision_radii: dict[str, float],
    maximum_radius: float,
) -> None:
    """Advance a saturated force on a stable deterministic traffic lattice."""

    active_ids = tuple(entity_id for entity_id, entity in movable_entities.items() if entity.path)
    if not active_ids:
        return
    active_owner_ids = {movable_entities[entity_id].owner_id for entity_id in active_ids}
    anchored_ids = frozenset(
        entity_id
        for entity_id, entity in movable_entities.items()
        if not entity.path
        and (
            entity.owner_id not in active_owner_ids
            or (entity.state is UnitState.HOLDING and not entity.congestion_stopped)
        )
    )
    has_fixed_units = bool(anchored_ids)
    traffic_radius = max(
        radius for entity_id, radius in collision_radii.items() if entity_id not in anchored_ids
    )
    # Cell-centred maps and formations already use one-unit rows.  Keeping that exact grid for
    # scouts and light tanks means a large order does not first shuffle every identity onto an
    # unrelated fractional lattice.  Larger vehicles widen it to the accepted moving-clearance
    # envelope.
    spacing = max(1.0, traffic_radius * 2 * 0.91 + 1e-6)
    origin = 0.5 if spacing == 1.0 else spacing / 2
    if (
        not has_fixed_units
        and len(active_ids) == len(movable_entities)
        and simulation._all_terrain_passable
        and simulation._open_force_slots is None
        and _move_separated_coherent_flows(
            simulation,
            movable_entities,
            movable_ids,
            active_ids,
            static_occupant_cells,
            collision_radii=collision_radii,
        )
    ):
        return
    if (
        not has_fixed_units
        and not static_occupant_cells
        and (
            len(active_ids) == len(movable_entities)
            or len(simulation.assignments) == len(movable_entities)
        )
        and _move_coherent_force(simulation, movable_entities, movable_ids, active_ids, spacing)
    ):
        return
    traffic_ids = tuple(
        entity_id for entity_id in sorted(movable_entities) if entity_id not in anchored_ids
    )
    fixed_index = SpatialIndex(
        {
            entity_id: entity.position
            for entity_id, entity in movable_entities.items()
            if entity_id in anchored_ids
        },
        bucket_size=1.0,
    )
    route_obstacle_cells = static_occupant_cells.union(
        cell
        for entity_id in anchored_ids
        for cell in simulation._cells_at(
            movable_entities[entity_id], movable_entities[entity_id].position
        )
    )
    cached_slots = simulation._open_force_slots
    if (
        cached_slots is not None
        and abs(cached_slots[0] - spacing) <= 1e-9
        and cached_slots[1].keys() == dict.fromkeys(traffic_ids).keys()
    ):
        source_slots = cached_slots[1]
        occupant_by_slot = cached_slots[2]
    else:
        occupied: set[tuple[int, int]] = set()
        source_slots = {}
        occupant_by_slot = {}
        maximum_ring = (
            ceil(max(simulation.game_map.width, simulation.game_map.height) / spacing) + 2
        )
        direct_slots = {
            entity_id: (
                floor((movable_entities[entity_id].position.x - origin) / spacing + 0.5),
                floor((movable_entities[entity_id].position.y - origin) / spacing + 0.5),
            )
            for entity_id in traffic_ids
        }
        direct_slot_points = {
            entity_id: _large_force_slot_point(direct_slots[entity_id], spacing, origin)
            for entity_id in traffic_ids
        }
        use_direct_slots = (
            spacing == 1.0
            and simulation._all_terrain_passable
            and not has_fixed_units
            and len(set(direct_slots.values())) == len(traffic_ids)
            and all(
                simulation.game_map.contains(direct_slot_points[entity_id])
                and simulation.game_map.cell_for(direct_slot_points[entity_id])
                not in static_occupant_cells
                for entity_id in traffic_ids
            )
        )
        for entity_id in traffic_ids:
            entity = movable_entities[entity_id]
            source_slots[entity_id] = (
                direct_slots[entity_id]
                if use_direct_slots
                else _nearest_large_force_slot(
                    simulation,
                    entity,
                    entity.position,
                    spacing,
                    origin,
                    occupied,
                    fixed_index,
                    movable_entities,
                    collision_radii,
                    maximum_radius,
                    static_occupant_cells,
                    maximum_ring,
                    has_fixed_units,
                )
            )
            occupied.add(source_slots[entity_id])
            occupant_by_slot[source_slots[entity_id]] = entity_id
        simulation._open_force_slots = (spacing, source_slots, occupant_by_slot)

    ordered_ids = tuple(
        sorted(
            active_ids,
            key=lambda entity_id: (
                -unit_mass(movable_entities[entity_id].kind),
                _movement_order_key(movable_entities[entity_id]),
            ),
        )
    )
    if simulation._all_terrain_passable and not has_fixed_units and len(ordered_ids) > 512:
        ordered_ids = ordered_ids[simulation.tick % 32 :: 32]
    planned: set[str] = set()
    visiting: set[str] = set()

    def reserve_next_slot(entity_id: str, inherited_target: Point | None = None) -> bool:
        """Reserve one safe edge, recursively advancing a same-flow queue in front."""

        if entity_id in planned or entity_id in visiting:
            return False
        entity = movable_entities[entity_id]
        source_slot = source_slots[entity_id]
        source = _large_force_slot_point(source_slot, spacing, origin)
        # A reservation advances only after its owner physically reaches it.  This single rule
        # removes the old multi-cell logical lead that let identities exchange places while their
        # rendered bodies crossed through one another.
        if _squared_distance(entity.position, source) > 1e-12:
            planned.add(entity_id)
            return False
        visiting.add(entity_id)
        if entity.path:
            repath_phase = sum(map(ord, entity_id)) % simulation.DESTINATION_REPATH_TICKS
            if (
                not simulation._all_terrain_passable
                and len(entity.path) == 1
                and entity.route_ticks >= simulation.DESTINATION_REPATH_TICKS
                and entity.route_ticks % simulation.DESTINATION_REPATH_TICKS == repath_phase
                and _passable_segment_cells(simulation, entity.position, entity.path[0]) is None
            ):
                simulation._repath_stalled_entity(entity, reason="STATIC_TOPOLOGY_REPATH")
            _skip_large_force_path_prefix(simulation, entity, spacing, route_obstacle_cells)
            target = entity.path[0]
        elif inherited_target is not None:
            target = inherited_target
        else:
            visiting.remove(entity_id)
            planned.add(entity_id)
            return False
        candidates = tuple(
            sorted(
                _orthogonal_lattice_slots(source_slot),
                key=lambda slot: (
                    _squared_distance(_large_force_slot_point(slot, spacing, origin), target),
                    slot[1],
                    slot[0],
                ),
            )
        )
        chosen_slot = source_slot
        for candidate_slot in candidates:
            occupant_id = occupant_by_slot.get(candidate_slot)
            if occupant_id is not None:
                simulation._collision_pair_check_count += 1
                if occupant_id not in visiting:
                    reserve_next_slot(occupant_id, target)
                    occupant_id = occupant_by_slot.get(candidate_slot)
                if occupant_id is not None:
                    continue
            candidate = _large_force_slot_point(candidate_slot, spacing, origin)
            if not simulation.game_map.is_passable(candidate):
                continue
            if not _large_force_step_is_passable(
                simulation,
                source,
                candidate,
                static_occupant_cells,
            ):
                continue
            cells = simulation._cells_at(entity, candidate)
            if static_occupant_cells and not cells.isdisjoint(static_occupant_cells):
                continue
            if has_fixed_units and not _large_force_slot_clears_fixed_units(
                simulation,
                entity_id,
                candidate,
                fixed_index,
                movable_entities,
                collision_radii,
                maximum_radius,
            ):
                continue
            chosen_slot = candidate_slot
            break
        if chosen_slot == source_slot:
            entity.collision_pressure += 1
        if chosen_slot != source_slot:
            del occupant_by_slot[source_slot]
            occupant_by_slot[chosen_slot] = entity_id
            source_slots[entity_id] = chosen_slot
        visiting.remove(entity_id)
        planned.add(entity_id)
        return chosen_slot != source_slot

    for entity_id in ordered_ids:
        simulation._movement_step_attempt_count += 1
        movable_entities[entity_id].congestion_stopped = False
        reserve_next_slot(entity_id)

    for entity_id in traffic_ids:
        entity = movable_entities[entity_id]
        destination = _large_force_slot_point(source_slots[entity_id], spacing, origin)
        distance = entity.position.distance_to(destination)
        maximum_step = entity.speed * simulation.TICK_SECONDS
        candidate = (
            destination
            if distance <= maximum_step
            else Point(
                entity.position.x + (destination.x - entity.position.x) * maximum_step / distance,
                entity.position.y + (destination.y - entity.position.y) * maximum_step / distance,
            )
        )
        if has_fixed_units and candidate != entity.position:
            local_neighbor_ids = fixed_index.nearby(
                entity.position,
                maximum_step + collision_radii[entity_id] + maximum_radius,
            )
            local_colliders = tuple(
                (
                    other_id,
                    movable_entities[other_id].position,
                    collision_radii[other_id],
                    True,
                )
                for other_id in local_neighbor_ids
            )
            candidate = simulation._clamp_to_collider_contact(
                entity,
                candidate,
                collision_radii[entity_id],
                local_colliders,
            )
        if candidate != entity.position:
            if int(candidate.x) != int(entity.position.x) or int(candidate.y) != int(
                entity.position.y
            ):
                try:
                    simulation.occupancy.move(
                        entity_id,
                        simulation._cells_at(entity, candidate),
                        movable_ids,
                    )
                except OccupancyError as error:
                    raise RuntimeError(
                        f"large-force slot move failed for {entity_id}: {error}"
                    ) from error
            entity.position = candidate
        simulation._movement_blocked.discard(entity_id)
        simulation._blocked_ticks.pop(entity_id, None)
        if entity.path:
            _advance_large_force_path(simulation, entity, spacing)


def _move_separated_coherent_flows(
    simulation: Simulation,
    movable_entities: dict[str, Entity],
    movable_ids: frozenset[str],
    active_ids: tuple[str, ...],
    static_occupant_cells: frozenset[Cell],
    collision_radii: dict[str, float],
) -> bool:
    """Translate separated same-heading ranks continuously until their broadphases touch."""

    flow_ids: dict[tuple[str, int], list[str]] = {}
    flow_bounds: dict[tuple[str, int], list[float]] = {}
    candidates: dict[str, Point] = {}
    directions: dict[str, tuple[float, float]] = {}
    for entity_id in active_ids:
        entity = movable_entities[entity_id]
        target = entity.path[0]
        offset_x = target.x - entity.position.x
        offset_y = target.y - entity.position.y
        distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
        if distance <= 1e-9:
            return False
        direction_x = offset_x / distance
        direction_y = offset_y / distance
        directions[entity_id] = (direction_x, direction_y)
        key = (
            ("x", 1 if direction_x > 0 else -1)
            if abs(direction_x) >= abs(direction_y)
            else ("y", 1 if direction_y > 0 else -1)
        )
        flow_ids.setdefault(key, []).append(entity_id)
        maximum_step = entity.speed * simulation.TICK_SECONDS
        candidate = (
            target
            if distance <= maximum_step
            else Point(
                entity.position.x + direction_x * maximum_step,
                entity.position.y + direction_y * maximum_step,
            )
        )
        candidates[entity_id] = candidate
        bounds = flow_bounds.get(key)
        if bounds is None:
            flow_bounds[key] = [candidate.x, candidate.x, candidate.y, candidate.y]
        else:
            if candidate.x < bounds[0]:
                bounds[0] = candidate.x
            if candidate.x > bounds[1]:
                bounds[1] = candidate.x
            if candidate.y < bounds[2]:
                bounds[2] = candidate.y
            if candidate.y > bounds[3]:
                bounds[3] = candidate.y
    if len(flow_ids) > 2:
        return False
    flow_items = tuple(
        (tuple(sorted(ids)), tuple(flow_bounds[key])) for key, ids in sorted(flow_ids.items())
    )
    flows = tuple(item[0] for item in flow_items)
    for ids in flows:
        reference_x, reference_y = directions[ids[0]]
        if any(
            reference_x * directions[entity_id][0] + reference_y * directions[entity_id][1] < 0.80
            for entity_id in ids[1:]
        ):
            return False
    if len(flows) == 2 and _coherent_flows_contact(
        simulation,
        candidates,
        flows[0],
        flows[1],
        flow_items[0][1],
        flow_items[1][1],
        collision_radii,
    ):
        return False
    simulation._open_force_slots = None
    for entity_id in sorted(active_ids):
        simulation._movement_step_attempt_count += 1
        entity = movable_entities[entity_id]
        candidate = candidates[entity_id]
        if int(candidate.x) != int(entity.position.x) or int(candidate.y) != int(entity.position.y):
            try:
                simulation.occupancy.move(
                    entity_id,
                    simulation._cells_at(entity, candidate),
                    movable_ids,
                )
            except OccupancyError as error:
                raise RuntimeError(f"coherent-flow move failed for {entity_id}: {error}") from error
        entity.position = candidate
        simulation._movement_blocked.discard(entity_id)
        simulation._blocked_ticks.pop(entity_id, None)
        _advance_large_force_path(simulation, entity, 1.0)
    return True


def _flow_bounds_are_near(
    first: tuple[float, ...],
    second: tuple[float, ...],
) -> bool:
    gap_x = max(0.0, second[0] - first[1], first[0] - second[1])
    gap_y = max(0.0, second[2] - first[3], first[2] - second[3])
    return gap_x * gap_x + gap_y * gap_y <= NEIGHBOR_RADIUS * NEIGHBOR_RADIUS


def _coherent_flows_contact(
    simulation: Simulation,
    candidates: dict[str, Point],
    first_ids: tuple[str, ...],
    second_ids: tuple[str, ...],
    first_bounds: tuple[float, ...],
    second_bounds: tuple[float, ...],
    collision_radii: dict[str, float],
) -> bool:
    if not _flow_bounds_are_near(first_bounds, second_bounds):
        return False
    first_min_x, first_max_x, first_min_y, first_max_y = first_bounds
    second_min_x, second_max_x, second_min_y, second_max_y = second_bounds
    center_gap_x = abs((first_min_x + first_max_x) / 2 - (second_min_x + second_max_x) / 2)
    center_gap_y = abs((first_min_y + first_max_y) / 2 - (second_min_y + second_max_y) / 2)
    if center_gap_x >= center_gap_y:
        boundary = (
            (first_max_x + second_min_x) / 2
            if first_min_x < second_min_x
            else (second_max_x + first_min_x) / 2
        )
        first_boundary_ids = tuple(
            entity_id
            for entity_id in first_ids
            if abs(candidates[entity_id].x - boundary) <= NEIGHBOR_RADIUS
        )
        second_boundary_ids = tuple(
            entity_id
            for entity_id in second_ids
            if abs(candidates[entity_id].x - boundary) <= NEIGHBOR_RADIUS
        )
    else:
        boundary = (
            (first_max_y + second_min_y) / 2
            if first_min_y < second_min_y
            else (second_max_y + first_min_y) / 2
        )
        first_boundary_ids = tuple(
            entity_id
            for entity_id in first_ids
            if abs(candidates[entity_id].y - boundary) <= NEIGHBOR_RADIUS
        )
        second_boundary_ids = tuple(
            entity_id
            for entity_id in second_ids
            if abs(candidates[entity_id].y - boundary) <= NEIGHBOR_RADIUS
        )
    first_index = SpatialIndex(
        {entity_id: candidates[entity_id] for entity_id in first_boundary_ids},
        bucket_size=1.5,
    )
    contact = False
    for second_id in second_boundary_ids:
        second_position = candidates[second_id]
        for first_id in first_index.nearby(second_position, NEIGHBOR_RADIUS):
            simulation._collision_pair_check_count += 1
            if second_position.distance_to(candidates[first_id]) < (
                collision_radii[second_id] + collision_radii[first_id]
            ):
                contact = True
    return contact


def _move_coherent_force(
    simulation: Simulation,
    movable_entities: dict[str, Entity],
    movable_ids: frozenset[str],
    active_ids: tuple[str, ...],
    spacing: float,
) -> bool:
    """Translate one unobstructed flow continuously without reassigning unit identities."""

    directions: list[tuple[float, float]] = []
    for entity_id in active_ids:
        entity = movable_entities[entity_id]
        target = entity.path[0]
        offset_x = target.x - entity.position.x
        offset_y = target.y - entity.position.y
        distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
        if distance <= 1e-9:
            continue
        directions.append((offset_x / distance, offset_y / distance))
    if not directions:
        return False
    reference_x, reference_y = directions[0]
    if any(
        reference_x * direction_x + reference_y * direction_y < 0.80
        for direction_x, direction_y in directions[1:]
    ):
        return False

    candidates: dict[str, Point] = {}
    for entity_id in sorted(active_ids):
        entity = movable_entities[entity_id]
        target = entity.path[0]
        distance = entity.position.distance_to(target)
        maximum_step = entity.speed * simulation.TICK_SECONDS
        candidate = (
            target
            if distance <= maximum_step
            else Point(
                entity.position.x + (target.x - entity.position.x) * maximum_step / distance,
                entity.position.y + (target.y - entity.position.y) * maximum_step / distance,
            )
        )
        if not simulation.game_map.is_passable(candidate):
            return False
        candidates[entity_id] = candidate

    simulation._open_force_slots = None
    for entity_id in sorted(active_ids):
        simulation._movement_step_attempt_count += 1
        entity = movable_entities[entity_id]
        candidate = candidates[entity_id]
        if candidate != entity.position:
            if int(candidate.x) != int(entity.position.x) or int(candidate.y) != int(
                entity.position.y
            ):
                try:
                    simulation.occupancy.move(
                        entity_id,
                        simulation._cells_at(entity, candidate),
                        movable_ids,
                    )
                except OccupancyError as error:
                    raise RuntimeError(
                        f"coherent-force move failed for {entity_id}: {error}"
                    ) from error
            entity.position = candidate
        simulation._movement_blocked.discard(entity_id)
        simulation._blocked_ticks.pop(entity_id, None)
        _advance_large_force_path(simulation, entity, spacing)
    return True


def _advance_large_force_path(
    simulation: Simulation,
    entity: Entity,
    spacing: float,
) -> None:
    reach = spacing * 0.75
    reach_squared = reach * reach
    while (
        len(entity.path) > 1 and _squared_distance(entity.position, entity.path[0]) <= reach_squared
    ):
        entity.path.pop(0)
    final_reach_squared = (
        (spacing * 0.15) ** 2 if entity.entity_id in simulation.assignments else 1e-12
    )
    if entity.path and _squared_distance(entity.position, entity.path[0]) <= final_reach_squared:
        entity.path.pop(0)
    if entity.path:
        return
    entity.move_target = None
    entity.state = simulation._state_for_assignment(entity.entity_id)
    simulation.events.record(
        simulation.tick,
        EventType.MOVEMENT_COMPLETED,
        entity.entity_id,
        position=[entity.position.x, entity.position.y],
        assignment=simulation.assignments.get(entity.entity_id),
    )


def _skip_large_force_path_prefix(
    simulation: Simulation,
    entity: Entity,
    spacing: float,
    route_obstacle_cells: frozenset[Cell],
) -> None:
    """Discard route cells already bypassed by collision-safe traffic circulation."""

    if len(entity.path) <= 1:
        return
    if simulation._all_terrain_passable and not route_obstacle_cells:
        del entity.path[:-1]
        return
    first_distance = _squared_distance(entity.position, entity.path[0])
    lookahead = min(len(entity.path), 32)
    best_index = min(
        range(lookahead),
        key=lambda index: (_squared_distance(entity.position, entity.path[index]), index),
    )
    if not best_index:
        return
    best = entity.path[best_index]
    if _squared_distance(entity.position, best) >= first_distance - (spacing * 0.5) ** 2:
        return
    segment_cells = _passable_segment_cells(simulation, entity.position, best)
    if segment_cells is not None and route_obstacle_cells.isdisjoint(segment_cells[1:]):
        del entity.path[:best_index]


def _nearest_large_force_slot(
    simulation: Simulation,
    entity: Entity,
    desired: Point,
    spacing: float,
    origin: float,
    occupied: set[tuple[int, int]],
    fixed_index: SpatialIndex,
    movable_entities: dict[str, Entity],
    collision_radii: dict[str, float],
    maximum_radius: float,
    static_occupant_cells: frozenset[Cell],
    maximum_ring: int,
    has_fixed_units: bool,
) -> tuple[int, int]:
    center = (
        round((desired.x - origin) / spacing),
        round((desired.y - origin) / spacing),
    )
    center_point = _large_force_slot_point(center, spacing, origin)
    if (
        center not in occupied
        and center_point == desired
        and (
            not has_fixed_units
            or _large_force_slot_clears_fixed_units(
                simulation,
                entity.entity_id,
                desired,
                fixed_index,
                movable_entities,
                collision_radii,
                maximum_radius,
            )
        )
    ):
        return center
    for ring in range(maximum_ring + 1):
        slots = sorted(
            _lattice_ring(center, ring),
            key=lambda slot: (
                _large_force_slot_point(slot, spacing, origin).distance_to(desired),
                slot[1],
                slot[0],
            ),
        )
        for slot in slots:
            if slot in occupied:
                continue
            candidate = _large_force_slot_point(slot, spacing, origin)
            if not simulation.game_map.is_passable(candidate):
                continue
            cells = simulation._cells_at(entity, candidate)
            if static_occupant_cells and not cells.isdisjoint(static_occupant_cells):
                continue
            if not has_fixed_units or _large_force_slot_clears_fixed_units(
                simulation,
                entity.entity_id,
                candidate,
                fixed_index,
                movable_entities,
                collision_radii,
                maximum_radius,
            ):
                return slot
    raise RuntimeError(f"no collision-safe traffic slot for {entity.entity_id}")


def _large_force_step_is_passable(
    simulation: Simulation,
    source: Point,
    candidate: Point,
    static_occupant_cells: frozenset[Cell],
) -> bool:
    source_cell = simulation.game_map.cell_for(source)
    candidate_cell = simulation.game_map.cell_for(candidate)
    if source_cell == candidate_cell:
        return True
    if source_cell[0] != candidate_cell[0] and source_cell[1] != candidate_cell[1]:
        corners = (
            (candidate_cell[0], source_cell[1]),
            (source_cell[0], candidate_cell[1]),
        )
        if any(
            not simulation.game_map.is_cell_passable(corner) or corner in static_occupant_cells
            for corner in corners
        ):
            return False
    return True


def _large_force_slot_clears_fixed_units(
    simulation: Simulation,
    entity_id: str,
    candidate: Point,
    fixed_index: SpatialIndex,
    movable_entities: dict[str, Entity],
    collision_radii: dict[str, float],
    maximum_radius: float,
) -> bool:
    neighbors = fixed_index.nearby(
        candidate,
        collision_radii[entity_id] + maximum_radius,
    )
    simulation._collision_pair_check_count += len(neighbors)
    return all(
        candidate.distance_to(movable_entities[other_id].position)
        >= collision_radii[entity_id] + collision_radii[other_id] - 1e-6
        for other_id in neighbors
    )


def _large_force_slot_point(
    slot: tuple[int, int],
    spacing: float,
    origin: float,
) -> Point:
    return Point(origin + slot[0] * spacing, origin + slot[1] * spacing)


def _orthogonal_lattice_slots(slot: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    """Return non-crossing reservation edges around one traffic vertex."""

    return (
        (slot[0] - 1, slot[1]),
        (slot[0] + 1, slot[1]),
        (slot[0], slot[1] - 1),
        (slot[0], slot[1] + 1),
    )


def _lattice_ring(center: tuple[int, int], ring: int) -> tuple[tuple[int, int], ...]:
    if ring == 0:
        return (center,)
    slots = [(center[0] + offset, center[1] - ring) for offset in range(-ring, ring + 1)]
    slots.extend((center[0] + offset, center[1] + ring) for offset in range(-ring, ring + 1))
    slots.extend((center[0] - ring, center[1] + offset) for offset in range(-ring + 1, ring))
    slots.extend((center[0] + ring, center[1] + offset) for offset in range(-ring + 1, ring))
    return tuple(slots)


def track_movement_progress(
    simulation: Simulation,
    movable_entities: dict[str, Entity] | None = None,
) -> None:
    """Temporarily yield orders that are not getting closer to their waypoint."""

    large_force = (
        movable_entities is not None
        or sum(entity.is_movable for entity in simulation.entities.values()) > 128
    )
    if large_force:
        entities = (
            simulation.entities.values() if movable_entities is None else movable_entities.values()
        )
        for entity in entities:
            if entity.path:
                entity.route_ticks += 1
            elif (
                entity.progress_target is not None
                or entity.progress_distance is not None
                or entity.no_progress_ticks
                or entity.route_ticks
            ):
                simulation._reset_movement_liveness(entity)
        return
    for entity_id in sorted(simulation.entities):
        entity = simulation.entities[entity_id]
        if not entity.path:
            if (
                entity.progress_target is not None
                or entity.progress_distance is not None
                or entity.no_progress_ticks
                or entity.route_ticks
            ):
                simulation._reset_movement_liveness(entity)
            continue
        entity.route_ticks += 1
        repath_phase = sum(map(ord, entity_id)) % simulation.DESTINATION_REPATH_TICKS
        if (
            not large_force
            and entity.route_ticks >= simulation.DESTINATION_REPATH_TICKS
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
        if large_force:
            entity.no_progress_ticks = 0
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


def simplify_waypoints(
    simulation: Simulation,
    start: Point,
    waypoints: tuple[Point, ...],
    path_cost: float,
) -> tuple[Point, ...]:
    """Remove grid-center funneling while retaining every static topology constraint."""

    if len(waypoints) <= 1:
        return waypoints
    direct_cells = _passable_segment_cells(simulation, start, waypoints[-1])
    if direct_cells is not None:
        direct_cost = sum(
            simulation.game_map.terrain_at_cell(cell).movement_cost for cell in direct_cells[1:]
        )
        if direct_cost <= path_cost + 1e-9:
            return (waypoints[-1],)

    simplified: list[Point] = []
    previous = start
    previous_axis: tuple[bool, bool] | None = None
    for waypoint in waypoints:
        axis = (abs(waypoint.x - previous.x) > 1e-9, abs(waypoint.y - previous.y) > 1e-9)
        if previous_axis is not None and axis != previous_axis:
            simplified.append(previous)
        previous_axis = axis
        previous = waypoint
    if not simplified or simplified[-1] != waypoints[-1]:
        simplified.append(waypoints[-1])
    return tuple(simplified)


def _passable_segment_cells(
    simulation: Simulation,
    start: Point,
    end: Point,
) -> tuple[Cell, ...] | None:
    distance = start.distance_to(end)
    sample_count = max(1, int(distance * 4))
    blocked = simulation._building_cells()
    cells: list[Cell] = [simulation.game_map.cell_for(start)]
    for index in range(1, sample_count + 1):
        fraction = index / sample_count
        point = Point(
            start.x + (end.x - start.x) * fraction,
            start.y + (end.y - start.y) * fraction,
        )
        cell = simulation.game_map.cell_for(point)
        if cell == cells[-1]:
            continue
        previous = cells[-1]
        if cell[0] != previous[0] and cell[1] != previous[1]:
            corners = ((cell[0], previous[1]), (previous[0], cell[1]))
            if any(
                not simulation.game_map.is_cell_passable(corner) or corner in blocked
                for corner in corners
            ):
                return None
        if not simulation.game_map.is_cell_passable(cell) or cell in blocked:
            return None
        cells.append(cell)
    return tuple(cells)


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
        or (
            _squared_distance(entity.position, position)
            < (entity_radius + other_radius - 1e-6) ** 2
            and _squared_distance(candidate, position)
            >= _squared_distance(entity.position, position) - 1e-9
        )
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


def resolve_unit_collisions(
    simulation: Simulation,
    unit_index: SpatialIndex,
    contact_ids: tuple[str, ...] | None = None,
) -> None:
    unit_ids = tuple(
        entity_id for entity_id, entity in sorted(simulation.entities.items()) if entity.is_movable
    )
    moving_ids = tuple(
        entity_id
        for entity_id in unit_ids
        if simulation.entities[entity_id].path
        and not simulation.entities[entity_id].congestion_stopped
    )
    active_ids = moving_ids if contact_ids is None else contact_ids
    if not active_ids:
        return
    force_pairs = unit_index.candidate_pairs_for(active_ids, 0.93)
    participating_ids = frozenset(item for pair in force_pairs for item in pair)
    forces = {
        entity_id: simulation._unit_drive_force(simulation.entities[entity_id])
        for entity_id in participating_ids
    }
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
            first_fixed = _unit_is_fixed_against(simulation, first, second)
            second_fixed = _unit_is_fixed_against(simulation, second, first)
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
            if net_pressure > 1e-9 and not second_fixed:
                simulation._apply_physical_push(
                    second,
                    normal_x,
                    normal_y,
                    net_pressure,
                    first_id,
                    unit_index,
                )
            elif net_pressure < -1e-9 and not first_fixed:
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
                first_share, second_share = _correction_shares(
                    first,
                    second,
                    first_fixed,
                    second_fixed,
                )
                if first_share:
                    simulation._apply_physical_push(
                        first,
                        -correction_x,
                        -correction_y,
                        overlap * first_share,
                        second_id,
                        unit_index,
                        correction=True,
                    )
                if second_share:
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


def relax_unit_spacing(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    required_spacing: float,
    center: Point,
    maximum_radius: float,
) -> None:
    """Apply bounded deterministic corrections until a formation has safe clearance."""

    unit_index = SpatialIndex(
        {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids},
        bucket_size=1.5,
    )
    allowed_conflicts = frozenset(entity_ids)
    projection_radius = max(0.0, maximum_radius - 1e-6)
    for _ in range(6):
        changed = False
        for first_id, second_id in unit_index.candidate_pairs(required_spacing):
            first = simulation.entities[first_id]
            second = simulation.entities[second_id]
            offset_x = second.position.x - first.position.x
            offset_y = second.position.y - first.position.y
            distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
            if distance >= required_spacing - 1e-6:
                continue
            if distance <= 1e-9:
                normal_x = 1.0 if first_id < second_id else -1.0
                normal_y = 0.0
            else:
                normal_x = offset_x / distance
                normal_y = offset_y / distance
            correction = required_spacing - distance
            changed = (
                simulation._apply_physical_push(
                    first,
                    -normal_x,
                    -normal_y,
                    correction / 2,
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
                    correction / 2,
                    first_id,
                    unit_index,
                    correction=True,
                )
                or changed
            )
        projected = False
        for entity_id in sorted(entity_ids):
            entity = simulation.entities[entity_id]
            offset_x = entity.position.x - center.x
            offset_y = entity.position.y - center.y
            distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
            if distance <= projection_radius:
                continue
            candidate = Point(
                center.x + offset_x * projection_radius / distance,
                center.y + offset_y * projection_radius / distance,
            )
            if not simulation.game_map.is_passable(candidate):
                continue
            try:
                simulation.occupancy.move(
                    entity_id,
                    simulation._cells_at(entity, candidate),
                    allowed_conflicts,
                )
            except OccupancyError:
                continue
            entity.position = candidate
            unit_index.move(entity_id, candidate)
            projected = True
        if not changed and not projected:
            break


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
            first_fixed = _unit_is_fixed_against(simulation, first, second)
            second_fixed = _unit_is_fixed_against(simulation, second, first)
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
            first_share, second_share = _correction_shares(
                first,
                second,
                first_fixed,
                second_fixed,
            )
            if first_share:
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
            if second_share:
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


def _unit_is_fixed_against(simulation: Simulation, entity: Entity, other: Entity) -> bool:
    """Return whether collision response must preserve this unit's exact position."""

    return (entity.state is UnitState.HOLDING and not entity.congestion_stopped) or (
        len(simulation.entities) > 128 and not entity.path and entity.owner_id != other.owner_id
    )


def _correction_shares(
    first: Entity,
    second: Entity,
    first_fixed: bool,
    second_fixed: bool,
) -> tuple[float, float]:
    if first_fixed and second_fixed:
        return 0.0, 0.0
    if first_fixed:
        return 0.0, 1.0
    if second_fixed:
        return 1.0, 0.0
    first_inverse_mass = 1 / unit_mass(first.kind)
    second_inverse_mass = 1 / unit_mass(second.kind)
    total_inverse_mass = first_inverse_mass + second_inverse_mass
    return first_inverse_mass / total_inverse_mass, second_inverse_mass / total_inverse_mass


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
    pusher = simulation.entities[pusher_id]
    if _unit_is_fixed_against(simulation, entity, pusher):
        return False
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


def _movement_order_key(entity: Entity) -> tuple[int, float, str]:
    """Move the front of each deterministic flow before its following ranks."""

    if not entity.path:
        return 1, 0.0, entity.entity_id
    return 0, entity.position.distance_to(entity.path[0]), entity.entity_id
