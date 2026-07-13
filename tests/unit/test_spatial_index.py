"""Focused tests for deterministic spatial indexing."""

from __future__ import annotations

from airts.geometry import Point
from airts.spatial_index import SpatialIndex


def test_spatial_index_queries_and_updates_in_stable_id_order() -> None:
    index = SpatialIndex(
        {
            "charlie": Point(3.0, 1.0),
            "alpha": Point(1.0, 1.0),
            "bravo": Point(2.0, 1.0),
        }
    )

    assert index.nearby(Point(1.0, 1.0), 1.1) == ("alpha", "bravo")
    index.move("charlie", Point(1.5, 1.5))
    assert index.nearby(Point(1.0, 1.0), 1.1) == (
        "alpha",
        "bravo",
        "charlie",
    )


def test_sparse_armies_do_not_generate_global_pair_comparisons() -> None:
    positions = {
        f"unit_{index:04d}": Point((index % 20) * 3.0, (index // 20) * 3.0) for index in range(400)
    }
    index = SpatialIndex(positions)

    assert index.candidate_pairs(0.93) == ()


def test_candidate_pairs_for_skips_pairs_between_inactive_units() -> None:
    index = SpatialIndex(
        {
            "active": Point(1.0, 1.0),
            "waiting_1": Point(1.5, 1.0),
            "waiting_2": Point(2.0, 1.0),
        }
    )

    assert index.candidate_pairs_for(("active",), 0.75) == (("active", "waiting_1"),)


def test_nearest_uses_distance_then_stable_id_without_sorting_a_result_set() -> None:
    index = SpatialIndex(
        {
            "charlie": Point(3.0, 1.0),
            "bravo": Point(1.0, 2.0),
            "alpha": Point(2.0, 1.0),
        }
    )

    assert index.nearest(Point(1.0, 1.0), 1.1) == "alpha"
    assert index.nearest(Point(1.0, 1.0), 0.5) is None
