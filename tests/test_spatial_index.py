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
