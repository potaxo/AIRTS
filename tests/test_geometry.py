from __future__ import annotations

import pytest

from airts.geometry import (
    Point,
    PolygonRegion,
    PolylineTarget,
    rectangle_region,
    simplify_freehand,
    target_to_dict,
)


def test_rectangle_normalizes_drag_direction_and_contains_boundary() -> None:
    region = rectangle_region(Point(5, 7), Point(2, 3))

    assert region.points == (
        Point(2, 3),
        Point(5, 3),
        Point(5, 7),
        Point(2, 7),
    )
    assert region.contains(Point(3, 4))
    assert region.contains(Point(2, 5))
    assert not region.contains(Point(1, 5))


def test_freehand_input_becomes_a_valid_polygon() -> None:
    region = simplify_freehand(
        (
            Point(1, 1),
            Point(2, 1),
            Point(4, 1),
            Point(4, 4),
            Point(1, 4),
            Point(1, 1),
        ),
        tolerance=0.1,
    )

    assert isinstance(region, PolygonRegion)
    assert region.contains(Point(2, 2))
    assert len(region.points) >= 3


def test_invalid_spatial_geometry_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="two points"):
        PolylineTarget((Point(1, 1),))
    with pytest.raises(ValueError, match="non-zero width"):
        rectangle_region(Point(1, 1), Point(1, 5))
    with pytest.raises(ValueError, match="non-zero area"):
        PolygonRegion((Point(1, 1), Point(2, 2), Point(3, 3)))
    with pytest.raises(ValueError, match="must not intersect"):
        PolygonRegion(
            (Point(0, 0), Point(4, 0), Point(1, 3), Point(4, 4), Point(0, 4), Point(3, 1))
        )


def test_spatial_target_is_serializable() -> None:
    target = PolylineTarget((Point(1, 2), Point(3, 4)))

    assert target_to_dict(target) == {
        "type": "polyline",
        "points": [[1, 2], [3, 4]],
    }
