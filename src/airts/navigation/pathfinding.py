"""Shared reverse Dijkstra routes on the static weighted grid."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from itertools import chain
from math import ceil, floor, inf

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import (
    BoolArray,
    FloatArray,
    IntArray,
    compile_numeric,
    compile_parallel_numeric,
    point_coordinates,
)
from airts.world.map import Cell, GameMap

LOOKAHEAD_WAYPOINT_LIMIT = 16


class PathfindingError(ValueError):
    """A requested endpoint or route is unavailable."""


class RouteDeferred(Exception):
    """Keep a queued order pending until more search work can be admitted."""


@dataclass(frozen=True, slots=True)
class PathResult:
    cells: tuple[Cell, ...]
    waypoints: tuple[Point, ...]
    cost: float
    prefix_costs: FloatArray | None = field(default=None, repr=False, compare=False)


@dataclass(slots=True)
class _SearchField:
    costs: FloatArray
    following: IntArray
    heap: IntArray
    positions: IntArray
    size: int


@dataclass(slots=True)
class _PreparedGoals:
    goals: tuple[Point, ...]
    blocked: frozenset[Cell]
    points: dict[int, Point]
    key: tuple[tuple[int, ...], frozenset[Cell]]
    search_field: _SearchField | None = None


@dataclass(slots=True)
class RouteAllowance:
    service: RoutingService
    limit: int
    used: int = 0
    search_remaining: int | None = None

    @property
    def available(self) -> bool:
        return (
            self.used < self.limit
            and self.service.automation_route_count < self.service.automation_budget
        )

    def claim(self) -> bool:
        if not self.available:
            return False
        self.used += 1
        self.service.automation_route_count += 1
        return True


class RoutingService:
    """Build once per destination set; extract paths in O(path length) per unit.

    Moving units never enter the field cache key. Local collision belongs to movement.
    Fields are disposable: their eviction or rebuilding cannot change a chosen route.
    """

    def __init__(
        self,
        game_map: GameMap,
        *,
        automation_budget: int = 32,
        combat_budget: int = 16,
        maximum_fields: int = 32,
    ) -> None:
        if min(automation_budget, combat_budget, maximum_fields) <= 0:
            raise ValueError("routing budgets and cache capacity must be positive")
        self.game_map = game_map
        self.automation_budget = automation_budget
        self.combat_budget = combat_budget
        self.maximum_fields = maximum_fields
        self.automation_route_count = 0
        self.combat_route_count = 0
        self.field_build_count = 0
        self.expanded_cell_count = 0
        self._cost_grid = np.fromiter(
            (t.movement_cost for row in game_map.terrain for t in row),
            np.float64,
            game_map.height * game_map.width,
        ).reshape((game_map.height, game_map.width))
        self._terrain_mask = np.isinf(self._cost_grid)
        # Leave a small margin below the cheapest step. Even along a long road,
        # every optimal predecessor then settles before its successor despite rounding.
        self._heuristic_step = float(self._cost_grid.min()) * (1023 / 1024)
        self._obstacle_masks: OrderedDict[frozenset[Cell], BoolArray] = OrderedDict()
        self._connected_regions: OrderedDict[frozenset[Cell], IntArray] = OrderedDict()
        _clear_segment(self._cost_grid, self.obstacle_mask(frozenset()), 0.5, 0.5, 0.5, 0.5)
        # Numba distinguishes an omitted default from an explicit cost threshold.
        # Warm both signatures before the first command, including weighted branches.
        _clear_segment(self._cost_grid, self.obstacle_mask(frozenset()), 0.5, 0.5, 0.5, 0.5, inf)
        # Compile before input processing; a first move must not trigger JIT work.
        _expand_field(
            np.ones((1, 1), dtype=np.float64),
            np.zeros((1, 1), dtype=np.bool_),
            np.zeros(1),
            np.full(1, -1, dtype=np.int64),
            np.zeros(1, dtype=np.int64),
            np.zeros(1, dtype=np.int64),
            1,
            0,
            0.0,
        )
        _reachable_cells(np.zeros((1, 1), dtype=np.bool_), 0, 0)
        _label_regions(np.zeros((1, 1), dtype=np.bool_))
        _connected_points(np.zeros((1, 1), dtype=np.int64), np.empty((0, 2)), np.empty((0, 2)))
        _extract_path(np.ones((1, 1)), np.full(1, -1, dtype=np.int64), 0)
        _segment_path(
            self._cost_grid, self.obstacle_mask(frozenset()), 0.5, 0.5, 0.5, 0.5, 1.0, 0.0, 0.0
        )
        _visible_waypoints(
            np.zeros((1, 5)),
            self._cost_grid,
            self.obstacle_mask(frozenset()),
            np.zeros((0, 2)),
        )
        _branch_index(
            self._cost_grid, self.obstacle_mask(frozenset()), np.zeros((0, 2)), 0.5, 0.5, inf
        )
        _route_coordinates(np.zeros(1, dtype=np.int64), game_map.width, 0.5, 0.5, True)
        self._fields: OrderedDict[tuple[tuple[int, ...], frozenset[Cell]], _SearchField] = (
            OrderedDict()
        )
        self._centers: dict[Cell, Point] = {}
        self._cells: dict[int, Cell] = {}
        self._arrival_goal_cache: OrderedDict[
            tuple[Point, float, float, frozenset[Cell]], tuple[Point, ...]
        ] = OrderedDict()
        self._prepared_goals: _PreparedGoals | None = None

    @property
    def cached_field_count(self) -> int:
        return len(self._fields)

    def clear(self) -> None:
        self._prepared_goals = None
        self._fields.clear()
        self._obstacle_masks.clear()
        self._connected_regions.clear()
        self._arrival_goal_cache.clear()

    def update_buildings(self, previous: frozenset[Cell], current: frozenset[Cell]) -> None:
        """Carry untouched reverse searches across a local building placement or removal."""
        width = self.game_map.width
        # Newly opened cells could have been entered from an already-expanded
        # neighbor. A conservative one-cell halo also covers goals and the frontier.
        affected = {
            ny * width + nx
            for x, y in previous ^ current
            for nx, ny in ((x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if self.game_map.contains_cell((nx, ny))
        }
        retained = [
            ((goals, current), field)
            for (goals, blocked), field in self._fields.items()
            if (blocked is previous or blocked == previous)
            and all(field.positions[index] == -1 for index in affected)
        ]
        self.clear()
        self._fields.update(retained)

    def arrival_goals(
        self, anchor: Point, distance: float, radius: float, blocked: frozenset[Cell]
    ) -> tuple[Point, ...]:
        """Bounded, body-clear entry points for an arrival disk; mobile occupancy is local."""
        key = anchor, distance, radius, blocked
        cached = self._arrival_goal_cache.get(key)
        if cached is not None:
            self._arrival_goal_cache.move_to_end(key)
            return cached
        candidates = [anchor]
        candidates.extend(
            Point(x + 0.5, y + 0.5)
            for y in range(
                max(0, floor(anchor.y - distance)),
                min(self.game_map.height, floor(anchor.y + distance) + 1),
            )
            for x in range(
                max(0, floor(anchor.x - distance)),
                min(self.game_map.width, floor(anchor.x + distance) + 1),
            )
        )
        goals = tuple(
            sorted(
                {
                    point
                    for point in candidates
                    if point.distance_to(anchor) <= distance + 1e-9
                    and self.disc_segment(point, point, blocked, radius)
                },
                key=lambda point: (point.distance_to(anchor), point.y, point.x),
            )
        )
        self._arrival_goal_cache[key] = goals
        if len(self._arrival_goal_cache) > self.maximum_fields * 4:
            self._arrival_goal_cache.popitem(last=False)
        return goals

    def movement_costs_at(self, positions: FloatArray) -> FloatArray:
        """Batch-sample the shared static terrain costs at continuous body centers."""
        if not np.all(
            (positions[:, 0] >= 0)
            & (positions[:, 0] < self.game_map.width)
            & (positions[:, 1] >= 0)
            & (positions[:, 1] < self.game_map.height)
        ):
            raise ValueError("movement position outside map")
        cells = positions.astype(np.int64)
        return self._cost_grid[cells[:, 1], cells[:, 0]]

    def can_reach_any(
        self, start: Point, goals: tuple[Point, ...], blocked: frozenset[Cell]
    ) -> bool:
        """Exact static connectivity, independent of path budgets and cache lifetime."""
        if (
            not goals
            or not self.game_map.is_passable(start)
            or self.game_map.cell_for(start) in blocked
        ):
            return False
        regions = self._connection_regions(blocked)
        x, y = self.game_map.cell_for(start)
        region = regions[y, x]
        for goal in goals:
            if self.game_map.contains(goal):
                gx, gy = self.game_map.cell_for(goal)
                if regions[gy, gx] == region:
                    return True
        return False

    def can_reach_many(
        self, starts: FloatArray, goals: tuple[Point, ...], blocked: frozenset[Cell]
    ) -> BoolArray:
        """Exact static connectivity for a group sharing one set of destinations."""
        if starts.ndim != 2 or starts.shape[1] != 2:
            raise ValueError("connectivity starts must have x and y columns")
        if not len(starts) or not goals:
            return np.zeros(len(starts), dtype=np.bool_)
        targets = point_coordinates(goals)
        return _connected_points(self._connection_regions(blocked), starts, targets)

    def _connection_regions(self, blocked: frozenset[Cell]) -> IntArray:
        regions = self._connected_regions.get(blocked)
        if regions is None:
            regions = _label_regions(self.obstacle_mask(blocked))
            self._connected_regions[blocked] = regions
            if len(self._connected_regions) > min(self.maximum_fields, 4):
                self._connected_regions.popitem(last=False)
        self._connected_regions.move_to_end(blocked)
        return regions

    def validate_connection(self, start: Point, goal: Point, blocked: frozenset[Cell]) -> None:
        """Validate a route without allocating an unused full path."""
        if not self.game_map.is_passable(start):
            raise PathfindingError("START_NOT_PASSABLE")
        if self.game_map.cell_for(start) in blocked:
            raise PathfindingError("START_OCCUPIED")
        if not self.game_map.is_passable(goal) or self.game_map.cell_for(goal) in blocked:
            raise PathfindingError("TARGET_NOT_PASSABLE")
        if not self.can_reach_any(start, (goal,), blocked):
            raise PathfindingError("NO_PATH")

    def segment_path(
        self,
        start: Point,
        end: Point,
        blocked: frozenset[Cell],
        *,
        maximum_cost: float = 1.0,
        initial_cost: float = 0.0,
        initial_correction: float = 0.0,
    ) -> PathResult | None:
        """Check, collect and cost a segment once, continuing an ordered prefix sum."""
        indices, cost = _segment_path(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(start.x),
            float(start.y),
            float(end.x),
            float(end.y),
            maximum_cost,
            initial_cost,
            initial_correction,
        )
        if not len(indices):
            return None
        cells = self._path_cells(indices.tolist())
        return PathResult(cells, () if start == end else (end,), cost)

    def _path_cells(self, indices: list[int]) -> tuple[Cell, ...]:
        cells = self._cells
        width = self.game_map.width
        return tuple(
            cells[index]
            if index in cells
            else cells.setdefault(index, (index % width, index // width))
            for index in indices
        )

    def clear_segment(self, start: Point, end: Point, blocked: frozenset[Cell]) -> bool:
        return _clear_segment(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(start.x),
            float(start.y),
            float(end.x),
            float(end.y),
        )

    def passable_segment(self, start: Point, end: Point, blocked: frozenset[Cell]) -> bool:
        return _clear_segment(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(start.x),
            float(start.y),
            float(end.x),
            float(end.y),
            inf,
        )

    def disc_segment(
        self,
        start: Point,
        end: Point,
        blocked: frozenset[Cell],
        radius: float,
        maximum_cost: float = inf,
    ) -> bool:
        """Certify a shortcut's full body, including rounded static corners."""
        return _clear_segment(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(start.x),
            float(start.y),
            float(end.x),
            float(end.y),
            maximum_cost,
            radius,
        )

    def group_path(
        self,
        start: Point,
        anchor: Point,
        target: Point,
        blocked: frozenset[Cell],
        *,
        maximum_distance: float = inf,
        compact_approach: bool = False,
        allowance: RouteAllowance | None = None,
    ) -> PathResult:
        """Branch a numeric shared route before materializing its retained cells/points."""
        direct = self.segment_path(start, target, blocked)
        if direct is not None:
            return direct
        try:
            goal, indices, prefixes, cost = self._shared_path_data(
                start, (anchor,), blocked, allowance
            )
        except PathfindingError:
            # A building can cut off a queued march's anchor while its station is
            # still accessible. The individual's destination remains authoritative.
            if not compact_approach or target == anchor:
                raise
            return self.shared_path_to_any(start, (target,), blocked, allowance=allowance)[1]
        if compact_approach and target == anchor:
            return self._materialize_path(start, goal, indices, prefixes, cost)
        coordinates = _route_coordinates(
            indices, self.game_map.width, float(goal.x), float(goal.y), start != goal
        )
        branch_index = _branch_index(
            self._cost_grid,
            self.obstacle_mask(blocked),
            coordinates,
            float(target.x),
            float(target.y),
            maximum_distance,
        )
        if branch_index < 0:
            goal, indices, prefixes, cost = self._shared_path_data(
                start, (target,), blocked, allowance
            )
            return self._materialize_path(start, goal, indices, prefixes, cost)
        last = min(branch_index + 1, len(indices) - 1)
        branch_cells, cost = _segment_path(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(coordinates[branch_index, 0]),
            float(coordinates[branch_index, 1]),
            float(target.x),
            float(target.y),
            inf,
            float(prefixes[last, 0]),
            float(prefixes[last, 1]),
        )
        assert len(branch_cells), "certified group branch must remain passable"
        cells = self._path_cells(indices[: last + 1].tolist() + branch_cells[1:].tolist())
        centers = self._centers
        center_count = min(branch_index + 1, max(0, len(indices) - 2))
        waypoints = tuple(
            centers[cell]
            if cell in centers
            else centers.setdefault(cell, Point(cell[0] + 0.5, cell[1] + 0.5))
            for cell in cells[1 : center_count + 1]
        )
        if branch_index >= len(indices) - 2:
            waypoints += (goal,)
        waypoints += (target,)
        return PathResult(cells, waypoints, cost)

    def visible_waypoints(
        self,
        starts: list[Point],
        paths: list[list[Point]],
        blocked: frozenset[Cell],
        radii: list[float] | None = None,
    ) -> IntArray:
        """Each reader checks up to sixteen candidates against one obstacle snapshot."""
        windows = [path[1 : 1 + LOOKAHEAD_WAYPOINT_LIMIT] for path in paths]
        points = point_coordinates(list(chain.from_iterable(windows)))
        return self.visible_waypoint_rows(starts, list(map(len, windows)), points, blocked, radii)

    def visible_waypoint_rows(
        self,
        starts: list[Point],
        lengths: list[int],
        points: FloatArray,
        blocked: frozenset[Cell],
        radii: list[float] | None,
    ) -> IntArray:
        """Check packed candidate rows from the current starts and obstacles."""
        queries = np.empty((len(starts), 5), dtype=np.float64)
        point_coordinates(starts, queries[:, :2])
        queries[:, 2] = 0.0 if radii is None else radii
        ends = np.cumsum(lengths, dtype=np.int64)
        queries[:, 4] = ends
        if len(queries):
            queries[0, 3] = 0
            queries[1:, 3] = ends[:-1]
        return _visible_waypoints(queries, self._cost_grid, self.obstacle_mask(blocked), points)

    def nearest_cell(self, point: Point, blocked: frozenset[Cell]) -> Cell:
        mask = self.obstacle_mask(blocked)
        if self.game_map.contains(point):
            x, y = int(point.x), int(point.y)
            if x < point.x and y < point.y and not mask[y, x]:
                return x, y
        # On cell boundaries retain the row-major tie rule across all candidates.
        distances = ((np.arange(self.game_map.width) + 0.5 - point.x) ** 2)[None, :] + (
            (np.arange(self.game_map.height) + 0.5 - point.y) ** 2
        )[:, None]
        distances[mask] = inf
        index = int(np.argmin(distances))
        if not np.isfinite(distances.flat[index]):
            raise ValueError("gathering point has no passable space")
        return index % self.game_map.width, index // self.game_map.width

    def reachable_cells(self, anchor: Cell, blocked: frozenset[Cell]) -> BoolArray:
        return _reachable_cells(self.obstacle_mask(blocked), *anchor)

    def obstacle_mask(self, blocked: frozenset[Cell]) -> BoolArray:
        mask = self._obstacle_masks.get(blocked)
        if mask is None:
            mask = self._terrain_mask.copy()
            if blocked:
                cells = np.fromiter(chain.from_iterable(blocked), np.intp, 2 * len(blocked))
                x, y = cells.reshape(-1, 2).T
                height, width = mask.shape
                inside = (0 <= x) & (x < width) & (0 <= y) & (y < height)
                mask[y[inside], x[inside]] = True
            self._obstacle_masks[blocked] = mask
            if len(self._obstacle_masks) > self.maximum_fields:
                self._obstacle_masks.popitem(last=False)
        self._obstacle_masks.move_to_end(blocked)
        return mask

    def begin_tick(self) -> None:
        self.automation_route_count = self.combat_route_count = 0

    def automation_allowance(
        self, limit: int, *, search_limit: int | None = None
    ) -> RouteAllowance:
        if limit <= 0 or (search_limit is not None and search_limit <= 0):
            raise ValueError("route and search allowances must be positive")
        return RouteAllowance(self, limit, search_remaining=search_limit)

    def claim_combat_route(self) -> bool:
        if self.combat_route_count >= self.combat_budget:
            return False
        self.combat_route_count += 1
        return True

    def shared_path(
        self, start: Point, goal: Point, blocked: frozenset[Cell] = frozenset()
    ) -> PathResult:
        return self.shared_path_to_any(start, (goal,), blocked)[1]

    def shared_path_to_any(
        self,
        start: Point,
        goals: tuple[Point, ...],
        blocked: frozenset[Cell] = frozenset(),
        *,
        allowance: RouteAllowance | None = None,
    ) -> tuple[Point, PathResult]:
        goal, indices, prefixes, cost = self._shared_path_data(start, goals, blocked, allowance)
        return goal, self._materialize_path(start, goal, indices, prefixes, cost)

    def recovery_path(self, start: Point, goal: Point, blocked: frozenset[Cell]) -> PathResult:
        """Use a shared field when resident; direct an isolated repair toward its start."""
        endpoint, indices, prefixes, cost = self._shared_path_data(
            start, (goal,), blocked, single_use=True
        )
        return self._materialize_path(start, endpoint, indices, prefixes, cost)

    def _shared_path_data(
        self,
        start: Point,
        goals: tuple[Point, ...],
        blocked: frozenset[Cell],
        allowance: RouteAllowance | None = None,
        *,
        single_use: bool = False,
    ) -> tuple[Point, IntArray, FloatArray, float]:
        game_map = self.game_map
        if not game_map.is_passable(start):
            raise PathfindingError("START_NOT_PASSABLE")
        start_cell = game_map.cell_for(start)
        if start_cell in blocked:
            raise PathfindingError("START_OCCUPIED")
        if not goals:
            raise PathfindingError("NO_DESTINATIONS")
        prepared = self._prepared_goals
        if (
            prepared is None
            or prepared.goals is not goals
            and prepared.goals != goals
            or prepared.blocked is not blocked
            and prepared.blocked != blocked
        ):
            points: dict[int, Point] = {}
            for point in sorted(goals, key=lambda p: (p.y, p.x)):
                if not game_map.is_passable(point):
                    continue
                cell = game_map.cell_for(point)
                if cell not in blocked:
                    points.setdefault(cell[1] * game_map.width + cell[0], point)
            if not points:
                raise PathfindingError("TARGET_NOT_PASSABLE")
            prepared = _PreparedGoals(goals, blocked, points, (tuple(sorted(points)), blocked))
            self._prepared_goals = prepared
        points, key = prepared.points, prepared.key
        # Consecutive followers use the most recently touched field. No other
        # method inserts/evicts fields, so it stays resident until goals change
        # or clear() drops this prepared snapshot. Avoid rehashing a large ring
        # of goal cells for every member of the same army.
        field = prepared.search_field
        if field is None:
            field = self._fields.get(key)
        index = start_cell[1] * game_map.width + start_cell[0]
        if (
            allowance is not None
            and allowance.search_remaining == 0
            and (field is None or field.positions[index] != -2)
        ):
            raise RouteDeferred
        directed = field is None and single_use
        if field is None:
            field = self._build_field(key[0])
            if not directed:
                self._fields[key] = field
                if len(self._fields) > self.maximum_fields:
                    self._fields.popitem(last=False)
        if not directed and prepared.search_field is None:
            self._fields.move_to_end(key)
            prepared.search_field = field
        costs, following = field.costs, field.following
        if field.positions[index] != -2:
            field.size, expanded = _expand_field(
                self._cost_grid,
                self.obstacle_mask(blocked),
                costs,
                following,
                field.heap,
                field.positions,
                field.size,
                index,
                self._heuristic_step if directed else 0.0,
            )
            self.expanded_cell_count += expanded
            if allowance is not None and allowance.search_remaining is not None:
                # Finish one admitted search atomically, then defer further cold work.
                # Cached settled paths and cheap direct segments can still dispatch.
                allowance.search_remaining = max(0, allowance.search_remaining - expanded)
        cost = float(costs[index])
        if cost == inf:
            raise PathfindingError("NO_PATH")
        indices, prefix_costs = _extract_path(self._cost_grid, following, index)
        goal = points[int(indices[-1])]
        return goal, indices, prefix_costs, cost

    def _materialize_path(
        self,
        start: Point,
        goal: Point,
        indices: IntArray,
        prefix_costs: FloatArray,
        cost: float,
    ) -> PathResult:
        cells = self._path_cells(indices.tolist())
        # Cell centers keep every turn inside a passable cell. Continuous steering may
        # shorten these segments only after checking the intervening terrain.
        centers = self._centers
        waypoints = tuple(
            centers[cell]
            if cell in centers
            else centers.setdefault(cell, Point(cell[0] + 0.5, cell[1] + 0.5))
            for cell in cells[1:-1]
        )
        if start != goal:
            waypoints += (goal,)
        return PathResult(cells, waypoints, cost, prefix_costs)

    def _build_field(self, goals: tuple[int, ...]) -> _SearchField:
        count = self.game_map.width * self.game_map.height
        costs = np.full(count, inf)
        positions = np.full(count, -1, dtype=np.int64)
        heap = np.empty(count, dtype=np.int64)
        following = np.empty(count, dtype=np.int64)
        for offset, index in enumerate(goals):
            costs[index], positions[index], heap[offset] = 0.0, offset, index
            following[index] = -1
        # Relaxation initializes every reachable non-goal before extraction.
        field = _SearchField(costs, following, heap, positions, len(goals))
        self.field_build_count += 1
        return field


@compile_numeric
def _connected_points(regions: IntArray, starts: FloatArray, goals: FloatArray) -> BoolArray:
    """Classify a group against exact component labels without per-group NumPy set setup."""
    height, width = regions.shape
    # Labels are bounded by the map size. One bit per possible component avoids
    # quadratic deduplication on fragmented maps and NumPy's per-query set setup.
    allowed = np.zeros((regions.size + 63) // 64, dtype=np.uint64)
    for x, y in goals:
        if not (0 <= x < width and 0 <= y < height):
            continue
        label = regions[int(y), int(x)]
        if label < 0:
            continue
        allowed[label // 64] |= np.uint64(1) << np.uint64(label % 64)
    result = np.zeros(len(starts), dtype=np.bool_)
    for i in range(len(starts)):
        x, y = starts[i]
        if not (0 <= x < width and 0 <= y < height):
            continue
        label = regions[int(y), int(x)]
        if label >= 0:
            result[i] = bool(allowed[label // 64] & (np.uint64(1) << np.uint64(label % 64)))
    return result


@compile_numeric
def _label_regions(blocked: BoolArray) -> IntArray:
    height, width = blocked.shape
    regions = np.full((height, width), -1, dtype=np.int64)
    queue = np.empty(height * width, dtype=np.int64)
    for start in range(height * width):
        x, y = start % width, start // width
        if blocked[y, x] or regions[y, x] != -1:
            continue
        queue[0], regions[y, x] = start, start
        read, size = 0, 1
        while read < size:
            index = queue[read]
            read += 1
            x, y = index % width, index // width
            for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
                if (
                    0 <= nx < width
                    and 0 <= ny < height
                    and not blocked[ny, nx]
                    and regions[ny, nx] == -1
                ):
                    regions[ny, nx] = start
                    queue[size] = ny * width + nx
                    size += 1
    return regions


@compile_numeric
def _route_coordinates(
    indices: IntArray, width: int, goal_x: float, goal_y: float, has_goal: bool
) -> FloatArray:
    count = max(1, len(indices) - 1) if has_goal else 0
    coordinates = np.empty((count, 2), dtype=np.float64)
    for offset in range(count - 1):
        cell = indices[offset + 1]
        coordinates[offset, 0] = cell % width + 0.5
        coordinates[offset, 1] = cell // width + 0.5
    if count:
        coordinates[-1, 0], coordinates[-1, 1] = goal_x, goal_y
    return coordinates


@compile_numeric
def _extract_path(
    costs: FloatArray, following: IntArray, start: int
) -> tuple[IntArray, FloatArray]:
    size, index = 1, start
    while following[index] != -1:
        size += 1
        index = following[index]
    indices = np.empty(size, dtype=np.int64)
    # Preserve Python 3.13 sum's compensated high/low state across a branch,
    # rather than rounding the prefix and restarting a different float sum.
    prefixes = np.zeros((size, 2), dtype=np.float64)
    indices[0] = start
    width = costs.shape[1]
    for offset in range(1, size):
        start = following[start]
        indices[offset] = start
        high, low = prefixes[offset - 1]
        value = costs[start // width, start % width]
        total = high + value
        low += (high - total) + value if abs(high) >= abs(value) else (value - total) + high
        prefixes[offset, 0], prefixes[offset, 1] = total, low
    return indices, prefixes


@compile_numeric
def _segment_path(
    costs: FloatArray,
    blocked: BoolArray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    maximum_cost: float,
    initial_cost: float,
    initial_correction: float,
) -> tuple[IntArray, float]:
    height, width = costs.shape
    empty = np.empty(0, dtype=np.int64)
    if not (0 <= ax < width and 0 <= ay < height and 0 <= bx < width and 0 <= by < height):
        return empty, 0.0
    x, y, ex, ey = floor(ax), floor(ay), floor(bx), floor(by)
    if blocked[y, x] or costs[y, x] > maximum_cost:
        return empty, 0.0
    cells = np.empty(abs(ex - x) + abs(ey - y) + 1, dtype=np.int64)
    cells[0], size, cost = y * width + x, 1, initial_cost
    correction = initial_correction
    dx, dy = bx - ax, by - ay
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    tx = ((x + 1 - ax) if dx > 0 else (ax - x)) / abs(dx) if dx else inf
    ty = ((y + 1 - ay) if dy > 0 else (ay - y)) / abs(dy) if dy else inf
    step_x, step_y = (1 / abs(dx) if dx else inf), (1 / abs(dy) if dy else inf)
    while x != ex or y != ey:
        if x == ex:
            tx = inf
        if y == ey:
            ty = inf
        if abs(tx - ty) < 1e-12:
            if blocked[y, x + sx] or blocked[y + sy, x]:
                return empty, 0.0
            x, y = x + sx, y + sy
            tx, ty = tx + step_x, ty + step_y
        elif tx < ty:
            x += sx
            tx += step_x
        else:
            y += sy
            ty += step_y
        if blocked[y, x] or costs[y, x] > maximum_cost:
            return empty, 0.0
        cells[size] = y * width + x
        size += 1
        value = costs[y, x]
        total = cost + value
        correction += (cost - total) + value if abs(cost) >= abs(value) else (value - total) + cost
        cost = total
    return cells[:size], cost + correction


@compile_numeric
def _expand_field(
    terrain_costs: FloatArray,
    blocked: BoolArray,
    costs: FloatArray,
    following: IntArray,
    heap: IntArray,
    positions: IntArray,
    size: int,
    stop: int,
    heuristic_step: float,
) -> tuple[int, int]:
    """Expand a shared Dijkstra frontier or an isolated single-goal A* frontier."""
    width = terrain_costs.shape[1]
    terrain = terrain_costs.ravel()
    obstacles = blocked.ravel()
    ranks = costs
    stop_x, stop_y = stop % width, stop // width
    if heuristic_step:
        ranks = np.empty_like(costs)
        # The directed search is created with a single goal and never resumed.
        seed = heap[0]
        ranks[seed] = heuristic_step * (abs(seed % width - stop_x) + abs(seed // width - stop_y))
    expanded = 0
    while size:
        current = heap[0]
        size -= 1
        if size:
            last = heap[size]
            last_cost = ranks[last]
            offset = 0
            while offset * 2 + 1 < size:
                child = offset * 2 + 1
                if child + 1 < size:
                    left, right = heap[child], heap[child + 1]
                    if ranks[right] < ranks[left] or (ranks[right] == ranks[left] and right < left):
                        child += 1
                candidate_index = heap[child]
                if last_cost < ranks[candidate_index] or (
                    last_cost == ranks[candidate_index] and last < candidate_index
                ):
                    break
                heap[offset] = candidate_index
                positions[candidate_index] = offset
                offset = child
            heap[offset], positions[last] = last, offset
        positions[current] = -2
        expanded += 1
        x = current % width
        candidate = costs[current] + terrain[current]
        left = current - 1 if x else -1
        right = current + 1 if x + 1 < width else -1
        for neighbor in (current - width, left, right, current + width):
            if not 0 <= neighbor < len(costs) or obstacles[neighbor]:
                continue
            if heuristic_step and candidate == costs[neighbor]:
                # Match the predecessor a cost/ID ordered Dijkstra heap visits first.
                old = following[neighbor]
                if costs[current] < costs[old] or (costs[current] == costs[old] and current < old):
                    following[neighbor] = current
            elif candidate < costs[neighbor]:
                costs[neighbor] = candidate
                rank = candidate
                if heuristic_step:
                    rank += heuristic_step * (
                        abs(neighbor % width - stop_x) + abs(neighbor // width - stop_y)
                    )
                    ranks[neighbor] = rank
                following[neighbor] = current
                offset = positions[neighbor]
                if offset == -1:
                    offset = size
                    size += 1
                while offset:
                    parent = (offset - 1) // 2
                    parent_index = heap[parent]
                    if ranks[parent_index] < rank or (
                        ranks[parent_index] == rank and parent_index < neighbor
                    ):
                        break
                    heap[offset], positions[parent_index] = parent_index, offset
                    offset = parent
                heap[offset], positions[neighbor] = neighbor, offset
        if current == stop:
            break
    return size, expanded


@compile_numeric
def _reachable_cells(blocked: BoolArray, ax: int, ay: int) -> BoolArray:
    height, width = blocked.shape
    reached = np.zeros((height, width), dtype=np.bool_)
    queue = np.empty(width * height, dtype=np.int64)
    queue[0] = ay * width + ax
    reached[ay, ax] = True
    head, tail = 0, 1
    while head < tail:
        current = queue[head]
        head += 1
        x, y = current % width, current // width
        for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height and not blocked[ny, nx] and not reached[ny, nx]:
                reached[ny, nx] = True
                queue[tail] = ny * width + nx
                tail += 1
    return reached


def passable_segment_cells(
    game_map: GameMap, start: Point, end: Point, blocked: frozenset[Cell] = frozenset()
) -> tuple[Cell, ...] | None:
    """Traverse every touched grid cell; reject diagonal corner cutting."""
    if not game_map.is_passable(start) or not game_map.is_passable(end):
        return None
    x, y = game_map.cell_for(start)
    ex, ey = game_map.cell_for(end)
    dx, dy = end.x - start.x, end.y - start.y
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    tx = ((x + 1 - start.x) if dx > 0 else (start.x - x)) / abs(dx) if dx else inf
    ty = ((y + 1 - start.y) if dy > 0 else (start.y - y)) / abs(dy) if dy else inf
    step_x, step_y = (1 / abs(dx) if dx else inf), (1 / abs(dy) if dy else inf)
    cells = [(x, y)]
    terrain = game_map.terrain
    if (x, y) in blocked:
        return None
    while (x, y) != (ex, ey):
        if x == ex:
            tx = inf
        if y == ey:
            ty = inf
        if abs(tx - ty) < 1e-12:
            if any(
                c in blocked or not terrain[c[1]][c[0]].passable for c in ((x + sx, y), (x, y + sy))
            ):
                return None
            x, y = x + sx, y + sy
            tx, ty = tx + step_x, ty + step_y
        elif tx < ty:
            x += sx
            tx += step_x
        else:
            y += sy
            ty += step_y
        if (x, y) in blocked or not terrain[y][x].passable:
            return None
        cells.append((x, y))
    return tuple(cells)


@compile_numeric
def _clear_segment(
    costs: FloatArray,
    blocked: BoolArray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    maximum_cost: float = 1.0,
    radius: float = 0.0,
) -> bool:
    """Exact DDA lookahead without allocating a cell list on every movement tick."""
    height, width = costs.shape
    if not (0 <= ax < width and 0 <= ay < height and 0 <= bx < width and 0 <= by < height):
        return False
    x, y, ex, ey = floor(ax), floor(ay), floor(bx), floor(by)
    if blocked[y, x] or costs[y, x] > maximum_cost:
        return False
    dx, dy = bx - ax, by - ay
    sx, sy = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
    tx = ((x + 1 - ax) if dx > 0 else (ax - x)) / abs(dx) if dx else inf
    ty = ((y + 1 - ay) if dy > 0 else (ay - y)) / abs(dy) if dy else inf
    step_x, step_y = (1 / abs(dx) if dx else inf), (1 / abs(dy) if dy else inf)
    while x != ex or y != ey:
        if x == ex:
            tx = inf
        if y == ey:
            ty = inf
        if abs(tx - ty) < 1e-12:
            if blocked[y, x + sx] or blocked[y + sy, x]:
                return False
            x, y = x + sx, y + sy
            tx, ty = tx + step_x, ty + step_y
        elif tx < ty:
            x += sx
            tx += step_x
        else:
            y += sy
            ty += step_y
        if blocked[y, x] or costs[y, x] > maximum_cost:
            return False
    return not radius or _disc_clear(blocked, ax, ay, bx, by, radius)


@compile_numeric
def _disc_clear(
    blocked: BoolArray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    radius: float,
) -> bool:
    """Centerline is already passable; test neighboring rectangles in a narrow sweep."""
    height, width = blocked.shape
    if min(ax, bx) < radius or max(ax, bx) > width - radius:
        return False
    if min(ay, by) < radius or max(ay, by) > height - radius:
        return False
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    radius_squared = radius * radius - 1e-12
    # Rows of the swept capsule bound its work by segment length, not rectangle area.
    for y in range(
        max(0, floor(min(ay, by) - radius)), min(height, floor(max(ay, by) + radius) + 1)
    ):
        if dy:
            t0 = max(0.0, min(1.0, (y - radius - ay) / dy))
            t1 = max(0.0, min(1.0, (y + 1 + radius - ay) / dy))
            x0, x1 = ax + t0 * dx, ax + t1 * dx
        else:
            x0, x1 = ax, bx
        for x in range(max(0, floor(min(x0, x1) - radius)), min(width, ceil(max(x0, x1) + radius))):
            if not blocked[y, x]:
                continue
            # A segment's closest point on a rectangle is an endpoint or a corner
            # projection unless it intersects its interior (excluded by the DDA).
            for px, py in ((ax, ay), (bx, by)):
                ex, ey = max(x - px, 0.0, px - x - 1), max(y - py, 0.0, py - y - 1)
                if ex * ex + ey * ey < radius_squared:
                    return False
            if length_squared:
                for cx, cy in ((x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)):
                    t = min(1.0, max(0.0, ((cx - ax) * dx + (cy - ay) * dy) / length_squared))
                    ex, ey = cx - ax - t * dx, cy - ay - t * dy
                    if ex * ex + ey * ey < radius_squared:
                        return False
    return True


@compile_numeric
def _branch_index(
    costs: FloatArray,
    blocked: BoolArray,
    points: FloatArray,
    bx: float,
    by: float,
    maximum_distance: float,
) -> int:
    for index in range(len(points)):
        ax, ay = points[index]
        if (ax - bx) ** 2 + (ay - by) ** 2 > maximum_distance**2:
            continue
        if _clear_segment(costs, blocked, ax, ay, bx, by, inf):
            return index
    return -1


@compile_parallel_numeric
def _visible_waypoints(
    queries: FloatArray,
    costs: FloatArray,
    blocked: BoolArray,
    points: FloatArray,
) -> IntArray:
    result = np.zeros(len(queries), dtype=np.int64)
    for index in range(len(queries)):
        ax, ay, radius, start, end = queries[index]
        for candidate in range(int(end) - 1, int(start) - 1, -1):
            bx, by = points[candidate]
            # Forest is slower terrain, not an obstruction. Permit equal-cost
            # shortcuts inside it while retaining the cheaper route around it.
            maximum = max(1.0, costs[floor(ay), floor(ax)], costs[floor(by), floor(bx)])
            if _clear_segment(costs, blocked, ax, ay, bx, by, maximum, radius):
                result[index] = candidate - int(start) + 1
                break
    return result
