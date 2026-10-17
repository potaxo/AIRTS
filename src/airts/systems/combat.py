"""Deterministic combat targeting and projectile resolution."""

from __future__ import annotations

from math import ceil, isclose
from typing import TYPE_CHECKING

from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    defend_parameters,
    patrol_parameters,
)
from airts.contracts.commands import RemoveEntityCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, distance_to_target
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems import automation_execution, combat_geometry, movement
from airts.world.entities import SHELL_BLAST_TICKS, Entity, Projectile, ProjectileTrace, UnitState

if TYPE_CHECKING:
    from airts.simulation import Simulation


def drive_combat(simulation: Simulation) -> None:
    """Acquire deterministic targets and launch projectiles for armed entities."""

    positions_by_owner: dict[str, dict[str, Point]] = {}
    for entity_id, entity in simulation.entities.items():
        positions_by_owner.setdefault(entity.owner_id, {})[entity_id] = entity.selection_position
    if len(positions_by_owner) <= 1:
        for entity in simulation.entities.values():
            if entity.attack_cooldown > 0:
                entity.attack_cooldown -= 1
        return
    owner_indexes = {
        owner_id: SpatialIndex(positions) for owner_id, positions in positions_by_owner.items()
    }
    positions = {
        key: point for owned in positions_by_owner.values() for key, point in owned.items()
    }
    nearest_enemies: dict[str, str] = {}
    approach_cache: dict[tuple[str, float], tuple[Point, ...]] = {}
    pending_routes: list[str] = []
    blocked_routes: set[str] = set()
    interaction_cache: dict[str, frozenset[tuple[int, int]]] = {}
    fired: dict[str, str] = {}
    for owner_id, owned in positions_by_owner.items():
        armed = []
        query_list = []
        for key in owned:
            entity = simulation.entities[key]
            profile = entity.kind.profile
            if profile.attack_damage <= 0 or not entity.is_complete:
                continue
            target = simulation.entities.get(entity.attack_target_id or "")
            if (
                target is not None
                and simulation.are_hostile(target.owner_id, owner_id)
                and _squared_distance(positions[key], positions[target.entity_id])
                <= profile.attack_range * profile.attack_range
            ):
                continue
            assigned = simulation.assignments.get(key)
            if (
                assigned is not None
                and simulation.automations[assigned].kind is AutomationKind.REPAIR_AND_RETURN
            ):
                continue
            if (
                entity.pursue_target
                and target is not None
                and simulation.are_hostile(target.owner_id, owner_id)
                and (
                    not entity.path
                    or (
                        entity.route_ticks >= simulation.TICKS_PER_SECOND
                        and entity.move_target is not None
                        and _squared_distance(entity.move_target, positions[target.entity_id])
                        > profile.attack_range * profile.attack_range
                    )
                )
                and simulation.game_map.cell_for(entity.position)
                not in _interaction_cells(simulation, target, interaction_cache)
            ):
                goals = _approach_points(simulation, entity, target, approach_cache)
                if simulation._routes.can_reach_any(
                    entity.position, goals, simulation._building_cells()
                ):
                    pending_routes.append(key)
                else:
                    blocked_routes.add(key)
            armed.append(key)
            query_list.append((positions[key], profile.attack_range))
        armed_ids, queries = tuple(armed), tuple(query_list)
        for other_id in sorted(owner_indexes):
            if not simulation.are_hostile(other_id, owner_id):
                continue
            candidates = owner_indexes[other_id].nearest_many(queries)
            for key, candidate in zip(armed_ids, candidates, strict=True):
                if candidate is None:
                    continue
                previous = nearest_enemies.get(key)
                if previous is None or (
                    _squared_distance(positions[key], positions[candidate]),
                    candidate,
                ) < (_squared_distance(positions[key], positions[previous]), previous):
                    nearest_enemies[key] = candidate
    # Admission rotates independently of the stable firing/event order. Static
    # failures never consume route allowance; connectivity is derived and exact
    # even after save/load or cache eviction.
    pending_routes.sort()
    remaining = simulation._routes.combat_budget - simulation._routes.combat_route_count
    if pending_routes:
        offset = simulation.tick % len(pending_routes)
        pending_routes = pending_routes[offset:] + pending_routes[:offset]
    admitted_routes = frozenset(pending_routes[:remaining])
    # Persistence deliberately serializes entity mappings by stable ID.  Combat
    # therefore has to use the same order as a freshly loaded simulation rather
    # than depending on the construction history of the live dict.
    for entity_id in sorted(simulation.entities):
        attacker = simulation.entities.get(entity_id)
        if attacker is None or attacker.kind.profile.attack_damage <= 0 or not attacker.is_complete:
            continue
        attacker_position = positions[entity_id]
        assigned_id = simulation.assignments.get(entity_id)
        if (
            assigned_id is not None
            and simulation.automations[assigned_id].kind is AutomationKind.REPAIR_AND_RETURN
        ):
            continue
        if attacker.attack_cooldown > 0:
            attacker.attack_cooldown -= 1
        ordered_target = simulation.entities.get(attacker.attack_target_id or "")
        if ordered_target is not None and attacker.attack_gather_point is not None:
            attacker.attack_gather_point = ordered_target.selection_position
        if ordered_target is None or not simulation.are_hostile(
            ordered_target.owner_id, attacker.owner_id
        ):
            attacker.pursue_target = False
            ordered_target = None
            attacker.attack_target_id = None
        attack_range = attacker.kind.profile.attack_range
        attack_range_squared = attack_range * attack_range
        ordered_target_distance_squared = (
            _squared_distance(attacker_position, positions[ordered_target.entity_id])
            if ordered_target is not None
            else None
        )
        if (
            ordered_target is not None
            and not attacker.pursue_target
            and ordered_target_distance_squared is not None
            and ordered_target_distance_squared > attack_range_squared
        ):
            ordered_target = None
            attacker.attack_target_id = None
            ordered_target_distance_squared = None
        if (
            attacker.pursue_target
            and ordered_target is not None
            and ordered_target_distance_squared is not None
            and ordered_target_distance_squared <= attack_range_squared
        ):
            if attacker.path or attacker.move_target is not None:
                attacker.path.clear()
                attacker.move_target = None
                movement.reset_movement_liveness(attacker)
            attacker.state = UnitState.ATTACKING
        if entity_id in blocked_routes:
            if attacker.state is not UnitState.WAITING:
                simulation.events.record(
                    simulation.tick,
                    EventType.PATHFINDING_FAILED,
                    entity_id,
                    operation="combat",
                    reason="NO_REACHABLE_FIRING_POSITION",
                    target_id=attacker.attack_target_id,
                )
            attacker.state = UnitState.WAITING
            attacker.path.clear()
            attacker.move_target = None
            movement.reset_movement_liveness(attacker)
        elif entity_id in admitted_routes and simulation._routes.claim_combat_route():
            assert ordered_target is not None
            chase_target(simulation, attacker, ordered_target, approach_cache)
        firing_target: Entity | None
        if (
            ordered_target is not None
            and ordered_target_distance_squared is not None
            and ordered_target_distance_squared <= attack_range_squared
        ):
            firing_target = ordered_target
        else:
            firing_target = simulation.entities.get(nearest_enemies.get(entity_id, ""))
        if firing_target is None:
            continue
        if not attacker.pursue_target:
            attacker.attack_target_id = firing_target.entity_id
        if attacker.attack_cooldown:
            continue
        speed = attacker.kind.profile.projectile_speed
        if speed <= 0:
            continue
        destination = positions[firing_target.entity_id]
        if attacker.kind.profile.splash_radius > 0:
            destination = combat_geometry.predicted_impact_point(
                attacker_position,
                destination,
                movement.observed_velocity(simulation, firing_target),
                speed,
                simulation.game_map.width,
                simulation.game_map.height,
            )
        projectile_id = f"projectile_{simulation._next_projectile_number:06d}"
        simulation._next_projectile_number += 1
        projectile = Projectile(
            projectile_id=projectile_id,
            source_entity_id=attacker.entity_id,
            target_entity_id=firing_target.entity_id,
            owner_id=attacker.owner_id,
            weapon_kind=attacker.kind,
            position=attacker_position,
            destination=destination,
            damage=attacker.kind.profile.attack_damage,
            speed=speed,
        )
        simulation.projectiles[projectile_id] = projectile
        fired[entity_id] = firing_target.entity_id
        attacker.attack_cooldown = attacker.kind.profile.attack_interval_ticks
        simulation.events.record(
            simulation.tick,
            EventType.PROJECTILE_LAUNCHED,
            attacker.entity_id,
            projectile_id=projectile_id,
            target_id=firing_target.entity_id,
            damage=projectile.damage,
            position=[projectile.position.x, projectile.position.y],
        )
    _join_nearby_combat(simulation, positions_by_owner, positions, fired)


def _join_nearby_combat(
    simulation: Simulation,
    positions_by_owner: dict[str, dict[str, Point]],
    positions: dict[str, Point],
    fired: dict[str, str],
) -> None:
    if not fired:
        return
    sources_by_owner: dict[str, dict[str, Point]] = {}
    for key in fired:
        source_owner = simulation.entities[key].owner_id
        sources_by_owner.setdefault(source_owner, {})[key] = positions[key]
    for owner in sorted(positions_by_owner):
        owned = positions_by_owner[owner]
        sources = {
            key: point
            for source_owner, shots in sources_by_owner.items()
            if not simulation.are_hostile(source_owner, owner)
            for key, point in shots.items()
        }
        if not sources:
            continue
        eligible = []
        for key in sorted(owned):
            entity = simulation.entities[key]
            if (
                key not in fired
                and entity.kind.profile.combat_support
                and entity.is_complete
                and not entity.pursue_target
                and entity.hold_anchor is None
                and (
                    entity.last_player_control_tick is None
                    or simulation.tick - entity.last_player_control_tick
                    >= simulation.COMBAT_SUPPORT_MANUAL_COOLDOWN_TICKS
                )
            ):
                assigned = simulation.assignments.get(key)
                if assigned is not None:
                    automation = simulation.automations[assigned]
                    if automation.status is not AutomationStatus.ACTIVE or automation.kind not in {
                        AutomationKind.PATROL,
                        AutomationKind.DEFEND,
                    }:
                        continue
                eligible.append(key)
        ids = tuple(eligible)
        if not ids:
            continue
        nearby = SpatialIndex(sources).nearest_many(
            tuple((positions[key], simulation.COMBAT_SUPPORT_RADIUS) for key in ids)
        )
        for key, source in zip(ids, nearby, strict=True):
            if source is None:
                continue
            entity = simulation.entities[key]
            target = simulation.entities[fired[source]]
            if not simulation.are_hostile(target.owner_id, owner):
                continue
            assigned = simulation.assignments.get(key)
            if assigned is not None:
                automation = simulation.automations[assigned]
                if automation.status is not AutomationStatus.ACTIVE:
                    continue
                if automation.kind is AutomationKind.PATROL:
                    parameters = patrol_parameters(automation)
                    if (
                        distance_to_target(target.selection_position, parameters.target)
                        > simulation.PATROL_PURSUIT_RADIUS
                    ):
                        continue
                    automation_execution.interrupt_patrol(entity, parameters)
                elif automation.kind is AutomationKind.DEFEND:
                    station = defend_parameters(automation).stations[key]
                    if (
                        target.selection_position.distance_to(station)
                        > simulation.DEFEND_PURSUIT_RADIUS
                    ):
                        continue
                    entity.clear_recovery_request()
                else:
                    continue
            elif entity.move_target is not None:
                entity.move_queue.insert(0, entity.move_target)
            entity.path.clear()
            entity.move_target = None
            entity.attack_target_id = target.entity_id
            entity.pursue_target = True
            entity.state = UnitState.ATTACKING
            movement.reset_movement_liveness(entity)
            simulation.events.record(
                simulation.tick,
                EventType.COMBAT_SUPPORT_JOINED,
                key,
                source_id=source,
                target_id=target.entity_id,
            )


def drive_projectiles(simulation: Simulation) -> None:
    """Advance live projectiles and retain bounded visual traces."""

    splash_index: SpatialIndex | None = None
    with simulation._batch_entity_removals():
        simulation.projectile_traces = [
            trace for trace in simulation.projectile_traces if trace.expires_tick > simulation.tick
        ]
        for projectile_id in sorted(tuple(simulation.projectiles)):
            projectile = simulation.projectiles.get(projectile_id)
            if projectile is None:
                continue
            splash_radius = projectile.weapon_kind.profile.splash_radius
            target = (
                simulation.entities.get(projectile.target_entity_id) if splash_radius == 0 else None
            )
            if (
                splash_radius == 0
                and target is not None
                and simulation.are_hostile(target.owner_id, projectile.owner_id)
            ):
                projectile.destination = target.selection_position
            destination = projectile.destination
            distance = projectile.position.distance_to(destination)
            maximum_step = projectile.speed * simulation.TICK_SECONDS
            if distance <= maximum_step or isclose(distance, maximum_step):
                projectile.position = destination
                projectile.trajectory.append(destination)
                if splash_radius > 0:
                    if splash_index is None:
                        splash_index = SpatialIndex(
                            {
                                key: entity.selection_position
                                for key, entity in simulation.entities.items()
                            }
                        )
                    impact_shell(simulation, projectile, splash_index)
                elif target is None or not simulation.are_hostile(
                    target.owner_id, projectile.owner_id
                ):
                    finish_projectile(simulation, projectile)
                else:
                    impact_projectile(simulation, projectile, target)
                continue
            fraction = maximum_step / distance
            projectile.position = Point(
                projectile.position.x + (destination.x - projectile.position.x) * fraction,
                projectile.position.y + (destination.y - projectile.position.y) * fraction,
            )
            projectile.trajectory.append(projectile.position)


def impact_projectile(simulation: Simulation, projectile: Projectile, target: Entity) -> None:
    """Apply one authoritative impact and remove a destroyed target."""

    simulation.events.record(
        simulation.tick,
        EventType.PROJECTILE_IMPACT,
        projectile.projectile_id,
        source_id=projectile.source_entity_id,
        target_id=target.entity_id,
        damage=projectile.damage,
        target_health=max(0, target.health - projectile.damage),
        position=[projectile.position.x, projectile.position.y],
    )
    damage_projectile_target(simulation, projectile, target, projectile.damage)
    finish_projectile(simulation, projectile)


def impact_shell(simulation: Simulation, projectile: Projectile, index: SpatialIndex) -> None:
    """Explode at the launch-time destination, even if the original target is gone."""

    radius = projectile.weapon_kind.profile.splash_radius
    simulation.events.record(
        simulation.tick,
        EventType.PROJECTILE_IMPACT,
        projectile.projectile_id,
        source_id=projectile.source_entity_id,
        damage=projectile.damage,
        splash_radius=radius,
        position=[projectile.position.x, projectile.position.y],
    )
    for entity_id in index.nearby(projectile.destination, radius):
        target = simulation.entities.get(entity_id)
        if target is None or not simulation.are_hostile(target.owner_id, projectile.owner_id):
            continue
        distance = target.selection_position.distance_to(projectile.destination)
        if distance >= radius:
            continue
        damage = ceil(projectile.damage * (1 - distance / radius))
        damage_projectile_target(simulation, projectile, target, damage)
    finish_projectile(simulation, projectile)


def damage_projectile_target(
    simulation: Simulation, projectile: Projectile, target: Entity, damage: int
) -> None:
    """Share damage attribution, casualty removal and control cleanup for every victim."""

    target.health = max(0, target.health - damage)
    source_alive = projectile.source_entity_id in simulation.entities
    target.last_attacker_id = projectile.source_entity_id if source_alive else None
    target.last_attacked_tick = simulation.tick if source_alive else None
    simulation.events.record(
        simulation.tick,
        EventType.COMBAT_ATTACK,
        projectile.source_entity_id,
        projectile_id=projectile.projectile_id,
        target_id=target.entity_id,
        damage=damage,
        target_health=target.health,
    )
    if target.health == 0:
        simulation.events.record(
            simulation.tick,
            EventType.ENTITY_DESTROYED,
            target.entity_id,
            attacker_id=projectile.source_entity_id,
            projectile_id=projectile.projectile_id,
        )
        simulation._remove_entity(RemoveEntityCommand(target.entity_id, "COMBAT_DESTROYED"))


def finish_projectile(simulation: Simulation, projectile: Projectile) -> None:
    """Retire a projectile and publish its deterministic trace."""

    simulation.projectiles.pop(projectile.projectile_id, None)
    simulation.projectile_traces.append(
        ProjectileTrace(
            projectile.projectile_id,
            projectile.weapon_kind,
            tuple(projectile.trajectory),
            simulation.tick
            + (
                SHELL_BLAST_TICKS
                if projectile.weapon_kind.profile.splash_radius > 0
                else simulation.TICKS_PER_SECOND
            ),
        )
    )


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y


def chase_target(
    simulation: Simulation,
    attacker: Entity,
    target: Entity,
    approach_cache: dict[tuple[str, float], tuple[Point, ...]] | None = None,
) -> None:
    """Route an ordered attacker to a valid interaction point."""

    goals = _approach_points(simulation, attacker, target, approach_cache)
    if not goals:
        return
    slot = sum((index + 1) * ord(char) for index, char in enumerate(attacker.entity_id)) % len(
        goals
    )
    approach = goals[slot]
    direct = simulation._routes.segment_path(
        attacker.position, approach, simulation._building_cells(), maximum_cost=float("inf")
    )
    if direct is not None:
        point = approach
        path = direct
    else:
        try:
            point, path = simulation._routes.shared_path_to_any(
                attacker.position,
                goals,
                simulation._building_cells(),
            )
        except PathfindingError:
            return
    movement.start_path(simulation, attacker, point, path, "combat", UnitState.ATTACKING)


def _approach_points(
    simulation: Simulation,
    attacker: Entity,
    target: Entity,
    cache: dict[tuple[str, float], tuple[Point, ...]] | None,
) -> tuple[Point, ...]:
    key = (target.entity_id, attacker.kind.profile.attack_range)
    goals = cache.get(key) if cache is not None else None
    if goals is None:
        goals = combat_geometry.firing_approach_points(simulation, attacker, target)
        if cache is not None:
            cache[key] = goals
    return goals


def _interaction_cells(
    simulation: Simulation,
    target: Entity,
    cache: dict[str, frozenset[tuple[int, int]]],
) -> frozenset[tuple[int, int]]:
    cells = cache.get(target.entity_id)
    if cells is None:
        cells = frozenset(
            simulation.game_map.cell_for(point) for point in simulation._interaction_points(target)
        )
        cache[target.entity_id] = cells
    return cells
