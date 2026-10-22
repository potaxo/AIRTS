"""Constant-velocity artillery aiming, including degenerate and map-edge geometry."""

from math import ceil, floor, hypot, isfinite

import numpy as np
import pytest

from airts.contracts.spatial import Point
from airts.systems.combat_geometry import _firing_ring, predicted_impact_point


@pytest.mark.parametrize(
    "velocity", [(0.0, 0.0), (2.0, 0.0), (-2.0, 0.0), (0.0, 3.0), (-10.0, 0.0), (-20.0, 0.0)]
)
def test_predicted_ground_point_intercepts_constant_velocity(velocity: tuple[float, float]) -> None:
    origin, target = Point(20, 20), Point(30, 20)
    aim = predicted_impact_point(origin, target, velocity, 10, 80, 80)
    flight = origin.distance_to(aim) / 10
    assert aim.x == pytest.approx(target.x + velocity[0] * flight)
    assert aim.y == pytest.approx(target.y + velocity[1] * flight)


@pytest.mark.parametrize("velocity", [(20.0, 0.0), (0.0, 20.0), (10.0, 0.0)])
def test_unreachable_intercept_still_makes_a_finite_estimated_ground_shot(
    velocity: tuple[float, float],
) -> None:
    aim = predicted_impact_point(Point(20, 20), Point(30, 20), velocity, 10, 80, 80)
    assert isfinite(aim.x) and isfinite(aim.y)
    assert hypot(aim.x - 30, aim.y - 20) > 0


@pytest.mark.parametrize(
    "target,velocity",
    [
        (Point(9, 5), (3.0, 0.0)),
        (Point(1, 5), (-3.0, 0.0)),
        (Point(5, 9), (0.0, 3.0)),
        (Point(5, 1), (0.0, -3.0)),
    ],
)
def test_prediction_remains_inside_every_map_edge(
    target: Point, velocity: tuple[float, float]
) -> None:
    aim = predicted_impact_point(Point(5, 5), target, velocity, 10, 10, 10)
    assert 0 <= aim.x < 10 and 0 <= aim.y < 10


def test_zero_distance_does_not_divide_by_zero() -> None:
    assert predicted_impact_point(Point(5, 5), Point(5, 5), (3.0, 0.0), 10, 10, 10) == Point(5, 5)


@pytest.mark.parametrize("center", [(0.5, 0.5), (12.0, 9.0), (11.132, 9.293), (31.5, 23.5)])
@pytest.mark.parametrize("outer", [0.5, 3.75, 8.75, 11.75])
@pytest.mark.parametrize("footprint", [(1, 1), (2, 3)])
def test_numeric_firing_ring_matches_complete_geometric_oracle(
    center: tuple[float, float], outer: float, footprint: tuple[int, int]
) -> None:
    terrain = np.zeros((24, 32), dtype=np.bool_)
    terrain[3:17, 8:10] = True
    terrain[12, 2:28] = True
    cx, cy = center
    width, height = footprint
    tx, ty = max(0, int(cx) - width // 2), max(0, int(cy) - height // 2)
    inner = max(0.0, outer - 1.0)
    expected = [
        [x + 0.5, y + 0.5]
        for y in range(floor(cy - outer), ceil(cy + outer) + 1)
        for x in range(floor(cx - outer), ceil(cx + outer) + 1)
        if 0 <= x < 32
        and 0 <= y < 24
        and not terrain[y, x]
        and not (tx <= x < tx + width and ty <= y < ty + height)
        and inner <= hypot(x + 0.5 - cx, y + 0.5 - cy) <= outer
    ]
    actual = _firing_ring(terrain, cx, cy, inner, outer, tx, ty, width, height)
    assert actual.tolist() == expected
