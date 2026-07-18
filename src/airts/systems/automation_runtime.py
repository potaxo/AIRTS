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
    patrol_formation_waypoint,
    retain_formation_slots,
    target_center,
)
from airts.events import EventType
from airts.geometry import Point, PointTarget, PolylineTarget, SpatialTarget
from airts.navigation.collision import NEIGHBOR_RADIUS, collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.world.entities import UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def drive_automations(simulation: Simulation) -> None:
    """Give each live automation one deterministic turn per simulation tick."""

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
    """Rotate work order so a route budget cannot starve the tail of a group."""

    entity_ids = automation.entity_ids
    if not entity_ids:
        return ()
    stable_offset = sum(ord(character) for character in automation.automation_id)
    offset = (simulation.tick + stable_offset) % len(entity_ids)
    return tuple(entity_ids[offset:] + entity_ids[:offset])


def drive_patrol(simulation: Simulation, automation: Automation) -> None:
    parameters = _patrol_parameters(automation)
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    ordered_ids = tuple(sorted(automation.entity_ids))
    formation_indices = {entity_id: index for index, entity_id in enumerate(ordered_ids)}
    area_slots: dict[int, tuple[Point, ...]] = {}
    area_radius = (
        max(collision_radius(simulation.entities[entity_id].kind) for entity_id in ordered_ids)
        if len(ordered_ids) > 1 and not isinstance(parameters.target, PolylineTarget)
        else None
    )

    for entity_id in simulation._scheduled_entity_ids(automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        if entity.path or entity.move_target is not None or not allowance.claim():
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
        if area_radius is not None:
            slots = area_slots.get(waypoint_index)
            if slots is None:
                slots = simulation._gathering_slots(
                    PointTarget(parameters.waypoints[waypoint_index], radius=0.01),
                    len(ordered_ids),
                    area_radius,
                )
                area_slots[waypoint_index] = slots
            target = slots[formation_indices[entity_id]]

        try:
            path = simulation._routes.shared_path(
                entity.position,
                target,
                simulation._building_cells(),
            )
        except PathfindingError as error:
            _block_for_path_error(simulation, automation, entity_id, error)
            return
        simulation._start_path(entity, target, path, automation.automation_id, UnitState.PATROLLING)


def drive_defend(simulation: Simulation, automation: Automation) -> None:
    """Return defenders to unique stations and respond to nearby attacks."""

    parameters = _defend_parameters(automation)
    assigned_ids = tuple(
        entity_id
        for entity_id in automation.entity_ids
        if simulation.assignments.get(entity_id) == automation.automation_id
    )
    if not assigned_ids:
        return

    _assign_defense_responses(simulation, automation, parameters, assigned_ids)
    allowance = simulation._routes.automation_allowance(
        simulation.GATHERING_PATH_BUDGET
        if parameters.gathering_point
        else simulation.AUTOMATION_ROUTE_BUDGET
    )
    building_cells = simulation._building_cells()

    for entity_id in simulation._scheduled_entity_ids(automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        station = parameters.stations[entity_id]
        target = simulation.entities.get(entity.attack_target_id or "")
        if target is not None and target.owner_id != automation.owner_id:
            if target.position.distance_to(station) <= simulation.DEFEND_PURSUIT_RADIUS:
                entity.state = UnitState.ATTACKING
                continue

        if entity.attack_target_id is not None:
            entity.attack_target_id = None
            entity.pursue_target = False
            entity.path.clear()
            entity.move_target = None
            simulation._reset_movement_liveness(entity, clear_stop=True)

        station_distance = entity.position.distance_to(station)
        if (
            not entity.path
            and entity.move_target is None
            and station_distance > collision_radius(entity.kind)
            and simulation.tick - automation.created_tick >= simulation.NO_PROGRESS_YIELD_TICKS
            and station_distance <= NEIGHBOR_RADIUS
            and _adopt_reached_station(simulation, automation, parameters, entity_id)
        ):
            entity.state = UnitState.DEFENDING
            continue

        # Exact identity-to-slot docking is unnecessary. Local collision already guarantees that
        # this reached position is safe, and accepting the documented tolerance prevents the last
        # few members of a dense formation from circulating forever around equivalent stations.
        if station_distance <= collision_radius(entity.kind):
            entity.path.clear()
            entity.move_target = None
            entity.state = UnitState.DEFENDING
            simulation._reset_movement_liveness(entity, clear_stop=True)
            continue
        if entity.path or not allowance.claim():
            continue

        try:
            path = simulation._routes.shared_path(entity.position, station, building_cells)
        except PathfindingError as error:
            _block_for_path_error(simulation, automation, entity_id, error)
            return
        simulation._start_path(entity, station, path, automation.automation_id, UnitState.DEFENDING)


def _adopt_reached_station(
    simulation: Simulation,
    automation: Automation,
    parameters: DefendParameters,
    entity_id: str,
) -> bool:
    """Keep a nearby collision-safe reached point instead of circulating around an exact slot."""

    entity = simulation.entities[entity_id]
    position = entity.position
    for other_id, other in simulation.entities.items():
        if other_id == entity_id or not other.is_movable:
            continue
        other_automation = simulation.automations.get(simulation.assignments.get(other_id, ""))
        if (
            other_automation is None
            or other_automation.kind is not AutomationKind.DEFEND
            or other_automation.owner_id != automation.owner_id
            or not isinstance(other_automation.parameters, DefendParameters)
            or other_automation.parameters.target != parameters.target
        ):
            continue
        minimum_distance = collision_radius(entity.kind) + collision_radius(other.kind)
        if position.distance_to(other.position) < minimum_distance - 1e-6:
            return False

    previous = parameters.stations[entity_id]
    if position in (station for key, station in parameters.stations.items() if key != entity_id):
        return False
    parameters.stations[entity_id] = position
    parameters.deployment_slots = tuple(
        position if slot == previous else slot for slot in parameters.deployment_slots
    )
    parameters.assembly_radius = max(
        parameters.assembly_radius,
        position.distance_to(target_center(parameters.target)),
    )
    return True


def _assign_defense_responses(
    simulation: Simulation,
    automation: Automation,
    parameters: DefendParameters,
    assigned_ids: tuple[str, ...],
) -> None:
    attacked: list[tuple[str, str]] = []
    for victim_id in assigned_ids:
        victim = simulation.entities[victim_id]
        attacker = simulation.entities.get(victim.last_attacker_id or "")
        attacked_tick = victim.last_attacked_tick
        if attacker is None or attacked_tick is None:
            continue
        if (
            attacker.owner_id == automation.owner_id
            or simulation.tick - attacked_tick > simulation.DEFEND_ATTACK_MEMORY_TICKS
            or attacker.position.distance_to(parameters.stations[victim_id])
            > simulation.DEFEND_PURSUIT_RADIUS
        ):
            victim.last_attacker_id = None
            victim.last_attacked_tick = None
            continue
        attacked.append((victim_id, attacker.entity_id))
    if not attacked:
        return

    positions = {entity_id: parameters.stations[entity_id] for entity_id in assigned_ids}
    responders = SpatialIndex(positions)
    for victim_id, attacker_id in attacked:
        attacker = simulation.entities[attacker_id]
        for responder_id in responders.nearby(
            parameters.stations[victim_id], simulation.DEFEND_RESPONSE_RADIUS
        ):
            if (
                attacker.position.distance_to(parameters.stations[responder_id])
                > simulation.DEFEND_PURSUIT_RADIUS
            ):
                continue
            responder = simulation.entities[responder_id]
            if responder.attack_target_id == attacker_id and responder.pursue_target:
                continue
            responder.path.clear()
            responder.move_target = None
            simulation._reset_movement_liveness(responder, clear_stop=True)
            responder.attack_target_id = attacker_id
            responder.pursue_target = True
            responder.state = UnitState.ATTACKING
            simulation.events.record(
                simulation.tick,
                EventType.DEFEND_ENGAGED,
                responder_id,
                automation_id=automation.automation_id,
                victim_id=victim_id,
                attacker_id=attacker_id,
            )


def _block_for_path_error(
    simulation: Simulation,
    automation: Automation,
    entity_id: str,
    error: PathfindingError,
) -> None:
    simulation._transition(automation, AutomationStatus.BLOCKED, str(error))
    simulation.events.record(
        simulation.tick,
        EventType.PATHFINDING_FAILED,
        entity_id,
        reason=str(error),
        automation_id=automation.automation_id,
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
    """Reconcile a defense after membership changes without moving valid survivors."""

    if automation.kind is not AutomationKind.DEFEND:
        return
    parameters = _defend_parameters(automation)
    if not parameters.gathering_point and not parameters.deployment_slots:
        return
    if coordinate_shared_defend_stations(simulation, parameters.target, automation.owner_id):
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
    stations = retain_formation_slots(
        {entity_id: simulation.entities[entity_id].position for entity_id in automation.entity_ids},
        slots,
        center,
        previous_stations,
    )
    parameters.deployment_slots = slots
    parameters.stations = stations
    parameters.assembly_radius = max(point.distance_to(center) for point in slots)
    _clear_changed_station_paths(simulation, automation, previous_stations, stations)


def coordinate_shared_defend_stations(
    simulation: Simulation,
    target: SpatialTarget,
    owner_id: str,
) -> bool:
    """Use one collision-safe slot set for matching same-owner defenses."""

    defenses = tuple(
        automation
        for automation in sorted(
            simulation.automations.values(), key=lambda item: item.automation_id
        )
        if automation.kind is AutomationKind.DEFEND
        and automation.owner_id == owner_id
        and not automation.status.terminal
        and isinstance(automation.parameters, DefendParameters)
        and automation.parameters.target == target
    )
    if len(defenses) <= 1:
        return False
    entity_ids = tuple(
        entity_id
        for automation in defenses
        for entity_id in automation.entity_ids
        if entity_id in simulation.entities
        and simulation.assignments.get(entity_id) == automation.automation_id
    )
    if not entity_ids:
        for automation in defenses:
            parameters = _defend_parameters(automation)
            parameters.stations.clear()
            parameters.deployment_slots = ()
            parameters.assembly_radius = 0.0
        return True

    radius = max(collision_radius(simulation.entities[entity_id].kind) for entity_id in entity_ids)
    slots = simulation._gathering_slots(target, len(entity_ids), radius)
    previous_stations = {
        entity_id: station
        for automation in defenses
        for entity_id, station in _defend_parameters(automation).stations.items()
        if entity_id in entity_ids
    }
    stations = retain_formation_slots(
        {entity_id: simulation.entities[entity_id].position for entity_id in entity_ids},
        slots,
        target_center(target),
        previous_stations,
    )
    center = target_center(target)
    for automation in defenses:
        parameters = _defend_parameters(automation)
        previous = parameters.stations
        assigned_ids = tuple(
            entity_id
            for entity_id in automation.entity_ids
            if entity_id in stations
            and simulation.assignments.get(entity_id) == automation.automation_id
        )
        current = {entity_id: stations[entity_id] for entity_id in assigned_ids}
        parameters.stations = current
        parameters.deployment_slots = tuple(current.values())
        parameters.assembly_radius = max(
            (station.distance_to(center) for station in current.values()),
            default=0.0,
        )
        _clear_changed_station_paths(simulation, automation, previous, current)
    return True


def _clear_changed_station_paths(
    simulation: Simulation,
    automation: Automation,
    previous: dict[str, Point],
    current: dict[str, Point],
) -> None:
    for entity_id, station in current.items():
        if previous.get(entity_id) == station:
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
            raise RuntimeError(
                f"defend station missing for assigned entity {entity_id} "
                f"in {automation.automation_id}"
            )
        entity.state = UnitState.DEFENDING
    elif automation.kind is AutomationKind.PRODUCTION:
        entity.state = UnitState.PRODUCING
    elif automation.kind is AutomationKind.CONSTRUCTION:
        entity.state = UnitState.MOVING
    elif automation.kind is AutomationKind.REPAIR_AND_RETURN:
        entity.state = UnitState.RETURNING
    elif automation.kind is AutomationKind.ECONOMY:
        entity.state = UnitState.IDLE


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
