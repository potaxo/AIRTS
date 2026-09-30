"""Shared reverse Dijkstra routes on the static weighted grid."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from heapq import heappop, heappush
from math import floor, inf

import numpy as np

from airts._numeric import BoolArray, FloatArray, compile_numeric
from airts.geometry import Point
from airts.world.map_model import Cell, GameMap


class PathfindingError(ValueError):
    """A requested endpoint or route is unavailable."""


@dataclass(frozen=True, slots=True)
class PathResult:
    cells: tuple[Cell, ...]
    waypoints: tuple[Point, ...]
    cost: float


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
        self._obstacle_masks: OrderedDict[frozenset[Cell], BoolArray] = OrderedDict()
        width, height = game_map.width, game_map.height
        self._neighbors = tuple(
            tuple(
                ny * width + nx
                for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))
                if 0 <= nx < width and 0 <= ny < height
            )
            for y in range(height)
            for x in range(width)
        )
        _clear_segment(self._cost_grid, self.obstacle_mask(frozenset()), 0.5, 0.5, 0.5, 0.5)
        self._fields: OrderedDict[
            tuple[tuple[int, ...], frozenset[Cell]], tuple[list[float], list[int]]
        ] = OrderedDict()

    @property
    def cached_field_count(self) -> int:
        return len(self._fields)

    def clear(self) -> None:
        self._fields.clear()
        self._obstacle_masks.clear()

    def clear_segment(self, start: Point, end: Point, blocked: frozenset[Cell]) -> bool:
        return _clear_segment(
            self._cost_grid,
            self.obstacle_mask(blocked),
            float(start.x),
            float(start.y),
            float(end.x),
            float(end.y),
        )

    def obstacle_mask(self, blocked: frozenset[Cell]) -> BoolArray:
        mask = self._obstacle_masks.get(blocked)
        if mask is None:
            mask = np.isinf(np.asarray(self._costs)).reshape(
                (self.game_map.height, self.game_map.width)
            )
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
        costs, following = field
        index = start_cell[1] * game_map.width + start_cell[0]
        cost = costs[index]
        if cost == inf:
            raise PathfindingError("NO_PATH")
        indices = [index]
        while following[index] != -1:
            index = following[index]
            indices.append(index)
        goal = points[index]
        cells = tuple((i % game_map.width, i // game_map.width) for i in indices)
        # Cell centers keep every turn inside a passable cell. Continuous steering may
        # shorten these segments only after checking the intervening terrain.
        waypoints = tuple(Point(x + 0.5, y + 0.5) for x, y in cells[1:-1])
        if start != goal:
            waypoints += (goal,)
        return goal, PathResult(cells, waypoints, cost)

    def _build_field(
        self, goals: tuple[int, ...], blocked: frozenset[Cell]
    ) -> tuple[list[float], list[int]]:
        costs = [inf] * len(self._costs)
        following = [-1] * len(costs)
        blocked_indices = {
            y * self.game_map.width + x for x, y in blocked if self.game_map.contains_cell((x, y))
        }
        frontier: list[tuple[float, int]] = []
        for index in goals:
            costs[index] = 0.0
            heappush(frontier, (0.0, index))
        while frontier:
            distance, current = heappop(frontier)
            if distance != costs[current]:
                continue
            candidate = distance + self._costs[current]
            for neighbor in self._neighbors[current]:
                if (
                    neighbor not in blocked_indices
                    and self._costs[neighbor] != inf
                    and candidate < costs[neighbor]
                ):
                    costs[neighbor] = candidate
                    following[neighbor] = current
                    heappush(frontier, (candidate, neighbor))
        self.field_build_count += 1
        return costs, following


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
    costs: FloatArray, blocked: BoolArray, ax: float, ay: float, bx: float, by: float
) -> bool:
    """Exact DDA lookahead without allocating a cell list on every movement tick."""
    height, width = costs.shape
    if not (0 <= ax < width and 0 <= ay < height and 0 <= bx < width and 0 <= by < height):
        return False
    x, y, ex, ey = floor(ax), floor(ay), floor(bx), floor(by)
    if blocked[y, x] or costs[y, x] > 1.0:
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
        if blocked[y, x] or costs[y, x] > 1.0:
            return False
    return True
