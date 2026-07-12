"""Deterministic four-direction A* pathfinding."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from heapq import heappop, heappush

from airts.geometry import Point
from airts.map_model import Cell, GameMap


class PathfindingError(ValueError):
    """Raised when no valid path can be calculated."""


@dataclass(frozen=True, slots=True)
class PathResult:
    cells: tuple[Cell, ...]
    waypoints: tuple[Point, ...]
    cost: float


@dataclass(slots=True)
class _NavigationField:
    costs: dict[Cell, float]
    next_cells: dict[Cell, Cell]
    goals: frozenset[Cell]


class Pathfinder:
    """Cache deterministic reverse paths for many units sharing destinations."""

    def __init__(self, game_map: GameMap, maximum_fields: int = 32) -> None:
        self.game_map = game_map
        self.maximum_fields = maximum_fields
        self._fields: OrderedDict[tuple[frozenset[Cell], frozenset[Cell]], _NavigationField] = (
            OrderedDict()
        )
        self.field_build_count = 0

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
        if start_cell not in field.costs:
            raise PathfindingError("NO_PATH")
        cells = [start_cell]
        while cells[-1] not in field.goals:
            cells.append(field.next_cells[cells[-1]])
        goal = goal_points[cells[-1]]
        waypoints = tuple(
            goal if cell == cells[-1] else Point(cell[0] + 0.5, cell[1] + 0.5) for cell in cells[1:]
        )
        return goal, PathResult(tuple(cells), waypoints, field.costs[start_cell])

    def _build_field(
        self,
        goal_cells: frozenset[Cell],
        blocked: frozenset[Cell],
    ) -> _NavigationField:
        frontier: list[tuple[float, int, int, int, int, Cell]] = []
        costs: dict[Cell, float] = {}
        next_cells: dict[Cell, Cell] = {}
        goal_for: dict[Cell, Cell] = {}
        for cell in sorted(goal_cells, key=lambda item: (item[1], item[0])):
            costs[cell] = 0.0
            goal_for[cell] = cell
            heappush(frontier, (0.0, cell[1], cell[0], cell[1], cell[0], cell))
        while frontier:
            current_cost, goal_y, goal_x, _, _, current = heappop(frontier)
            if current_cost > costs[current] or goal_for[current] != (goal_x, goal_y):
                continue
            step_cost = self.game_map.terrain_at_cell(current).movement_cost
            for predecessor in _neighbors(current):
                if predecessor in blocked or not self.game_map.is_cell_passable(predecessor):
                    continue
                cost = current_cost + step_cost
                previous_cost = costs.get(predecessor)
                if previous_cost is not None and cost > previous_cost:
                    continue
                if previous_cost is not None and cost == previous_cost:
                    previous_goal = goal_for[predecessor]
                    candidate_goal_key = (goal_y, goal_x)
                    previous_goal_key = (previous_goal[1], previous_goal[0])
                    if candidate_goal_key > previous_goal_key or (
                        candidate_goal_key == previous_goal_key
                        and (current[1], current[0])
                        >= (next_cells[predecessor][1], next_cells[predecessor][0])
                    ):
                        continue
                    goal_for[predecessor] = (goal_x, goal_y)
                    next_cells[predecessor] = current
                    heappush(
                        frontier,
                        (cost, goal_y, goal_x, predecessor[1], predecessor[0], predecessor),
                    )
                    continue
                costs[predecessor] = cost
                goal_for[predecessor] = (goal_x, goal_y)
                next_cells[predecessor] = current
                heappush(
                    frontier,
                    (cost, goal_y, goal_x, predecessor[1], predecessor[0], predecessor),
                )
        return _NavigationField(costs, next_cells, goal_cells)


def find_path(
    game_map: GameMap,
    start: Point,
    goal: Point,
    blocked: frozenset[Cell] = frozenset(),
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
            cost = current_cost + game_map.terrain_at_cell(neighbor).movement_cost
            if cost >= costs.get(neighbor, float("inf")):
                continue
            costs[neighbor] = cost
            came_from[neighbor] = current
            priority = cost + _heuristic(neighbor, goal_cell)
            heappush(frontier, (priority, cost, neighbor[1], neighbor[0], neighbor))
    raise PathfindingError("NO_PATH")


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
