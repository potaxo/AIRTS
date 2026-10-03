"""Deterministic patrol, defense, reinforcement, and repair execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from airts.contracts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    RepairPhase,
    assign_formation_slots,
    build_defend_stations,
    reinforcement_parameters,
    repair_parameters,
    target_center,
)
from airts.contracts.automations import (
    defend_parameters as _defend_parameters,
)
from airts.contracts.automations import (
    patrol_parameters as _patrol_parameters,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, PointTarget, SpatialTarget
from airts.navigation.collision import collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems import movement
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def scheduled_entity_ids(simulation: Simulation, automation: Automation) -> tuple[str, ...]:
    """Rotate deterministic work order so deferred large groups make fair progress."""

    entity_ids = automation.entity_ids
    if not entity_ids:
        return ()
    stable_offset = sum(ord(character) for character in automation.automation_id)
    offset = (simulation.tick + stable_offset) % len(entity_ids)
    return tuple(entity_ids[offset:] + entity_ids[:offset])


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
    for entity_id in scheduled_entity_ids(simulation, automation):
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


def drive_patrol(simulation: Simulation, automation: Automation) -> None:
    parameters = _patrol_parameters(automation)
    ordered = sorted(automation.entity_ids)
    indices = {key: index for index, key in enumerate(ordered)}
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    held_cells = frozenset(
        simulation.game_map.cell_for(e.position)
        for e in simulation.entities.values()
        if e.state is UnitState.HOLDING and e.is_movable
    )
    for key in scheduled_entity_ids(simulation, automation):
        entity = simulation.entities[key]
        if entity.path or simulation.assignments.get(key) != automation.automation_id:
            continue
        if not allowance.claim():
            break
        waypoint_index = parameters.waypoint_indices.get(key, 0)
        anchor = parameters.waypoints[waypoint_index]
        target = (
            simulation._gathering_slots(
                PointTarget(anchor), len(ordered), collision_radius(entity.kind)
            )[indices[key]]
            if len(ordered) > 1
            else anchor
        )
        try:
            paths = movement.plan_group_paths(
                simulation, (key,), {key: target}, shared_goal=anchor, held_cells=held_cells
            )
        except PathfindingError as error:
            simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
            return
        parameters.waypoint_indices[key] = (waypoint_index + 1) % len(parameters.waypoints)
        simulation._start_path(
            entity, target, paths[key], automation.automation_id, UnitState.PATROLLING
        )


def drive_defend(simulation: Simulation, automation: Automation) -> None:
    parameters = _defend_parameters(automation)
    allowance = simulation._routes.automation_allowance(
        simulation.GATHERING_PATH_BUDGET
        if parameters.gathering_point
        else simulation.AUTOMATION_ROUTE_BUDGET
    )
    buildings = simulation._building_cells()
    held_cells = frozenset(
        simulation.game_map.cell_for(e.position)
        for e in simulation.entities.values()
        if e.state is UnitState.HOLDING and e.is_movable
    )
    if parameters.deployment_slots:
        anchor = next(
            (
                p
                for p in parameters.deployment_slots
                if simulation.game_map.cell_for(p) not in buildings
            ),
            None,
        )
    else:
        center = target_center(parameters.target)
        anchor = min(
            (
                p
                for p in parameters.stations.values()
                if simulation.game_map.cell_for(p) not in buildings
            ),
            key=lambda p: (p.distance_to(center), p.y, p.x),
            default=None,
        )
    enemies = {
        key: e.position
        for key, e in simulation.entities.items()
        if e.owner_id != automation.owner_id
    }
    enemy_index = SpatialIndex(enemies)
    attacked = [
        simulation.entities[other]
        for other in automation.entity_ids
        if simulation.entities[other].last_attacker_id in enemies
        and simulation.entities[other].last_attacked_tick is not None
        and simulation.tick - (simulation.entities[other].last_attacked_tick or 0)
        <= simulation.DEFEND_ATTACK_MEMORY_TICKS
    ]
    attacked_index = SpatialIndex({e.entity_id: e.position for e in attacked})
    station_distances = {
        key: simulation.entities[key].position.distance_to(parameters.stations[key])
        for key in automation.entity_ids
    }
    responding = tuple(
        key
        for key in automation.entity_ids
        if station_distances[key] <= simulation.DEFEND_RESPONSE_RADIUS
    )
    enemy_ids = dict(
        zip(
            responding,
            enemy_index.nearest_many(
                tuple(
                    (parameters.stations[key], simulation.DEFEND_RESPONSE_RADIUS)
                    for key in responding
                )
            ),
            strict=True,
        )
    )
    victim_ids = dict(
        zip(
            responding,
            attacked_index.nearest_many(
                tuple(
                    (simulation.entities[key].position, simulation.DEFEND_RESPONSE_RADIUS * 2)
                    for key in responding
                )
            ),
            strict=True,
        )
    )
    # Dispatch front ranks first so early arrivals do not seal off deeper stations.
    ordered = sorted(automation.entity_ids, key=lambda key: (station_distances[key], key))
    for key in ordered:
        if simulation.assignments.get(key) != automation.automation_id:
            continue
        entity = simulation.entities[key]
        station = parameters.stations[key]
        station_distance = station_distances[key]
        enemy_id = enemy_ids.get(key)
        victim_id = victim_ids.get(key)
        if victim_id is not None:
            enemy_id = simulation.entities[victim_id].last_attacker_id
        current = simulation.entities.get(entity.attack_target_id or "")
        if (
            entity.pursue_target
            and current is not None
            and current.position.distance_to(station) <= simulation.DEFEND_PURSUIT_RADIUS
        ):
            continue
        if (
            enemy_id is not None
            and not attacked
            and entity.position.distance_to(enemies[enemy_id]) <= entity.kind.profile.attack_range
        ):
            enemy_id = None
        if enemy_id is not None:
            if not entity.pursue_target or entity.attack_target_id != enemy_id:
                entity.path.clear()
                entity.move_target = None
                entity.attack_target_id = enemy_id
                entity.pursue_target = True
                entity.state = UnitState.ATTACKING
                simulation.events.record(
                    simulation.tick,
                    EventType.DEFEND_ENGAGED,
                    key,
                    automation_id=automation.automation_id,
                    target_id=enemy_id,
                )
            continue
        if entity.pursue_target:
            entity.pursue_target = False
            entity.attack_target_id = None
            entity.path.clear()
            entity.move_target = None
            simulation.events.record(
                simulation.tick,
                EventType.DEFEND_RETURNED,
                key,
                automation_id=automation.automation_id,
            )
        if entity.path:
            continue
        entity.state = UnitState.DEFENDING
        tolerance = simulation.DEFEND_STATION_TOLERANCE
        if station_distance <= tolerance:
            continue
        if not allowance.claim():
            continue
        try:
            paths = movement.plan_group_paths(
                simulation, (key,), {key: station}, shared_goal=anchor, held_cells=held_cells
            )
        except PathfindingError as error:
            simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
            return
        simulation._start_path(
            entity, station, paths[key], automation.automation_id, UnitState.DEFENDING
        )


def refresh_gathering_formation(simulation: Simulation, automation: Automation) -> None:
    if isinstance(automation.parameters, DefendParameters):
        coordinate_shared_defend_stations(
            simulation, automation.parameters.target, automation.owner_id
        )


def coordinate_shared_defend_stations(
    simulation: Simulation, target: SpatialTarget, owner_id: str
) -> None:
    automations = [
        a
        for a in simulation.automations.values()
        if isinstance(a.parameters, DefendParameters)
        and a.parameters.target == target
        and a.owner_id == owner_id
        and a.status in {AutomationStatus.ACTIVE, AutomationStatus.WAITING, AutomationStatus.PAUSED}
    ]
    ids = sorted(
        {
            key
            for a in automations
            for key in a.entity_ids
            if key in simulation.entities and simulation.assignments.get(key) == a.automation_id
        }
    )
    if not ids:
        return
    id_set = frozenset(ids)
    previous = {
        key: point
        for a in automations
        for key, point in _defend_parameters(a).stations.items()
        if key in id_set
    }
    if not any(
        _defend_parameters(a).deployment_slots or _defend_parameters(a).gathering_point
        for a in automations
    ):
        stations = build_defend_stations(target, tuple(ids), simulation.game_map)
        slots: tuple[Point, ...] = ()
    else:
        slots = simulation._gathering_slots(
            target, len(ids), max(collision_radius(simulation.entities[key].kind) for key in ids)
        )
        stations = assign_formation_slots(
            {key: simulation.entities[key].position for key in ids},
            slots,
            target_center(target),
            previous,
        )
    center = target_center(target)
    assembly_radius = max((p.distance_to(center) for p in slots), default=0.0)
    for automation in automations:
        parameters = _defend_parameters(automation)
        parameters.stations = {
            key: stations[key] for key in automation.entity_ids if key in stations
        }
        parameters.deployment_slots = slots
        parameters.assembly_radius = assembly_radius
        for key, station in parameters.stations.items():
            if previous.get(key) != station:
                entity = simulation.entities[key]
                entity.path.clear()
                entity.move_target = None
                simulation._reset_movement_liveness(entity, clear_stop=True)
