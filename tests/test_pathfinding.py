from __future__ import annotations

import pytest

from airts.geometry import Point
from airts.map_model import Terrain, load_example_map, load_map_data
from airts.pathfinding import Pathfinder, PathfindingError, find_path


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
