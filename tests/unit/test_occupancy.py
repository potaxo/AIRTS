"""Focused tests for authoritative occupancy state."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from airts.commands import MoveCommand
from airts.geometry import Point
from airts.simulation import Simulation
from airts.world.map_model import GameMap, load_map_data
from airts.world.occupancy import OccupancyError, OccupancyGrid


def test_occupancy_move_is_atomic_on_conflict() -> None:
    occupancy = OccupancyGrid(5, 5)
    occupancy.place("first", frozenset({(1, 1)}))
    occupancy.place("second", frozenset({(2, 1)}))

    with pytest.raises(OccupancyError, match="occupied by second"):
        occupancy.move("first", frozenset({(2, 1)}))

    assert occupancy.cells_for("first") == frozenset({(1, 1)})
    assert occupancy.occupants((2, 1)) == frozenset({"second"})


def test_simulation_updates_occupancy_as_a_unit_moves(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    assert simulation.occupancy.cells_for("unit_01") == frozenset({(1, 1)})

    simulation.execute(MoveCommand(("unit_01",), Point(5.5, 1.5)))
    simulation.advance(8)

    assert simulation.entities["unit_01"].position == Point(5.5, 1.5)
    assert simulation.occupancy.cells_for("unit_01") == frozenset({(5, 1)})


def test_group_move_allocates_distinct_destination_cells(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))

    result = simulation.execute(MoveCommand(("unit_01", "unit_02"), Point(7.5, 7.5)))
    simulation.advance(30)

    assert result.accepted
    assert simulation.occupancy.cells_for("unit_01") != simulation.occupancy.cells_for("unit_02")


def test_building_footprint_is_a_hard_pathfinding_obstacle() -> None:
    game_map = load_map_data(
        {
            "id": "building_path",
            "name": "Building Path",
            "width": 13,
            "height": 9,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "unit", "kind": "scout", "position": [1.5, 4.5]},
                {"id": "factory", "kind": "factory", "position": [4, 2]},
            ],
        }
    )
    simulation = Simulation(game_map)

    result = simulation.execute(MoveCommand(("unit",), Point(10.5, 4.5)))
    path_cells = {(int(point.x), int(point.y)) for point in simulation.entities["unit"].path}

    assert result.accepted
    assert path_cells.isdisjoint(simulation.occupancy.cells_for("factory"))
