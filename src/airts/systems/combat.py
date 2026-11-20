"""Deterministic combat targeting and projectile resolution."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import chain
from math import ceil, floor, isclose
from typing import TYPE_CHECKING

import numpy as np

from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    defend_parameters,
    patrol_parameters,
)
from airts.contracts.commands import RemoveEntityCommand
from airts.contracts.events import EventType
from airts.contracts.spatial import Point, distance_to_target
from airts.navigation.pathfinding import PathfindingError, PathResult, RouteAllowance, RouteDeferred
from airts.navigation.spatial_index import GroupedSpatialIndex, SpatialIndex
from airts.systems import automation_execution, combat_geometry, formations, movement
from airts.world._numeric import point_coordinates
from airts.world.entities import (
    SHELL_BLAST_TICKS,
    Entity,
    OrderKind,
    Projectile,
    ProjectileTrace,
    UnitState,
)

if TYPE_CHECKING:
    from airts.simulation import Simulation


@dataclass(slots=True)
class _AttackRouteBudget:
    """One bounded search/materialization allowance for all pursuit groups this tick."""

    search: RouteAllowance
    attempts: int = 256
    cells: int = 32_768

    def admit(self) -> bool:
        if self.attempts <= 0 or self.cells <= 0:
            return False
        self.attempts -= 1
        return True

    def account(self, path: PathResult) -> None:
        self.cells -= len(path.cells)


def drive_combat(
    simulation: Simulation, *, share_positions: bool = False
) -> dict[str, dict[str, Point]] | None:
    """Acquire targets and launch projectiles, optionally retaining their geometry."""
    positions_by_owner: dict[str, dict[str, Point]] = defaultdict(dict)
    formation_attacks: dict[tuple[str, str], list[Entity]] = {}
    recent = simulation.tick - 2
    fresh_control = False
    for entity_id, entity in simulation.entities.items():
        positions_by_owner[entity.owner_id][entity_id] = entity.selection_position
        if entity.pursue_target:
            if not fresh_control:
                stamp = entity.last_player_control_tick
                fresh_control = stamp is not None and stamp >= recent
            if entity.formation_leg is not None:
                assert entity.formation_group is not None and entity.attack_target_id is not None
                formation_attacks.setdefault(
                    (entity.formation_group, entity.attack_target_id), []
                ).append(entity)
    budget = _AttackRouteBudget(
        simulation._routes.automation_allowance(simulation.COMBAT_PATH_BUDGET, search_limit=8_192),
        attempts=1_024 if fresh_control else 256,
    )
    if formation_attacks:
        _drive_formation_attacks(simulation, budget, formation_attacks)
    if len(positions_by_owner) <= 1:
        for entity in simulation.entities.values():
            if entity.attack_cooldown > 0:
                entity.attack_cooldown -= 1
        return positions_by_owner if share_positions else None
    owner_index: GroupedSpatialIndex | None = None
    positions: dict[str, Point] = {}
    for owned in positions_by_owner.values():
        positions.update(owned)
    nearest_enemies: dict[str, str] = {}
    firing_targets: dict[str, Entity] = {}
    route_groups: dict[tuple[str, float], list[str]] = {}
    pending_groups: dict[tuple[str, float], tuple[list[str], tuple[Point, ...]]] = {}
    blocked_routes: set[str] = set()
    interaction_cache: dict[str, frozenset[tuple[int, int]]] = {}
    fired: dict[str, str] = {}
    for owner_id, owned in positions_by_owner.items():
        hostile_owners = tuple(
            other for other in sorted(positions_by_owner) if simulation.are_hostile(other, owner_id)
        )
        armed = []
        query_list: list[tuple[float, float, float]] = []
        for key, point in owned.items():
            entity = simulation.entities[key]
            profile = entity.kind.profile
            if profile.attack_damage <= 0 or not entity.is_complete:
                continue
            assigned = simulation.assignments.get(key)
            if (
                assigned is not None
                and simulation.automations[assigned].kind is AutomationKind.REPAIR_AND_RETURN
            ):
                continue
            if entity.attack_cooldown > 0:
                entity.attack_cooldown -= 1
            target = simulation.entities.get(entity.attack_target_id or "")
            target_position = None if target is None else positions[target.entity_id]
            if target is not None and entity.attack_gather_point is not None:
                entity.attack_gather_point = target_position
            if target is None:
                if entity.attack_target_id is not None:
                    entity.attack_target_id = None
                if entity.pursue_target:
                    entity.pursue_target = False
            elif target.owner_id not in hostile_owners:
                entity.pursue_target = False
                entity.attack_target_id = None
                target = None
            attack_range = profile.attack_range
            range_squared = attack_range * attack_range
            in_range = False
            if target is not None and target_position is not None:
                dx, dy = point.x - target_position.x, point.y - target_position.y
                in_range = dx * dx + dy * dy <= range_squared
            if target is not None and not entity.pursue_target and not in_range:
                entity.attack_target_id = None
                target = None
            if entity.pursue_target and entity.formation_leg is None and in_range:
                if entity.path or entity.move_target is not None:
                    entity.path.clear()
                    entity.move_target = None
                    movement.reset_movement_liveness(entity)
                entity.state = UnitState.ATTACKING
            if (
                not entity.pursue_target
                and not entity.path
                and entity.order_queue
                and entity.order_queue[0].kind is OrderKind.ATTACK
            ):
                continue
            if target is not None and in_range:
                # Target validity, pursuit and locomotion were already updated.
                # A cooling weapon with a valid lock needs no firing candidate
                # allocation/sort; acquisition of a new lock still runs below.
                if not entity.attack_cooldown:
                    firing_targets[key] = target
                continue
            if (
                entity.pursue_target
                and entity.formation_leg is None
                and target is not None
                and (
                    not entity.path
                    or (
                        entity.route_ticks >= simulation.TICKS_PER_SECOND
                        and entity.move_target is not None
                        and target_position is not None
                        and _squared_distance(entity.move_target, target_position) > range_squared
                    )
                )
                and simulation.game_map.cell_for(entity.position)
                not in _interaction_cells(simulation, target, interaction_cache)
            ):
                route_groups.setdefault((target.entity_id, attack_range), []).append(key)
            # A cooling pursuer retains its ordered target. A nearby alternative
            # can only be used for a shot once its weapon is ready.
            if entity.pursue_target and entity.attack_cooldown:
                continue
            armed.append(key)
            query_list.append((point.x, point.y, attack_range))
        if not armed:
            continue
        if owner_index is None:
            owner_index = simulation._combat_index
            if owner_index is None:
                owner_index = GroupedSpatialIndex(positions_by_owner)
                simulation._combat_index = owner_index
            else:
                owner_index.refresh(positions_by_owner)
        queries = np.fromiter(chain.from_iterable(query_list), np.float64, len(armed) * 3).reshape(
            -1, 3
        )
        # One nearest query across all hostiles retains the distance/ID tie rule,
        # without repeated batches or Python merging for each opposing owner.
        nearest_enemies.update(
            (key, candidate)
            for key, candidate in zip(
                armed, owner_index.nearest_many_array(queries, hostile_owners), strict=True
            )
            if candidate is not None
        )
    obstacles = simulation._building_cells() if route_groups else frozenset()
    for group, members in route_groups.items():
        target_id, _ = group
        goals = combat_geometry.firing_approach_points(
            simulation,
            simulation.entities[members[0]],
            simulation.entities[target_id],
        )
        starts = point_coordinates(list(map(positions.__getitem__, members)))
        reachable = simulation._routes.can_reach_many(starts, goals, obstacles)
        ready = []
        for key, can_reach in zip(members, reachable, strict=True):
            if can_reach:
                ready.append(key)
            else:
                blocked_routes.add(key)
        if ready:
            pending_groups[group] = ready, goals
    # A route claim admits a shared target/range group, not a single follower.
    # Rotate groups for fairness; a newly issued player order gets first response.
    ordered = sorted(pending_groups)
    if ordered:
        offset = simulation.tick % len(ordered)
        ordered = ordered[offset:] + ordered[:offset]
        ordered.sort(
            key=lambda group: (
                not any(
                    (stamp := simulation.entities[key].last_player_control_tick) is not None
                    and stamp >= simulation.tick - 1
                    for key in pending_groups[group][0]
                )
            )
        )
    for group in ordered:
        if budget.attempts <= 0 or budget.cells <= 0 or not simulation._routes.claim_combat_route():
            break
        target_id, _ = group
        members, goals = pending_groups[group]
        members.sort()
        offset = simulation.tick % len(members)
        # Rotate the first claim so rear units can initiate a search. The reverse
        # field grows out from the target; nearer followers can often use cells
        # it has already settled after the cold-search allowance is exhausted.
        leader = members.pop(offset)
        center = positions[target_id]
        members.sort(key=lambda key: _squared_distance(positions[key], center))
        members.insert(0, leader)
        _route_attack_group(simulation, members, goals, budget)
    # Only candidates and failed routes need the firing pass. Stable ID order
    # retains projectile IDs and diagnostic/event order across save restoration.
    for entity_id in sorted(firing_targets.keys() | nearest_enemies.keys() | blocked_routes):
        attacker = simulation.entities[entity_id]
        attacker_position = positions[entity_id]
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
        firing_target = firing_targets.get(entity_id)
        if firing_target is None:
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
    return positions_by_owner if share_positions else None


def _drive_formation_attacks(
    simulation: Simulation,
    budget: _AttackRouteBudget,
    groups: dict[tuple[str, str], list[Entity]],
) -> None:
    ordered = sorted(groups.items())
    offset = simulation.tick % len(ordered)
    for (_, target_id), members in ordered[offset:] + ordered[:offset]:
        if budget.attempts <= 0 or budget.cells <= 0:
            break
        target = simulation.entities.get(target_id)
        if target is None:
            continue
        goal = members[0].formation_goal
        assert goal is not None
        # Keep a valid firing station until the target leaves its weapon envelope.
        # Refresh moving-target routes no more often than ordinary pursuit does.
        can_fire = any(
            formations.slot(e).distance_to(target.selection_position) <= e.profile.attack_range
            for e in members
        )
        claimed = False
        if not can_fire and (
            not any(e.path for e in members)
            or max(e.route_ticks for e in members) >= simulation.TICKS_PER_SECOND
        ):
            if not simulation._routes.claim_combat_route():
                break
            claimed = True
            try:
                goal = formations.attack_goal(simulation, members, target)
            except PathfindingError as error:
                for entity in members:
                    if entity.state is not UnitState.WAITING:
                        simulation.events.record(
                            simulation.tick,
                            EventType.PATHFINDING_FAILED,
                            entity.entity_id,
                            operation="formation_attack",
                            reason=str(error),
                            target_id=target_id,
                        )
                    entity.path.clear()
                    entity.move_target = None
                    movement.reset_movement_liveness(entity)
                    entity.state = UnitState.WAITING
                continue
            if goal != members[0].formation_goal:
                for entity in members:
                    entity.formation_goal = goal
                    entity.path.clear()
                    entity.move_target = None
                    movement.reset_movement_liveness(entity)
        pending = []
        for entity in members:
            if not entity.path:
                if entity.position.distance_to(formations.slot(entity)) > 0.04:
                    pending.append(entity)
                else:
                    entity.state = UnitState.ATTACKING
        if pending:
            offset = simulation.tick % len(pending)
            pending = pending[offset:] + pending[:offset]
        if pending and not claimed and not simulation._routes.claim_combat_route():
            break
        for entity in pending:
            if not budget.admit():
                break
            assert entity.formation_offset is not None
            destination = Point(
                goal.x + entity.formation_offset.x, goal.y + entity.formation_offset.y
            )
            try:
                route = movement.plan_group_paths(
                    simulation,
                    (entity.entity_id,),
                    {entity.entity_id: destination},
                    shared_goal=goal,
                    compact_approach=False,
                    allowance=budget.search,
                )[entity.entity_id]
            except RouteDeferred:
                continue
            except PathfindingError as error:
                if entity.state is not UnitState.WAITING:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        entity.entity_id,
                        operation="formation_attack",
                        reason=str(error),
                        target_id=target_id,
                    )
                entity.state = UnitState.WAITING
                continue
            budget.account(route)
            movement.start_path(
                simulation, entity, destination, route, "formation_attack", UnitState.ATTACKING
            )


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
        for key in owned:
            entity = simulation.entities[key]
            if (
                not entity.pursue_target
                and key not in fired
                and entity.kind.profile.combat_support
                and entity.is_complete
                and entity.formation_leg is None
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
                elif (
                    entity.path
                    or entity.move_target is not None
                    or entity.order_queue
                    or entity.repair_target_id is not None
                    or entity.reclaim_target_id is not None
                ):
                    # Finite work keeps control for its entire lifetime, including
                    # deferred FIFO dispatch. This applies to every control source;
                    # passive in-range fire above never changes its locomotion.
                    continue
                eligible.append(key)
        ids = sorted(eligible)
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

    with simulation._batch_entity_removals():
        simulation.projectile_traces = [
            trace for trace in simulation.projectile_traces if trace.expires_tick > simulation.tick
        ]
        for projectile_id in sorted(simulation.projectiles):
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
                    impact_shell(simulation, projectile)
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


def impact_shell(simulation: Simulation, projectile: Projectile) -> None:
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
    center = projectile.destination
    candidates = simulation.occupancy.occupants_in(
        floor(center.x - radius),
        floor(center.y - radius),
        floor(center.x + radius) + 1,
        floor(center.y + radius) + 1,
    )
    for entity_id in sorted(candidates):
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


def _route_attack_group(
    simulation: Simulation,
    members: list[str],
    goals: tuple[Point, ...],
    budget: _AttackRouteBudget,
) -> None:
    """Start cheap followers together; expensive field expansion stays bounded and shared."""
    if not goals:
        return
    center = Point(
        sum(simulation.entities[key].position.x for key in members) / len(members),
        sum(simulation.entities[key].position.y for key in members) / len(members),
    )
    approach = min(goals, key=lambda p: (p.distance_to(center), p.y, p.x))
    blocked = simulation._building_cells()
    for key in members:
        if not budget.admit():
            break
        attacker = simulation.entities[key]
        path = simulation._routes.segment_path(attacker.position, approach, blocked)
        point = approach
        if path is None:
            try:
                point, path = simulation._routes.shared_path_to_any(
                    attacker.position, goals, blocked, allowance=budget.search
                )
            except RouteDeferred:
                continue
            except PathfindingError as error:
                if attacker.state is not UnitState.WAITING:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        key,
                        operation="combat",
                        reason=str(error),
                        target_id=attacker.attack_target_id,
                    )
                attacker.state = UnitState.WAITING
                continue
        budget.account(path)
        movement.start_path(simulation, attacker, point, path, "combat", UnitState.ATTACKING)


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
