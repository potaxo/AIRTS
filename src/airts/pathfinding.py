"""Deterministic four-direction A* pathfinding."""

from __future__ import annotations

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
