"""Shared reverse Dijkstra routes on the static weighted grid."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from math import ceil, floor, inf

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import BoolArray, FloatArray, IntArray, compile_numeric
from airts.world.map import Cell, GameMap


class PathfindingError(ValueError):
    """A requested endpoint or route is unavailable."""


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
class RouteAllowance:
    service: RoutingService
    limit: int
    used: int = 0

    def claim(self) -> bool:
        if (
            self.used >= self.limit
            or self.service.automation_route_count >= self.service.automation_budget
        ):
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
        self._costs = tuple(t.movement_cost for row in game_map.terrain for t in row)
        self._cost_grid = np.asarray(self._costs).reshape((game_map.height, game_map.width))
        self._terrain_mask = np.isinf(self._cost_grid)
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
        )
        _reachable_cells(np.zeros((1, 1), dtype=np.bool_), 0, 0)
        _label_regions(np.zeros((1, 1), dtype=np.bool_))
        _extract_path(np.ones((1, 1)), np.full(1, -1, dtype=np.int64), 0)
        _segment_path(
            self._cost_grid, self.obstacle_mask(frozenset()), 0.5, 0.5, 0.5, 0.5, 1.0, 0.0, 0.0
        )
        _visible_waypoints(
            self._cost_grid,
            self.obstacle_mask(frozenset()),
            np.zeros((1, 2)),
            np.zeros((0, 2)),
            np.zeros(2, dtype=np.int64),
            np.zeros(1),
        )
        _branch_index(self._cost_grid, self.obstacle_mask(frozenset()), np.zeros((0, 2)), 0.5, 0.5)
        self._fields: OrderedDict[tuple[tuple[int, ...], frozenset[Cell]], _SearchField] = (
            OrderedDict()
        )
        self._centers: dict[Cell, Point] = {}
        self._cells: dict[int, Cell] = {}

    @property
    def cached_field_count(self) -> int:
        return len(self._fields)

    def clear(self) -> None:
        self._fields.clear()
        self._obstacle_masks.clear()
        self._connected_regions.clear()

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
        regions = self._connected_regions.get(blocked)
        if regions is None:
            regions = _label_regions(self.obstacle_mask(blocked))
            self._connected_regions[blocked] = regions
            if len(self._connected_regions) > min(self.maximum_fields, 4):
                self._connected_regions.popitem(last=False)
        self._connected_regions.move_to_end(blocked)
        x, y = self.game_map.cell_for(start)
        region = regions[y, x]
        for goal in goals:
            if self.game_map.contains(goal):
                gx, gy = self.game_map.cell_for(goal)
                if regions[gy, gx] == region:
                    return True
        return False

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

    def branch_index(
        self,
        path: PathResult,
        target: Point,
        blocked: frozenset[Cell],
    ) -> int:
        """Find the first safe exit in one numeric pass, materializing only its winner."""
        return _branch_index(
            self._cost_grid,
            self.obstacle_mask(blocked),
            np.fromiter(
                (coordinate for p in path.waypoints for coordinate in (p.x, p.y)),
                dtype=np.float64,
                count=2 * len(path.waypoints),
            ).reshape((-1, 2)),
            float(target.x),
            float(target.y),
        )

    def visible_waypoints(
        self,
        starts: list[Point],
        paths: list[list[Point]],
        blocked: frozenset[Cell],
        radii: list[float] | None = None,
    ) -> IntArray:
        """Bounded sixteen-waypoint lookahead in one compiled call per batch."""
        offsets = np.zeros(len(paths) + 1, dtype=np.int64)
        for index, path in enumerate(paths):
            offsets[index + 1] = offsets[index] + min(len(path) - 1, 16)
        points = np.fromiter(
            (
                coordinate
                for path in paths
                for point in path[1:17]
                for coordinate in (point.x, point.y)
            ),
            dtype=np.float64,
            count=int(offsets[-1]) * 2,
        ).reshape((-1, 2))
        return _visible_waypoints(
            self._cost_grid,
            self.obstacle_mask(blocked),
            np.asarray([(point.x, point.y) for point in starts], dtype=np.float64),
            points,
            offsets,
            np.zeros(len(starts)) if radii is None else np.asarray(radii, dtype=np.float64),
        )

    def nearest_cell(self, point: Point, blocked: frozenset[Cell]) -> Cell:
        distances = ((np.arange(self.game_map.width) + 0.5 - point.x) ** 2)[None, :] + (
            (np.arange(self.game_map.height) + 0.5 - point.y) ** 2
        )[:, None]
        distances[self.obstacle_mask(blocked)] = inf
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
            for x, y in blocked:
                if self.game_map.contains_cell((x, y)):
                    mask[y, x] = True
            self._obstacle_masks[blocked] = mask
            if len(self._obstacle_masks) > self.maximum_fields:
                self._obstacle_masks.popitem(last=False)
        self._obstacle_masks.move_to_end(blocked)
        return mask

    def begin_tick(self) -> None:
        self.automation_route_count = self.combat_route_count = 0

    def automation_allowance(self, limit: int) -> RouteAllowance:
        if limit <= 0:
            raise ValueError("route allowance must be positive")
        return RouteAllowance(self, limit)

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
        self, start: Point, goals: tuple[Point, ...], blocked: frozenset[Cell] = frozenset()
    ) -> tuple[Point, PathResult]:
        game_map = self.game_map
        if not game_map.is_passable(start):
            raise PathfindingError("START_NOT_PASSABLE")
        start_cell = game_map.cell_for(start)
        if start_cell in blocked:
            raise PathfindingError("START_OCCUPIED")
        if not goals:
            raise PathfindingError("NO_DESTINATIONS")
        points: dict[int, Point] = {}
        for point in sorted(goals, key=lambda p: (p.y, p.x)):
            if not game_map.is_passable(point):
                continue
            cell = game_map.cell_for(point)
            if cell not in blocked:
                points.setdefault(cell[1] * game_map.width + cell[0], point)
        if not points:
            raise PathfindingError("TARGET_NOT_PASSABLE")
        key = (tuple(sorted(points)), blocked)
        field = self._fields.get(key)
        if field is None:
            field = self._build_field(key[0], blocked)
            self._fields[key] = field
            if len(self._fields) > self.maximum_fields:
                self._fields.popitem(last=False)
        self._fields.move_to_end(key)
        costs, following = field.costs, field.following
        index = start_cell[1] * game_map.width + start_cell[0]
        if field.positions[index] != -2:
            field.size = _expand_field(
                self._cost_grid,
                self.obstacle_mask(blocked),
                costs,
                following,
                field.heap,
                field.positions,
                field.size,
                index,
            )
        cost = float(costs[index])
        if cost == inf:
            raise PathfindingError("NO_PATH")
        indices, prefix_costs = _extract_path(self._cost_grid, following, index)
        goal = points[int(indices[-1])]
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
        return goal, PathResult(cells, waypoints, cost, prefix_costs)

    def _build_field(self, goals: tuple[int, ...], blocked: frozenset[Cell]) -> _SearchField:
        count = self.game_map.width * self.game_map.height
        costs = np.full(count, inf)
        positions = np.full(count, -1, dtype=np.int64)
        heap = np.empty(count, dtype=np.int64)
        for offset, index in enumerate(goals):
            costs[index], positions[index], heap[offset] = 0.0, offset, index
        field = _SearchField(costs, np.full(count, -1, dtype=np.int64), heap, positions, len(goals))
        self.field_build_count += 1
        return field


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
) -> int:
    """Resume exact Dijkstra until the requested start is settled, then retain its frontier.

    An indexed heap avoids tuple allocations and stale duplicate entries. Comparison
    remains (cost, cell index); every neighbor of the stopping cell is relaxed before
    suspension, so later requests and cache eviction cannot affect chosen routes.
    """
    height, width = terrain_costs.shape
    while size:
        current = heap[0]
        size -= 1
        if size:
            last = heap[size]
            offset = 0
            while offset * 2 + 1 < size:
                child = offset * 2 + 1
                if child + 1 < size:
                    left, right = heap[child], heap[child + 1]
                    if costs[right] < costs[left] or (costs[right] == costs[left] and right < left):
                        child += 1
                candidate_index = heap[child]
                if costs[last] < costs[candidate_index] or (
                    costs[last] == costs[candidate_index] and last < candidate_index
                ):
                    break
                heap[offset] = candidate_index
                positions[candidate_index] = offset
                offset = child
            heap[offset], positions[last] = last, offset
        positions[current] = -2
        x, y = current % width, current // width
        candidate = costs[current] + terrain_costs[y, x]
        for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)):
            if not 0 <= nx < width or not 0 <= ny < height or blocked[ny, nx]:
                continue
            neighbor = ny * width + nx
            if candidate < costs[neighbor]:
                costs[neighbor] = candidate
                following[neighbor] = current
                offset = positions[neighbor]
                if offset == -1:
                    offset = size
                    size += 1
                while offset:
                    parent = (offset - 1) // 2
                    parent_index = heap[parent]
                    if costs[parent_index] < candidate or (
                        costs[parent_index] == candidate and parent_index < neighbor
                    ):
                        break
                    heap[offset], positions[parent_index] = parent_index, offset
                    offset = parent
                heap[offset], positions[neighbor] = neighbor, offset
        if current == stop:
            break
    return size


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
) -> int:
    for index in range(len(points)):
        ax, ay = points[index]
        if _clear_segment(costs, blocked, ax, ay, bx, by, inf):
            return index
    return -1


@compile_numeric
def _visible_waypoints(
    costs: FloatArray,
    blocked: BoolArray,
    starts: FloatArray,
    points: FloatArray,
    offsets: IntArray,
    radii: FloatArray,
) -> IntArray:
    result = np.zeros(len(starts), dtype=np.int64)
    for index in range(len(starts)):
        ax, ay = starts[index]
        for candidate in range(offsets[index + 1] - 1, offsets[index] - 1, -1):
            bx, by = points[candidate]
            # Forest is slower terrain, not an obstruction. Permit equal-cost
            # shortcuts inside it while retaining the cheaper route around it.
            maximum = max(1.0, costs[floor(ay), floor(ax)], costs[floor(by), floor(bx)])
            if _clear_segment(costs, blocked, ax, ay, bx, by, maximum, radii[index]):
                result[index] = candidate - offsets[index] + 1
                break
    return result
