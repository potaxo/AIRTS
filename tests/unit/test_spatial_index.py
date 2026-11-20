"""Focused tests for deterministic spatial indexing."""

from __future__ import annotations

from random import Random

import numpy as np
import pytest

from airts.contracts.spatial import Point
from airts.navigation.spatial_index import GroupedSpatialIndex, SpatialIndex


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


@pytest.mark.parametrize("spread", (1.0, 100_000.0))
def test_grouped_batches_preserve_hostility_radius_and_id_ties(spread: float) -> None:
    groups = {
        "a": {"a1": Point(-spread, 0), "a2": Point(spread, 0)},
        "b": {"b": Point(-spread, 0)},
        "ally": {"near": Point(0, 0)},
    }
    index = GroupedSpatialIndex(groups)
    queries = np.asarray([(0.0, 0.0, spread), (0.0, 0.0, spread / 2)] * 1_025)
    assert index.nearest_many_array(queries, ("b", "a")) == ("a1", None) * 1_025
    assert index.nearest_many_array(queries, ()) == (None,) * len(queries)
    # A different allowed set reuses coordinates without retaining prior results.
    assert index.nearest_many_array(queries, ("ally",)) == ("near",) * len(queries)


@pytest.mark.parametrize("spread", (1.0, 100_000.0))
def test_intersection_queries_match_exact_disk_union_in_dense_and_sparse_maps(
    spread: float,
) -> None:
    points = {"a": Point(-spread, 0), "b": Point(spread, 0)}
    index = SpatialIndex(points)
    for queries in (
        (),
        ((Point(0, 0), spread),),
        ((Point(0, 0), spread / 2),),
        ((Point(-spread, 0), 0),),
        ((Point(0, spread), 0), (Point(spread, 0), 0)),
    ):
        expected = any(
            point.distance_to(center) <= radius
            for center, radius in queries
            for point in points.values()
        )
        assert index.intersects(queries) == expected
    with pytest.raises(ValueError, match="radius"):
        index.intersects(((Point(0, 0), -1),))


def test_grouped_refresh_reuses_storage_and_handles_birth_death_team_and_sparse_changes() -> None:
    groups = {"a": {"one": Point(1, 1)}, "b": {"two": Point(4, 3)}}
    index = GroupedSpatialIndex(groups)
    positions, heads = index.positions, index.heads
    queries = np.asarray([(0, 0, 8), (100_000, 0, 1)], dtype=np.float64)
    groups["a"]["one"] = Point(2, 2)
    index.refresh(groups)
    assert np.shares_memory(index.positions, positions)
    assert index.heads is heads
    changes = [
        {"a": {"one": Point(2, 2), "born": Point(0, 0)}, "b": {}},
        {"a": {"born": Point(0, 0)}, "b": {"one": Point(100_000, 0)}},
        {"a": {}, "b": {"one": Point(0, 0)}},
        {"a": {}, "b": {}},
    ]
    for current in changes:
        index.refresh(current)
        for allowed in (("a",), ("b",), ("a", "b")):
            expected = []
            for x, y, radius in queries:
                point = Point(x, y)
                candidates = [
                    (position.distance_to(point), key)
                    for group in allowed
                    for key, position in current[group].items()
                    if position.distance_to(point) <= radius
                ]
                expected.append(min(candidates)[1] if candidates else None)
            assert index.nearest_many_array(queries, allowed) == tuple(expected)


def test_large_native_query_batches_match_partitioned_serial_queries() -> None:
    random = Random(71)
    positions = {
        f"unit_{i:04d}": Point(random.uniform(-50, 50), random.uniform(-50, 50))
        for i in range(1_300)
    }
    index = SpatialIndex(positions)
    queries = tuple(
        (Point(random.uniform(-60, 60), random.uniform(-60, 60)), random.uniform(0, 10))
        for _ in range(2_053)
    )
    expected = tuple(
        key
        for start in range(0, len(queries), 500)
        for key in index.nearest_many(queries[start : start + 500])
    )
    for _ in range(3):
        assert index.nearest_many(queries) == expected
        assert index.any_many(queries).tolist() == [key is not None for key in expected]
    index.move("unit_0000", queries[0][0])
    assert index.nearest_many(queries)[0] == "unit_0000"


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
    packed = np.asarray([(point.x, point.y, radius) for point, radius in queries])
    assert index.nearest_many_array(packed) == expected_nearest
    expected = tuple(
        (first, second)
        for first in sorted(positions)
        for second in sorted(positions)
        if first < second
        and (first in active or second in active)
        and positions[first].distance_to(positions[second]) <= radius
    )
    assert index.candidate_pairs_for((*active, active[0], "missing"), radius) == expected


@pytest.mark.parametrize("outlier", [False, True])
def test_queries_match_distance_oracle_for_compact_and_widely_separated_points(outlier) -> None:
    random = np.random.default_rng(87)
    positions = {
        f"p_{i:04d}": Point(float(x), float(y))
        for i, (x, y) in enumerate(random.uniform(-30, 30, (300, 2)))
    }
    if outlier:
        positions["remote"] = Point(1_000_000, -1_000_000)
    index = SpatialIndex(positions, bucket_size=3.5)
    queries = tuple(
        (Point(float(x), float(y)), float(r))
        for x, y, r in random.uniform([-35, -35, 0], [35, 35, 15], (100, 3))
    )
    expected = []
    for point, radius in queries:
        nearby = tuple(
            sorted(
                key
                for key, p in positions.items()
                if (p.x - point.x) ** 2 + (p.y - point.y) ** 2 <= radius**2
            )
        )
        assert index.nearby(point, radius) == nearby
        nearest = (
            min(
                nearby,
                key=lambda key: (
                    (positions[key].x - point.x) ** 2 + (positions[key].y - point.y) ** 2,
                    key,
                ),
            )
            if nearby
            else None
        )
        assert index.nearest(point, radius) == nearest
        expected.append(nearest)
    assert index.nearest_many(queries) == tuple(expected)
    positions["p_0000"] = Point(0, 0)
    index.move("p_0000", Point(0, 0))
    assert index.nearby(Point(0, 0), 0) == ("p_0000",)
    assert index.nearby(Point(0, 0), 2_000_000) == tuple(sorted(positions))
    assert index.nearest(Point(0, 0), 2_000_000) == "p_0000"
