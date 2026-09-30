"""One deterministic local movement controller for every group size."""

from __future__ import annotations

from math import floor, hypot
from typing import TYPE_CHECKING

import numpy as np

from airts.automations import DefendParameters
from airts.events import EventType
from airts.geometry import Point
from airts.navigation.collision import advance_positions, collision_radius
from airts.navigation.pathfinding import PathfindingError, PathResult
from airts.world.entities import Entity, UnitState
from airts.world.map_model import Cell

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


def move_entities(simulation: Simulation) -> None:
    entities = {key: e for key, e in simulation.entities.items() if e.is_movable}
    active = [key for key, e in entities.items() if e.path]
    if not active:
        return
    keys = sorted(entities)
    offsets = {key: index for index, key in enumerate(keys)}
    records = []
    fronts: dict[str, tuple[float, float, float]] = {}
    held_cells = set()
    for key in keys:
        entity = entities[key]
        point = entity.position
        goal = entity.move_target or (entity.path[-1] if entity.path else point)
        is_active = bool(entity.path)
        records.append(
            (
                point.x,
                point.y,
                goal.x,
                goal.y,
                collision_radius(entity.kind),
                entity.speed * simulation.TICK_SECONDS,
                is_active,
                not is_active and entity.state is not UnitState.HOLDING,
            )
        )
        if entity.state is UnitState.HOLDING:
            held_cells.add(simulation.game_map.cell_for(point))
        if is_active:
            dx, dy = goal.x - point.x, goal.y - point.y
            length = max(hypot(dx, dy), 1e-9)
            fronts[key] = dx / length, dy / length, -(point.x * dx + point.y * dy) / length
    data = np.asarray(records, dtype=np.float64)
    positions, goals = data[:, :2].copy(), data[:, 2:4].copy()
    radii, speeds = data[:, 4].copy(), data[:, 5].copy()
    moving, stationary = data[:, 6].astype(np.bool_), data[:, 7].astype(np.bool_)
    stationary_cells = simulation._building_cells() | frozenset(held_cells)
    direction_axis = int(
        sum(abs(fronts[key][1]) for key in active) > sum(abs(fronts[key][0]) for key in active)
    )

    def front_order(key: str) -> tuple[int, float, str]:
        return int(fronts[key][direction_axis] >= 0) ^ (simulation.tick % 2), fronts[key][2], key

    active.sort(key=front_order)
    planned: list[str] = []
    simulation._movement_step_attempt_count += len(active)
    for key in active:
        entity = entities[key]
        entity.collision_pressure = 0
        entity.route_ticks += 1
        skip_crowded_waypoints(simulation, entity, stationary_cells)
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
            _finish_path(simulation, entity)
            continue
        planned.append(key)
    order = np.asarray([offsets[key] for key in planned], dtype=np.int64)
    targets = np.asarray(
        [
            (p.x, p.y)
            for key in keys
            for p in (entities[key].path[0] if entities[key].path else entities[key].position,)
        ],
        dtype=np.float64,
    )
    updated, pressure, checks = advance_positions(
        positions,
        targets,
        goals,
        radii,
        speeds,
        order,
        moving,
        stationary,
        simulation._routes.obstacle_mask(simulation._building_cells()),
        direction_axis,
    )
    simulation._collision_pair_check_count += checks
    movable = frozenset(entities)
    xs, ys = updated[:, 0].tolist(), updated[:, 1].tolist()
    pressures = pressure.tolist()
    for key in planned:
        entity = entities[key]
        index = offsets[key]
        target = entity.path[0]
        if pressures[index]:
            entity.collision_pressure = 1
            if entity.no_progress_ticks == simulation.NO_PROGRESS_YIELD_TICKS:
                simulation._movement_blocked.add(key)
                simulation.events.record(
                    simulation.tick,
                    EventType.MOVEMENT_BLOCKED,
                    key,
                    reason="LOCAL_OBSTACLE",
                    target=[target.x, target.y],
                )
            if entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS:
                _recover_path(simulation, entity, stationary_cells)
            if not entity.path:
                _finish_path(simulation, entity)
            continue
        candidate = Point(xs[index], ys[index])
        simulation.occupancy.move(key, simulation._cells_at(entity, candidate), movable)
        entity.position = candidate
        simulation._movement_blocked.discard(key)
        if candidate.distance_to(target) <= 1e-9:
            entity.position = target
            entity.path.pop(0)
        if entity.progress_target != (entity.path[0] if entity.path else None):
            entity.progress_target = entity.progress_distance = None
            entity.no_progress_ticks = 0
        if entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS:
            _recover_path(simulation, entity, stationary_cells)
        if not entity.path:
            _finish_path(simulation, entity)


def _finish_path(simulation: Simulation, entity: Entity) -> None:
    if entity.path:
        return
    entity.move_target = None
    reset_movement_liveness(entity, clear_stop=True)
    entity.state = simulation._state_for_assignment(entity.entity_id)
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
