"""Deterministic patrol, defense, reinforcement, and repair execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AUTO_REPAIR_HEALTH_THRESHOLD,
    Automation,
    AutomationStatus,
    DefendParameters,
    PatrolParameters,
    RepairPhase,
    reinforcement_parameters,
    repair_parameters,
    target_center,
)
from airts.contracts.automations import defend_parameters as _defend_parameters
from airts.contracts.automations import patrol_parameters as _patrol_parameters
from airts.contracts.commands import CreateRepairAndReturnCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import (
    Point,
    PointTarget,
    SpatialTarget,
    distance_to_target,
)
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems import automation_assignments, automation_lifecycle, automation_runtime, movement
from airts.systems.repair_positions import in_service_range, repair_position_clear
from airts.world.entities import Entity, UnitState
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory

if TYPE_CHECKING:
    from airts.simulation import Simulation


@dataclass(slots=True)
class AutomationQueries:
    """Derived queries shared only within one position-stable automation pass."""

    enemy_indexes: dict[str, tuple[dict[str, Point], SpatialIndex]] = field(default_factory=dict)
    patrol_enemies: dict[str, dict[str, Point]] = field(default_factory=dict)
    damaged_buildings: dict[str, dict[str, Point]] = field(default_factory=dict)
    source_indexes: dict[tuple[str, bool], SpatialIndex] = field(default_factory=dict)
    patrol_indexes: dict[tuple[str, SpatialTarget, bool], tuple[dict[str, Point], SpatialIndex]] = (
        field(default_factory=dict)
    )

    def patrol_index(
        self, simulation: Simulation, owner: str, target: SpatialTarget, *, repair: bool = False
    ) -> tuple[dict[str, Point], SpatialIndex]:
        key = owner, target, repair
        cached = self.patrol_indexes.get(key)
        if cached is not None:
            return cached
        snapshots = self.damaged_buildings if repair else self.patrol_enemies
        points = snapshots.get(owner)
        if points is None:
            points = {
                key: entity.selection_position
                for key, entity in simulation.entities.items()
                if (
                    entity.owner_id == owner
                    and entity.category is EntityCategory.BUILDING
                    and entity.is_complete
                    and entity.health < entity.kind.profile.max_health
                    if repair
                    else simulation.are_hostile(entity.owner_id, owner)
                )
            }
            snapshots[owner] = points
            self.source_indexes[owner, repair] = SpatialIndex(points)
        if isinstance(target, PointTarget):
            center = target.point
            radius = target.radius
        else:
            left = min(point.x for point in target.points)
            right = max(point.x for point in target.points)
            top = min(point.y for point in target.points)
            bottom = max(point.y for point in target.points)
            center = Point((left + right) / 2, (top + bottom) / 2)
            radius = hypot(right - left, bottom - top) / 2
        # The enclosing disk is conservative for filled polygons and route segments.
        # Keep the exact geometry test and a small outward rounding allowance.
        candidates = self.source_indexes[owner, repair].nearby(
            center, radius + simulation.PATROL_PURSUIT_RADIUS + 1e-9
        )
        eligible = {
            key: points[key]
            for key in candidates
            if distance_to_target(points[key], target) <= simulation.PATROL_PURSUIT_RADIUS
        }
        cached = eligible, SpatialIndex(eligible)
        self.patrol_indexes[key] = cached
        return cached


def scheduled_entity_ids(simulation: Simulation, automation: Automation) -> tuple[str, ...]:
    """Rotate deterministic work order so deferred large groups make fair progress."""

    entity_ids = automation.entity_ids
    if not entity_ids:
        return ()
    stable_offset = sum(ord(character) for character in automation.automation_id)
    offset = (simulation.tick + stable_offset) % len(entity_ids)
    return tuple(entity_ids[offset:] + entity_ids[:offset])


def drive_auto_repair(simulation: Simulation, automation: Automation) -> None:
    parameters = automation.parameters
    assert isinstance(parameters, PatrolParameters | DefendParameters)
    if not parameters.auto_repair or simulation.tick % simulation.TICKS_PER_SECOND != 1:
        return
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for entity_id in scheduled_entity_ids(simulation, automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        entity = simulation.entities[entity_id]
        if entity.health >= entity.kind.profile.max_health * AUTO_REPAIR_HEALTH_THRESHOLD:
            continue
        if not allowance.claim():
            break
        simulation._execute_command(
            CreateRepairAndReturnCommand(
                (entity_id,),
                health_threshold=AUTO_REPAIR_HEALTH_THRESHOLD,
                owner_id=automation.owner_id,
                original_instruction=f"Auto repair for {automation.automation_id}",
            ),
            record=False,
        )


def drive_reinforcement(simulation: Simulation, automation: Automation) -> None:
    parameters = reinforcement_parameters(automation)
    target = simulation.automations.get(parameters.target_automation_id)
    if target is None or target.status.terminal:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.FAILED, "TARGET_AUTOMATION_UNAVAILABLE"
        )
        return
    if len(target.entity_ids) >= parameters.minimum_units:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED"
        )
        return
    transferred = False
    for entity_id in parameters.candidate_entity_ids:
        if entity_id in target.entity_ids or entity_id not in simulation.entities:
            continue
        if not automation_lifecycle.claim_wins(simulation, target, entity_id):
            continue
        automation_assignments.assign(simulation, entity_id, target)
        target.entity_ids.append(entity_id)
        automation_runtime.initialize_runtime_entity(simulation, target, entity_id)
        parameters.transferred_entity_ids.append(entity_id)
        transferred = True
        if len(target.entity_ids) >= parameters.minimum_units:
            break
    if len(target.entity_ids) >= parameters.minimum_units:
        if target.status is AutomationStatus.WAITING:
            automation_lifecycle.transition(
                simulation, target, AutomationStatus.ACTIVE, "REINFORCED"
            )
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.COMPLETED, "MINIMUM_FORCE_REACHED"
        )
    elif not transferred and automation.status is AutomationStatus.ACTIVE:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.WAITING, "NO_ELIGIBLE_UNITS"
        )
    elif transferred and automation.status is AutomationStatus.WAITING:
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.ACTIVE, "UNITS_AVAILABLE"
        )


def drive_repair(simulation: Simulation, automation: Automation) -> None:
    parameters = repair_parameters(automation)
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for entity_id in scheduled_entity_ids(simulation, automation):
        if simulation.assignments.get(entity_id) != automation.automation_id:
            continue
        phase = parameters.phases[entity_id]
        entity = simulation.entities[entity_id]
        if phase is RepairPhase.TRAVELING:
            building = simulation.entities.get(parameters.destinations[entity_id])
            if building is None:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.FAILED, "REPAIR_SOURCE_REMOVED"
                )
                automation_assignments.release_automation(
                    simulation, automation, clear_suspended=True
                )
                return
            if entity.health >= entity.kind.profile.max_health:
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
                parameters.phases[entity_id] = RepairPhase.RETURNING
                continue
            in_range = in_service_range(simulation, entity, building)
            clear = (
                repair_position_clear(simulation, entity, entity.position) if in_range else False
            )
            if in_range and clear:
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
                parameters.phases[entity_id] = RepairPhase.REPAIRING
                entity.state = UnitState.REPAIRING
                if automation.status is AutomationStatus.WAITING:
                    automation_lifecycle.transition(
                        simulation, automation, AutomationStatus.ACTIVE, "REPAIR_SPACE_AVAILABLE"
                    )
                continue
            if (
                entity.path
                and not in_range
                and entity.no_progress_ticks < simulation.TICKS_PER_SECOND
            ):
                continue
            if not allowance.claim():
                continue
            entity.path.clear()
            entity.move_target = None
            movement.reset_movement_liveness(entity)
            try:
                destination, point, path = simulation._nearest_repair_destination(
                    entity, building.entity_id
                )
            except PathfindingError:
                try:
                    destination, point, path = simulation._nearest_repair_destination(entity)
                except PathfindingError:
                    entity.state = UnitState.WAITING
                    if automation.status is AutomationStatus.ACTIVE:
                        automation_lifecycle.transition(
                            simulation, automation, AutomationStatus.WAITING, "NO_REPAIR_SPACE"
                        )
                    continue
            parameters.destinations[entity_id] = destination
            if automation.status is AutomationStatus.WAITING:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.ACTIVE, "REPAIR_SPACE_AVAILABLE"
                )
            movement.start_path(
                simulation, entity, point, path, automation.automation_id, UnitState.RETURNING
            )
        elif phase is RepairPhase.REPAIRING:
            if entity.path:
                continue
            building = simulation.entities.get(parameters.destinations[entity_id])
            if building is None:
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.FAILED, "REPAIR_SOURCE_REMOVED"
                )
                automation_assignments.release_automation(
                    simulation, automation, clear_suspended=True
                )
                return
            if not in_service_range(simulation, entity, building) or not repair_position_clear(
                simulation, entity, entity.position
            ):
                parameters.phases[entity_id] = RepairPhase.TRAVELING
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
                if building.kind is not EntityKind.REPAIR_HUB:
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
                automation_assignments.resume_suspended_assignment(
                    simulation, automation, entity_id
                )
                parameters.phases[entity_id] = RepairPhase.DONE
                continue
            return_position = parameters.return_positions[entity_id]
            if entity.path or entity.move_target is not None:
                continue
            if entity.position.distance_to(return_position) <= 0.05:
                automation_assignments.resume_suspended_assignment(
                    simulation, automation, entity_id
                )
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
                automation_lifecycle.transition(
                    simulation, automation, AutomationStatus.BLOCKED, str(error)
                )
                return
            movement.start_path(
                simulation,
                entity,
                return_position,
                path,
                automation.automation_id,
                UnitState.RETURNING,
            )
    if all(phase is RepairPhase.DONE for phase in parameters.phases.values()):
        automation_lifecycle.transition(
            simulation, automation, AutomationStatus.COMPLETED, "ALL_UNITS_REPAIRED"
        )


def interrupt_patrol(entity: Entity, parameters: PatrolParameters) -> None:
    # Dispatch advances the cursor. Rewind once to resume the interrupted leg.
    if entity.path or entity.move_target is not None:
        parameters.waypoint_indices[entity.entity_id] = (
            parameters.waypoint_indices[entity.entity_id] - 1
        ) % len(parameters.waypoints)
    entity.clear_recovery_request()
    entity.path.clear()
    entity.move_target = None
    entity.attack_target_id = None
    movement.reset_movement_liveness(entity)


def drive_patrol(
    simulation: Simulation,
    automation: Automation,
    queries: AutomationQueries,
) -> None:
    parameters = _patrol_parameters(automation)
    ordered = sorted(automation.entity_ids)
    indices = {key: index for index, key in enumerate(ordered)}
    radius = max(collision_radius(simulation.entities[key].kind) for key in ordered)
    _, enemy_index = queries.patrol_index(simulation, automation.owner_id, parameters.target)
    enemies = queries.patrol_enemies[automation.owner_id]
    responding = tuple(
        key
        for key in ordered
        if simulation.entities[key].kind.profile.attack_damage > 0
        and not simulation.entities[key].pursue_target
    )
    targets = dict(
        zip(
            responding,
            enemy_index.nearest_many(
                tuple(
                    (simulation.entities[key].position, simulation.entities[key].vision_range)
                    for key in responding
                )
            ),
            strict=True,
        )
    )
    builders = tuple(
        key
        for key in ordered
        if simulation.entities[key].profile.movable and simulation.entities[key].profile.can_repair
    )
    if builders:
        damaged, repair_index = queries.patrol_index(
            simulation, automation.owner_id, parameters.target, repair=True
        )
    else:
        damaged, repair_index = {}, SpatialIndex({})
    repair_targets = dict(
        zip(
            builders,
            repair_index.nearest_many(
                tuple(
                    (simulation.entities[key].position, simulation.entities[key].vision_range)
                    for key in builders
                )
            ),
            strict=True,
        )
    )
    # Responses must run for every member, even after the routing budget is exhausted.
    for key in ordered:
        if simulation.assignments.get(key) != automation.automation_id:
            continue
        entity = simulation.entities[key]
        if entity.profile.movable and entity.profile.can_repair:
            current = simulation.entities.get(entity.repair_target_id or "")
            if current is not None and current.entity_id in damaged:
                continue
            if entity.repair_target_id is not None:
                entity.repair_target_id = None
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
            target_id = repair_targets.get(key)
            if target_id is not None:
                interrupt_patrol(entity, parameters)
                entity.repair_target_id = target_id
                entity.state = UnitState.REPAIRING
                simulation.events.record(
                    simulation.tick,
                    EventType.REPAIR_STARTED,
                    target_id,
                    automation_id=automation.automation_id,
                    destination_id=key,
                )
                continue
        elif entity.kind.profile.attack_damage > 0:
            current = simulation.entities.get(entity.attack_target_id or "")
            if entity.pursue_target:
                if (
                    current is not None
                    and current.entity_id in enemies
                    and distance_to_target(current.selection_position, parameters.target)
                    <= simulation.PATROL_PURSUIT_RADIUS
                ):
                    continue
                entity.pursue_target = False
                entity.attack_target_id = None
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
                simulation.events.record(
                    simulation.tick,
                    EventType.PATROL_RETURNED,
                    key,
                    automation_id=automation.automation_id,
                )
                # Resume the route before considering another engagement.
                entity.state = UnitState.PATROLLING
                continue
            target_id = targets.get(key)
            if target_id is not None:
                interrupt_patrol(entity, parameters)
                entity.attack_target_id = target_id
                entity.pursue_target = True
                entity.state = UnitState.ATTACKING
                simulation.events.record(
                    simulation.tick,
                    EventType.PATROL_ENGAGED,
                    key,
                    automation_id=automation.automation_id,
                    target_id=target_id,
                )
                continue
        entity.state = UnitState.PATROLLING
    allowance = simulation._routes.automation_allowance(simulation.AUTOMATION_ROUTE_BUDGET)
    for key in scheduled_entity_ids(simulation, automation):
        if simulation.assignments.get(key) != automation.automation_id:
            continue
        entity = simulation.entities[key]
        if entity.path and not entity.pursue_target and entity.repair_target_id is None:
            previous_index = (parameters.waypoint_indices.get(key, 0) - 1) % len(
                parameters.waypoints
            )
            anchor = parameters.waypoints[previous_index]
            next_anchor = parameters.waypoints[(previous_index + 1) % len(parameters.waypoints)]
            tolerance = min(
                max(0.75, collision_radius(entity.kind) * 2),
                anchor.distance_to(next_anchor) * 0.35,
            )
            if entity.position.distance_to(entity.move_target or anchor) <= tolerance:
                entity.path.clear()
                entity.move_target = None
                movement.reset_movement_liveness(entity)
                simulation.events.record(
                    simulation.tick,
                    EventType.MOVEMENT_COMPLETED,
                    key,
                    position=[entity.position.x, entity.position.y],
                )
        if (
            entity.path
            or entity.pursue_target
            or entity.repair_target_id is not None
            or simulation.assignments.get(key) != automation.automation_id
        ):
            continue
        if not allowance.claim():
            break
        waypoint_index = parameters.waypoint_indices.get(key, 0)
        anchor = parameters.waypoints[waypoint_index]
        target = (
            simulation._gathering_slots(PointTarget(anchor), len(ordered), radius)[indices[key]]
            if len(ordered) > 1
            else anchor
        )
        try:
            paths = movement.plan_group_paths(simulation, (key,), {key: target}, shared_goal=anchor)
        except PathfindingError as error:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.BLOCKED, str(error)
            )
            return
        parameters.waypoint_indices[key] = (waypoint_index + 1) % len(parameters.waypoints)
        movement.start_path(
            simulation, entity, target, paths[key], automation.automation_id, UnitState.PATROLLING
        )


def drive_defend(
    simulation: Simulation,
    automation: Automation,
    queries: AutomationQueries,
) -> None:
    parameters = _defend_parameters(automation)
    allowance = simulation._routes.automation_allowance(
        simulation.GATHERING_PATH_BUDGET
        if parameters.gathering_point
        else simulation.AUTOMATION_ROUTE_BUDGET
    )
    buildings = simulation._building_cells()
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
    enemy_indexes = queries.enemy_indexes
    snapshot = enemy_indexes.get(automation.owner_id)
    if snapshot is None:
        enemies = {
            key: e.position
            for key, e in simulation.entities.items()
            if simulation.are_hostile(e.owner_id, automation.owner_id)
        }
        enemy_index = SpatialIndex(enemies)
        enemy_indexes[automation.owner_id] = enemies, enemy_index
    else:
        enemies, enemy_index = snapshot
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
    destinations: dict[str, Point] = {}
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
                entity.clear_recovery_request()
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
            entity.defend_arrived = True
            continue
        if not allowance.claim():
            continue
        destinations[key] = station
    if destinations:
        try:
            paths = movement.plan_group_paths(
                simulation, tuple(destinations), destinations, shared_goal=anchor
            )
        except PathfindingError as error:
            automation_lifecycle.transition(
                simulation, automation, AutomationStatus.BLOCKED, str(error)
            )
            return
        for key, station in destinations.items():
            movement.start_path(
                simulation,
                simulation.entities[key],
                station,
                paths[key],
                automation.automation_id,
                UnitState.DEFENDING,
            )
