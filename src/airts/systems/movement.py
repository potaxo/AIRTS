"""One deterministic local movement controller for every group size."""

from __future__ import annotations

from math import floor, hypot
from typing import TYPE_CHECKING

import numpy as np

from airts.contracts.automations import DefendParameters
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.collision.solver import advance_positions
from airts.navigation.pathfinding import PathfindingError, PathResult, passable_segment_cells
from airts.world._numeric import BoolArray, FloatArray, IntArray
from airts.world.entities import Entity, UnitState
from airts.world.map import Cell, footprint_cells
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


class MovementWorkspace:
    """Reusable contiguous inputs/scratch; never saved or fed back as world state."""

    def __init__(self, entities: dict[str, Entity]) -> None:
        self.capacity = 0
        self.floats: dict[str, FloatArray] = {}
        self.booleans: dict[str, BoolArray] = {}
        self.integers: dict[str, IntArray] = {}
        self.members = {key: entity.kind for key, entity in entities.items()}
        self.keys = sorted(key for key, entity in entities.items() if entity.is_movable)
        self.ensure(len(self.keys))

    def ensure(self, count: int) -> None:
        if count <= self.capacity:
            return
        self.capacity = max(count, 128, self.capacity * 2)
        self.floats = {
            key: np.empty(
                (self.capacity, 2)
                if key in {"positions", "targets", "goals", "recovery_targets"}
                else self.capacity,
                dtype=np.float64,
            )
            for key in (
                "positions",
                "targets",
                "goals",
                "radii",
                "speeds",
                "masses",
                "recovery_targets",
                "tolerances",
            )
        }
        self.booleans = {
            key: np.empty(self.capacity, dtype=np.bool_)
            for key in ("moving", "stationary", "routed", "stalled", "recovering", "released")
        }
        self.integers = {
            key: np.empty(self.capacity, dtype=np.int64) for key in ("ticks", "status")
        }
        self.integers["work"] = np.empty(4, dtype=np.int64)


def reset_movement_liveness(entity: Entity) -> None:
    entity.progress_target = None
    entity.progress_distance = None
    entity.no_progress_ticks = entity.route_ticks = entity.collision_pressure = 0


def start_path(
    simulation: Simulation,
    entity: Entity,
    target: Point,
    path: PathResult,
    source: str,
    state: UnitState,
) -> None:
    reset_movement_liveness(entity)
    entity.path = list(path.waypoints)
    entity.move_target = target if entity.path else None
    entity.path_cost = path.cost
    entity.state = state if entity.path else simulation._state_for_assignment(entity.entity_id)
    simulation._movement_blocked.discard(entity.entity_id)
    simulation.events.record_path(
        simulation.tick,
        entity.entity_id,
        source=source,
        cost=path.cost,
        cells=path.cells,
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
            simulation._movement_replan_count += 1
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


def _recovery_goal(simulation: Simulation, entity: Entity) -> tuple[Point | None, float]:
    """Read the business-owned weak return intent without creating a player order."""
    if entity.hold_anchor is not None:
        return entity.hold_anchor, HOLD_TOLERANCE
    if not entity.defend_arrived or entity.state is not UnitState.DEFENDING:
        return None, HOLD_TOLERANCE
    automation = simulation.automations.get(simulation.assignments.get(entity.entity_id, ""))
    if automation is not None and isinstance(automation.parameters, DefendParameters):
        return (
            automation.parameters.stations.get(entity.entity_id),
            simulation.DEFEND_STATION_TOLERANCE,
        )
    return None, HOLD_TOLERANCE


def _track_progress(entity: Entity, target: Point, minimum: float) -> float:
    """Count monotonic advance to an unchanged intent, including internal Hold return."""
    distance = entity.position.distance_to(target)
    if entity.progress_target != target:
        entity.progress_target = target
        entity.progress_distance = distance
        entity.no_progress_ticks = 0
    if entity.progress_distance is None or distance < entity.progress_distance - minimum:
        entity.progress_distance = distance
        entity.no_progress_ticks = 0
    else:
        entity.no_progress_ticks += 1
    return distance


def move_entities(simulation: Simulation) -> None:
    workspace = simulation._movement_workspace
    members: dict[str, EntityKind] = workspace.members
    if len(members) != len(simulation.entities) or any(
        members.get(key) is not entity.kind for key, entity in simulation.entities.items()
    ):
        workspace.members = {key: entity.kind for key, entity in simulation.entities.items()}
        workspace.keys = sorted(
            key for key, entity in simulation.entities.items() if entity.is_movable
        )
    keys = workspace.keys
    if not keys:
        return
    entities = [simulation.entities[key] for key in keys]
    obstacles = simulation._building_cells()
    lookahead = []
    for entity in entities:
        if len(entity.path) < 2:
            continue
        if _route_obstacles(simulation, entity, obstacles) != obstacles:
            skip_crowded_waypoints(simulation, entity, obstacles)
        else:
            lookahead.append(entity)
    if lookahead:
        visible = simulation._routes.visible_waypoints(
            [entity.position for entity in lookahead],
            [entity.path for entity in lookahead],
            obstacles,
        )
        for entity, index in zip(lookahead, visible.tolist(), strict=True):
            if index:
                del entity.path[:index]
    planned: set[str] = set()
    for entity in entities:
        key = entity.entity_id
        if entity.contact_wait_ticks:
            entity.contact_wait_ticks -= 1
        if not entity.path:
            continue
        entity.route_ticks += 1
        target = entity.path[0]
        distance = _track_progress(entity, target, simulation.MIN_PROGRESS_DISTANCE)
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
    recovery_goals: list[Point | None] = []
    recovery_targets: list[tuple[float, float]] = []
    recovering: list[bool] = []
    recovery_ticks: list[int] = []
    recovery_released: list[bool] = []
    recovery_tolerances: list[float] = []
    stalled: list[bool] = []
    for entity in entities:
        target = entity.path[0] if entity.path else _hold_target(simulation, entity)
        point = entity.position
        recovery_goal, recovery_tolerance = _recovery_goal(simulation, entity)
        goal = entity.move_target or (entity.path[-1] if entity.path else recovery_goal or point)
        strong_intent = bool(entity.path) and not (
            entity.defend_arrived and entity.state is UnitState.DEFENDING
        )
        pending_recovery = (
            recovery_goal is not None
            and not strong_intent
            and point.distance_to(recovery_goal) > recovery_tolerance
        )
        if recovery_goal is None or strong_intent:
            entity.clear_recovery_request()
        elif pending_recovery and entity.recovery_request_tick is None:
            entity.recovery_request_tick = simulation.tick
        elif not pending_recovery and entity.recovery_request_tick is not None:
            entity.recovery_released = True
        recovery_target = target
        if (
            pending_recovery
            and entity.hold_anchor is None
            and not entity.path
            and recovery_goal is not None
            and simulation._routes.clear_segment(point, recovery_goal, obstacles)
        ):
            # An arrived defender can await its business route allowance while
            # its station remains directly reachable. Dispatch/quiet waiting
            # must not turn that geometric return corridor into a wall.
            recovery_target = recovery_goal
        if entity.hold_anchor is not None and not entity.path:
            if pending_recovery:
                entity.route_ticks += 1
                _track_progress(entity, entity.hold_anchor, simulation.MIN_PROGRESS_DISTANCE)
            else:
                reset_movement_liveness(entity)
        recovery_goals.append(recovery_goal)
        recovery_targets.append((recovery_target.x, recovery_target.y))
        recovering.append(pending_recovery)
        recovery_ticks.append(
            entity.recovery_request_tick if entity.recovery_request_tick is not None else -1
        )
        recovery_released.append(entity.recovery_released)
        recovery_tolerances.append(recovery_tolerance)
        stalled.append(
            (strong_intent or pending_recovery)
            and entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS
        )
        if not strong_intent and entity.contact_wait_ticks:
            target = point
        profile = entity.kind.profile
        assert profile.movement_speed is not None
        # Compute ordering geometry while the authoritative points are already in hand.
        # Keep scalar hypot and Python's ordered sums, including their rounding behavior.
        has_route = bool(entity.path)
        if has_route:
            px, py = float(point.x), float(point.y)
            gx, gy = float(goal.x) - px, float(goal.y) - py
            length = max(hypot(gx, gy), 1e-9)
            heading_x, heading_y = gx / length, gy / length
            dx, dy = float(target.x) - px, float(target.y) - py
            front = -(px * dx + py * dy) / max(hypot(dx, dy), 1e-9)
        else:
            # Pathless bodies use the common axis/sign below, never route geometry.
            heading_x = heading_y = front = 0.0
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
                has_route,
                heading_x,
                heading_y,
                front,
                profile.mass,
            )
        )
    data = np.asarray(records, dtype=np.float64)
    count = len(keys)
    workspace.ensure(count)
    floats = {key: value[:count] for key, value in workspace.floats.items()}
    booleans = {key: value[:count] for key, value in workspace.booleans.items()}
    integers = {key: value[:count] for key, value in workspace.integers.items()}
    positions, target_data, goals = floats["positions"], floats["targets"], floats["goals"]
    positions[:], target_data[:], goals[:] = data[:, :2], data[:, 2:4], data[:, 4:6]
    radii, speeds = floats["radii"], floats["speeds"]
    radii[:], speeds[:] = data[:, 6], data[:, 7]
    # Sample original centers in one batch; the solver shares this cap for all motion.
    speeds /= simulation._routes.movement_costs_at(positions)
    masses = floats["masses"]
    masses[:] = data[:, 13]
    moving, stationary = booleans["moving"], booleans["stationary"]
    moving[:] = data[:, 8]
    np.logical_not(moving, out=stationary)
    # One stable front ordering for active routes and nearby resting bodies.
    # Batch the arithmetic; IDs are already sorted and provide the last tie break.
    routed = booleans["routed"]
    routed[:] = data[:, 9]
    headings = data[:, 10:12]
    totals = [sum(np.abs(headings[routed, axis]).tolist()) for axis in (0, 1)]
    axis = int(totals[1] > totals[0])
    sign = 1.0 if sum(headings[routed, axis].tolist()) >= 0 else -1.0
    priority = np.where(routed, headings[:, axis] >= 0, sign >= 0).astype(np.int64)
    priority ^= simulation.tick % 2
    # At a turn or merge, the front is measured along the segment being submitted,
    # not a remote goal behind a wall. Keep group/parity/ID tie breaks unchanged.
    fronts = np.where(
        routed,
        data[:, 12],
        -sign * positions[:, axis],
    )
    order = np.asarray(np.lexsort((np.arange(len(keys)), fronts, priority)), dtype=np.int64)
    simulation._movement_step_attempt_count += len(planned)
    work = workspace.integers["work"]
    work.fill(0)
    recovery_status = integers["status"]
    recovery_status.fill(0)
    booleans["stalled"][:] = stalled
    booleans["recovering"][:] = recovering
    booleans["released"][:] = recovery_released
    integers["ticks"][:] = recovery_ticks
    floats["recovery_targets"][:] = recovery_targets
    floats["tolerances"][:] = recovery_tolerances
    updated, pressure, checks = advance_positions(
        positions,
        target_data,
        goals,
        radii,
        speeds,
        masses,
        order,
        moving,
        stationary,
        simulation._routes.obstacle_mask(obstacles),
        axis,
        work,
        booleans["stalled"],
        recovering=booleans["recovering"],
        recovery_ticks=integers["ticks"],
        recovery_released=booleans["released"],
        recovery_targets=floats["recovery_targets"],
        recovery_tolerances=floats["tolerances"],
        recovery_status=recovery_status,
    )
    simulation._collision_pair_check_count += checks
    simulation._contact_candidate_count += int(work[0])
    simulation._contact_component_count += int(work[1])
    simulation._contact_coordination_work += int(work[2])
    simulation._contact_blocked_component_count += int(work[3])
    movable = frozenset(keys)
    xs, ys, pressures = updated[:, 0].tolist(), updated[:, 1].tolist(), pressure.tolist()
    statuses = recovery_status.tolist()
    for index, entity in enumerate(entities):
        key = entity.entity_id
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
        if statuses[index] == 1:
            entity.clear_recovery_request()
            if entity.hold_anchor is not None:
                reset_movement_liveness(entity)
        elif statuses[index] == 2:
            entity.recovery_released = False
        recovery_goal = recovery_goals[index]
        if recovery_goal is not None:
            if candidate.distance_to(recovery_goal) > recovery_tolerances[index]:
                if entity.recovery_request_tick is None:
                    entity.recovery_request_tick = simulation.tick
            elif entity.recovery_request_tick is not None and statuses[index] != 2:
                entity.recovery_released = True
                if entity.hold_anchor is not None:
                    reset_movement_liveness(entity)
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


def drive_move_queues(simulation: Simulation) -> None:
    pending = sorted(
        key
        for key, entity in simulation.entities.items()
        if entity.move_queue and not entity.path and not entity.pursue_target
    )
    if not pending:
        return
    offset = simulation.tick % len(pending)
    allowance = simulation._routes.automation_allowance(simulation.GATHERING_PATH_BUDGET)
    for key in pending[offset:] + pending[:offset]:
        if not allowance.claim():
            break
        entity = simulation.entities[key]
        target = entity.move_queue[0]
        try:
            route = simulation._routes.shared_path(
                entity.position, target, simulation._building_cells()
            )
        except PathfindingError as error:
            if entity.state is not UnitState.WAITING:
                simulation.events.record(
                    simulation.tick,
                    EventType.PATHFINDING_FAILED,
                    key,
                    operation="queued_move",
                    reason=str(error),
                    target=[target.x, target.y],
                )
            entity.state = UnitState.WAITING
            continue
        entity.move_queue.pop(0)
        start_path(simulation, entity, target, route, "human_queue", UnitState.MOVING)


def _finish_path(simulation: Simulation, entity: Entity) -> None:
    if entity.path:
        return
    entity.move_target = None
    reset_movement_liveness(entity)
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
        simulation._movement_replan_count += 1
        route = simulation._routes.shared_path(
            entity.position,
            entity.move_target or entity.path[-1],
            _route_obstacles(simulation, entity, obstacles),
        )
    except PathfindingError:
        return
    entity.path = list(route.waypoints)
    target = entity.path[0] if entity.path else None
    if target != entity.progress_target:
        entity.progress_target = target
        entity.progress_distance = entity.position.distance_to(target) if target else None
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
        direct = simulation._routes.segment_path(start, target, blocked)
        if direct is not None:
            result[key] = direct
            continue
        shared = simulation._routes.shared_path(start, anchor, blocked)
        for index, point in enumerate(shared.waypoints):
            # A distinct anchor in the start cell has a waypoint but no cell transition.
            last = min(index + 1, len(shared.cells) - 1)
            assert shared.prefix_costs is not None
            branch = simulation._routes.segment_path(
                point,
                target,
                blocked,
                maximum_cost=float("inf"),
                initial_cost=float(shared.prefix_costs[last, 0]),
                initial_correction=float(shared.prefix_costs[last, 1]),
            )
            if branch is not None:
                result[key] = PathResult(
                    shared.cells[: last + 1] + branch.cells[1:],
                    shared.waypoints[: index + 1] + (target,),
                    branch.cost,
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
        simulation._routes.validate_connection(
            simulation.entities[key].position, targets[0], blocked
        )
    for point in targets[1:]:
        simulation._routes.validate_connection(point, targets[0], blocked)
