"""Collision neighbors algorithms; numeric inputs contain no world mutation."""

from math import floor, hypot

from airts.navigation.collision._compile import compile_collision
from airts.world._numeric import BoolArray, FloatArray, IntArray


@compile_collision
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
    # Tiny local lists need no NumPy sorting workspace. Dense lists retain its
    # comparison sort; no neighbors are truncated and the final ID order is identical.
    if count > 16:
        scratch[:count].sort()
    else:
        for index in range(1, count):
            value = scratch[index]
            previous = index - 1
            while previous >= 0 and scratch[previous] > value:
                scratch[previous + 1] = scratch[previous]
                previous -= 1
            scratch[previous + 1] = value
    return count


@compile_collision
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
