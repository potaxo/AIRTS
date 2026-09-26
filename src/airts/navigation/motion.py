"""Deterministic continuous collision constraints for simultaneous unit motion.

Plans are proposals. Reserve physical swept discs, not only distinct destination points:
two safe endpoints can still intersect between ticks (including GPU interpolation).
"""

from __future__ import annotations

from collections import deque
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
    if len(proposals) == len(positions):
        xs, ys = zip(*velocities.values(), strict=True)
        if (max(xs) - min(xs)) + (max(ys) - min(ys)) <= 1e-12:
            # A common translation has no relative motion. This is the same tolerance used
            # by the pair solver, and includes every body so a stationary anchor cannot hide.
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
            first_approach = dx * ax + dy * ay
            second_approach = -(dx * bx + dy * by)
            if first_approach * second_approach < 0:
                # Let a leader move away instead of stopping it together with its faster
                # follower. Only the participant closing the gap needs to slow down.
                if abs(ax * by - ay * bx) <= 1e-12:
                    # Same-heading queues have an exact solution: preserve the leader's
                    # velocity and shorten only the relative closing motion.
                    fraction = max(0.0, fraction - 1e-7)
                    if first_approach < 0:
                        velocities[first] = (bx + vx * fraction, by + vy * fraction)
                    else:
                        velocities[second] = (ax - vx * fraction, ay - vy * fraction)
                    changed = True
                    continue
                lower, upper = 0.0, 1.0
                for _ in range(16):
                    checks += 1
                    scale = (lower + upper) / 2
                    relative_x, relative_y = (
                        (ax * scale - bx, ay * scale - by)
                        if first_approach < 0
                        else (ax - bx * scale, ay - by * scale)
                    )
                    if contact_fraction(dx, dy, relative_x, relative_y, radius) >= 1.0:
                        lower = scale
                    else:
                        upper = scale
                if first_approach < 0:
                    velocities[first] = (ax * lower, ay * lower)
                else:
                    velocities[second] = (bx * lower, by * lower)
                changed = True
                continue
            fraction = max(0.0, fraction - 1e-7)
            velocities[first] = (ax * fraction, ay * fraction)
            velocities[second] = (bx * fraction, by * fraction)
            changed = True
        if not changed:
            break
    else:
        # Propagate stops only to incident pairs. Rescanning the whole army for each
        # stopped follower makes a long queue quadratic even with a spatial broadphase.
        incident: dict[str, list[int]] = {}
        for pair_index, (first, second, *_rest) in enumerate(constraints):
            incident.setdefault(first, []).append(pair_index)
            incident.setdefault(second, []).append(pair_index)
        pending = deque(range(len(constraints)))
        queued = set(pending)
        while pending:
            pair_index = pending.popleft()
            queued.remove(pair_index)
            first, second, dx, dy, radius = constraints[pair_index]
            ax, ay = velocities.get(first, (0.0, 0.0))
            bx, by = velocities.get(second, (0.0, 0.0))
            if ax == bx and ay == by:
                continue
            checks += 1
            if contact_fraction(dx, dy, ax - bx, ay - by, radius) >= 1.0:
                continue
            for key in (first, second):
                if velocities.get(key, (0.0, 0.0)) == (0.0, 0.0):
                    continue
                velocities[key] = (0.0, 0.0)
                for affected in incident[key]:
                    if affected not in queued:
                        pending.append(affected)
                        queued.add(affected)
    result = {}
    for key, target in proposals.items():
        dx, dy = velocities[key]
        original = positions[key]
        if (dx, dy) == (target.x - original.x, target.y - original.y):
            result[key] = target
        else:
            result[key] = Point(original.x + dx, original.y + dy)
    return result, checks
