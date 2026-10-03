"""Focused tests for deterministic spatial indexing."""

from __future__ import annotations

from random import Random

import pytest

from airts.contracts.spatial import Point
from airts.navigation.spatial_index import SpatialIndex


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
    assert index.nearest_many(((Point(1, 1), 1.1), (Point(1, 1), 0.5), (Point(100, 100), 1))) == (
        "alpha",
        None,
        None,
    )


@pytest.mark.parametrize("bucket_size", (0.5, 2.5))
@pytest.mark.parametrize("radius", (0.0, 0.9, 3.0))
def test_broadphase_matches_brute_force_after_bucket_crossings(
    bucket_size: float, radius: float
) -> None:
    random = Random(73)
    positions = {
        str(index): Point(random.uniform(-4, 4), random.uniform(-4, 4)) for index in range(40)
    }
    index = SpatialIndex(positions, bucket_size)
    for entity_id in tuple(positions)[::3]:
        positions[entity_id] = Point(random.uniform(-4, 4), random.uniform(-4, 4))
        index.move(entity_id, positions[entity_id])
    active = tuple(positions)[::2]
    for point in positions.values():
        assert index.nearby(point, radius) == tuple(
            sorted(
                entity_id
                for entity_id, position in positions.items()
                if point.distance_to(position) <= radius
            )
        )
    queries = tuple((point, radius) for point in positions.values())
    expected_nearest = tuple(
        min(
            (key for key, position in positions.items() if point.distance_to(position) <= radius),
            key=lambda key: (point.distance_to(positions[key]), key),
            default=None,
        )
        for point, radius in queries
    )
    assert index.nearest_many(queries) == expected_nearest
    expected = tuple(
        (first, second)
        for first in sorted(positions)
        for second in sorted(positions)
        if first < second
        and (first in active or second in active)
        and positions[first].distance_to(positions[second]) <= radius
    )
    assert index.candidate_pairs_for((*active, active[0], "missing"), radius) == expected
