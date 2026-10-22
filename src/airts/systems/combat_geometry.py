"""Shared combat-position queries used by combat execution and opponent planning."""

from __future__ import annotations

from math import ceil, floor, hypot, nextafter, sqrt
from typing import TYPE_CHECKING

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import BoolArray, FloatArray, compile_numeric
from airts.world.entities import Entity

if TYPE_CHECKING:
    from airts.simulation import Simulation


def predicted_impact_point(
    origin: Point,
    target: Point,
    velocity: tuple[float, float],
    projectile_speed: float,
    width: int,
    height: int,
) -> Point:
    """Solve constant-velocity interception once, then clamp the ground aim to the map.

    If a faster target cannot be intercepted, lead by one nominal flight time instead.
    This is an estimate from observed motion, not a simulation of future orders/paths.
    """

    vx, vy = velocity
    if vx == 0 and vy == 0:
        return target
    dx, dy = target.x - origin.x, target.y - origin.y
    distance_squared = dx * dx + dy * dy
    if distance_squared == 0:
        return target
    dot = dx * vx + dy * vy
    speed_difference = projectile_speed * projectile_speed - vx * vx - vy * vy
    discriminant = dot * dot + speed_difference * distance_squared
    if speed_difference > 0 and dot >= 0:
        flight_time = (sqrt(discriminant) + dot) / speed_difference
    elif discriminant >= 0 and dot < 0:
        # Rationalized root avoids cancellation for approaching targets and near-equal speeds.
        flight_time = distance_squared / (sqrt(discriminant) - dot)
    else:
        flight_time = sqrt(distance_squared) / projectile_speed
    return Point(
        min(nextafter(float(width), 0.0), max(0.0, target.x + vx * flight_time)),
        min(nextafter(float(height), 0.0), max(0.0, target.y + vy * flight_time)),
    )


def firing_approach_points(
    simulation: Simulation,
    attacker: Entity,
    target: Entity,
) -> tuple[Point, ...]:
    """Return a passable firing ring instead of four congested adjacent cells."""

    center = target.selection_position
    attack_range = attacker.profile.attack_range
    outer_radius = max(0.5, attack_range - 0.25)
    inner_radius = max(0.0, outer_radius - 1.0)
    width, height = target.kind.profile.footprint
    coordinates = _firing_ring(
        simulation._routes.obstacle_mask(frozenset()),
        center.x,
        center.y,
        inner_radius,
        outer_radius,
        int(target.position.x),
        int(target.position.y),
        width,
        height,
    )
    points = tuple(Point(x, y) for x, y in coordinates.tolist())
    return points or simulation._interaction_points(target)


@compile_numeric
def _firing_ring(
    terrain: BoolArray,
    center_x: float,
    center_y: float,
    inner_radius: float,
    outer_radius: float,
    target_x: int,
    target_y: int,
    target_width: int,
    target_height: int,
) -> FloatArray:
    """Filter the complete firing ring numerically, retaining row-major candidate order."""
    left = max(0, floor(center_x - outer_radius))
    right = min(terrain.shape[1] - 1, ceil(center_x + outer_radius))
    top = max(0, floor(center_y - outer_radius))
    bottom = min(terrain.shape[0] - 1, ceil(center_y + outer_radius))
    points = np.empty(((right - left + 1) * (bottom - top + 1), 2), dtype=np.float64)
    count = 0
    for y in range(top, bottom + 1):
        for x in range(left, right + 1):
            if terrain[y, x] or (
                target_x <= x < target_x + target_width and target_y <= y < target_y + target_height
            ):
                continue
            if inner_radius <= hypot(x + 0.5 - center_x, y + 0.5 - center_y) <= outer_radius:
                points[count, 0], points[count, 1] = x + 0.5, y + 0.5
                count += 1
    return points[:count]


def warm_firing_geometry() -> None:
    """Compile before commands/ticks rather than stalling the first pursuing army."""
    _firing_ring(np.zeros((1, 1), dtype=np.bool_), 0.5, 0.5, 0.0, 0.5, 0, 0, 1, 1)
