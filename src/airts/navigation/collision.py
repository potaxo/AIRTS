"""Deterministic swept-disc movement, compiled as one serial numeric loop."""

from math import ceil, floor, hypot, sqrt

import numpy as np

from airts._numeric import BoolArray, FloatArray, IntArray, compile_numeric
from airts.geometry import Point
from airts.world.map_model import EntityKind

_RADII = {
    EntityKind.SCOUT: 0.30,
    EntityKind.LIGHT_TANK: 0.38,
    EntityKind.HEAVY_TANK: 0.45,
    EntityKind.BUILDER: 0.32,
}


def collision_radius(kind: EntityKind) -> float:
    return _RADII.get(kind, 0.5)


@compile_numeric
def _solve_step(
    vx: float, vy: float, maximum: float, planes: FloatArray, count: int
) -> tuple[float, float, bool]:
    """Closest step in a speed circle and linear clearance half-planes."""
    rx, ry = vx, vy
    length = hypot(rx, ry)
    if length > maximum:
        rx, ry = rx * maximum / length, ry * maximum / length
    for index in range(count):
        nx, ny, clearance = planes[index]
        if nx * rx + ny * ry >= clearance - 1e-10:
            continue
        discriminant = maximum * maximum - clearance * clearance
        if discriminant < -1e-10:
            return 0.0, 0.0, False
        extent = sqrt(max(0.0, discriminant))
        lower, upper = -extent, extent
        bx, by = nx * clearance, ny * clearance
        tx, ty = -ny, nx
        for previous in range(index):
            ox, oy, other_clearance = planes[previous]
            slope = ox * tx + oy * ty
            intercept = other_clearance - ox * bx - oy * by
            if abs(slope) <= 1e-12:
                if intercept > 1e-9:
                    return 0.0, 0.0, False
            elif slope > 0:
                lower = max(lower, intercept / slope)
            else:
                upper = min(upper, intercept / slope)
            if lower > upper + 1e-9:
                return 0.0, 0.0, False
        along = min(upper, max(lower, tx * vx + ty * vy))
        rx, ry = bx + tx * along, by + ty * along
    return rx, ry, True


def constrained_step(
    preferred: Point, maximum: float, planes: list[tuple[float, float, float]]
) -> Point | None:
    coefficients = np.asarray(planes, dtype=np.float64).reshape((-1, 3))
    x, y, feasible = _solve_step(preferred.x, preferred.y, maximum, coefficients, len(planes))
    return Point(x, y) if feasible else None


@compile_numeric
def _neighbors(
    x: float,
    y: float,
    radius: float,
    positions: FloatArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    scratch: IntArray,
) -> int:
    count = 0
    for by in range(
        max(0, floor((y - radius) / bucket_size)),
        min(rows - 1, floor((y + radius) / bucket_size)) + 1,
    ):
        for bx in range(
            max(0, floor((x - radius) / bucket_size)),
            min(columns - 1, floor((x + radius) / bucket_size)) + 1,
        ):
            other = heads[by * columns + bx]
            while other != -1:
                dx, dy = x - positions[other, 0], y - positions[other, 1]
                if dx * dx + dy * dy <= radius * radius:
                    scratch[count] = other
                    count += 1
                other = links[other]
    # Numeric indices correspond to sorted entity IDs, independent of bucket order.
    scratch[:count].sort()
    return count


@compile_numeric
def _steering_obstacles(
    key: int,
    x: float,
    y: float,
    dx: float,
    dy: float,
    distance: float,
    step: float,
    positions: FloatArray,
    goals: FloatArray,
    radii: FloatArray,
    headings: FloatArray,
    active: BoolArray,
    stationary: BoolArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    direction_axis: int,
    has_opposing: bool,
    has_stationary: bool,
) -> tuple[bool, bool]:
    seek_opposing = has_opposing and hypot(goals[key, 0] - x, goals[key, 1] - y) > 3.0
    seek_idle = has_stationary and distance > step
    if not seek_opposing and not seek_idle:
        return False, False
    radius = 4.0 if seek_opposing else 2.0
    opposing = idle = False
    gx, gy = headings[key]
    idle_min_x, idle_max_x = floor((x - 2.0) / bucket_size), floor((x + 2.0) / bucket_size)
    idle_min_y, idle_max_y = floor((y - 2.0) / bucket_size), floor((y + 2.0) / bucket_size)
    for by in range(
        max(0, floor((y - radius) / bucket_size)),
        min(rows - 1, floor((y + radius) / bucket_size)) + 1,
    ):
        for bx in range(
            max(0, floor((x - radius) / bucket_size)),
            min(columns - 1, floor((x + radius) / bucket_size)) + 1,
        ):
            if (opposing or not seek_opposing) and not (
                idle_min_x <= bx <= idle_max_x and idle_min_y <= by <= idle_max_y
            ):
                continue
            other = heads[by * columns + bx]
            while other != -1:
                ox, oy = positions[other, 0] - x, positions[other, 1] - y
                squared = ox * ox + oy * oy
                if seek_opposing and not opposing and active[other] and squared <= 16.0:
                    hx, hy = headings[other]
                    if (
                        (headings[other, direction_axis] >= 0)
                        != (headings[key, direction_axis] >= 0)
                        and hx * gx + hy * gy < -0.5
                        and hypot(
                            goals[other, 0] - positions[other, 0],
                            goals[other, 1] - positions[other, 1],
                        )
                        > 3.0
                        and ox * dx + oy * dy > 0
                    ):
                        opposing = True
                if seek_idle and not idle and stationary[other] and squared <= 4.0:
                    along, across = (ox * dx + oy * dy) / distance, (ox * dy - oy * dx) / distance
                    if 0 < along < 2.0 and abs(across) < radii[key] + radii[other]:
                        idle = True
                if (opposing or not seek_opposing) and (idle or not seek_idle):
                    return opposing, idle
                other = links[other]
    return opposing, idle


@compile_numeric
def advance_positions(
    positions: FloatArray,
    targets: FloatArray,
    goals: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    order: IntArray,
    active: BoolArray,
    stationary: BoolArray,
    obstacles: BoolArray,
    direction_axis: int,
) -> tuple[FloatArray, BoolArray, int]:
    """One sequential controller for all unit counts; no parallel reductions or reservations.

    Neighbor constraints use the original pair normal and already committed displacement.
    This keeps the entire relative trajectory outside the contact distance, not just its end.
    """
    result = positions.copy()
    pressure = np.zeros(len(positions), dtype=np.bool_)
    if len(positions) == 0:
        return result, pressure, 0
    height, width = obstacles.shape
    maximum_step, maximum_radius = float(speeds.max()), float(radii.max())
    bucket_size = maximum_step * 2 + maximum_radius * 2
    columns, rows = ceil(width / bucket_size), ceil(height / bucket_size)
    heads = np.full(columns * rows, -1, dtype=np.int64)
    links = np.full(len(positions), -1, dtype=np.int64)
    headings = np.zeros_like(positions)
    for key in range(len(positions)):
        x, y = positions[key]
        bx = min(columns - 1, max(0, floor(x / bucket_size)))
        by = min(rows - 1, max(0, floor(y / bucket_size)))
        bucket = by * columns + bx
        links[key], heads[bucket] = heads[bucket], key
        if active[key]:
            gx, gy = goals[key, 0] - x, goals[key, 1] - y
            length = max(hypot(gx, gy), 1e-9)
            headings[key, 0], headings[key, 1] = gx / length, gy / length
    scratch = np.empty(len(positions), dtype=np.int64)
    static_capacity = (ceil((maximum_step + maximum_radius) * 2) + 2) ** 2
    planes = np.empty((len(positions) + static_capacity + 4, 3), dtype=np.float64)
    checks = 0
    has_stationary = bool(stationary.any())
    has_opposing = False
    positive = negative = False
    for key in range(len(positions)):
        if not active[key]:
            continue
        if headings[key, direction_axis] >= 0:
            positive = True
        else:
            negative = True
    has_opposing = positive and negative
    for key in order:
        x, y = positions[key]
        dx, dy = targets[key, 0] - x, targets[key, 1] - y
        distance = hypot(dx, dy)
        step = min(speeds[key], distance)
        vx, vy = dx / distance * step, dy / distance * step
        planes[0] = (1.0, 0.0, min(0.0, radii[key] - x))
        planes[1] = (-1.0, 0.0, min(0.0, x + radii[key] - width))
        planes[2] = (0.0, 1.0, min(0.0, radii[key] - y))
        planes[3] = (0.0, -1.0, min(0.0, y + radii[key] - height))
        count = 4
        extent = step + radii[key]
        for oy in range(max(0, floor(y - extent)), min(height - 1, floor(y + extent)) + 1):
            for ox in range(max(0, floor(x - extent)), min(width - 1, floor(x + extent)) + 1):
                if not obstacles[oy, ox]:
                    continue
                px = x - max(ox, min(ox + 1, x))
                py = y - max(oy, min(oy + 1, y))
                length = hypot(px, py)
                if length > 1e-9:
                    planes[count] = (px / length, py / length, min(0.0, radii[key] - length))
                    count += 1
        neighbors = _neighbors(
            x,
            y,
            maximum_step * 2 + maximum_radius + radii[key],
            positions,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            scratch,
        )
        for index in range(neighbors):
            other = scratch[index]
            if other == key:
                continue
            px, py = x - positions[other, 0], y - positions[other, 1]
            length = hypot(px, py)
            if length <= 1e-9:
                continue
            checks += 1
            nx, ny = px / length, py / length
            clearance = min(0.0, radii[key] + radii[other] + 1e-7 - length)
            clearance += nx * (result[other, 0] - positions[other, 0]) + ny * (
                result[other, 1] - positions[other, 1]
            )
            planes[count] = (nx, ny, clearance)
            count += 1
        opposing, idle = _steering_obstacles(
            key,
            x,
            y,
            dx,
            dy,
            distance,
            step,
            positions,
            goals,
            radii,
            headings,
            active,
            stationary,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            direction_axis,
            has_opposing,
            has_stationary,
        )
        if opposing:
            vx, vy = (vx - vy) * 0.707106781, (vx + vy) * 0.707106781
        if idle:
            vx, vy = (vx - vy) * 0.707106781, (vy + vx) * 0.707106781
        mx, my, feasible = _solve_step(vx, vy, step, planes, count)
        if feasible and mx * vx + my * vy < step * step * 0.9:
            best = (mx * vx + my * vy) / step + hypot(mx, my) * 0.25
            for sx, sy in (
                ((vx - vy) * 0.707106781, (vx + vy) * 0.707106781),
                ((vx + vy) * 0.707106781, (vy - vx) * 0.707106781),
                (-vy, vx),
                (vy, -vx),
            ):
                ax, ay, possible = _solve_step(sx, sy, step, planes, count)
                if possible:
                    score = (ax * vx + ay * vy) / step + hypot(ax, ay) * 0.25
                    if score > best + 1e-9:
                        mx, my, best = ax, ay, score
        if not feasible or hypot(mx, my) < 1e-8:
            pressure[key] = True
        else:
            result[key, 0], result[key, 1] = x + mx, y + my
    return result, pressure, checks


def warm_collision_kernel() -> None:
    """Compile before a simulation becomes interactive, including empty worlds."""
    empty = np.empty((0, 2), dtype=np.float64)
    advance_positions(
        empty,
        empty,
        empty,
        np.empty(0),
        np.empty(0),
        np.empty(0, dtype=np.int64),
        np.empty(0, dtype=np.bool_),
        np.empty(0, dtype=np.bool_),
        np.zeros((1, 1), dtype=np.bool_),
        0,
    )
