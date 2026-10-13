"""Collision geometry algorithms; numeric inputs contain no world mutation."""

from math import floor, hypot, sqrt

import numpy as np

from airts.contracts.spatial import Point
from airts.navigation.collision._compile import compile_collision
from airts.world._numeric import BoolArray, FloatArray, IntArray
from airts.world.units.catalog import EntityKind


def collision_radius(kind: EntityKind) -> float:
    return kind.profile.collision_radius


@compile_collision
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


@compile_collision
def _static_planes(
    x: float,
    y: float,
    radius: float,
    step: float,
    obstacles: BoolArray,
    planes: FloatArray,
) -> int:
    height, width = obstacles.shape
    planes[0] = (1.0, 0.0, min(0.0, radius - x))
    planes[1] = (-1.0, 0.0, min(0.0, x + radius - width))
    planes[2] = (0.0, 1.0, min(0.0, radius - y))
    planes[3] = (0.0, -1.0, min(0.0, y + radius - height))
    count = 4
    extent = step + radius
    for oy in range(max(0, floor(y - extent)), min(height - 1, floor(y + extent)) + 1):
        for ox in range(max(0, floor(x - extent)), min(width - 1, floor(x + extent)) + 1):
            if not obstacles[oy, ox]:
                continue
            px = x - max(ox, min(ox + 1, x))
            py = y - max(oy, min(oy + 1, y))
            if px and py:
                # A nearer occupied cell extends this corner into a rectangle.
                # Its supporting plane certifies that rectangle, including this
                # cell. Keeping the interior corner plane would incorrectly
                # constrain tangential travel along a continuous wall.
                adjacent_x = ox + (1 if px > 0 else -1)
                adjacent_y = oy + (1 if py > 0 else -1)
                if (0 <= adjacent_x < width and obstacles[oy, adjacent_x]) or (
                    0 <= adjacent_y < height and obstacles[adjacent_y, ox]
                ):
                    continue
            length = hypot(px, py)
            if length > 1e-9:
                planes[count] = (px / length, py / length, min(0.0, radius - length))
                count += 1
    return count


@compile_collision
def _pair_normal(
    first: int, second: int, positions: FloatArray, identity_directions: FloatArray
) -> tuple[float, float, float]:
    px = positions[first, 0] - positions[second, 0]
    py = positions[first, 1] - positions[second, 1]
    length = hypot(px, py)
    if length > 1e-9:
        return px / length, py / length, length
    # Stable ID indices supply an antisymmetric normal even at identical coordinates.
    px = identity_directions[first, 0] - identity_directions[second, 0]
    py = identity_directions[first, 1] - identity_directions[second, 1]
    length = hypot(px, py)
    return px / length, py / length, 0.0


@compile_collision
def _root(parents: IntArray, key: int) -> int:
    while parents[key] != key:
        parents[key] = parents[parents[key]]
        key = parents[key]
    return key
