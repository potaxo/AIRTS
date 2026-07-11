"""Deterministic local swarm steering layered over authoritative A* paths."""

from __future__ import annotations

from math import cos, hypot, radians, sin

from airts.geometry import Point

NEIGHBOR_RADIUS = 2.25
PREFERRED_SEPARATION = 1.15


def steering_candidates(
    position: Point,
    waypoint: Point,
    maximum_step: float,
    neighbors: tuple[Point, ...],
) -> tuple[Point, ...]:
    """Rank local velocities by path progress, separation, and deterministic turn bias."""

    dx = waypoint.x - position.x
    dy = waypoint.y - position.y
    distance = hypot(dx, dy)
    if distance == 0:
        return (waypoint,)
    step = min(maximum_step, distance)
    desired = (dx / distance, dy / distance)
    nearby = tuple(
        neighbor for neighbor in neighbors if 0 < position.distance_to(neighbor) <= NEIGHBOR_RADIUS
    )
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
    for order, angle in enumerate((22.5, 45.0, 67.5, 90.0, -22.5, -45.0, -67.5, -90.0), 2):
        directions.append((*_rotate(desired, angle), order))

    ranked: list[tuple[float, int, float, float, Point]] = []
    seen: set[tuple[int, int]] = set()
    for direction_x, direction_y, order in directions:
        candidate = Point(position.x + direction_x * step, position.y + direction_y * step)
        key = (round(candidate.x * 1_000_000), round(candidate.y * 1_000_000))
        if key in seen:
            continue
        seen.add(key)
        clearance = min((candidate.distance_to(item) for item in nearby), default=NEIGHBOR_RADIUS)
        separation_penalty = max(0.0, PREFERRED_SEPARATION - clearance) * 8.0
        imminent_penalty = max(0.0, 0.72 - clearance) * 100.0
        route_cost = candidate.distance_to(waypoint)
        score = route_cost + separation_penalty + imminent_penalty + order * 0.002
        ranked.append((score, order, candidate.y, candidate.x, candidate))
    ranked.sort(key=lambda item: item[:-1])
    return tuple(item[-1] for item in ranked)


def _rotate(vector: tuple[float, float], degrees: float) -> tuple[float, float]:
    angle = radians(degrees)
    cosine = cos(angle)
    sine = sin(angle)
    return vector[0] * cosine - vector[1] * sine, vector[0] * sine + vector[1] * cosine
