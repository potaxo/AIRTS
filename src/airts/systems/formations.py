"""Persistent direct-move formations and bounded, collision-safe coordination."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from math import hypot
from typing import TYPE_CHECKING

from airts.contracts.commands import (
    CommandResult,
    SetFormationCommand,
    ValidationFailure,
    ValidationPhase,
)
from airts.contracts.spatial import Point
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems.validation import validate_entities
from airts.world._numeric import FloatArray
from airts.world.entities import Entity

if TYPE_CHECKING:
    from airts.simulation import Simulation


def _offset(entity: Entity) -> Point:
    value = entity.formation_offset
    assert value is not None
    return value


def set_formation(simulation: Simulation, command: SetFormationCommand) -> CommandResult:
    failure = validate_entities(
        simulation, command.entity_ids, command.owner_id, require_movable=True
    )
    if failure is not None:
        return simulation._reject_validation("set_formation", failure)
    if type(command.enabled) is not bool:
        return simulation._reject_validation(
            "set_formation",
            ValidationFailure(ValidationPhase.SCHEMA, "ENABLED_MUST_BE_BOOLEAN", "enabled"),
        )
    members = [simulation.entities[key] for key in command.entity_ids]
    center = Point(
        sum(e.position.x for e in members) / len(members),
        sum(e.position.y for e in members) / len(members),
    )
    group = f"{simulation.tick}:{simulation.command_count}:{min(command.entity_ids)}"
    used = {e.formation_group for e in simulation.entities.values()}
    while group in used:
        group += ":"
    for entity in members:
        entity.formation_group = group if command.enabled else None
        entity.formation_offset = (
            Point(entity.position.x - center.x, entity.position.y - center.y)
            if command.enabled
            else None
        )
        entity.formation_goal = entity.formation_leg = None
        entity.order_queue = [
            replace(order, formation_goal=None, formation_leg=None) for order in entity.order_queue
        ]
    return simulation._accept("set_formation")


def new_leg(simulation: Simulation) -> str:
    """Internal commands may share a tick/history index; live leg IDs must stay distinct."""
    used = {e.formation_leg for e in simulation.entities.values()}
    used.update(
        order.formation_leg for e in simulation.entities.values() for order in e.order_queue
    )
    leg = f"{simulation.tick}:{simulation.command_count}"
    while leg in used:
        leg += ":"
    return leg


def destinations(
    simulation: Simulation, ids: tuple[str, ...], target: Point, origins: dict[str, Point]
) -> tuple[dict[str, Point], dict[str, Point]]:
    groups: dict[str | None, list[str]] = defaultdict(list)
    for key in ids:
        groups[simulation.entities[key].formation_group].append(key)
    result: dict[str, Point] = {}
    goals: dict[str, Point] = {}
    for group, keys in groups.items():
        source = {key: origins[key] for key in keys}
        if group is not None:
            offsets = {key: _offset(simulation.entities[key]) for key in keys}
            x = sum(source[k].x - offsets[k].x for k in keys) / len(keys)
            y = sum(source[k].y - offsets[k].y for k in keys) / len(keys)
            source = {k: Point(x + offsets[k].x, y + offsets[k].y) for k in keys}
        planned = simulation._allocate_destinations(
            tuple(keys), target, preserve_formation=group is not None, origins=source
        )
        result.update(planned)
        if group is not None:
            first = keys[0]
            relative = _offset(simulation.entities[first])
            goal = Point(planned[first].x - relative.x, planned[first].y - relative.y)
            goals.update(dict.fromkeys(keys, goal))
    if len(groups) > 1:
        index = SpatialIndex(result)
        radius = max(simulation.entities[key].profile.collision_radius for key in ids)
        for key, point in result.items():
            own = simulation.entities[key].profile.collision_radius
            for other in index.nearby(point, own + radius):
                if (
                    other > key
                    and point.distance_to(result[other])
                    < own + simulation.entities[other].profile.collision_radius - 1e-8
                ):
                    raise PathfindingError("FORMATION_COHORTS_OVERLAP")
    return result, goals


def waiting_members(simulation: Simulation) -> set[str]:
    """A completed strict leg waits for its cohort before dispatching the FIFO tail."""
    if not any(e.formation_leg is not None for e in simulation.entities.values()):
        return set()
    active = {
        (e.formation_group, e.formation_leg)
        for e in simulation.entities.values()
        if e.formation_leg is not None and (e.path or return_target(e) is not None)
    }
    for e in simulation.entities.values():
        if e.formation_group is not None and e.order_queue:
            leg = e.order_queue[0].formation_leg
            if leg is not None:
                active.add((e.formation_group, leg))
    return {
        e.entity_id
        for e in simulation.entities.values()
        if e.formation_leg is not None and (e.formation_group, e.formation_leg) in active
    }


def return_target(entity: Entity) -> Point | None:
    """Arrived members retain their slot while the cohort settles or waits at its destination."""
    if entity.formation_goal is None or entity.path:
        return None
    relative = _offset(entity)
    target = Point(entity.formation_goal.x + relative.x, entity.formation_goal.y + relative.y)
    return target if entity.position.distance_to(target) > 0.04 else None


def coordinate(
    simulation: Simulation, entities: list[Entity], targets: FloatArray, speeds: FloatArray
) -> None:
    """Use existing paths in constrictions; recover translated slots in clear final approaches."""
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    pending: set[tuple[str, str]] = set()
    for i, e in enumerate(entities):
        if e.formation_group is None:
            continue
        if e.formation_leg is not None:
            groups[e.formation_group, e.formation_leg].append(i)
        if e.order_queue and e.order_queue[0].formation_leg is not None:
            pending.add((e.formation_group, e.order_queue[0].formation_leg))
    obstacles = simulation._building_cells()
    for key, indices in groups.items():
        if not any(entities[i].path for i in indices):
            continue
        step = min(float(speeds[i]) for i in indices)
        for i in indices:
            speeds[i] = step
        if key in pending:
            for i in indices:
                speeds[i] = 0
                entities[i].no_progress_ticks = 0
            continue
        members = [entities[i] for i in indices]
        goal = members[0].formation_goal
        assert goal is not None
        offsets = [_offset(e) for e in members]
        cx = sum(e.position.x - p.x for e, p in zip(members, offsets, strict=True)) / len(members)
        cy = sum(e.position.y - p.y for e, p in zip(members, offsets, strict=True)) / len(members)
        distance = hypot(goal.x - cx, goal.y - cy)
        fraction = min(1.0, step / distance) if distance > 1e-9 else 1.0
        anchor = Point(cx + (goal.x - cx) * fraction, cy + (goal.y - cy) * fraction)
        desired = [Point(anchor.x + p.x, anchor.y + p.y) for p in offsets]
        clear = all(
            len(e.path) <= 1
            and simulation._routes.disc_segment(
                e.position, p, obstacles, e.profile.collision_radius
            )
            for e, p in zip(members, desired, strict=True)
        )
        if clear:
            for i, p in zip(indices, desired, strict=True):
                if entities[i].path:
                    targets[i] = p.x, p.y
            continue
        # Bound stretching during single-file passage without freezing the front at the entrance.
        if distance > 1e-9:
            extent = max(4.0, *(hypot(p.x, p.y) * 2 for p in offsets))
            for i, e, relative in zip(indices, members, offsets, strict=True):
                lead = (
                    (e.position.x - relative.x - cx) * (goal.x - cx)
                    + (e.position.y - relative.y - cy) * (goal.y - cy)
                ) / distance
                if lead > extent:
                    speeds[i] = 0
                    e.no_progress_ticks = 0
