"""Focused tests for authoritative occupancy state."""

from __future__ import annotations

import pytest

from airts.world.occupancy import OccupancyError, OccupancyGrid


def test_occupancy_move_is_atomic_on_conflict() -> None:
    occupancy = OccupancyGrid(5, 5)
    occupancy.place("first", frozenset({(1, 1)}))
    occupancy.place("second", frozenset({(2, 1)}))

    with pytest.raises(OccupancyError, match="occupied by second"):
        occupancy.move("first", frozenset({(2, 1)}))

    assert occupancy.cells_for("first") == frozenset({(1, 1)})
    assert occupancy.occupants((2, 1)) == frozenset({"second"})
