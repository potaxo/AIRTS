"""Deterministic continuous collision constraints for simultaneous unit motion.

Plans are proposals. Reserve physical swept discs, not only distinct destination points:
two safe endpoints can still intersect between ticks (including GPU interpolation).
"""

from __future__ import annotations

from math import sqrt

from airts.geometry import Point
from airts.navigation.spatial_index import SpatialIndex


def contact_fraction(dx: float, dy: float, vx: float, vy: float, radius: float) -> float:
    """First disc contact in [0, 1], or 1 when relative motion is safe."""
    speed_squared = vx * vx + vy * vy
    approach = dx * vx + dy * vy
    if speed_squared <= 1e-20 or approach >= 0:
        return 1.0
    clearance = dx * dx + dy * dy - radius * radius
    if clearance <= 1e-10:
        return 0.0
    discriminant = approach * approach - speed_squared * clearance
    if discriminant <= 0:
        return 1.0
    return min(1.0, max(0.0, (-approach - sqrt(discriminant)) / speed_squared))


def constrain_motion(
    positions: dict[str, Point],
    proposals: dict[str, Point],
    radii: dict[str, float],
) -> tuple[dict[str, Point], int]:
    """Shorten conflicting moves atomically; unchanged safe group translations stay exact.

    Pairwise clipping is repeated because slowing a leader can obstruct its follower. If
    local clipping does not converge, stop only the connected conflicting moves and propagate
    that constraint. From a separated initial state this cannot introduce an intersection.
    """
    if not proposals:
        return {}, 0
    velocities = {
        key: (target.x - positions[key].x, target.y - positions[key].y)
        for key, target in proposals.items()
    }
    active = tuple(key for key, velocity in velocities.items() if velocity != (0.0, 0.0))
    if not active:
        return proposals, 0
    maximum_step = max(sqrt(x * x + y * y) for x, y in velocities.values())
    index = SpatialIndex(positions, bucket_size=1.5)
    pairs = index.candidate_pairs_for(active, 2 * (max(radii.values()) + maximum_step))
    constraints = []
    for first, second in pairs:
        dx = positions[first].x - positions[second].x
        dy = positions[first].y - positions[second].y
        # Numerical tolerance is not a smaller collision profile.
        radius = radii[first] + radii[second] - 1e-8
        constraints.append((first, second, dx, dy, radius))
    checks = 0
    for _ in range(8):
        changed = False
        for first, second, dx, dy, radius in constraints:
            ax, ay = velocities.get(first, (0.0, 0.0))
            bx, by = velocities.get(second, (0.0, 0.0))
            vx, vy = ax - bx, ay - by
            if abs(vx) + abs(vy) <= 1e-12:
                continue
            checks += 1
            fraction = contact_fraction(dx, dy, vx, vy, radius)
            if fraction >= 1.0:
                continue
            fraction = max(0.0, fraction - 1e-7)
            velocities[first] = (ax * fraction, ay * fraction)
            velocities[second] = (bx * fraction, by * fraction)
            changed = True
        if not changed:
            break
    else:
        # Each pass permanently stops at least one further moving participant.
        while True:
            changed = False
            for first, second, dx, dy, radius in constraints:
                ax, ay = velocities.get(first, (0.0, 0.0))
                bx, by = velocities.get(second, (0.0, 0.0))
                if ax == bx and ay == by:
                    continue
                checks += 1
                if contact_fraction(dx, dy, ax - bx, ay - by, radius) < 1.0:
                    velocities[first] = velocities[second] = (0.0, 0.0)
                    changed = True
            if not changed:
                break
    result = {}
    for key, target in proposals.items():
        dx, dy = velocities[key]
        original = positions[key]
        if (dx, dy) == (target.x - original.x, target.y - original.y):
            result[key] = target
        else:
            result[key] = Point(original.x + dx, original.y + dy)
    return result, checks
