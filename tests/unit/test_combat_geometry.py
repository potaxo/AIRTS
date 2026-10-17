"""Constant-velocity artillery aiming, including degenerate and map-edge geometry."""

from math import hypot, isfinite

import pytest

from airts.contracts.spatial import Point
from airts.systems.combat_geometry import predicted_impact_point


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
