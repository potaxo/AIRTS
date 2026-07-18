"""Deterministic four-direction routing and shared navigation fields."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from heapq import heappop, heappush

from airts.geometry import Point
from airts.world.map_model import Cell, GameMap


class PathfindingError(ValueError):
    """Raised when no valid path can be calculated."""


@dataclass(frozen=True, slots=True)
class PathResult:
    cells: tuple[Cell, ...]
    waypoints: tuple[Point, ...]
    cost: float


@dataclass(slots=True)
class _NavigationField:
    costs: list[float]
    next_indices: list[int]
    goal_indices: frozenset[int]
    width: int

    def index(self, cell: Cell) -> int:
        return cell[1] * self.width + cell[0]

    def reachable(self, cell: Cell) -> bool:
        return self.costs[self.index(cell)] != float("inf")

    def is_goal(self, cell: Cell) -> bool:
        return self.index(cell) in self.goal_indices

    def next_cell(self, cell: Cell) -> Cell:
        index = self.next_indices[self.index(cell)]
        return index % self.width, index // self.width

    def cost_at(self, cell: Cell) -> float:
        return self.costs[self.index(cell)]


class Pathfinder:
    """Cache deterministic reverse paths for many units sharing destinations."""

    def __init__(self, game_map: GameMap, maximum_fields: int = 32) -> None:
        self.game_map = game_map
        self.maximum_fields = maximum_fields
        self._fields: OrderedDict[tuple[frozenset[Cell], frozenset[Cell]], _NavigationField] = (
            OrderedDict()
        )
        self.field_build_count = 0
        self._cell_count = game_map.width * game_map.height
        self._passable_indices = tuple(
            terrain.passable for row in game_map.terrain for terrain in row
        )
        self._movement_costs = tuple(
            terrain.movement_cost for row in game_map.terrain for terrain in row
        )
        self._neighbor_indices = tuple(
            tuple(
                neighbor_y * game_map.width + neighbor_x
                for neighbor_x, neighbor_y in _neighbors(
                    (index % game_map.width, index // game_map.width)
                )
                if 0 <= neighbor_x < game_map.width and 0 <= neighbor_y < game_map.height
            )
            for index in range(self._cell_count)
        )

    @property
    def cached_field_count(self) -> int:
        return len(self._fields)

    def clear(self) -> None:
        self._fields.clear()

    def find_path(
        self,
        start: Point,
        goal: Point,
        blocked: frozenset[Cell] = frozenset(),
    ) -> PathResult:
        _, path = self.find_path_to_any(start, (goal,), blocked)
        return path

    def find_path_to_any(
        self,
        start: Point,
        goals: tuple[Point, ...],
        blocked: frozenset[Cell] = frozenset(),
    ) -> tuple[Point, PathResult]:
        if not self.game_map.is_passable(start):
            raise PathfindingError("START_NOT_PASSABLE")
        if not goals:
            raise PathfindingError("NO_PATH")
        if any(not self.game_map.is_passable(goal) for goal in goals):
            raise PathfindingError("TARGET_NOT_PASSABLE")
        start_cell = self.game_map.cell_for(start)
        goal_points: dict[Cell, Point] = {}
        for goal in sorted(goals, key=lambda point: (point.y, point.x)):
            goal_points.setdefault(self.game_map.cell_for(goal), goal)
        goal_cells = frozenset(goal_points)
        effective_blocked = blocked.difference({start_cell})
        available_goals = goal_cells.difference(effective_blocked)
        if not available_goals:
            raise PathfindingError("TARGET_OCCUPIED")
        if start_cell in available_goals:
            goal = goal_points[start_cell]
            waypoints: tuple[Point, ...] = () if start == goal else (goal,)
            return goal, PathResult((start_cell,), waypoints, 0.0)

        key = (available_goals, effective_blocked)
        field = self._fields.get(key)
        if field is None:
            field = self._build_field(available_goals, effective_blocked)
            self.field_build_count += 1
            self._fields[key] = field
            if len(self._fields) > self.maximum_fields:
                self._fields.popitem(last=False)
        else:
            self._fields.move_to_end(key)
        if not field.reachable(start_cell):
            raise PathfindingError("NO_PATH")
        cells = [start_cell]
        while not field.is_goal(cells[-1]):
            cells.append(field.next_cell(cells[-1]))
        goal = goal_points[cells[-1]]
        waypoints = tuple(
            goal if cell == cells[-1] else Point(cell[0] + 0.5, cell[1] + 0.5) for cell in cells[1:]
        )
        return goal, PathResult(tuple(cells), waypoints, field.cost_at(start_cell))

    def _build_field(
        self,
        goal_cells: frozenset[Cell],
        blocked: frozenset[Cell],
    ) -> _NavigationField:
        """Build one deterministic weighted reverse field for every terrain mix."""

        costs = [float("inf")] * self._cell_count
        next_indices = [-1] * self._cell_count
        goal_for = [-1] * self._cell_count
        goal_indices = frozenset(cell[1] * self.game_map.width + cell[0] for cell in goal_cells)
        blocked_indices = frozenset(cell[1] * self.game_map.width + cell[0] for cell in blocked)
        frontier: list[tuple[float, int, int]] = []
        for index in sorted(goal_indices):
            costs[index] = 0.0
            goal_for[index] = index
            heappush(frontier, (0.0, index, index))
        while frontier:
            current_cost, goal, current = heappop(frontier)
            if current_cost > costs[current] or goal_for[current] != goal:
                continue
            step_cost = self._movement_costs[current]
            for predecessor in self._neighbor_indices[current]:
                if predecessor in blocked_indices or not self._passable_indices[predecessor]:
                    continue
                cost = current_cost + step_cost
                previous_cost = costs[predecessor]
                if cost > previous_cost:
                    continue
                if cost == previous_cost:
                    previous_goal = goal_for[predecessor]
                    if goal > previous_goal or (
                        goal == previous_goal and current >= next_indices[predecessor]
                    ):
                        continue
                    goal_for[predecessor] = goal
                    next_indices[predecessor] = current
                    heappush(frontier, (cost, goal, predecessor))
                    continue
                costs[predecessor] = cost
                goal_for[predecessor] = goal
                next_indices[predecessor] = current
                heappush(frontier, (cost, goal, predecessor))
        return _NavigationField(
            costs,
            next_indices,
            goal_indices,
            self.game_map.width,
        )


@dataclass(slots=True)
class RouteAllowance:
    """Per-controller view of the shared per-tick automation route budget."""

    service: RoutingService
    limit: int
    used: int = 0

    def claim(self) -> bool:
        if self.used >= self.limit or not self.service._claim_automation_route():
            return False
        self.used += 1
        return True


class RoutingService:
    """Centralize cached static routing, dynamic routing, and per-tick work budgets."""

    def __init__(
        self,
        game_map: GameMap,
        *,
        automation_budget: int,
        combat_budget: int,
        maximum_fields: int = 32,
    ) -> None:
        if automation_budget <= 0 or combat_budget <= 0:
            raise ValueError("route budgets must be positive")
        self.game_map = game_map
        self.automation_budget = automation_budget
        self.combat_budget = combat_budget
        self._shared = Pathfinder(game_map, maximum_fields)
        self.automation_route_count = 0
        self.combat_route_count = 0

    @property
    def field_build_count(self) -> int:
        return self._shared.field_build_count

    @property
    def cached_field_count(self) -> int:
        return self._shared.cached_field_count

    def begin_tick(self) -> None:
        self.automation_route_count = 0
        self.combat_route_count = 0

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
        self,
        start: Point,
        goal: Point,
        blocked: frozenset[Cell] = frozenset(),
    ) -> PathResult:
        return self._shared.find_path(start, goal, blocked)

    def shared_path_to_any(
        self,
        start: Point,
        goals: tuple[Point, ...],
        blocked: frozenset[Cell] = frozenset(),
    ) -> tuple[Point, PathResult]:
        return self._shared.find_path_to_any(start, goals, blocked)

    def dynamic_path(
        self,
        start: Point,
        goal: Point,
        blocked: frozenset[Cell] = frozenset(),
        *,
        cell_penalties: Mapping[Cell, float] | None = None,
    ) -> PathResult:
        return find_path(
            self.game_map,
            start,
            goal,
            blocked,
            cell_penalties=cell_penalties,
        )

    def local_path(
        self,
        start: Point,
        goal: Point,
        blocked: frozenset[Cell] = frozenset(),
    ) -> PathResult:
        """Use a cheap deterministic clear L-corridor before falling back to A*."""

        return find_local_path(self.game_map, start, goal, blocked)

    def clear(self) -> None:
        self._shared.clear()

    def _claim_automation_route(self) -> bool:
        if self.automation_route_count >= self.automation_budget:
            return False
        self.automation_route_count += 1
        return True


def find_path(
    game_map: GameMap,
    start: Point,
    goal: Point,
    blocked: frozenset[Cell] = frozenset(),
    *,
    cell_penalties: Mapping[Cell, float] | None = None,
) -> PathResult:
    if not game_map.is_passable(start):
        raise PathfindingError("START_NOT_PASSABLE")
    if not game_map.is_passable(goal):
        raise PathfindingError("TARGET_NOT_PASSABLE")
    start_cell = game_map.cell_for(start)
    goal_cell = game_map.cell_for(goal)
    effective_blocked = blocked.difference({start_cell})
    if goal_cell in effective_blocked:
        raise PathfindingError("TARGET_OCCUPIED")
    if start_cell == goal_cell:
        waypoints: tuple[Point, ...]
        waypoints = () if start == goal else (goal,)
        return PathResult((start_cell,), waypoints, 0.0)

    frontier: list[tuple[float, float, int, int, Cell]] = []
    heappush(
        frontier, (_heuristic(start_cell, goal_cell), 0.0, start_cell[1], start_cell[0], start_cell)
    )
    came_from: dict[Cell, Cell] = {}
    costs: dict[Cell, float] = {start_cell: 0.0}
    while frontier:
        _, current_cost, _, _, current = heappop(frontier)
        if current == goal_cell:
            cells = _reconstruct(came_from, start_cell, goal_cell)
            waypoints = tuple(
                goal if cell == goal_cell else Point(cell[0] + 0.5, cell[1] + 0.5)
                for cell in cells[1:]
            )
            return PathResult(cells, waypoints, current_cost)
        if current_cost > costs[current]:
            continue
        for neighbor in _neighbors(current):
            if neighbor in effective_blocked or not game_map.is_cell_passable(neighbor):
                continue
            penalty = 0.0 if cell_penalties is None else cell_penalties.get(neighbor, 0.0)
            if penalty < 0:
                raise ValueError("cell penalties cannot be negative")
            cost = current_cost + game_map.terrain_at_cell(neighbor).movement_cost + penalty
            if cost >= costs.get(neighbor, float("inf")):
                continue
            costs[neighbor] = cost
            came_from[neighbor] = current
            priority = cost + _heuristic(neighbor, goal_cell)
            heappush(frontier, (priority, cost, neighbor[1], neighbor[0], neighbor))
    raise PathfindingError("NO_PATH")


def find_local_path(
    game_map: GameMap,
    start: Point,
    goal: Point,
    blocked: frozenset[Cell] = frozenset(),
) -> PathResult:
    """Route a short branch without allocating a heap when an axis corridor is clear."""

    if not game_map.is_passable(start):
        raise PathfindingError("START_NOT_PASSABLE")
    if not game_map.is_passable(goal):
        raise PathfindingError("TARGET_NOT_PASSABLE")
    start_cell = game_map.cell_for(start)
    goal_cell = game_map.cell_for(goal)
    effective_blocked = blocked.difference({start_cell})
    if goal_cell in effective_blocked:
        raise PathfindingError("TARGET_OCCUPIED")
    if start_cell == goal_cell:
        waypoints: tuple[Point, ...] = () if start == goal else (goal,)
        return PathResult((start_cell,), waypoints, 0.0)

    candidates: list[tuple[float, tuple[Cell, ...]]] = []
    for horizontal_first in (True, False):
        cells = _axis_cells(start_cell, goal_cell, horizontal_first=horizontal_first)
        if any(
            cell in effective_blocked or not game_map.is_cell_passable(cell) for cell in cells[1:]
        ):
            continue
        cost = sum(game_map.terrain_at_cell(cell).movement_cost for cell in cells[1:])
        candidates.append((cost, cells))
    if not candidates:
        return find_path(game_map, start, goal, blocked)
    cost, cells = min(candidates, key=lambda item: (item[0], item[1]))
    waypoints = tuple(
        goal if cell == goal_cell else Point(cell[0] + 0.5, cell[1] + 0.5) for cell in cells[1:]
    )
    return PathResult(cells, waypoints, cost)


def _axis_cells(
    start: Cell,
    goal: Cell,
    *,
    horizontal_first: bool,
) -> tuple[Cell, ...]:
    x, y = start
    cells = [start]
    axes: tuple[tuple[int, bool], ...] = ((goal[0], True), (goal[1], False))
    if not horizontal_first:
        axes = tuple(reversed(axes))
    for target, horizontal in axes:
        current = x if horizontal else y
        step = 1 if target > current else -1
        for value in range(current + step, target + step, step):
            if horizontal:
                x = value
            else:
                y = value
            cells.append((x, y))
    return tuple(cells)


def _heuristic(current: Cell, goal: Cell) -> float:
    return (abs(current[0] - goal[0]) + abs(current[1] - goal[1])) * 0.75


def _neighbors(cell: Cell) -> tuple[Cell, ...]:
    x, y = cell
    return ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))


def _reconstruct(came_from: dict[Cell, Cell], start: Cell, goal: Cell) -> tuple[Cell, ...]:
    path = [goal]
    while path[-1] != start:
        path.append(came_from[path[-1]])
    path.reverse()
    return tuple(path)
