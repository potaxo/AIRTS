"""Deterministic combat targeting and projectile resolution."""

from __future__ import annotations

from math import ceil, floor, isclose
from typing import TYPE_CHECKING

from airts.automations import AutomationKind
from airts.commands import RemoveEntityCommand
from airts.events import EventType
from airts.geometry import Point
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.world.entities import Entity, UnitState
from airts.world.projectiles import Projectile, ProjectileTrace, projectile_speed

if TYPE_CHECKING:
    from airts.simulation import Simulation


def drive_combat(simulation: Simulation) -> None:
    """Acquire deterministic targets and launch projectiles for armed entities."""

    entity_values = simulation.entities.values()
    first_entity = next(iter(entity_values), None)
    if first_entity is None:
        return
    if all(entity.owner_id == first_entity.owner_id for entity in entity_values):
        for entity in entity_values:
            if entity.attack_cooldown > 0:
                entity.attack_cooldown -= 1
        return
    positions_by_owner: dict[str, dict[str, Point]] = {}
    selection_positions: dict[str, Point] = {}
    mutable_owner_bounds: dict[str, list[float]] = {}
    for entity_id, entity in simulation.entities.items():
        position = entity.selection_position
        selection_positions[entity_id] = position
        positions_by_owner.setdefault(entity.owner_id, {})[entity_id] = position
        bounds = mutable_owner_bounds.get(entity.owner_id)
        if bounds is None:
            mutable_owner_bounds[entity.owner_id] = [
                position.x,
                position.y,
                position.x,
                position.y,
            ]
        else:
            if position.x < bounds[0]:
                bounds[0] = position.x
            if position.y < bounds[1]:
                bounds[1] = position.y
            if position.x > bounds[2]:
                bounds[2] = position.x
            if position.y > bounds[3]:
                bounds[3] = position.y
    owner_indexes = {
        owner_id: SpatialIndex(positions) for owner_id, positions in positions_by_owner.items()
    }
    hostile_indexes = {
        owner_id: tuple(
            owner_indexes[other_id] for other_id in sorted(owner_indexes) if other_id != owner_id
        )
        for owner_id in owner_indexes
    }
    owner_bounds = {
        owner_id: (bounds[0], bounds[1], bounds[2], bounds[3])
        for owner_id, bounds in mutable_owner_bounds.items()
    }
    hostile_bounds = {
        owner_id: tuple(
            owner_bounds[other_id] for other_id in sorted(owner_bounds) if other_id != owner_id
        )
        for owner_id in owner_bounds
    }
    # Persistence deliberately serializes entity mappings by stable ID.  Combat
    # therefore has to use the same order as a freshly loaded simulation rather
    # than depending on the construction history of the live dict.
    for entity_id in sorted(simulation.entities):
        attacker = simulation.entities.get(entity_id)
        if attacker is None or attacker.kind.profile.attack_damage <= 0:
            continue
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
            _squared_distance(
                selection_positions[entity_id],
                selection_positions[ordered_target.entity_id],
            )
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
        elif _hostile_bounds_in_range(
            selection_positions[entity_id],
            hostile_bounds[attacker.owner_id],
            attack_range_squared,
        ):
            firing_target = nearest_enemy_in_range(
                simulation,
                attacker,
                hostile_indexes[attacker.owner_id],
                selection_positions,
            )
        else:
            firing_target = None
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
            position=selection_positions[entity_id],
            destination=selection_positions[firing_target.entity_id],
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


def nearest_enemy_in_range(
    simulation: Simulation,
    attacker: Entity,
    enemy_indexes: tuple[SpatialIndex, ...],
    selection_positions: dict[str, Point] | None = None,
) -> Entity | None:
    """Return the nearest in-range hostile with stable ID tie-breaking."""

    attack_range = attacker.kind.profile.attack_range
    attacker_position = (
        attacker.selection_position
        if selection_positions is None
        else selection_positions[attacker.entity_id]
    )
    candidate_ids = tuple(
        entity_id
        for entity_index in enemy_indexes
        if (entity_id := entity_index.nearest(attacker_position, attack_range)) is not None
    )
    if not candidate_ids:
        return None
    nearest_id = min(
        candidate_ids,
        key=lambda entity_id: (
            _squared_distance(
                attacker_position,
                (
                    simulation.entities[entity_id].selection_position
                    if selection_positions is None
                    else selection_positions[entity_id]
                ),
            ),
            entity_id,
        ),
    )
    return simulation.entities[nearest_id]


def _squared_distance(first: Point, second: Point) -> float:
    offset_x = first.x - second.x
    offset_y = first.y - second.y
    return offset_x * offset_x + offset_y * offset_y


def _squared_distance_to_bounds(
    point: Point,
    bounds: tuple[float, float, float, float],
) -> float:
    minimum_x, minimum_y, maximum_x, maximum_y = bounds
    offset_x = max(minimum_x - point.x, 0.0, point.x - maximum_x)
    offset_y = max(minimum_y - point.y, 0.0, point.y - maximum_y)
    return offset_x * offset_x + offset_y * offset_y


def _hostile_bounds_in_range(
    point: Point,
    bounds: tuple[tuple[float, float, float, float], ...],
    range_squared: float,
) -> bool:
    """Reject distant hostile owners without a generator on the common two-owner path."""

    if len(bounds) == 1:
        return _squared_distance_to_bounds(point, bounds[0]) <= range_squared
    return any(_squared_distance_to_bounds(point, item) <= range_squared for item in bounds)


def chase_target(simulation: Simulation, attacker: Entity, target: Entity) -> None:
    """Route an ordered attacker to a valid interaction point."""

    if attacker.congestion_stopped:
        return
    try:
        point, path = simulation._routes.shared_path_to_any(
            attacker.position,
            _firing_approach_points(simulation, attacker, target),
            simulation._building_cells(),
        )
    except PathfindingError:
        return
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
