"""Persistent automation scheduling and deterministic behavior execution."""

from __future__ import annotations

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
from airts.navigation.movement import collision_radius
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

    entity_ids = sorted(automation.entity_ids)
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
    area_slot_anchors: dict[int, dict[Point, Point]] = {}
    formation_cluster_size = (
        8
        if len(ordered_ids) > 128
        and all(simulation.entities[item].kind is EntityKind.SCOUT for item in ordered_ids)
        else 5
    )
    if len(ordered_ids) > 1 and not isinstance(parameters.target, PolylineTarget):
        unit_radius = max(
            collision_radius(simulation.entities[entity_id].kind) for entity_id in ordered_ids
        )
        area_slots = {
            waypoint_index: simulation._gathering_slots(
                PointTarget(waypoint, radius=0.01),
                len(ordered_ids),
                unit_radius,
            )
            for waypoint_index, waypoint in enumerate(parameters.waypoints)
        }
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
        if area_slots:
            target = area_slots[waypoint_index][formation_indices[entity_id]]
            anchors = area_slot_anchors.get(waypoint_index)
            if anchors is None:
                anchors = _formation_slot_anchors(
                    area_slots[waypoint_index],
                    formation_cluster_size,
                    simulation,
                )
                area_slot_anchors[waypoint_index] = anchors
        try:
            if area_slots:
                assert anchors is not None
                anchor = anchors[target]
                path = _formation_path(
                    simulation,
                    entity.position,
                    target,
                    anchor,
                    building_cells,
                    branch_distance=formation_cluster_size * 2,
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


def _formation_slot_anchors(
    slots: tuple[Point, ...],
    cluster_size: int,
    simulation: Simulation,
) -> dict[Point, Point]:
    clusters: dict[tuple[int, int], list[Point]] = {}
    for slot in slots:
        cell = simulation.game_map.cell_for(slot)
        clusters.setdefault((cell[0] // cluster_size, cell[1] // cluster_size), []).append(slot)
    anchors: dict[Point, Point] = {}
    for cluster, members in clusters.items():
        center = Point((cluster[0] + 0.5) * cluster_size, (cluster[1] + 0.5) * cluster_size)
        anchor = min(members, key=lambda point: (point.distance_to(center), point.y, point.x))
        anchors.update((member, anchor) for member in members)
    return anchors


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
    local = simulation._routes.dynamic_path(junction, target, building_cells)
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
    saturated_formation = bool(parameters.deployment_slots) and len(assigned_ids) > 128
    responder_index = SpatialIndex(
        {entity_id: simulation.entities[entity_id].position for entity_id in assigned_ids}
    )
    station_anchors: dict[Point, Point] = {}
    formation_cluster_size = 5
    if parameters.deployment_slots:
        formation_cluster_size = (
            4
            if len(assigned_ids) > 128
            and all(
                simulation.entities[entity_id].kind is EntityKind.SCOUT
                for entity_id in assigned_ids
            )
            else 5
        )
        station_anchors = _formation_slot_anchors(
            parameters.deployment_slots,
            formation_cluster_size,
            simulation,
        )
    for victim_id in assigned_ids:
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

    assigned_set = set(assigned_ids)
    for entity_id in simulation._scheduled_entity_ids(automation):
        if entity_id not in assigned_set:
            continue
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        station = parameters.stations[entity_id]
        station_distance = entity.position.distance_to(station)
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
        inside_formation = (
            entity.position.distance_to(formation_center)
            <= parameters.assembly_radius + simulation.DEFEND_FORMATION_TOLERANCE
        )
        inside_formation_core = (
            entity.position.distance_to(formation_center) <= parameters.assembly_radius
        )
        formation_congested = entity.congestion_stopped or (
            entity.collision_pressure > 0
            and entity.route_ticks >= simulation.NO_PROGRESS_YIELD_TICKS
        )
        formation_mature = (
            simulation.tick - automation.created_tick >= simulation.DEFEND_FORMATION_SETTLE_TICKS
        )
        relaxed_arrival = formation_mature and entity.collision_pressure == 0
        if (
            saturated_formation
            and inside_formation
            and ((formation_congested and inside_formation_core) or relaxed_arrival)
        ):
            station = entity.position
            parameters.stations[entity_id] = station
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            simulation._reset_movement_liveness(entity, clear_stop=True)
            continue
        station_tolerance = (
            simulation.DEFEND_FORMATION_TOLERANCE
            if saturated_formation
            else simulation.DEFEND_STATION_TOLERANCE
        )
        if station_distance <= station_tolerance and (not saturated_formation or inside_formation):
            if saturated_formation and inside_formation_core:
                parameters.stations[entity_id] = entity.position
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            simulation._reset_movement_liveness(entity, clear_stop=True)
            continue
        if entity.path:
            continue
        if not allowance.claim():
            continue
        try:
            if station_anchors:
                anchor = station_anchors.get(station, station)
                path = _formation_path(
                    simulation,
                    entity.position,
                    station,
                    anchor,
                    building_cells,
                    branch_distance=formation_cluster_size * 2,
                )
            else:
                path = simulation._routes.shared_path(entity.position, station, building_cells)
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
