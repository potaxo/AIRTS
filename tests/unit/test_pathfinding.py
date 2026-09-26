"""Focused tests for deterministic routing."""

from __future__ import annotations

from itertools import pairwise

import pytest

from airts.geometry import Point
from airts.navigation.pathfinding import (
    Pathfinder,
    PathfindingError,
    RoutingService,
    find_path,
    passable_segment_cells,
)
from airts.world.map_model import Terrain, load_example_map, load_map_data


def test_pathfinding_routes_deterministically_through_a_gap() -> None:
    game_map = load_map_data(
        {
            "id": "gap",
            "name": "Gap",
            "width": 7,
            "height": 6,
            "terrain": {"default": "grass", "rectangles": [[3, 0, 1, 5, "rock"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [1.5, 1.5]}],
        }
    )

    first = find_path(game_map, Point(1.5, 1.5), Point(5.5, 1.5))
    second = find_path(game_map, Point(1.5, 1.5), Point(5.5, 1.5))

    assert first == second
    assert (3, 5) in first.cells
    assert first.cells[0] == (1, 1)
    assert first.cells[-1] == (5, 1)


def test_pathfinding_uses_movement_costs_not_only_step_count() -> None:
    game_map = load_map_data(
        {
            "id": "costs",
            "name": "Costs",
            "width": 5,
            "height": 3,
            "terrain": {
                "default": "grass",
                "rectangles": [[0, 1, 5, 1, "forest"], [0, 0, 5, 1, "road"]],
            },
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 1.5]}],
        }
    )

    path = find_path(game_map, Point(0.5, 1.5), Point(4.5, 1.5))

    assert any(cell[1] == 0 for cell in path.cells)
    assert path.cost < 6.0


def test_pathfinding_reports_unreachable_targets() -> None:
    game_map = load_map_data(
        {
            "id": "closed",
            "name": "Closed",
            "width": 5,
            "height": 5,
            "terrain": {"default": "grass", "rectangles": [[2, 0, 1, 5, "water"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 2.5]}],
        }
    )

    with pytest.raises(PathfindingError, match="NO_PATH"):
        find_path(game_map, Point(0.5, 2.5), Point(4.5, 2.5))


def test_example_map_cross_river_path_uses_the_bridge() -> None:
    game_map = load_example_map()

    path = find_path(game_map, Point(8.5, 28.5), Point(40.5, 28.5))

    assert any(game_map.terrain_at_cell(cell) is Terrain.BRIDGE for cell in path.cells)


def test_shared_navigation_field_reuses_one_search_for_many_starts() -> None:
    game_map = load_example_map()
    pathfinder = Pathfinder(game_map)
    goal = Point(40.5, 28.5)

    first_start = Point(8.5, 28.5)
    second_start = Point(10.5, 26.5)
    first = pathfinder.find_path(first_start, goal)
    second = pathfinder.find_path(second_start, goal)

    assert first.cells[-1] == second.cells[-1] == (40, 28)
    assert first.cost == find_path(game_map, first_start, goal).cost
    assert second.cost == find_path(game_map, second_start, goal).cost
    assert pathfinder.field_build_count == 1
    assert pathfinder.cached_field_count == 1


def test_routing_service_enforces_local_and_global_tick_budgets() -> None:
    service = RoutingService(load_example_map(), automation_budget=2, combat_budget=1)
    first = service.automation_allowance(1)
    second = service.automation_allowance(2)

    assert first.claim()
    assert not first.claim()
    assert second.claim()
    assert not second.claim()
    assert service.automation_route_count == 2
    assert service.claim_combat_route()
    assert not service.claim_combat_route()

    service.begin_tick()

    assert service.automation_route_count == 0
    assert service.combat_route_count == 0
    assert service.automation_allowance(2).claim()


def test_routing_service_keeps_dynamic_unit_penalty_routes_out_of_static_cache() -> None:
    game_map = load_example_map()
    service = RoutingService(game_map, automation_budget=4, combat_budget=4)
    goal = Point(40.5, 28.5)

    service.shared_path(Point(8.5, 28.5), goal)
    service.shared_path(Point(10.5, 26.5), goal)
    service.dynamic_path(
        Point(8.5, 28.5),
        goal,
        cell_penalties={(20, 28): 1.5},
    )

    assert service.field_build_count == 1
    assert service.cached_field_count == 1


def test_five_hundred_military_obstacles_are_costly_but_not_an_impassable_wall() -> None:
    game_map = load_map_data(
        {
            "id": "soft_military_wall",
            "name": "Soft Military Wall",
            "width": 80,
            "height": 20,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "unit", "kind": "scout", "owner": "player", "position": [2.5, 10.5]}
            ],
        }
    )
    military_cells = frozenset((x, y) for x in range(20, 45) for y in range(20))
    start = Point(2.5, 10.5)
    goal = Point(70.5, 10.5)

    with pytest.raises(PathfindingError, match="NO_PATH"):
        find_path(game_map, start, goal, military_cells)

    path = find_path(
        game_map,
        start,
        goal,
        cell_penalties={cell: 1.5 for cell in military_cells},
    )

    assert len(military_cells) == 500
    assert path.cells[-1] == (70, 10)
    assert any(cell in military_cells for cell in path.cells)


@pytest.mark.parametrize("terrain", ("grass", "road", "forest"))
def test_uniform_multi_goal_fields_match_optimal_costs_and_stable_goal_ties(terrain: str) -> None:
    game_map = load_map_data(
        {
            "id": "multi_goal",
            "name": "Multi-goal routing",
            "width": 8,
            "height": 7,
            "terrain": {"default": terrain, "rectangles": [[3, 0, 1, 5, "water"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    blocked = frozenset({(5, 3), (5, 4)})
    goals = (Point(6.5, 1.5), Point(1.5, 5.5), Point(6.5, 5.5))
    pathfinder = Pathfinder(game_map)
    for y in range(game_map.height):
        for x in range(game_map.width):
            if not game_map.is_cell_passable((x, y)) or (x, y) in blocked:
                continue
            start = Point(x + 0.5, y + 0.5)
            optimal = min(
                (find_path(game_map, start, goal, blocked).cost, goal.y, goal.x) for goal in goals
            )
            goal, path = pathfinder.find_path_to_any(start, goals, blocked)
            assert (path.cost, goal.y, goal.x) == optimal
            assert path.cells[0] == (x, y)
            assert path.cells[-1] == game_map.cell_for(goal)
            assert blocked.isdisjoint(path.cells)
            assert all(
                abs(first[0] - second[0]) + abs(first[1] - second[1]) == 1
                for first, second in pairwise(path.cells)
            )
            assert pathfinder.find_path_to_any(start, tuple(reversed(goals)), blocked) == (
                goal,
                path,
            )
    assert pathfinder.field_build_count == 1


def test_segment_clearance_preserves_corner_obstacles_and_sampled_cell_order() -> None:
    game_map = load_map_data(
        {
            "id": "segment",
            "name": "Segment clearance",
            "width": 5,
            "height": 5,
            "terrain": {"default": "grass", "rectangles": [[2, 1, 1, 1, "water"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [0.5, 0.5]}],
        }
    )
    assert passable_segment_cells(game_map, Point(0.5, 2.5), Point(4.5, 2.5)) == tuple(
        (x, 2) for x in range(5)
    )
    assert passable_segment_cells(game_map, Point(1.5, 1.5), Point(3.5, 3.5)) is None
    assert (
        passable_segment_cells(game_map, Point(0.5, 2.5), Point(4.5, 2.5), frozenset({(3, 2)}))
        is None
    )
    assert passable_segment_cells(game_map, Point(0.1, 0.1), Point(0.9, 0.9)) == ((0, 0),)
    with pytest.raises(ValueError, match="point outside map"):
        passable_segment_cells(game_map, Point(0.5, 0.5), Point(5.5, 0.5))
