"""One deterministic local movement controller for every group size."""

from __future__ import annotations

from math import floor, hypot
from typing import TYPE_CHECKING

import numpy as np

from airts.contracts.automations import DefendParameters
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.navigation.collision import advance_positions, collision_radius
from airts.navigation.pathfinding import PathfindingError, PathResult, passable_segment_cells
from airts.world.entities import Entity, UnitState
from airts.world.map import Cell, footprint_cells

if TYPE_CHECKING:
    from airts.simulation import Simulation


def reset_movement_liveness(entity: Entity, *, clear_stop: bool = False) -> None:
    entity.progress_target = None
    entity.progress_distance = None
    entity.no_progress_ticks = entity.route_ticks = entity.collision_pressure = 0
    if clear_stop:
        entity.congestion_stopped = False


def start_path(
    simulation: Simulation,
    entity: Entity,
    target: Point,
    path: PathResult,
    source: str,
    state: UnitState,
) -> None:
    reset_movement_liveness(entity, clear_stop=True)
    entity.path = list(path.waypoints)
    entity.move_target = target if entity.path else None
    entity.path_cost = path.cost
    entity.state = state if entity.path else simulation._state_for_assignment(entity.entity_id)
    simulation._movement_blocked.discard(entity.entity_id)
    simulation.events.record(
        simulation.tick,
        EventType.PATH_COMPUTED,
        entity.entity_id,
        source=source,
        cost=path.cost,
        cells=[list(c) for c in path.cells],
    )
    if entity.path:
        simulation.events.record(
            simulation.tick,
            EventType.MOVEMENT_STARTED,
            entity.entity_id,
            source=source,
            target=[target.x, target.y],
        )


def position_clear(
    point: Point, radius: float, width: int, height: int, blocked: frozenset[Cell]
) -> bool:
    if not radius <= point.x <= width - radius or not radius <= point.y <= height - radius:
        return False
    for y in range(floor(point.y - radius), floor(point.y + radius) + 1):
        for x in range(floor(point.x - radius), floor(point.x + radius) + 1):
            if (x, y) in blocked:
                dx = max(x - point.x, 0.0, point.x - x - 1)
                dy = max(y - point.y, 0.0, point.y - y - 1)
                if dx * dx + dy * dy < radius * radius - 1e-12:
                    return False
    return True


def skip_crowded_waypoints(
    simulation: Simulation, entity: Entity, obstacles: frozenset[Cell] | None = None
) -> None:
    """Look ahead on a visible, equally cheap stretch of the existing route."""
    if len(entity.path) < 2:
        return
    blocked = _route_obstacles(
        simulation, entity, simulation._building_cells() if obstacles is None else obstacles
    )
    for index in range(min(len(entity.path) - 1, 16), 0, -1):
        if simulation._routes.clear_segment(entity.position, entity.path[index], blocked):
            del entity.path[:index]
            return


def _route_obstacles(
    simulation: Simulation, entity: Entity, blocked: frozenset[Cell]
) -> frozenset[Cell]:
    start = simulation.game_map.cell_for(entity.position)
    end = simulation.game_map.cell_for(entity.move_target or entity.path[-1])
    return blocked - {start, end} if start in blocked or end in blocked else blocked


HOLD_TOLERANCE = 0.04
CONTACT_WAIT_TICKS = 8
RECOVERY_RETRY_TICKS = 20


def _hold_target(simulation: Simulation, entity: Entity) -> Point:
    anchor = entity.hold_anchor
    if anchor is None or entity.position.distance_to(anchor) <= HOLD_TOLERANCE:
        entity.recovery_path.clear()
        return entity.position
    if entity.contact_wait_ticks:
        return entity.position
    buildings = simulation._building_cells()
    if (
        entity.recovery_path
        and passable_segment_cells(
            simulation.game_map, entity.position, entity.recovery_path[0], buildings
        )
        is None
    ):
        entity.recovery_path.clear()
    if simulation._routes.clear_segment(entity.position, anchor, buildings):
        entity.recovery_path.clear()
        entity.recovery_blocked_cells = None
        return anchor
    if entity.recovery_blocked_cells == buildings:
        return entity.position
    if not entity.recovery_path and simulation.tick >= entity.recovery_retry_tick:
        if simulation._routes.claim_combat_route():
            entity.recovery_retry_tick = simulation.tick + RECOVERY_RETRY_TICKS
            try:
                route = simulation._routes.shared_path(entity.position, anchor, buildings)
            except PathfindingError:
                entity.recovery_blocked_cells = buildings
                return entity.position
            entity.recovery_blocked_cells = None
            entity.recovery_path = list(route.waypoints)
    while (
        entity.recovery_path
        and entity.position.distance_to(entity.recovery_path[0]) <= HOLD_TOLERANCE
    ):
        entity.recovery_path.pop(0)
    return entity.recovery_path[0] if entity.recovery_path else entity.position


def move_entities(simulation: Simulation) -> None:
    entities = {key: e for key, e in simulation.entities.items() if e.is_movable}
    if not entities:
        return
    keys = sorted(entities)
    obstacles = simulation._building_cells()
    planned: set[str] = set()
    for key in keys:
        entity = entities[key]
        if entity.contact_wait_ticks:
            entity.contact_wait_ticks -= 1
        if not entity.path:
            continue
        entity.route_ticks += 1
        skip_crowded_waypoints(simulation, entity, obstacles)
        target = entity.path[0]
        distance = entity.position.distance_to(target)
        if entity.progress_target != target:
            entity.progress_target = target
            entity.progress_distance = distance
            entity.no_progress_ticks = 0
        if (
            entity.progress_distance is None
            or distance < entity.progress_distance - simulation.MIN_PROGRESS_DISTANCE
        ):
            entity.progress_distance = distance
            entity.no_progress_ticks = 0
        else:
            entity.no_progress_ticks += 1
        automation = simulation.automations.get(simulation.assignments.get(key, ""))
        tolerance = (
            simulation.DEFEND_STATION_TOLERANCE
            if automation is not None and isinstance(automation.parameters, DefendParameters)
            else 1e-9
        )
        if len(entity.path) == 1 and distance <= tolerance:
            entity.path.clear()
            _finish_path(simulation, entity)
            continue
        if distance <= 1e-9:
            entity.path.pop(0)
            entity.progress_target = entity.progress_distance = None
            entity.no_progress_ticks = 0
            if not entity.path:
                _finish_path(simulation, entity)
                continue
        planned.add(key)
    records = []
    for key in keys:
        entity = entities[key]
        target = entity.path[0] if entity.path else _hold_target(simulation, entity)
        point = entity.position
        goal = entity.move_target or (
            entity.path[-1] if entity.path else entity.hold_anchor or point
        )
        strong_intent = bool(entity.path) and not (
            entity.defend_arrived and entity.state is UnitState.DEFENDING
        )
        profile = entity.kind.profile
        assert profile.movement_speed is not None
        records.append(
            (
                point.x,
                point.y,
                target.x,
                target.y,
                goal.x,
                goal.y,
                collision_radius(entity.kind),
                profile.movement_speed * simulation.TICK_SECONDS,
                strong_intent,
                bool(entity.path),
            )
        )
    data = np.asarray(records, dtype=np.float64)
    positions, target_data, goals = data[:, :2].copy(), data[:, 2:4].copy(), data[:, 4:6].copy()
    radii, speeds = data[:, 6].copy(), data[:, 7].copy()
    moving = data[:, 8].astype(np.bool_)
    stationary = ~moving
    # One stable front ordering for active routes and nearby resting bodies.
    # Batch the arithmetic; IDs are already sorted and provide the last tie break.
    routed = data[:, 9].astype(np.bool_)
    vectors = goals - positions
    lengths = np.asarray(
        [
            max(hypot(x, y), 1e-9)
            for x, y in zip(vectors[:, 0].tolist(), vectors[:, 1].tolist(), strict=True)
        ]
    )
    headings = vectors / lengths[:, None]
    totals = [sum(np.abs(headings[routed, axis]).tolist()) for axis in (0, 1)]
    axis = int(totals[1] > totals[0])
    sign = 1.0 if sum(headings[routed, axis].tolist()) >= 0 else -1.0
    priority = np.where(routed, headings[:, axis] >= 0, sign >= 0).astype(np.int64)
    priority ^= simulation.tick % 2
    fronts = np.where(
        routed,
        -(positions[:, 0] * vectors[:, 0] + positions[:, 1] * vectors[:, 1]) / lengths,
        -sign * positions[:, axis],
    )
    order = np.asarray(np.lexsort((np.arange(len(keys)), fronts, priority)), dtype=np.int64)
    simulation._movement_step_attempt_count += len(planned)
    updated, pressure, checks = advance_positions(
        positions,
        target_data,
        goals,
        radii,
        speeds,
        order,
        moving,
        stationary,
        simulation._routes.obstacle_mask(obstacles),
        axis,
    )
    simulation._collision_pair_check_count += checks
    movable = frozenset(entities)
    xs, ys, pressures = updated[:, 0].tolist(), updated[:, 1].tolist(), pressure.tolist()
    for index, key in enumerate(keys):
        entity = entities[key]
        changed = xs[index] != entity.position.x or ys[index] != entity.position.y
        candidate = Point(xs[index], ys[index]) if changed else entity.position
        entity.collision_pressure = int(pressures[index])
        if changed:
            if simulation.game_map.cell_for(candidate) != simulation.game_map.cell_for(
                entity.position
            ):
                simulation.occupancy.move(key, footprint_cells(entity.kind, candidate), movable)
            entity.position = candidate
        if pressures[index]:
            entity.contact_wait_ticks = CONTACT_WAIT_TICKS
        if key not in planned:
            continue
        target = entity.path[0]
        if pressures[index] and entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS:
            simulation._movement_blocked.add(key)
            if entity.no_progress_ticks == simulation.NO_PROGRESS_YIELD_TICKS:
                simulation.events.record(
                    simulation.tick,
                    EventType.MOVEMENT_BLOCKED,
                    key,
                    reason="LOCAL_OBSTACLE",
                    target=[target.x, target.y],
                )
        else:
            simulation._movement_blocked.discard(key)
        if candidate.distance_to(target) <= 1e-9:
            entity.path.pop(0)
        if entity.progress_target != (entity.path[0] if entity.path else None):
            entity.progress_target = entity.progress_distance = None
            entity.no_progress_ticks = 0
        if entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS:
            _recover_path(simulation, entity, obstacles)
        if not entity.path:
            _finish_path(simulation, entity)


def _finish_path(simulation: Simulation, entity: Entity) -> None:
    if entity.path:
        return
    entity.move_target = None
    reset_movement_liveness(entity, clear_stop=True)
    entity.state = simulation._state_for_assignment(entity.entity_id)
    if entity.state is UnitState.DEFENDING:
        entity.defend_arrived = True
    simulation.events.record(
        simulation.tick,
        EventType.MOVEMENT_COMPLETED,
        entity.entity_id,
        position=[entity.position.x, entity.position.y],
    )


def _recover_path(simulation: Simulation, entity: Entity, obstacles: frozenset[Cell]) -> None:
    if (
        not entity.path
        or entity.no_progress_ticks < simulation.NO_PROGRESS_YIELD_TICKS
        or entity.route_ticks % simulation.NO_PROGRESS_YIELD_TICKS
        or not simulation._routes.claim_combat_route()
    ):
        return
    try:
        route = simulation._routes.shared_path(
            entity.position,
            entity.move_target or entity.path[-1],
            _route_obstacles(simulation, entity, obstacles),
        )
    except PathfindingError:
        return
    entity.path = list(route.waypoints)
    entity.progress_target = entity.path[0] if entity.path else None
    entity.progress_distance = entity.position.distance_to(entity.path[0]) if entity.path else None
    entity.no_progress_ticks = 0


def plan_group_paths(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    destinations: dict[str, Point],
    *,
    shared_goal: Point | None = None,
) -> dict[str, PathResult]:
    blocked = simulation._building_cells()
    center = Point(
        sum(p.x for p in destinations.values()) / len(destinations),
        sum(p.y for p in destinations.values()) / len(destinations),
    )
    anchor = shared_goal or min(
        destinations.values(), key=lambda p: (p.distance_to(center), p.y, p.x)
    )
    result: dict[str, PathResult] = {}
    for key in entity_ids:
        start, target = simulation.entities[key].position, destinations[key]
        direct = passable_segment_cells(simulation.game_map, start, target, blocked)
        if direct is not None and all(
            simulation.game_map.terrain_at_cell(c).movement_cost <= 1 for c in direct
        ):
            result[key] = PathResult(
                direct,
                () if start == target else (target,),
                sum(simulation.game_map.terrain_at_cell(c).movement_cost for c in direct[1:]),
            )
            continue
        shared = simulation._routes.shared_path(start, anchor, blocked)
        for index, point in enumerate(shared.waypoints):
            branch = passable_segment_cells(simulation.game_map, point, target, blocked)
            if branch is not None:
                last = shared.cells.index(simulation.game_map.cell_for(point))
                cells = shared.cells[: last + 1] + branch[1:]
                result[key] = PathResult(
                    cells,
                    shared.waypoints[: index + 1] + (target,),
                    sum(simulation.game_map.terrain_at_cell(c).movement_cost for c in cells[1:]),
                )
                break
        else:
            result[key] = simulation._routes.shared_path(start, target, blocked)
    return result


def validate_paths(
    simulation: Simulation, entity_ids: tuple[str, ...], targets: tuple[Point, ...]
) -> None:
    blocked = simulation._building_cells()
    # All members must reach the route; all route vertices must be connected.
    for key in entity_ids:
        simulation._routes.shared_path(simulation.entities[key].position, targets[0], blocked)
    for point in targets[1:]:
        simulation._routes.shared_path(point, targets[0], blocked)
