"""Deterministic local swarm steering layered over authoritative navigation paths."""

from __future__ import annotations

from math import cos, hypot, radians, sin, sqrt

from airts.geometry import Point
from airts.world.map_model import EntityKind

NEIGHBOR_RADIUS = 2.25
PREFERRED_SEPARATION = 1.15
_PASSING_ANGLES = (22.5, -22.5, 45.0, -45.0, 67.5, -67.5, 90.0, -90.0)
_PASSING_ROTATIONS = tuple((cos(radians(angle)), sin(radians(angle))) for angle in _PASSING_ANGLES)
_UNIT_MASSES = {
    EntityKind.SCOUT: 1,
    EntityKind.LIGHT_TANK: 2,
    EntityKind.HEAVY_TANK: 3,
    EntityKind.BUILDER: 1,
}
_COLLISION_RADII = {
    EntityKind.SCOUT: 0.30,
    EntityKind.LIGHT_TANK: 0.38,
    EntityKind.HEAVY_TANK: 0.45,
    EntityKind.BUILDER: 0.32,
}


def unit_mass(kind: EntityKind) -> int:
    return _UNIT_MASSES.get(kind, 100)


def collision_radius(kind: EntityKind) -> float:
    return _COLLISION_RADII.get(kind, 0.5)


def steering_candidates(
    position: Point,
    waypoint: Point,
    maximum_step: float,
    neighbors: tuple[Point, ...],
    candidate_limit: int | None = None,
) -> tuple[Point, ...]:
    """Rank local velocities by path progress, separation, and deterministic turn bias."""

    dx = waypoint.x - position.x
    dy = waypoint.y - position.y
    distance = hypot(dx, dy)
    if distance == 0:
        return (waypoint,)
    step = min(maximum_step, distance)
    desired = (dx / distance, dy / distance)
    nearby = neighbors
    separation_x = 0.0
    separation_y = 0.0
    for neighbor in nearby:
        offset_x = position.x - neighbor.x
        offset_y = position.y - neighbor.y
        squared_distance = max(offset_x * offset_x + offset_y * offset_y, 0.01)
        separation_x += offset_x / squared_distance
        separation_y += offset_y / squared_distance

    directions: list[tuple[float, float, int]] = [(desired[0], desired[1], 0)]
    separation_length = hypot(separation_x, separation_y)
    if separation_length:
        blended_x = desired[0] + 1.35 * separation_x / separation_length
        blended_y = desired[1] + 1.35 * separation_y / separation_length
        blended_length = hypot(blended_x, blended_y)
        if blended_length:
            directions.append((blended_x / blended_length, blended_y / blended_length, 1))
    # Every unit prefers a left-hand pass. Opposing headings therefore choose opposite
    # world-space sides, while the explicit ordering keeps equal situations reproducible.
    for order, (cosine, sine) in enumerate(_PASSING_ROTATIONS, 2):
        directions.append(
            (
                desired[0] * cosine - desired[1] * sine,
                desired[0] * sine + desired[1] * cosine,
                order,
            )
        )
    if candidate_limit is not None:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        directions = directions[:candidate_limit]

    ranked: list[tuple[float, int, float, float, Point]] = []
    seen: set[tuple[int, int]] = set()
    for direction_x, direction_y, order in directions:
        candidate = Point(position.x + direction_x * step, position.y + direction_y * step)
        key = (round(candidate.x * 1_000_000), round(candidate.y * 1_000_000))
        if key in seen:
            continue
        seen.add(key)
        squared_clearance = NEIGHBOR_RADIUS * NEIGHBOR_RADIUS
        for item in nearby:
            offset_x = candidate.x - item.x
            offset_y = candidate.y - item.y
            squared_clearance = min(
                squared_clearance,
                offset_x * offset_x + offset_y * offset_y,
            )
        clearance = sqrt(squared_clearance)
        separation_penalty = max(0.0, PREFERRED_SEPARATION - clearance) * 8.0
        imminent_penalty = max(0.0, 0.72 - clearance) * 100.0
        route_cost = candidate.distance_to(waypoint)
        score = route_cost + separation_penalty + imminent_penalty + order * 0.002
        ranked.append((score, order, candidate.y, candidate.x, candidate))
    ranked.sort(key=lambda item: item[:-1])
    return tuple(item[-1] for item in ranked)
