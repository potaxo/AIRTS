"""Persistent direct-order formations and bounded, collision-safe coordination."""

from __future__ import annotations

from collections import defaultdict
from math import hypot
from typing import TYPE_CHECKING

from airts.contracts.spatial import Point
from airts.navigation.pathfinding import PathfindingError
from airts.navigation.spatial_index import SpatialIndex
from airts.systems import combat_geometry
from airts.world._numeric import FloatArray
from airts.world.entities import Entity

if TYPE_CHECKING:
    from airts.simulation import Simulation


def _offset(entity: Entity) -> Point:
    value = entity.formation_offset
    assert value is not None
    return value


def slot(entity: Entity) -> Point:
    """The saved translated destination for an active direct-order leg."""
    goal = entity.formation_goal
    assert goal is not None
    relative = _offset(entity)
    return Point(goal.x + relative.x, goal.y + relative.y)


def begin_attack(simulation: Simulation, ids: tuple[str, ...]) -> None:
    """Direct and FIFO attacks enroll through the same saved cohort metadata."""
    groups: dict[tuple[str, str], list[Entity]] = defaultdict(list)
    for key in ids:
        entity = simulation.entities[key]
        if (
            entity.formation_group is not None
            and entity.pursue_target
            and entity.attack_gather_point is not None
        ):
            assert entity.attack_target_id is not None
            groups[entity.formation_group, entity.attack_target_id].append(entity)
    if not groups:
        return
    replacing = frozenset(ids)
    existing = {
        (e.formation_group, e.attack_target_id): e
        for e in simulation.entities.values()
        if e.entity_id not in replacing and e.formation_leg is not None and e.pursue_target
    }
    for group_key, members in groups.items():
        previous = existing.get(group_key)
        leg = previous.formation_leg if previous is not None else new_leg(simulation)
        goal = (
            previous.formation_goal
            if previous is not None
            else Point(
                sum(e.position.x - _offset(e).x for e in members) / len(members),
                sum(e.position.y - _offset(e).y for e in members) / len(members),
            )
        )
        for entity in members:
            entity.formation_leg = leg
            entity.formation_goal = goal


def attack_goal(simulation: Simulation, members: list[Entity], target: Entity) -> Point:
    """Place the front rank at firing range, retaining the complete translated layout."""
    offsets = {e.entity_id: _offset(e) for e in members}
    center = Point(
        sum(e.position.x - offsets[e.entity_id].x for e in members) / len(members),
        sum(e.position.y - offsets[e.entity_id].y for e in members) / len(members),
    )
    dx, dy = target.selection_position.x - center.x, target.selection_position.y - center.y
    front = max(members, key=lambda e: offsets[e.entity_id].x * dx + offsets[e.entity_id].y * dy)
    relative = offsets[front.entity_id]
    length = max(hypot(dx, dy), 1e-9)
    radius = max(0.5, front.profile.attack_range - 0.25)
    candidates = [
        Point(
            target.selection_position.x - dx / length * radius - relative.x,
            target.selection_position.y - dy / length * radius - relative.y,
        )
    ]
    if any(
        e.position.distance_to(target.selection_position) <= e.profile.attack_range for e in members
    ):
        candidates.insert(0, center)
    # A bounded alternative set accommodates terrain and map edges without a
    # separate per-unit firing-ring search or changing the captured offsets.
    alternatives = sorted(
        combat_geometry.firing_approach_points(simulation, front, target),
        key=lambda p: hypot(p.x - relative.x - center.x, p.y - relative.y - center.y),
    )
    candidates.extend(Point(p.x - relative.x, p.y - relative.y) for p in alternatives[:8])
    ids = tuple(e.entity_id for e in members)
    origins = {
        e.entity_id: Point(center.x + offsets[e.entity_id].x, center.y + offsets[e.entity_id].y)
        for e in members
    }
    for candidate in candidates:
        try:
            planned = simulation._allocate_destinations(
                ids, candidate, preserve_formation=True, origins=origins
            )
        except PathfindingError:
            continue
        if not any(
            planned[e.entity_id].distance_to(target.selection_position) <= e.profile.attack_range
            for e in members
        ):
            continue
        first = members[0]
        slot, offset = planned[first.entity_id], offsets[first.entity_id]
        return Point(slot.x - offset.x, slot.y - offset.y)
    raise PathfindingError("NO_REACHABLE_FORMATION_FIRING_POSITION")


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
    if entity.formation_goal is None or entity.path or entity.pursue_target:
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
        ready = not members[0].pursue_target or all(
            e.path or e.position.distance_to(slot(e)) <= 0.04 for e in members
        )
        clear = ready and all(
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
