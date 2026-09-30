"""Deterministic combat targeting and projectile resolution."""

from __future__ import annotations

from math import ceil, floor, isclose
from typing import TYPE_CHECKING

from airts.automations import AutomationKind
from airts.commands import RemoveEntityCommand
from airts.events import EventType
from airts.geometry import Point
from airts.navigation.pathfinding import PathfindingError, PathResult, passable_segment_cells
from airts.navigation.spatial_index import SpatialIndex
from airts.world.entities import Entity, UnitState
from airts.world.projectiles import Projectile, ProjectileTrace, projectile_speed

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
    for owner_id, owned in positions_by_owner.items():
        armed = []
        query_list = []
        for key in owned:
            entity = simulation.entities[key]
            profile = entity.kind.profile
            if profile.attack_damage <= 0:
                continue
            target = simulation.entities.get(entity.attack_target_id or "")
            if (
                target is not None
                and target.owner_id != owner_id
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
            armed.append(key)
            query_list.append((positions[key], profile.attack_range))
        armed_ids, queries = tuple(armed), tuple(query_list)
        for other_id in sorted(owner_indexes):
            if other_id == owner_id:
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
    # Persistence deliberately serializes entity mappings by stable ID.  Combat
    # therefore has to use the same order as a freshly loaded simulation rather
    # than depending on the construction history of the live dict.
    for entity_id in sorted(simulation.entities):
        attacker = simulation.entities.get(entity_id)
        if attacker is None or attacker.kind.profile.attack_damage <= 0:
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
        if ordered_target is None or ordered_target.owner_id == attacker.owner_id:
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
            and (attacker.path or attacker.move_target is not None)
        ):
            attacker.path.clear()
            attacker.move_target = None
            simulation._reset_movement_liveness(attacker, clear_stop=True)
            attacker.state = UnitState.ATTACKING
        if (
            attacker.pursue_target
            and ordered_target is not None
            and not attacker.path
            and ordered_target_distance_squared is not None
            and ordered_target_distance_squared > attack_range_squared
            and simulation.game_map.cell_for(attacker.position)
            not in {
                simulation.game_map.cell_for(point)
                for point in simulation._interaction_points(ordered_target)
            }
            and simulation._routes.claim_combat_route()
        ):
            chase_target(simulation, attacker, ordered_target)
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
        speed = projectile_speed(attacker.kind)
        if speed <= 0:
            continue
        projectile_id = f"projectile_{simulation._next_projectile_number:06d}"
        simulation._next_projectile_number += 1
        projectile = Projectile(
            projectile_id=projectile_id,
            source_entity_id=attacker.entity_id,
            target_entity_id=firing_target.entity_id,
            owner_id=attacker.owner_id,
            weapon_kind=attacker.kind,
            position=attacker_position,
            destination=positions[firing_target.entity_id],
            damage=attacker.kind.profile.attack_damage,
            speed=speed,
        )
        simulation.projectiles[projectile_id] = projectile
        attacker.attack_cooldown = 10
        simulation.events.record(
            simulation.tick,
            EventType.PROJECTILE_LAUNCHED,
            attacker.entity_id,
            projectile_id=projectile_id,
            target_id=firing_target.entity_id,
            damage=projectile.damage,
            position=[projectile.position.x, projectile.position.y],
        )


def drive_projectiles(simulation: Simulation) -> None:
    """Advance live projectiles and retain bounded visual traces."""

    simulation.projectile_traces = [
        trace for trace in simulation.projectile_traces if trace.expires_tick > simulation.tick
    ]
    for projectile_id in sorted(tuple(simulation.projectiles)):
        projectile = simulation.projectiles.get(projectile_id)
        if projectile is None:
            continue
        target = simulation.entities.get(projectile.target_entity_id)
        if target is not None and target.owner_id != projectile.owner_id:
            projectile.destination = target.selection_position
        destination = projectile.destination
        distance = projectile.position.distance_to(destination)
        maximum_step = projectile.speed * simulation.TICK_SECONDS
        if distance <= maximum_step or isclose(distance, maximum_step):
            projectile.position = destination
            projectile.trajectory.append(destination)
            if target is None or target.owner_id == projectile.owner_id:
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

    target.health = max(0, target.health - projectile.damage)
    target.last_attacker_id = projectile.source_entity_id
    target.last_attacked_tick = simulation.tick
    simulation.events.record(
        simulation.tick,
        EventType.PROJECTILE_IMPACT,
        projectile.projectile_id,
        source_id=projectile.source_entity_id,
        target_id=target.entity_id,
        damage=projectile.damage,
        target_health=target.health,
        position=[projectile.position.x, projectile.position.y],
    )
    simulation.events.record(
        simulation.tick,
        EventType.COMBAT_ATTACK,
        projectile.source_entity_id,
        projectile_id=projectile.projectile_id,
        target_id=target.entity_id,
        damage=projectile.damage,
        target_health=target.health,
    )
    finish_projectile(simulation, projectile)
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
            simulation.tick + simulation.TICKS_PER_SECOND,
        )
    )


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y


def chase_target(simulation: Simulation, attacker: Entity, target: Entity) -> None:
    """Route an ordered attacker to a valid interaction point."""

    if attacker.congestion_stopped:
        return
    goals = _firing_approach_points(simulation, attacker, target)
    try:
        point, path = simulation._routes.shared_path_to_any(
            attacker.position,
            goals,
            simulation._building_cells(),
        )
    except PathfindingError:
        return
    slot = sum((index + 1) * ord(char) for index, char in enumerate(attacker.entity_id)) % len(
        goals
    )
    approach = goals[slot]
    direct = passable_segment_cells(
        simulation.game_map, attacker.position, approach, simulation._building_cells()
    )
    if direct is not None:
        point = approach
        path = PathResult(
            direct,
            (point,),
            sum(simulation.game_map.terrain_at_cell(c).movement_cost for c in direct[1:]),
        )
    simulation._start_path(attacker, point, path, "combat", UnitState.ATTACKING)


def _firing_approach_points(
    simulation: Simulation,
    attacker: Entity,
    target: Entity,
) -> tuple[Point, ...]:
    """Return a shared passable firing ring instead of four congested adjacent cells."""

    center = target.selection_position
    attack_range = attacker.kind.profile.attack_range
    outer_radius = max(0.5, attack_range - 0.25)
    inner_radius = max(0.0, outer_radius - 1.0)
    target_cells = target.occupied_cells
    points = tuple(
        Point(x + 0.5, y + 0.5)
        for y in range(floor(center.y - outer_radius), ceil(center.y + outer_radius) + 1)
        for x in range(floor(center.x - outer_radius), ceil(center.x + outer_radius) + 1)
        if simulation.game_map.contains_cell((x, y))
        and simulation.game_map.is_cell_passable((x, y))
        and (x, y) not in target_cells
        and inner_radius <= Point(x + 0.5, y + 0.5).distance_to(center) <= outer_radius
    )
    return points or simulation._interaction_points(target)
