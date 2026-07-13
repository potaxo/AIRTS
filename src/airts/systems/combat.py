"""Deterministic combat targeting and projectile resolution."""

from __future__ import annotations

from math import isclose
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

    positions_by_owner: dict[str, dict[str, Point]] = {}
    for entity_id, entity in simulation.entities.items():
        positions_by_owner.setdefault(entity.owner_id, {})[entity_id] = entity.selection_position
    owner_indexes = {
        owner_id: SpatialIndex(positions) for owner_id, positions in positions_by_owner.items()
    }
    hostile_indexes = {
        owner_id: tuple(
            owner_indexes[other_id] for other_id in sorted(owner_indexes) if other_id != owner_id
        )
        for owner_id in owner_indexes
    }
    for entity_id in sorted(tuple(simulation.entities)):
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
        if (
            ordered_target is not None
            and not attacker.pursue_target
            and attacker.selection_position.distance_to(ordered_target.selection_position)
            > attack_range
        ):
            ordered_target = None
            attacker.attack_target_id = None
        if (
            attacker.pursue_target
            and ordered_target is not None
            and not attacker.path
            and simulation.game_map.cell_for(attacker.position)
            not in {
                simulation.game_map.cell_for(point)
                for point in simulation._interaction_points(ordered_target)
            }
            and simulation._routes.claim_combat_route()
        ):
            chase_target(simulation, attacker, ordered_target)
        firing_target = (
            ordered_target
            if ordered_target is not None
            and attacker.selection_position.distance_to(ordered_target.selection_position)
            <= attack_range
            else nearest_enemy_in_range(simulation, attacker, hostile_indexes[attacker.owner_id])
        )
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
            position=attacker.selection_position,
            destination=firing_target.selection_position,
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
) -> Entity | None:
    """Return the nearest in-range hostile with stable ID tie-breaking."""

    attack_range = attacker.kind.profile.attack_range
    candidates = [
        (
            attacker.selection_position.distance_to(entity.selection_position),
            entity.entity_id,
            entity,
        )
        for entity_index in enemy_indexes
        for entity_id in entity_index.nearby(attacker.selection_position, attack_range)
        if (entity := simulation.entities[entity_id]).entity_id != attacker.entity_id
        if entity.owner_id != attacker.owner_id
        and attacker.selection_position.distance_to(entity.selection_position) <= attack_range
    ]
    return min(candidates)[2] if candidates else None


def chase_target(simulation: Simulation, attacker: Entity, target: Entity) -> None:
    """Route an ordered attacker to a valid interaction point."""

    if attacker.congestion_stopped:
        return
    try:
        point, path = simulation._routes.shared_path_to_any(
            attacker.position,
            simulation._interaction_points(target),
            simulation._building_cells(),
        )
    except PathfindingError:
        return
    simulation._start_path(attacker, point, path, "combat", UnitState.ATTACKING)
