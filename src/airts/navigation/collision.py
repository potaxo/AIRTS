"""Deterministic swept-disc movement, compiled as one serial numeric loop."""

from math import ceil, cos, floor, hypot, sin, sqrt

import numpy as np

from airts.contracts.spatial import Point
from airts.world._numeric import BoolArray, FloatArray, IntArray, compile_numeric
from airts.world.map import EntityKind

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
            length = hypot(px, py)
            if length > 1e-9:
                planes[count] = (px / length, py / length, min(0.0, radius - length))
                count += 1
    return count


@compile_numeric
def _pair_normal(first: int, second: int, positions: FloatArray) -> tuple[float, float, float]:
    px = positions[first, 0] - positions[second, 0]
    py = positions[first, 1] - positions[second, 1]
    length = hypot(px, py)
    if length > 1e-9:
        return px / length, py / length, length
    # Stable ID indices supply an antisymmetric normal even at identical coordinates.
    px = sqrt(first + 1.0) * cos(first * 2.399963229728653) - sqrt(second + 1.0) * cos(
        second * 2.399963229728653
    )
    py = sqrt(first + 1.0) * sin(first * 2.399963229728653) - sqrt(second + 1.0) * sin(
        second * 2.399963229728653
    )
    length = hypot(px, py)
    return px / length, py / length, 0.0


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
    """Bounded contact prediction followed by continuous, serial certification.

    All discs can yield. Original-position buckets cover the full speed envelope, so
    corrections cannot invalidate the candidate set. Certification uses a common
    original normal for both displacements; its half-plane contains the entire
    relative trajectory. Infeasible prediction never licenses penetration.
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
    goal_heads = np.full(columns * rows, -1, dtype=np.int64)
    goal_links = np.full(len(positions), -1, dtype=np.int64)
    headings = np.zeros_like(positions)
    preferred = np.zeros_like(positions)
    driven = active.copy()
    for key in range(len(positions)):
        x, y = positions[key]
        bucket = min(rows - 1, max(0, floor(y / bucket_size))) * columns + min(
            columns - 1, max(0, floor(x / bucket_size))
        )
        links[key], heads[bucket] = heads[bucket], key
        goal_bucket = min(rows - 1, max(0, floor(goals[key, 1] / bucket_size))) * columns + min(
            columns - 1, max(0, floor(goals[key, 0] / bucket_size))
        )
        goal_links[key], goal_heads[goal_bucket] = goal_heads[goal_bucket], key
        gx, gy = goals[key, 0] - x, goals[key, 1] - y
        length = max(hypot(gx, gy), 1e-9)
        headings[key, 0], headings[key, 1] = gx / length, gy / length
    scratch = np.empty(len(positions), dtype=np.int64)
    static_capacity = (ceil((maximum_step + maximum_radius) * 2) + 2) ** 2
    planes = np.empty((len(positions) + static_capacity + 4, 3), dtype=np.float64)
    checks = 0
    yielded = np.zeros(len(positions), dtype=np.bool_)
    has_stationary = bool(stationary.any())
    positive = negative = False
    for key in range(len(positions)):
        if active[key]:
            if headings[key, direction_axis] >= 0:
                positive = True
            else:
                negative = True
    for key in order:
        x, y = positions[key]
        dx, dy = targets[key, 0] - x, targets[key, 1] - y
        distance = hypot(dx, dy)
        if distance <= 1e-9:
            continue
        if stationary[key]:
            # Recovery may request finite local yielding only when its destination
            # has capacity. An occupied/conflicting anchor waits; it cannot evict
            # that occupant indefinitely. This is geometry, not a control claim.
            neighbors = _neighbors(
                goals[key, 0],
                goals[key, 1],
                radii[key] + maximum_radius,
                goals,
                goal_heads,
                goal_links,
                columns,
                rows,
                bucket_size,
                scratch,
            )
            clear_goal = True
            for index in range(neighbors):
                other = scratch[index]
                if other == key:
                    continue
                checks += 1
                gx = goals[key, 0] - goals[other, 0]
                gy = goals[key, 1] - goals[other, 1]
                if gx * gx + gy * gy < (radii[key] + radii[other]) ** 2 - 1e-9:
                    clear_goal = False
            driven[key] = clear_goal
        step = min(speeds[key], distance)
        if stationary[key]:
            step = min(step, speeds[key] * 0.5)
        vx, vy = dx / distance * step, dy / distance * step
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
            positive and negative,
            has_stationary,
        )
        if active[key] and (opposing or idle):
            vx, vy = (vx - vy) * 0.707106781, (vx + vy) * 0.707106781
        count = _static_planes(x, y, radii[key], speeds[key], obstacles, planes)
        mx, my, feasible = _solve_step(vx, vy, speeds[key], planes, count)
        if feasible:
            preferred[key, 0], preferred[key, 1] = mx, my

    has_motion = bool(active.any()) or bool(np.any(preferred != 0.0))
    # Predict only local contact corrections. Two sweeps transmit pressure along
    # short queues without recursive pushing; longer chains advance over ticks.
    for _iteration in range(2):
        changed = False
        for key in order[::-1]:
            x, y = positions[key]
            search_radius = radii[key] + maximum_radius
            if stationary[key] and has_motion:
                search_radius += speeds[key] + maximum_step
            neighbors = _neighbors(
                x,
                y,
                search_radius,
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
                if other == key or (stationary[key] == stationary[other] and other <= key):
                    continue
                if active[key] and stationary[other]:
                    continue  # The pathless endpoint owns this prediction edge.
                checks += 1
                nx, ny, length = _pair_normal(key, other, positions)
                clearance = radii[key] + radii[other] - length
                if not (driven[key] or driven[other]) and clearance <= 1e-9:
                    continue
                if clearance < -speeds[key] - speeds[other]:
                    continue
                if clearance < 0.0 and active[key] and active[other]:
                    continue  # Certified directly; no passive intent to predict.
                # Non-overlaps have a feasible zero displacement. Recover existing
                # overlap gradually, never require an impossible full correction.
                required = min(clearance + 1e-8, (speeds[key] + speeds[other]) * 0.25)
                relative = nx * (preferred[key, 0] - preferred[other, 0]) + ny * (
                    preferred[key, 1] - preferred[other, 1]
                )
                deficit = required - relative
                if deficit <= 1e-9:
                    continue
                # Cancel recovery only under actual approaching contact pressure,
                # not merely because a moving neighbor is inside the envelope.
                for member, incoming in ((key, other), (other, key)):
                    member_offset = hypot(
                        goals[member, 0] - positions[member, 0],
                        goals[member, 1] - positions[member, 1],
                    )
                    incoming_offset = hypot(
                        goals[incoming, 0] - positions[incoming, 0],
                        goals[incoming, 1] - positions[incoming, 1],
                    )
                    if (
                        stationary[member]
                        and not yielded[member]
                        and (
                            active[incoming]
                            or (
                                driven[incoming]
                                and (
                                    not driven[member]
                                    or incoming_offset > member_offset + radii[member]
                                )
                            )
                        )
                    ):
                        preferred[member, 0] = preferred[member, 1] = 0.0
                        if driven[incoming]:
                            # Bounded lateral yielding opens a passing lane instead
                            # of dragging a resting body along the whole route.
                            side = (positions[member, 1] - positions[incoming, 1]) * headings[
                                incoming, 0
                            ] - (positions[member, 0] - positions[incoming, 0]) * headings[
                                incoming, 1
                            ]
                            side_sign = (
                                1.0
                                if side > 1e-9 or (abs(side) <= 1e-9 and member % 2 == 0)
                                else -1.0
                            )
                            preferred[member, 0] = (
                                -headings[incoming, 1] * speeds[member] * 0.4 * side_sign
                            )
                            preferred[member, 1] = (
                                headings[incoming, 0] * speeds[member] * 0.4 * side_sign
                            )
                        count = _static_planes(
                            positions[member, 0],
                            positions[member, 1],
                            radii[member],
                            speeds[member],
                            obstacles,
                            planes,
                        )
                        mx, my, feasible = _solve_step(
                            preferred[member, 0],
                            preferred[member, 1],
                            speeds[member] * 0.5,
                            planes,
                            count,
                        )
                        preferred[member, 0] = mx if feasible else 0.0
                        preferred[member, 1] = my if feasible else 0.0
                        driven[member] = True
                        yielded[member] = True
                relative = nx * (preferred[key, 0] - preferred[other, 0]) + ny * (
                    preferred[key, 1] - preferred[other, 1]
                )
                deficit = required - relative
                if deficit <= 1e-9:
                    continue
                for member in (key, other):
                    if active[member] or yielded[member] or not driven[member]:
                        pressure[member] = True
                # Pathless bodies take most of incoming contact; two active intents
                # share it. Recovery intents are weak and wait rather than push.
                first_weight = 0.15 if driven[key] and not yielded[key] else 1.0
                second_weight = 0.15 if driven[other] and not yielded[other] else 1.0
                fraction = first_weight / (first_weight + second_weight)
                for member, sign, amount in ((key, 1.0, fraction), (other, -1.0, 1.0 - fraction)):
                    vx = preferred[member, 0] + nx * deficit * sign * amount
                    vy = preferred[member, 1] + ny * deficit * sign * amount
                    count = _static_planes(
                        positions[member, 0],
                        positions[member, 1],
                        radii[member],
                        speeds[member],
                        obstacles,
                        planes,
                    )
                    limit = speeds[member] if active[member] else speeds[member] * 0.5
                    mx, my, feasible = _solve_step(vx, vy, limit, planes, count)
                    if feasible:
                        preferred[member, 0], preferred[member, 1] = mx, my
                        changed = True
        if not changed:
            break

    # Certify every moving body, including passive ones. Unprocessed bodies stay
    # at zero until their turn. Later certificates include earlier displacements.
    # Overlaps may remain when space is absent but never become deeper.
    for key in order:
        x, y = positions[key]
        vx, vy = preferred[key]
        if hypot(vx, vy) <= 1e-12:
            if active[key]:
                pressure[key] = True
            continue  # Earlier certificates already kept clear of this resting body.
        limit = speeds[key] if active[key] else speeds[key] * 0.5
        count = _static_planes(x, y, radii[key], speeds[key], obstacles, planes)
        neighbors = _neighbors(
            x,
            y,
            radii[key] + maximum_radius + speeds[key] + maximum_step,
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
            checks += 1
            nx, ny, length = _pair_normal(key, other, positions)
            clearance = min(0.0, radii[key] + radii[other] - length)
            clearance += nx * (result[other, 0] - positions[other, 0]) + ny * (
                result[other, 1] - positions[other, 1]
            )
            planes[count] = (nx, ny, clearance)
            count += 1
        mx, my, feasible = _solve_step(vx, vy, limit, planes, count)
        if feasible and hypot(vx, vy) > 1e-9 and mx * vx + my * vy < 0.9 * (vx * vx + vy * vy):
            step = hypot(vx, vy)
            best = (mx * vx + my * vy) / step + hypot(mx, my) * 0.25
            for sx, sy in (
                ((vx - vy) * 0.707106781, (vx + vy) * 0.707106781),
                ((vx + vy) * 0.707106781, (vy - vx) * 0.707106781),
                (-vy, vx),
                (vy, -vx),
            ):
                ax, ay, possible = _solve_step(sx, sy, limit, planes, count)
                if possible:
                    score = (ax * vx + ay * vy) / step + hypot(ax, ay) * 0.25
                    if score > best + 1e-9:
                        mx, my, best = ax, ay, score
        if feasible:
            result[key, 0], result[key, 1] = x + mx, y + my
        if (active[key] or yielded[key] or not driven[key]) and (
            not feasible or hypot(mx, my) < 1e-8
        ):
            pressure[key] = True
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
