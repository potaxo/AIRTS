"""Shared combat-position queries used by combat execution and opponent planning."""

from __future__ import annotations

from math import ceil, floor, hypot, nextafter, sqrt
from typing import TYPE_CHECKING

from airts.contracts.spatial import Point
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
    target_cells = target.occupied_cells
    points = tuple(
        Point(x + 0.5, y + 0.5)
        for y in range(floor(center.y - outer_radius), ceil(center.y + outer_radius) + 1)
        for x in range(floor(center.x - outer_radius), ceil(center.x + outer_radius) + 1)
        if simulation.game_map.contains_cell((x, y))
        and simulation.game_map.is_cell_passable((x, y))
        and (x, y) not in target_cells
        and inner_radius <= hypot(x + 0.5 - center.x, y + 0.5 - center.y) <= outer_radius
    )
    return points or simulation._interaction_points(target)
