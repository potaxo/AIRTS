"""Collision neighbors algorithms; numeric inputs contain no world mutation."""

from math import floor

from airts.navigation.collision._compile import compile_collision
from airts.world._numeric import FloatArray, IntArray

_FORWARD = 1
_BACKWARD = 2


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
def _opposing_traffic(
    key: int,
    x: float,
    y: float,
    dx: float,
    dy: float,
    positions: FloatArray,
    headings: FloatArray,
    directions: IntArray,
    bucket_directions: IntArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    opposing_direction: int,
) -> bool:
    radius = 4.0
    gx, gy = headings[key]
    for by in range(
        max(0, floor((y - radius) / bucket_size)),
        min(rows - 1, floor((y + radius) / bucket_size)) + 1,
    ):
        for bx in range(
            max(0, floor((x - radius) / bucket_size)),
            min(columns - 1, floor((x + radius) / bucket_size)) + 1,
        ):
            bucket = by * columns + bx
            if not bucket_directions[bucket] & opposing_direction:
                continue
            other = heads[bucket]
            while other != -1:
                if directions[other] == opposing_direction:
                    ox, oy = positions[other, 0] - x, positions[other, 1] - y
                    hx, hy = headings[other]
                    if (
                        ox * ox + oy * oy <= 16.0
                        and hx * gx + hy * gy < -0.5
                        and ox * dx + oy * dy > 0.0
                    ):
                        return True
                other = links[other]
    return False
