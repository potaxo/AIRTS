"""Focused tests for authoritative occupancy state."""

from __future__ import annotations

import pytest

from airts.world.map import OccupancyError, OccupancyGrid


def test_occupancy_move_is_atomic_on_conflict() -> None:
    occupancy = OccupancyGrid(5, 5)
    occupancy.place("first", frozenset({(1, 1)}))
    occupancy.place("second", frozenset({(2, 1)}))

    with pytest.raises(OccupancyError, match="occupied by second"):
        occupancy.move("first", frozenset({(2, 1)}))

    assert occupancy.cells_for("first") == frozenset({(1, 1)})
    assert occupancy.occupants((2, 1)) == frozenset({"second"})


def test_local_query_clips_deduplicates_and_tracks_cell_changes() -> None:
    occupancy = OccupancyGrid(5, 5)
    occupancy.place("building", frozenset({(0, 0), (1, 0)}))
    occupancy.place("scout", frozenset({(1, 1)}))
    occupancy.place("far", frozenset({(2, 1)}))
    assert occupancy.occupants_in(-3, -2, 2, 2) == {"building", "scout"}
    occupancy.move("scout", frozenset({(4, 4)}))
    occupancy.remove("building")
    assert occupancy.occupants_in(0, 0, 2, 2) == set()
    assert occupancy.occupants_in(4, 4, 9, 9) == {"scout"}
