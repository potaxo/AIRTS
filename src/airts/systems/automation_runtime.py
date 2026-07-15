"""Persistent automation scheduling and deterministic behavior execution."""

from __future__ import annotations

from collections import deque
from functools import lru_cache
from typing import TYPE_CHECKING

from airts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    PatrolParameters,
    ReinforcementParameters,
    RepairParameters,
    RepairPhase,
    assign_formation_slots,
    build_patrol_waypoints,
    patrol_formation_waypoint,
    target_center,
)
from airts.events import EventType
from airts.geometry import Point, PointTarget, PolylineTarget, SpatialTarget
from airts.navigation.movement import SETTLED_FORMATION_SPACING, collision_radius
from airts.navigation.pathfinding import PathfindingError, PathResult
from airts.navigation.spatial_index import SpatialIndex
from airts.world.entities import UnitState
from airts.world.map_model import Cell, EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


def drive_automations(simulation: Simulation) -> None:
    automation_ids = sorted(
        automation_id
        for automation_id, automation in simulation.automations.items()
        if automation.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING}
    )
    if automation_ids:
        offset = simulation.tick % len(automation_ids)
        automation_ids = automation_ids[offset:] + automation_ids[:offset]
    for automation_id in automation_ids:
        automation = simulation.automations[automation_id]
        if automation.kind is AutomationKind.PATROL:
            simulation._drive_patrol(automation)
        elif automation.kind is AutomationKind.DEFEND:
            simulation._drive_defend(automation)
        elif automation.kind is AutomationKind.PRODUCTION:
            simulation._drive_production(automation)
        elif automation.kind is AutomationKind.REINFORCEMENT:
            simulation._drive_reinforcement(automation)
        elif automation.kind is AutomationKind.ECONOMY:
            simulation._drive_economy(automation)
        elif automation.kind is AutomationKind.CONSTRUCTION:
            simulation._drive_construction(automation)
        else:
            simulation._drive_repair(automation)


def scheduled_entity_ids(simulation: Simulation, automation: Automation) -> tuple[str, ...]:
    """Rotate deterministic work order so deferred large groups make fair progress."""

    entity_ids = automation.entity_ids
    if not entity_ids:
        return ()
    stable_offset = sum(ord(character) for character in automation.automation_id)
    offset = (simulation.tick + stable_offset) % len(entity_ids)
    return tuple(entity_ids[offset:] + entity_ids[:offset])


def drive_patrol(simulation: Simulation, automation: Automation) -> None:
    building_cells = simulation._building_cells()
    parameters = _patrol_parameters(automation)
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    ordered_ids = tuple(sorted(automation.entity_ids))
    formation_indices = {entity_id: index for index, entity_id in enumerate(ordered_ids)}
    area_slots: dict[int, tuple[Point, ...]] = {}
    area_unit_radius: float | None = None
    if len(ordered_ids) > 1 and not isinstance(parameters.target, PolylineTarget):
        area_unit_radius = max(
            collision_radius(simulation.entities[entity_id].kind) for entity_id in ordered_ids
        )
    for entity_id in simulation._scheduled_entity_ids(automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        if entity.move_target is not None or entity.path:
            continue
        if not allowance.claim():
            continue
        waypoint_index = parameters.waypoint_indices[entity_id]
        automation.take_next_waypoint(entity_id)
        target = patrol_formation_waypoint(
            parameters,
            tuple(automation.entity_ids),
            entity_id,
            waypoint_index,
            simulation.game_map,
            formation_indices.get(entity_id),
        )
        formation_anchor: Point | None = None
        if area_unit_radius is not None:
            slots = area_slots.get(waypoint_index)
            if slots is None:
                slots = simulation._gathering_slots(
                    PointTarget(parameters.waypoints[waypoint_index], radius=0.01),
                    len(ordered_ids),
                    area_unit_radius,
                )
                area_slots[waypoint_index] = slots
            target = slots[formation_indices[entity_id]]
            formation_anchor = slots[0]
        try:
            if area_unit_radius is not None:
                assert formation_anchor is not None
                path = _formation_path(
                    simulation,
                    entity.position,
                    target,
                    formation_anchor,
                    building_cells,
                    branch_distance=16 if area_unit_radius < 0.4 else 10,
                )
            else:
                path = simulation._routes.shared_path(entity.position, target, building_cells)
        except PathfindingError as error:
            simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
            simulation.events.record(
                simulation.tick,
                EventType.PATHFINDING_FAILED,
                entity_id,
                reason=str(error),
                automation_id=automation.automation_id,
            )
            return
        simulation._start_path(entity, target, path, automation.automation_id, UnitState.PATROLLING)


def _formation_path(
    simulation: Simulation,
    start: Point,
    target: Point,
    anchor: Point,
    building_cells: frozenset[Cell],
    *,
    branch_distance: int,
) -> PathResult:
    shared = simulation._routes.shared_path(start, anchor, building_cells)
    if anchor == target:
        return shared
    target_cell = simulation.game_map.cell_for(target)
    branch_index = next(
        (
            index
            for index, cell in enumerate(shared.cells)
            if abs(cell[0] - target_cell[0]) + abs(cell[1] - target_cell[1]) <= branch_distance
        ),
        len(shared.cells) - 1,
    )
    junction = start if branch_index == 0 else shared.waypoints[branch_index - 1]
    local = simulation._routes.local_path(junction, target, building_cells)
    prefix_cost = sum(
        simulation.game_map.terrain_at_cell(cell).movement_cost
        for cell in shared.cells[1 : branch_index + 1]
    )
    return PathResult(
        shared.cells[: branch_index + 1] + local.cells[1:],
        shared.waypoints[:branch_index] + local.waypoints,
        prefix_cost + local.cost,
    )


def drive_defend(simulation: Simulation, automation: Automation) -> None:
    parameters = _defend_parameters(automation)
    formation_center = target_center(parameters.target)
    building_cells = simulation._building_cells()
    allowance = simulation._routes.automation_allowance(
        simulation.GATHERING_PATH_BUDGET
        if parameters.gathering_point
        else simulation.AUTOMATION_ROUTE_BUDGET
    )
    assigned_ids = tuple(
        entity_id
        for entity_id in automation.entity_ids
        if simulation.assignments.get(entity_id) == automation.automation_id
    )
    shared_ids = _shared_defend_entity_ids(simulation, automation, parameters.target)
    saturated_formation = bool(parameters.deployment_slots) and len(shared_ids) > 128
    shared_assembly_radius = max(
        (
            other.parameters.assembly_radius
            for other in simulation.automations.values()
            if other.kind is AutomationKind.DEFEND
            and other.owner_id == automation.owner_id
            and not other.status.terminal
            and isinstance(other.parameters, DefendParameters)
            and other.parameters.target == parameters.target
        ),
        default=parameters.assembly_radius,
    )
    formation_age = simulation.tick - automation.created_tick
    formation_index: SpatialIndex | None = None
    clearance_index: SpatialIndex | None = None
    station_anchor: Point | None = None
    formation_cluster_size = 5
    formation_slots = parameters.deployment_slots
    if not formation_slots and len(assigned_ids) > 32:
        formation_slots = tuple(parameters.stations[entity_id] for entity_id in assigned_ids)
    if formation_slots:
        formation_cluster_size = (
            4
            if len(assigned_ids) > 128
            and all(
                simulation.entities[entity_id].kind is EntityKind.SCOUT
                for entity_id in assigned_ids
            )
            else 5
        )
        station_anchor = formation_center
    attacked: list[tuple[str, str]] = []
    for victim_id in (
        entity_id
        for entity_id in assigned_ids
        if simulation.entities[entity_id].last_attacker_id is not None
    ):
        victim = simulation.entities[victim_id]
        attacker = simulation.entities.get(victim.last_attacker_id or "")
        attacked_tick = victim.last_attacked_tick
        if (
            attacker is None
            or attacker.owner_id == automation.owner_id
            or attacked_tick is None
            or simulation.tick - attacked_tick > simulation.DEFEND_ATTACK_MEMORY_TICKS
            or attacker.position.distance_to(parameters.stations[victim_id])
            > simulation.DEFEND_PURSUIT_RADIUS
        ):
            victim.last_attacker_id = None
            victim.last_attacked_tick = None
            continue
        attacked.append((victim_id, attacker.entity_id))
    if attacked:
        formation_index = SpatialIndex(
            {entity_id: simulation.entities[entity_id].position for entity_id in assigned_ids}
        )
        responder_index = formation_index
    for victim_id, attacker_id in attacked:
        victim = simulation.entities[victim_id]
        attacker = simulation.entities[attacker_id]
        for responder_id in responder_index.nearby(
            victim.position,
            simulation.DEFEND_RESPONSE_RADIUS,
        ):
            responder = simulation.entities[responder_id]
            if (
                attacker.position.distance_to(parameters.stations[responder_id])
                > simulation.DEFEND_PURSUIT_RADIUS
            ):
                continue
            if responder.attack_target_id != attacker.entity_id or not responder.pursue_target:
                responder.path.clear()
                responder.move_target = None
                simulation._reset_movement_liveness(responder, clear_stop=True)
                responder.attack_target_id = attacker.entity_id
                responder.pursue_target = True
                responder.state = UnitState.ATTACKING
                simulation.events.record(
                    simulation.tick,
                    EventType.DEFEND_ENGAGED,
                    responder_id,
                    automation_id=automation.automation_id,
                    victim_id=victim_id,
                    attacker_id=attacker.entity_id,
                )

    for entity_id in simulation._scheduled_entity_ids(automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        station = parameters.stations[entity_id]
        station_distance = entity.position.distance_to(station)
        center_distance_squared = (entity.position.x - formation_center.x) ** 2 + (
            entity.position.y - formation_center.y
        ) ** 2
        target = simulation.entities.get(entity.attack_target_id or "")
        if target is not None and target.owner_id != automation.owner_id:
            if target.position.distance_to(station) <= simulation.DEFEND_PURSUIT_RADIUS:
                entity.state = UnitState.ATTACKING
                continue
            entity.last_attacker_id = None
            entity.last_attacked_tick = None
        if entity.attack_target_id is not None:
            entity.attack_target_id = None
            entity.pursue_target = False
            entity.path.clear()
            entity.move_target = None
            simulation._reset_movement_liveness(entity, clear_stop=True)
        elif entity.path:
            continue
        inside_formation = (
            center_distance_squared
            <= (shared_assembly_radius + simulation.DEFEND_FORMATION_TOLERANCE) ** 2
        )
        inside_formation_core = center_distance_squared <= shared_assembly_radius**2
        formation_congested = entity.congestion_stopped or (
            entity.collision_pressure > 0
            and entity.route_ticks >= simulation.NO_PROGRESS_YIELD_TICKS
        )
        formation_mature = formation_age >= simulation.DEFEND_FORMATION_SETTLE_TICKS
        safe_current_station = False
        if (
            saturated_formation
            and inside_formation
            and station_distance <= simulation.DEFEND_FORMATION_TOLERANCE
        ):
            if clearance_index is None:
                clearance_index = SpatialIndex(
                    {shared_id: simulation.entities[shared_id].position for shared_id in shared_ids}
                )
            safe_current_station = _position_has_formation_clearance(
                clearance_index, simulation, entity_id
            )
        relaxed_arrival = (
            formation_mature
            and station_distance <= simulation.DEFEND_FORMATION_TOLERANCE
            and entity.collision_pressure == 0
            and safe_current_station
        )
        if (
            saturated_formation
            and inside_formation
            and station_distance <= simulation.DEFEND_FORMATION_TOLERANCE
            and safe_current_station
            and ((formation_congested and inside_formation_core) or relaxed_arrival)
        ):
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            simulation._reset_movement_liveness(entity, clear_stop=True)
            continue
        station_tolerance = simulation.DEFEND_STATION_TOLERANCE
        if station_distance <= station_tolerance and (not saturated_formation or inside_formation):
            if saturated_formation:
                assert clearance_index is not None
                if not _position_has_formation_clearance(clearance_index, simulation, entity_id):
                    continue
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            simulation._reset_movement_liveness(entity, clear_stop=True)
            continue
        if entity.path:
            continue
        if not allowance.claim():
            continue
        route_station = (
            _defend_arrival_point(
                entity.position,
                station,
                formation_center,
                simulation.DEFEND_FORMATION_TOLERANCE * 0.85,
            )
            if saturated_formation
            else station
        )
        if saturated_formation and _straight_route_is_clear(
            simulation, entity.position, route_station, building_cells
        ):
            start_cell = simulation.game_map.cell_for(entity.position)
            destination_cell = simulation.game_map.cell_for(route_station)
            path = PathResult(
                (
                    (start_cell,)
                    if start_cell == destination_cell
                    else (start_cell, destination_cell)
                ),
                (route_station,),
                entity.position.distance_to(route_station),
            )
        else:
            try:
                if station_anchor is not None:
                    path = _formation_path(
                        simulation,
                        entity.position,
                        route_station,
                        station_anchor,
                        building_cells,
                        branch_distance=formation_cluster_size * 2,
                    )
                else:
                    path = simulation._routes.shared_path(
                        entity.position, route_station, building_cells
                    )
            except PathfindingError as error:
                simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
                return
        simulation._start_path(entity, station, path, automation.automation_id, UnitState.DEFENDING)
        simulation.events.record(
            simulation.tick,
            EventType.DEFEND_RETURNED,
            entity_id,
            automation_id=automation.automation_id,
            station=[station.x, station.y],
        )


def _defend_arrival_point(
    position: Point,
    station: Point,
    center: Point,
    radius: float,
) -> Point:
    """Approach a dense station from its open side without entering the packed core."""

    offset_x = station.x - center.x
    offset_y = station.y - center.y
    distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
    if distance <= 1e-9:
        offset_x = position.x - station.x
        offset_y = position.y - station.y
        distance = (offset_x * offset_x + offset_y * offset_y) ** 0.5
    if distance <= 1e-9:
        return Point(station.x - radius, station.y)
    return Point(
        station.x + offset_x * radius / distance,
        station.y + offset_y * radius / distance,
    )


def _straight_route_is_clear(
    simulation: Simulation,
    start: Point,
    end: Point,
    blocked: frozenset[Cell],
) -> bool:
    """Check a short final formation segment without rebuilding an A* route."""

    sample_count = max(1, int(start.distance_to(end) * 4))
    previous = simulation.game_map.cell_for(start)
    for index in range(1, sample_count + 1):
        fraction = index / sample_count
        point = Point(
            start.x + (end.x - start.x) * fraction,
            start.y + (end.y - start.y) * fraction,
        )
        cell = simulation.game_map.cell_for(point)
        if cell == previous:
            continue
        if cell[0] != previous[0] and cell[1] != previous[1]:
            corners = ((cell[0], previous[1]), (previous[0], cell[1]))
            if any(
                not simulation.game_map.is_cell_passable(corner) or corner in blocked
                for corner in corners
            ):
                return False
        if not simulation.game_map.is_cell_passable(cell) or cell in blocked:
            return False
        previous = cell
    return True


def _shared_defend_entity_ids(
    simulation: Simulation,
    automation: Automation,
    target: SpatialTarget,
) -> tuple[str, ...]:
    """Return one deterministic collision group for defenses sharing an area."""

    return tuple(
        entity_id
        for other in sorted(simulation.automations.values(), key=lambda item: item.automation_id)
        if other.kind is AutomationKind.DEFEND
        and other.owner_id == automation.owner_id
        and not other.status.terminal
        and isinstance(other.parameters, DefendParameters)
        and other.parameters.target == target
        for entity_id in other.entity_ids
        if simulation.assignments.get(entity_id) == other.automation_id
    )


def _position_has_formation_clearance(
    index: SpatialIndex,
    simulation: Simulation,
    entity_id: str,
) -> bool:
    """Only accept a relaxed large-formation station when it is physically distinct."""

    position = simulation.entities[entity_id].position
    minimum_squared = (SETTLED_FORMATION_SPACING - 1e-6) ** 2
    return all(
        other_id == entity_id
        or (
            (position.x - simulation.entities[other_id].position.x) ** 2
            + (position.y - simulation.entities[other_id].position.y) ** 2
            >= minimum_squared
        )
        for other_id in index.nearby(position, SETTLED_FORMATION_SPACING)
    )


def _match_nearby_formation_stations(
    simulation: Simulation,
    parameters: DefendParameters,
    entity_ids: tuple[str, ...],
) -> bool:
    """Atomically match interchangeable defenders to distinct nearby slots."""

    if len(entity_ids) != len(parameters.deployment_slots):
        raise RuntimeError("defend formation entity and deployment-slot counts differ")
    slot_keys = {
        f"slot_{index:06d}": slot for index, slot in enumerate(parameters.deployment_slots)
    }
    slot_index = SpatialIndex(slot_keys, bucket_size=SETTLED_FORMATION_SPACING)
    edges: dict[str, tuple[str, ...]] = {}
    for entity_id in entity_ids:
        position = simulation.entities[entity_id].position
        edges[entity_id] = tuple(
            sorted(
                slot_index.nearby(position, simulation.DEFEND_FORMATION_TOLERANCE),
                key=lambda slot_key: (
                    position.distance_to(slot_keys[slot_key]),
                    slot_key,
                ),
            )
        )
        if not edges[entity_id]:
            return False
    entity_to_slot: dict[str, str] = {}
    slot_to_entity: dict[str, str] = {}
    for start_id in sorted(entity_ids, key=lambda entity_id: (len(edges[entity_id]), entity_id)):
        queue = deque([start_id])
        seen_entities = {start_id}
        seen_slots: set[str] = set()
        parent_slot: dict[str, str] = {}
        free_slot: str | None = None
        while queue and free_slot is None:
            entity_id = queue.popleft()
            for slot_key in edges[entity_id]:
                if slot_key in seen_slots:
                    continue
                seen_slots.add(slot_key)
                parent_slot[slot_key] = entity_id
                incumbent_id = slot_to_entity.get(slot_key)
                if incumbent_id is None:
                    free_slot = slot_key
                    break
                if incumbent_id not in seen_entities:
                    seen_entities.add(incumbent_id)
                    queue.append(incumbent_id)
        if free_slot is None:
            return False
        slot_key = free_slot
        while True:
            entity_id = parent_slot[slot_key]
            previous_slot = entity_to_slot.get(entity_id)
            entity_to_slot[entity_id] = slot_key
            slot_to_entity[slot_key] = entity_id
            if previous_slot is None:
                break
            slot_key = previous_slot
    parameters.stations = {
        entity_id: slot_keys[entity_to_slot[entity_id]] for entity_id in entity_ids
    }
    return True


def _dock_formation_entity(
    simulation: Simulation,
    entity_id: str,
    station: Point,
    movable_ids: frozenset[str],
    building_cells: frozenset[Cell],
) -> None:
    entity = simulation.entities[entity_id]
    cells = simulation._cells_at(entity, station)
    if not simulation.game_map.is_passable(station) or not cells.isdisjoint(building_cells):
        raise RuntimeError(f"invalid defend deployment slot for {entity_id}: {station}")
    simulation.occupancy.move(entity_id, cells, movable_ids)
    entity.position = station
    simulation._open_force_slots = None
    entity.path.clear()
    entity.move_target = None
    entity.state = UnitState.DEFENDING
    simulation._reset_movement_liveness(entity, clear_stop=True)


def _formation_station_is_clear(
    simulation: Simulation,
    index: SpatialIndex,
    entity_id: str,
    station: Point,
    maximum_radius: float,
) -> bool:
    radius = collision_radius(simulation.entities[entity_id].kind)
    nearby_ids = index.nearby(station, radius + maximum_radius)
    simulation._collision_pair_check_count += len(nearby_ids)
    return all(
        other_id == entity_id
        or station.distance_to(simulation.entities[other_id].position)
        >= (radius + collision_radius(simulation.entities[other_id].kind)) * 0.90 - 1e-6
        for other_id in nearby_ids
    )


def settle_automation_formations(simulation: Simulation) -> None:
    """Dock mature saturated formations onto their collision-safe deployment slots."""

    for automation in sorted(simulation.automations.values(), key=lambda item: item.automation_id):
        if automation.kind is not AutomationKind.DEFEND or automation.status.terminal:
            continue
        parameters = _defend_parameters(automation)
        shared_entity_count = sum(
            sum(
                simulation.assignments.get(entity_id) == other.automation_id
                for entity_id in other.entity_ids
            )
            for other in simulation.automations.values()
            if other.kind is AutomationKind.DEFEND
            and other.owner_id == automation.owner_id
            and not other.status.terminal
            and isinstance(other.parameters, DefendParameters)
            and other.parameters.target == parameters.target
        )
        entity_ids = tuple(
            entity_id
            for entity_id in automation.entity_ids
            if simulation.assignments.get(entity_id) == automation.automation_id
        )
        deployment_slots = _deployment_slot_set(parameters.deployment_slots)
        packing_started = (
            simulation.tick - automation.created_tick >= simulation.DEFEND_FORMATION_SETTLE_TICKS
            or not any(simulation.entities[entity_id].path for entity_id in entity_ids)
            or any(
                parameters.stations[entity_id] not in deployment_slots for entity_id in entity_ids
            )
        )
        if not deployment_slots or shared_entity_count <= 128 or not packing_started:
            continue
        formation_center = target_center(parameters.target)
        maximum_radius = parameters.assembly_radius + simulation.DEFEND_FORMATION_TOLERANCE
        inside_ids = tuple(
            entity_id
            for entity_id in entity_ids
            if simulation.entities[entity_id].position.distance_to(formation_center)
            <= maximum_radius
        )
        formation_ready = len(inside_ids) * 20 >= len(entity_ids) * 19
        movable_ids = frozenset(
            entity_id for entity_id, entity in simulation.entities.items() if entity.is_movable
        )
        building_cells = simulation._building_cells()
        unit_index = SpatialIndex(
            {entity_id: simulation.entities[entity_id].position for entity_id in movable_ids},
            bucket_size=1.0,
        )
        maximum_unit_radius = max(
            collision_radius(simulation.entities[entity_id].kind) for entity_id in movable_ids
        )
        for entity_id in inside_ids:
            entity = simulation.entities[entity_id]
            station = parameters.stations[entity_id]
            if not entity.path and entity.position.distance_to(station) <= 1e-9:
                continue
            if entity.position.distance_to(station) > simulation.DEFEND_FORMATION_TOLERANCE:
                continue
            if not _formation_station_is_clear(
                simulation,
                unit_index,
                entity_id,
                station,
                maximum_unit_radius,
            ):
                continue
            _dock_formation_entity(
                simulation,
                entity_id,
                station,
                movable_ids,
                building_cells,
            )
            unit_index.move(entity_id, station)
        if (
            formation_ready
            and shared_entity_count == len(entity_ids)
            and len(inside_ids) == len(entity_ids)
            and _match_nearby_formation_stations(simulation, parameters, entity_ids)
        ):
            for entity_id in entity_ids:
                _dock_formation_entity(
                    simulation,
                    entity_id,
                    parameters.stations[entity_id],
                    movable_ids,
                    building_cells,
                )


@lru_cache(maxsize=128)
def _deployment_slot_set(slots: tuple[Point, ...]) -> frozenset[Point]:
    return frozenset(slots)


def drive_reinforcement(simulation: Simulation, automation: Automation) -> None:
    parameters = reinforcement_parameters(automation)
    target = simulation.automations.get(parameters.target_automation_id)
    if target is None or target.status.terminal:
        simulation._transition(automation, AutomationStatus.FAILED, "TARGET_AUTOMATION_UNAVAILABLE")
        return
    if len(target.entity_ids) >= parameters.minimum_units:
        simulation._transition(automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED")
        return
    transferred = False
    for entity_id in parameters.candidate_entity_ids:
        if entity_id in target.entity_ids or entity_id not in simulation.entities:
            continue
        if not simulation._claim_wins(target, entity_id):
            continue
        simulation._assign(entity_id, target)
        target.entity_ids.append(entity_id)
        simulation._initialize_runtime_entity(target, entity_id)
        parameters.transferred_entity_ids.append(entity_id)
        transferred = True
        if len(target.entity_ids) >= parameters.minimum_units:
            break
    if len(target.entity_ids) >= parameters.minimum_units:
        if target.status is AutomationStatus.WAITING:
            simulation._transition(target, AutomationStatus.ACTIVE, "REINFORCED")
        simulation._transition(automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED")
    elif not transferred and automation.status is AutomationStatus.ACTIVE:
        simulation._transition(automation, AutomationStatus.WAITING, "NO_ELIGIBLE_UNITS")
    elif transferred and automation.status is AutomationStatus.WAITING:
        simulation._transition(automation, AutomationStatus.ACTIVE, "UNITS_AVAILABLE")


def drive_repair(simulation: Simulation, automation: Automation) -> None:
    parameters = repair_parameters(automation)
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for entity_id in simulation._scheduled_entity_ids(automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        phase = parameters.phases[entity_id]
        entity = simulation.entities[entity_id]
        if entity.congestion_stopped and phase in {
            RepairPhase.TRAVELING,
            RepairPhase.RETURNING,
        }:
            continue
        if phase is RepairPhase.TRAVELING:
            if entity.path or entity.move_target is not None:
                continue
            health_ratio = entity.health / entity.kind.profile.max_health
            if health_ratio > parameters.health_threshold:
                parameters.phases[entity_id] = RepairPhase.RETURNING
                continue
            building = simulation.entities.get(parameters.destinations[entity_id])
            if building is None:
                simulation._transition(automation, AutomationStatus.FAILED, "REPAIR_SOURCE_REMOVED")
                simulation._release_automation(automation, clear_suspended=True)
                return
            interaction_cells = {
                simulation.game_map.cell_for(point)
                for point in simulation._interaction_points(building)
            }
            if simulation.game_map.cell_for(entity.position) in interaction_cells:
                parameters.phases[entity_id] = RepairPhase.REPAIRING
                entity.state = UnitState.REPAIRING
                continue
            if not allowance.claim():
                continue
            try:
                _, point, path = simulation._nearest_repair_destination(entity, building.entity_id)
            except PathfindingError as error:
                simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
                return
            simulation._start_path(
                entity, point, path, automation.automation_id, UnitState.RETURNING
            )
        elif phase is RepairPhase.REPAIRING:
            if entity.path:
                continue
            if entity.health < entity.kind.profile.max_health:
                if entity.state is not UnitState.REPAIRING:
                    entity.state = UnitState.REPAIRING
                    simulation.events.record(
                        simulation.tick,
                        EventType.REPAIR_STARTED,
                        entity_id,
                        automation_id=automation.automation_id,
                        destination_id=parameters.destinations[entity_id],
                    )
                entity.health = min(
                    entity.kind.profile.max_health,
                    entity.health + parameters.repair_rate,
                )
            if entity.health >= entity.kind.profile.max_health:
                parameters.phases[entity_id] = RepairPhase.RETURNING
                simulation.events.record(
                    simulation.tick,
                    EventType.REPAIR_COMPLETED,
                    entity_id,
                    automation_id=automation.automation_id,
                )
        elif phase is RepairPhase.RETURNING:
            resume_id = simulation.suspended_assignments.get(
                entity_id
            ) or parameters.resume_automation_ids.get(entity_id)
            if resume_id is not None:
                simulation._resume_suspended_assignment(automation, entity_id)
                parameters.phases[entity_id] = RepairPhase.DONE
                continue
            return_position = parameters.return_positions[entity_id]
            if entity.path or entity.move_target is not None:
                continue
            if entity.position.distance_to(return_position) <= 0.05:
                simulation._resume_suspended_assignment(automation, entity_id)
                parameters.phases[entity_id] = RepairPhase.DONE
                continue
            if not allowance.claim():
                continue
            try:
                path = simulation._routes.shared_path(
                    entity.position,
                    return_position,
                    simulation._building_cells(),
                )
            except PathfindingError as error:
                simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
                return
            simulation._start_path(
                entity,
                return_position,
                path,
                automation.automation_id,
                UnitState.RETURNING,
            )
    if all(phase is RepairPhase.DONE for phase in parameters.phases.values()):
        simulation._transition(automation, AutomationStatus.COMPLETED, "ALL_UNITS_REPAIRED")


def refresh_gathering_formation(simulation: Simulation, automation: Automation) -> None:
    if automation.kind is not AutomationKind.DEFEND:
        return
    parameters = _defend_parameters(automation)
    if not parameters.gathering_point and not parameters.deployment_slots:
        return
    if not automation.entity_ids:
        parameters.stations.clear()
        parameters.deployment_slots = ()
        parameters.assembly_radius = 0.0
        return
    radius = max(
        collision_radius(simulation.entities[entity_id].kind) for entity_id in automation.entity_ids
    )
    slots = simulation._gathering_slots(parameters.target, len(automation.entity_ids), radius)
    center = target_center(parameters.target)
    previous_stations = parameters.stations
    if len(automation.entity_ids) > 128:
        stations = assign_formation_slots(
            {
                entity_id: simulation.entities[entity_id].position
                for entity_id in automation.entity_ids
            },
            slots,
            center,
        )
    else:
        ordered_ids = sorted(
            automation.entity_ids,
            key=lambda entity_id: (
                previous_stations.get(
                    entity_id, simulation.entities[entity_id].position
                ).distance_to(center),
                entity_id,
            ),
        )
        stations = dict(zip(ordered_ids, slots, strict=True))
    parameters.deployment_slots = slots
    parameters.stations = stations
    parameters.assembly_radius = max(point.distance_to(center) for point in slots)
    for entity_id in sorted(automation.entity_ids):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        if previous_stations.get(entity_id) == parameters.stations[entity_id]:
            continue
        entity = simulation.entities[entity_id]
        entity.path.clear()
        entity.move_target = None
        simulation._reset_movement_liveness(entity, clear_stop=True)


def initialize_runtime_entity(
    simulation: Simulation, automation: Automation, entity_id: str
) -> None:
    entity = simulation.entities[entity_id]
    entity.path.clear()
    entity.move_target = None
    entity.attack_target_id = None
    entity.pursue_target = False
    simulation._reset_movement_liveness(entity, clear_stop=True)
    if automation.kind is AutomationKind.PATROL:
        patrol_parameters = _patrol_parameters(automation)
        patrol_parameters.waypoint_indices.setdefault(entity_id, 0)
        entity.state = UnitState.PATROLLING
    elif automation.kind is AutomationKind.DEFEND:
        defend_parameters = _defend_parameters(automation)
        if entity_id not in defend_parameters.stations:
            defend_parameters.stations[entity_id] = next(iter(defend_parameters.stations.values()))
        entity.state = UnitState.DEFENDING
    elif automation.kind is AutomationKind.PRODUCTION:
        entity.state = UnitState.PRODUCING
    elif automation.kind is AutomationKind.CONSTRUCTION:
        entity.state = UnitState.MOVING
    elif automation.kind is AutomationKind.REPAIR_AND_RETURN:
        entity.state = UnitState.RETURNING
    elif automation.kind is AutomationKind.ECONOMY:
        entity.state = UnitState.IDLE


def next_reinforcement_station(
    simulation: Simulation, target: SpatialTarget, occupied: tuple[Point, ...]
) -> Point:
    candidates = build_patrol_waypoints(target, simulation.game_map)
    return max(
        candidates,
        key=lambda point: (
            min((point.distance_to(item) for item in occupied), default=float("inf")),
            -point.y,
            -point.x,
        ),
    )


def _patrol_parameters(automation: Automation) -> PatrolParameters:
    if not isinstance(automation.parameters, PatrolParameters):
        raise TypeError("automation does not have patrol parameters")
    return automation.parameters


def _defend_parameters(automation: Automation) -> DefendParameters:
    if not isinstance(automation.parameters, DefendParameters):
        raise TypeError("automation does not have defend parameters")
    return automation.parameters


def reinforcement_parameters(automation: Automation) -> ReinforcementParameters:
    if not isinstance(automation.parameters, ReinforcementParameters):
        raise TypeError("automation does not have reinforcement parameters")
    return automation.parameters


def repair_parameters(automation: Automation) -> RepairParameters:
    if not isinstance(automation.parameters, RepairParameters):
        raise TypeError("automation does not have repair parameters")
    return automation.parameters
