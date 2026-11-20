"""One deterministic local movement controller for every group size."""

from __future__ import annotations

from bisect import bisect_left
from math import floor, hypot
from operator import attrgetter
from struct import Struct
from typing import TYPE_CHECKING

import numpy as np

from airts.contracts.automations import DefendParameters, patrol_arrival_radius
from airts.contracts.events import EventType
from airts.contracts.spatial import Point
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.collision.solver import advance_positions
from airts.navigation.pathfinding import (
    LOOKAHEAD_WAYPOINT_LIMIT,
    PathfindingError,
    PathResult,
    RouteAllowance,
    passable_segment_cells,
)
from airts.systems import formations
from airts.systems.assignment_state import state_for_assignment
from airts.world._numeric import BoolArray, FloatArray, IntArray, compile_numeric
from airts.world.entities import Entity, OrderKind, UnitState
from airts.world.map import Cell, footprint_cells
from airts.world.units.catalog import EntityKind

if TYPE_CHECKING:
    from airts.simulation import Simulation


# All bodies supply position, real waypoint, goal, routed/stalled/pressure and
# an override flag. Only Hold and arrived defenders supply the optional tail:
# steering, recovery steering, recovery goal, strong/pending/released/tolerance/has goal.
_MOTION_ROW = Struct("=10d")
_RETURN_ROW = Struct("=11d")
_LOOKAHEAD_ROWS = tuple(Struct(f"={2 * count}d") for count in range(LOOKAHEAD_WAYPOINT_LIMIT + 1))
_LOOKAHEAD_CACHE_MIN_ROWS = 1_024
_MOVEMENT_RECORD_WIDTH = 21
_CHANGED = 1
_CELL_CHANGED = 2
_OUTSIDE_RECOVERY = 4
_PROGRESSED = 8
_AT_WAYPOINT = 16
_HAS_RECOVERY = 32


def move_origins(
    simulation: Simulation, entity_ids: tuple[str, ...], *, continuing: frozenset[str]
) -> dict[str, Point]:
    """Reuse a preceding move goal; target work stays in the current reachable component."""
    origins = {}
    for key in entity_ids:
        entity = simulation.entities[key]
        origins[key] = (
            entity.order_queue[-1].position
            if key in continuing
            and entity.order_queue
            and entity.order_queue[-1].kind is OrderKind.MOVE
            else entity.move_target
            if key in continuing and entity.move_target
            else entity.position
        )
    return origins


class MovementWorkspace:
    """Reusable contiguous inputs/scratch; never saved or fed back as world state."""

    def __init__(self, entities: dict[str, Entity]) -> None:
        self.capacity = 0
        self.floats: dict[str, FloatArray] = {}
        self.booleans: dict[str, BoolArray] = {}
        self.integers: dict[str, IntArray] = {}
        self.members = {key: entity.kind for key, entity in entities.items()}
        self.keys = sorted(key for key, entity in entities.items() if entity.is_movable)
        self.waypoint_windows: dict[str, tuple[list[Point], bytes]] = {}
        kinds = [kind for kind in EntityKind if kind.profile.movable]
        self.profile_indices = {kind: i for i, kind in enumerate(kinds)}
        self.profiles = np.asarray(
            [
                (kind.profile.collision_radius, kind.movement_speed, kind.profile.mass)
                for kind in kinds
            ]
        )
        self.profile_keys: tuple[EntityKind, ...] = ()
        self.records = np.empty((0, _MOVEMENT_RECORD_WIDTH), dtype=np.float64)
        self.tick: int | None = None
        self.ensure(len(self.keys))
        empty = np.empty((0, 2), dtype=np.float64)
        empty_flags = np.empty(0, dtype=np.bool_)
        _prepare_motion(
            self.records[:0],
            empty,
            empty,
            empty,
            empty,
            np.empty(0),
            empty_flags,
            empty_flags,
            empty_flags,
            empty_flags,
            empty_flags,
            empty_flags,
        )
        _motion_geometry(empty, empty, empty, empty_flags, empty, np.empty(0))
        _movement_feedback(
            np.empty((0, _MOVEMENT_RECORD_WIDTH)),
            empty,
            np.empty(0, dtype=np.bool_),
            np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64),
            np.empty(0, dtype=np.int64),
            1,
            1,
            0.0,
        )

    def ensure(self, count: int) -> None:
        if count <= self.capacity:
            return
        self.capacity = max(count, 128, self.capacity * 2)
        self.records = np.empty((self.capacity, _MOVEMENT_RECORD_WIDTH), dtype=np.float64)
        self.profile_keys = ()
        self.floats = {
            key: np.empty(
                (self.capacity, 2)
                if key in {"positions", "targets", "goals", "recovery_targets", "headings"}
                else self.capacity,
                dtype=np.float64,
            )
            for key in (
                "positions",
                "targets",
                "goals",
                "radii",
                "speeds",
                "base_speeds",
                "masses",
                "recovery_targets",
                "tolerances",
                "headings",
                "fronts",
            )
        }
        self.booleans = {
            key: np.empty(self.capacity, dtype=np.bool_)
            for key in ("moving", "stationary", "routed", "stalled", "recovering", "released")
        }
        self.integers = {
            key: np.empty(self.capacity, dtype=np.int64)
            for key in ("ticks", "status", "feedback", "feedback_indices")
        }
        self.integers["work"] = np.empty(4, dtype=np.int64)

    def refresh_profiles(self, entities: list[Entity], tick_seconds: float) -> None:
        """Immutable profiles are derived once per membership/kind change, not per tick."""
        kinds = tuple(map(attrgetter("kind"), entities))
        if kinds == self.profile_keys:
            return
        self.profile_keys = kinds
        indices = np.fromiter(map(self.profile_indices.__getitem__, kinds), np.intp, len(kinds))
        profiles = self.profiles[indices]
        self.floats["radii"][: len(kinds)] = profiles[:, 0]
        self.floats["base_speeds"][: len(kinds)] = profiles[:, 1] * tick_seconds
        self.floats["masses"][: len(kinds)] = profiles[:, 2]


@compile_numeric
def _prepare_motion(
    records: FloatArray,
    positions: FloatArray,
    targets: FloatArray,
    goals: FloatArray,
    recovery_targets: FloatArray,
    tolerances: FloatArray,
    moving: BoolArray,
    stationary: BoolArray,
    routed: BoolArray,
    recovering: BoolArray,
    released: BoolArray,
    stalled: BoolArray,
) -> None:
    """Unpack one complete dynamic snapshot into persistent solver inputs in one native pass."""
    for i in range(len(records)):
        for axis in range(2):
            positions[i, axis] = records[i, axis]
            goals[i, axis] = records[i, axis + 4]
        routed[i] = bool(records[i, 6])
        stalled[i] = bool(records[i, 7])
        if records[i, 9]:
            targets[i] = records[i, 10:12]
            recovery_targets[i] = records[i, 12:14]
            moving[i] = bool(records[i, 16])
            recovering[i] = bool(records[i, 17])
            released[i] = bool(records[i, 18])
            tolerances[i] = records[i, 19]
        else:
            targets[i] = records[i, 2:4]
            recovery_targets[i] = records[i, 2:4]
            moving[i] = routed[i]
            recovering[i] = released[i] = False
            tolerances[i] = HOLD_TOLERANCE
        stationary[i] = not moving[i]


@compile_numeric
def _motion_geometry(
    positions: FloatArray,
    targets: FloatArray,
    goals: FloatArray,
    routed: BoolArray,
    headings: FloatArray,
    fronts: FloatArray,
) -> None:
    """Pack ordering geometry in reusable arrays without per-unit Python temporaries."""
    for index in range(len(positions)):
        if routed[index]:
            px, py = positions[index]
            gx, gy = goals[index, 0] - px, goals[index, 1] - py
            length = max(hypot(gx, gy), 1e-9)
            headings[index, 0], headings[index, 1] = gx / length, gy / length
            dx, dy = targets[index, 0] - px, targets[index, 1] - py
            fronts[index] = -(px * dx + py * dy) / max(hypot(dx, dy), 1e-9)
        else:
            headings[index, 0] = headings[index, 1] = fronts[index] = 0.0


@compile_numeric
def _movement_feedback(
    records: FloatArray,
    updated: FloatArray,
    pressure: BoolArray,
    statuses: IntArray,
    feedback: IntArray,
    indices: IntArray,
    width: int,
    height: int,
    minimum_progress: float,
) -> int:
    """Batch numeric feedback; only bodies needing world submission enter the work list.

    All bodies have already participated in the collision solve. Quiet bodies can
    omit Python writeback, while previous pressure, route/recovery intent and solver
    status always retain a submission even when the body did not move.
    """
    count = 0
    for index in range(len(records)):
        feedback[index] = 0
        x, y = records[index, 0], records[index, 1]
        nx, ny = updated[index, 0], updated[index, 1]
        changed = x != nx or y != ny
        routed = bool(records[index, 6])
        recovery = bool(records[index, 9]) and bool(records[index, 20])
        if not (
            changed or routed or recovery or pressure[index] or statuses[index] or records[index, 8]
        ):
            continue
        mask = _HAS_RECOVERY if recovery else 0
        if changed:
            if not (0 <= nx < width and 0 <= ny < height):
                raise ValueError("movement result outside map")
            mask |= _CHANGED
            if int(x) != int(nx) or int(y) != int(ny):
                mask |= _CELL_CHANGED
        if routed:
            # A weak return can temporarily steer at its current position during
            # contact waiting. Arrival/progress still refer to the actual route.
            tx, ty = records[index, 2], records[index, 3]
            distance = hypot(tx - nx, ty - ny)
            if hypot(tx - x, ty - y) - distance >= minimum_progress:
                mask |= _PROGRESSED
            if distance <= 1e-9:
                mask |= _AT_WAYPOINT
        if (
            recovery
            and hypot(records[index, 14] - nx, records[index, 15] - ny) > records[index, 19]
        ):
            mask |= _OUTSIDE_RECOVERY
        feedback[index] = mask
        indices[count] = index
        count += 1
    return count


def observed_velocity(simulation: Simulation, entity: Entity) -> tuple[float, float]:
    """Read this tick's certified displacement without keeping extra motion history."""

    workspace = simulation._movement_workspace
    if workspace.tick != simulation.tick:
        return 0.0, 0.0
    index = bisect_left(workspace.keys, entity.entity_id)
    if index == len(workspace.keys) or workspace.keys[index] != entity.entity_id:
        return 0.0, 0.0
    original = workspace.floats["positions"]
    return (
        (entity.position.x - float(original[index, 0])) / simulation.TICK_SECONDS,
        (entity.position.y - float(original[index, 1])) / simulation.TICK_SECONDS,
    )


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
    entity.state = state if entity.path else state_for_assignment(simulation, entity.entity_id)
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
    index = int(
        simulation._routes.visible_waypoints(
            [entity.position],
            [entity.path],
            blocked,
            [
                collision_radius(entity.kind)
                if entity.entity_id in simulation._movement_blocked
                or entity.no_progress_ticks >= simulation.NO_PROGRESS_YIELD_TICKS
                else 0.0
            ],
        )[0]
    )
    if index:
        del entity.path[:index]


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
    if simulation._routes.disc_segment(
        entity.position,
        anchor,
        buildings,
        collision_radius(entity.kind),
    ):
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
            entity.recovery_path = _endpoint_waypoints(simulation, entity, anchor, route, buildings)
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
    if entity.progress_target is not target and entity.progress_target != target:
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
        if workspace.waypoint_windows:
            for removed in workspace.waypoint_windows.keys() - workspace.members.keys():
                del workspace.waypoint_windows[removed]
        workspace.keys = sorted(
            key for key, entity in simulation.entities.items() if entity.is_movable
        )
    keys = workspace.keys
    if not keys:
        return
    entities = list(map(simulation.entities.__getitem__, keys))
    obstacles = simulation._building_cells()
    returning = [
        (entity, target)
        for entity in entities
        if entity.formation_goal is not None
        and (target := formations.return_target(entity)) is not None
    ]
    if returning:
        offset = simulation.tick % len(returning)
        for entity, target in returning[offset:] + returning[:offset]:
            if not simulation._routes.claim_combat_route():
                break
            try:
                anchor = entity.formation_goal
                if (
                    anchor is None
                    or not simulation.game_map.is_passable(anchor)
                    or simulation.game_map.cell_for(anchor) in obstacles
                ):
                    anchor = target
                route = plan_group_paths(
                    simulation, (entity.entity_id,), {entity.entity_id: target}, shared_goal=anchor
                )[entity.entity_id]
            except PathfindingError as error:
                if entity.state is not UnitState.WAITING:
                    simulation.events.record(
                        simulation.tick,
                        EventType.PATHFINDING_FAILED,
                        entity.entity_id,
                        operation="formation_return",
                        reason=str(error),
                    )
                entity.state = UnitState.WAITING
                continue
            start_path(simulation, entity, target, route, "formation_return", UnitState.MOVING)
    lookahead = []
    starts, paths, lookahead_radii = [], [], []
    yield_ticks = simulation.NO_PROGRESS_YIELD_TICKS
    blocked_movers = simulation._movement_blocked
    for entity in entities:
        path = entity.path
        if len(path) < 2:
            continue
        # The helper returns this exact snapshot unless an embedded endpoint
        # needs an exception. Comparing equal frozensets rescans every building
        # cell for every routed body on a developed map.
        if _route_obstacles(simulation, entity, obstacles) is not obstacles:
            skip_crowded_waypoints(simulation, entity, obstacles)
        else:
            lookahead.append(entity)
            starts.append(entity.position)
            paths.append(path)
            lookahead_radii.append(
                collision_radius(entity.kind)
                if entity.entity_id in blocked_movers or entity.no_progress_ticks >= yield_ticks
                else 0.0
            )
    if lookahead:
        if len(lookahead) >= _LOOKAHEAD_CACHE_MIN_ROWS:
            packed_windows, lengths = [], []
            waypoint_windows = workspace.waypoint_windows
            for entity, path in zip(lookahead, paths, strict=True):
                # Routes consume prefixes in place. Compare the whole window
                # before reusing coordinates, even if the list identity matches.
                window = path[1 : 1 + LOOKAHEAD_WAYPOINT_LIMIT]
                cached = waypoint_windows.get(entity.entity_id)
                if cached is None or cached[0] != window:
                    cached = (
                        window,
                        _LOOKAHEAD_ROWS[len(window)].pack(
                            *(value for point in window for value in (point.x, point.y))
                        ),
                    )
                    waypoint_windows[entity.entity_id] = cached
                packed_windows.append(cached[1])
                lengths.append(len(window))
            points = np.frombuffer(bytearray().join(packed_windows), dtype=np.float64).reshape(
                (-1, 2)
            )
            visible = simulation._routes.visible_waypoint_rows(
                starts, lengths, points, obstacles, lookahead_radii
            )
        else:
            visible = simulation._routes.visible_waypoints(
                starts, paths, obstacles, lookahead_radii
            )
        for entity, index in zip(lookahead, visible.tolist(), strict=True):
            if index:
                del entity.path[:index]
    count = len(keys)
    workspace.ensure(count)
    workspace.refresh_profiles(entities, simulation.TICK_SECONDS)
    data = workspace.records[:count]
    pack_motion, pack_return = _MOTION_ROW.pack_into, _RETURN_ROW.pack_into
    stride = data.strides[0]
    recovery_ticks = workspace.integers["ticks"][:count]
    recovery_ticks.fill(-1)
    for index, entity in enumerate(entities):
        point = entity.position
        path = entity.path
        if entity.contact_wait_ticks:
            entity.contact_wait_ticks -= 1
        if path:
            entity.route_ticks += 1
            distance = _track_progress(entity, path[0], simulation.MIN_PROGRESS_DISTANCE)
            arrived = distance <= 1e-9
            if not arrived and len(path) == 1 and distance <= simulation.DEFEND_STATION_TOLERANCE:
                job = simulation.automations.get(simulation.assignments.get(entity.entity_id, ""))
                arrived = job is not None and isinstance(job.parameters, DefendParameters)
            if arrived:
                path.pop(0)
                if path:
                    entity.progress_target = entity.progress_distance = None
                    entity.no_progress_ticks = 0
                else:
                    _finish_path(simulation, entity)
        waypoint = path[0] if path else point
        stalled = entity.no_progress_ticks >= yield_ticks
        offset = index * stride
        if entity.hold_anchor is None and not (
            entity.defend_arrived and entity.state is UnitState.DEFENDING
        ):
            if entity.recovery_request_tick is not None or entity.recovery_released:
                entity.clear_recovery_request()
            if not path and entity.recovery_path:
                entity.recovery_path.clear()
            goal = entity.move_target or (path[-1] if path else point)
            pack_motion(
                data,
                offset,
                point.x,
                point.y,
                waypoint.x,
                waypoint.y,
                goal.x,
                goal.y,
                bool(path),
                bool(path) and stalled,
                entity.collision_pressure,
                False,
            )
            continue
        target = waypoint if path else _hold_target(simulation, entity)
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
            and simulation._routes.disc_segment(
                point,
                recovery_goal,
                obstacles,
                collision_radius(entity.kind),
            )
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
        if entity.recovery_request_tick is not None:
            recovery_ticks[index] = entity.recovery_request_tick
        if not strong_intent and entity.contact_wait_ticks:
            target = point
        pack_motion(
            data,
            offset,
            point.x,
            point.y,
            waypoint.x,
            waypoint.y,
            goal.x,
            goal.y,
            bool(path),
            (strong_intent or pending_recovery) and stalled,
            entity.collision_pressure,
            True,
        )
        pack_return(
            data,
            offset + _MOTION_ROW.size,
            target.x,
            target.y,
            recovery_target.x,
            recovery_target.y,
            recovery_goal.x if recovery_goal is not None else point.x,
            recovery_goal.y if recovery_goal is not None else point.y,
            strong_intent,
            pending_recovery,
            entity.recovery_released,
            recovery_tolerance,
            recovery_goal is not None,
        )
    floats = {key: value[:count] for key, value in workspace.floats.items()}
    booleans = {key: value[:count] for key, value in workspace.booleans.items()}
    integers = {key: value[:count] for key, value in workspace.integers.items()}
    positions, target_data, goals = floats["positions"], floats["targets"], floats["goals"]
    _prepare_motion(
        data,
        positions,
        target_data,
        goals,
        floats["recovery_targets"],
        floats["tolerances"],
        booleans["moving"],
        booleans["stationary"],
        booleans["routed"],
        booleans["recovering"],
        booleans["released"],
        booleans["stalled"],
    )
    radii, speeds = floats["radii"], floats["speeds"]
    speeds[:] = floats["base_speeds"]
    # Sample original centers in one batch; the solver shares this cap for all motion.
    speeds /= simulation._routes.movement_costs_at(positions)
    formations.coordinate(simulation, entities, target_data, speeds)
    masses = floats["masses"]
    moving, stationary = booleans["moving"], booleans["stationary"]
    # One stable front ordering for active routes and nearby resting bodies.
    # Batch the arithmetic; IDs are already sorted and provide the last tie break.
    routed = booleans["routed"]
    headings = floats["headings"]
    _motion_geometry(positions, target_data, goals, routed, headings, floats["fronts"])
    # Preserve Python's ordered compensated sums and the existing axis/parity ties.
    totals = [sum(np.abs(headings[routed, axis]).tolist()) for axis in (0, 1)]
    axis = int(totals[1] > totals[0])
    sign = 1.0 if sum(headings[routed, axis].tolist()) >= 0 else -1.0
    priority = np.where(routed, headings[:, axis] >= 0, sign >= 0).astype(np.int64)
    priority ^= simulation.tick % 2
    # At a turn or merge, the front is measured along the segment being submitted,
    # not a remote goal behind a wall. Keep group/parity/ID tie breaks unchanged.
    fronts = np.where(
        routed,
        floats["fronts"],
        -sign * positions[:, axis],
    )
    order = np.asarray(np.lexsort((np.arange(len(keys)), fronts, priority)), dtype=np.int64)
    simulation._movement_step_attempt_count += int(np.count_nonzero(routed))
    work = workspace.integers["work"]
    work.fill(0)
    recovery_status = integers["status"]
    recovery_status.fill(0)
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
    feedback = integers["feedback"]
    feedback_indices = integers["feedback_indices"]
    submissions = _movement_feedback(
        data,
        updated,
        pressure,
        recovery_status,
        feedback,
        feedback_indices,
        simulation.game_map.width,
        simulation.game_map.height,
        simulation.MIN_PROGRESS_DISTANCE,
    )
    rows = feedback_indices[:submissions]
    for index, mask, x, y, pressed, status in zip(
        rows.tolist(),
        feedback[rows].tolist(),
        updated[rows, 0].tolist(),
        updated[rows, 1].tolist(),
        pressure[rows].tolist(),
        recovery_status[rows].tolist(),
        strict=True,
    ):
        entity = entities[index]
        key = entity.entity_id
        entity.collision_pressure = int(pressed)
        if mask & _CHANGED:
            candidate = Point(x, y)
            if mask & _CELL_CHANGED:
                simulation.occupancy.move(key, footprint_cells(entity.kind, candidate), movable)
            entity.position = candidate
        if pressed:
            entity.contact_wait_ticks = CONTACT_WAIT_TICKS
        if status == 1:
            entity.clear_recovery_request()
            if entity.hold_anchor is not None:
                reset_movement_liveness(entity)
        elif status == 2:
            entity.recovery_released = False
        if mask & _HAS_RECOVERY:
            if mask & _OUTSIDE_RECOVERY:
                if entity.recovery_request_tick is None:
                    entity.recovery_request_tick = simulation.tick
            elif entity.recovery_request_tick is not None and status != 2:
                entity.recovery_released = True
                if entity.hold_anchor is not None:
                    reset_movement_liveness(entity)
        if not entity.path:
            continue
        target = entity.path[0]
        if pressed and entity.no_progress_ticks >= yield_ticks:
            simulation._movement_blocked.add(key)
            if entity.no_progress_ticks == simulation.NO_PROGRESS_YIELD_TICKS:
                simulation.events.record(
                    simulation.tick,
                    EventType.MOVEMENT_BLOCKED,
                    key,
                    reason="LOCAL_OBSTACLE",
                    target=[target.x, target.y],
                )
        elif mask & _PROGRESSED:
            simulation._movement_blocked.discard(key)
        if mask & _AT_WAYPOINT:
            entity.path.pop(0)
            # Progress was sampled against this waypoint before the solve; only
            # consuming it can change the target here. Equal repeated waypoints
            # retain the existing monotonic-progress semantics.
            if entity.progress_target != (entity.path[0] if entity.path else None):
                entity.progress_target = entity.progress_distance = None
                entity.no_progress_ticks = 0
        if entity.no_progress_ticks >= yield_ticks:
            _recover_path(simulation, entity, obstacles)
        if not entity.path:
            _finish_path(simulation, entity)

    workspace.tick = simulation.tick


def _finish_path(simulation: Simulation, entity: Entity) -> None:
    if entity.path:
        return
    entity.move_target = None
    simulation._movement_blocked.discard(entity.entity_id)
    reset_movement_liveness(entity)
    entity.state = state_for_assignment(simulation, entity.entity_id)
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
        route = simulation._routes.recovery_path(
            entity.position,
            entity.move_target or entity.path[-1],
            _route_obstacles(simulation, entity, obstacles),
        )
    except PathfindingError:
        return
    entity.path = _endpoint_waypoints(
        simulation,
        entity,
        entity.move_target or entity.path[-1],
        route,
        _route_obstacles(simulation, entity, obstacles),
    )
    target = entity.path[0] if entity.path else None
    if target != entity.progress_target:
        entity.progress_target = target
        entity.progress_distance = entity.position.distance_to(target) if target else None
        entity.no_progress_ticks = 0


def _endpoint_waypoints(
    simulation: Simulation,
    entity: Entity,
    target: Point,
    route: PathResult,
    blocked: frozenset[Cell],
) -> list[Point]:
    # Grid turns are safe for current mobile radii (< half a cell), but
    # continuous source/destination offsets may need their cell's center.
    points = list(route.waypoints)
    start = entity.position
    radius = collision_radius(entity.kind)
    if points and not simulation._routes.disc_segment(start, points[0], blocked, radius):
        x, y = simulation.game_map.cell_for(start)
        points.insert(0, Point(x + 0.5, y + 0.5))
    previous = points[-2] if len(points) > 1 else start
    if points and not simulation._routes.disc_segment(previous, target, blocked, radius):
        x, y = simulation.game_map.cell_for(target)
        points.insert(len(points) - 1, Point(x + 0.5, y + 0.5))
    return points


def plan_group_paths(
    simulation: Simulation,
    entity_ids: tuple[str, ...],
    destinations: dict[str, Point],
    *,
    shared_goal: Point | None = None,
    compact_approach: bool = False,
    allowance: RouteAllowance | None = None,
) -> dict[str, PathResult]:
    blocked = simulation._building_cells()
    center = Point(
        sum(p.x for p in destinations.values()) / len(destinations),
        sum(p.y for p in destinations.values()) / len(destinations),
    )
    anchor = shared_goal or min(
        destinations.values(), key=lambda p: (p.distance_to(center), p.y, p.x)
    )
    # March together along the weighted field, splitting only near the compact
    # arrival area. An early visible exit can otherwise cut across slow terrain
    # and discard almost the entire shared route.
    branch_distance = (
        max(point.distance_to(anchor) for point in destinations.values()) + 2.0
        if compact_approach
        else float("inf")
    )
    return {
        key: simulation._routes.group_path(
            simulation.entities[key].position,
            anchor,
            destinations[key],
            blocked,
            maximum_distance=branch_distance,
            compact_approach=compact_approach,
            allowance=allowance,
        )
        for key in entity_ids
    }


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


def validate_patrol_paths(
    simulation: Simulation, entity_ids: tuple[str, ...], waypoints: tuple[Point, ...]
) -> None:
    """Every member must reach each arrival area, not its possibly obstructed center."""
    blocked = simulation._building_cells()
    radius = max(collision_radius(simulation.entities[key].kind) for key in entity_ids)
    for index, anchor in enumerate(waypoints):
        goals = simulation._routes.arrival_goals(
            anchor,
            patrol_arrival_radius(waypoints, index, radius, len(entity_ids)),
            radius,
            blocked,
        )
        if not goals:
            raise PathfindingError(f"NO_PATROL_APPROACH:{index}")
        if any(
            not simulation._routes.can_reach_any(simulation.entities[key].position, goals, blocked)
            for key in entity_ids
        ):
            raise PathfindingError(f"NO_PATH_TO_PATROL_AREA:{index}")
