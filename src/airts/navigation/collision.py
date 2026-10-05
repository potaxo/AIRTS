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


@compile_numeric
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


@compile_numeric
def _root(parents: IntArray, key: int) -> int:
    while parents[key] != key:
        parents[key] = parents[parents[key]]
        key = parents[key]
    return key


@compile_numeric
def _recovery_regions(
    positions: FloatArray,
    goals: FloatArray,
    targets: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    strong: BoolArray,
    stationary: BoolArray,
    recovering: BoolArray,
    tickets: IntArray,
    released: BoolArray,
    tolerances: FloatArray,
    status: IntArray,
    identity_directions: FloatArray,
    heads: IntArray,
    links: IntArray,
    goal_heads: IntArray,
    goal_links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    scratch: IntArray,
    work: IntArray,
) -> tuple[IntArray, IntArray, IntArray, IntArray, int]:
    """Identify compatible destination regions and their stable opening request.

    Goal adjacency, rather than a transient physical union, owns the return
    phase. A released ticket survives until every destination in its region
    settles; subsequent real traffic invalidates that phase explicitly.
    """
    size = len(positions)
    parents = np.arange(size, dtype=np.int64)
    incompatible = np.zeros(size, dtype=np.bool_)
    checks = 0
    maximum_radius, maximum_step = float(radii.max()), float(speeds.max())
    # Identical, no-larger destination discs have identical or contained graph
    # neighborhoods. One witnessed query suffices, including conflicting goals.
    witnesses = np.full(size, -1, dtype=np.int64)
    for key in range(size):
        if not stationary[key]:
            continue
        witness = witnesses[key]
        if witness != -1:
            first, second = _root(parents, key), _root(parents, witness)
            root, joined = min(first, second), max(first, second)
            parents[joined] = root
            incompatible[root] = incompatible[first] or incompatible[second]
            continue
        neighbors = _neighbors(
            goals[key, 0],
            goals[key, 1],
            radii[key] + maximum_radius + (speeds[key] + maximum_step) * 0.25 + 1e-6,
            goals,
            goal_heads,
            goal_links,
            columns,
            rows,
            bucket_size,
            scratch,
        )
        work[0] += neighbors - 1
        for index in range(neighbors):
            other = scratch[index]
            if other == key or not stationary[other]:
                continue
            checks += 1
            work[2] += 1
            dx, dy = goals[key, 0] - goals[other, 0], goals[key, 1] - goals[other, 1]
            squared = dx * dx + dy * dy
            if (
                squared
                > (radii[key] + radii[other] + (speeds[key] + speeds[other]) * 0.25 + 1e-6) ** 2
            ):
                continue
            first, second = _root(parents, key), _root(parents, other)
            root, joined = min(first, second), max(first, second)
            parents[joined] = root
            incompatible[root] = (
                incompatible[first]
                or incompatible[second]
                or (squared < (radii[key] + radii[other]) ** 2 - 1e-9)
            )
            if (
                other > key
                and squared == 0.0
                and radii[other] + speeds[other] * 0.25 <= radii[key] + speeds[key] * 0.25
            ):
                witnesses[other] = key
    member_heads = np.full(size, -1, dtype=np.int64)
    member_links = np.full(size, -1, dtype=np.int64)
    leaders = np.full(size, -1, dtype=np.int64)
    pending = np.zeros(size, dtype=np.bool_)
    direct = np.ones(size, dtype=np.bool_)
    for key in range(size):
        parents[key] = _root(parents, key)
        root = parents[key]
        member_links[key], member_heads[root] = member_heads[root], key
        pending[root] = pending[root] or recovering[key]
        if recovering[key]:
            direct[root] = (
                direct[root]
                and hypot(targets[key, 0] - goals[key, 0], targets[key, 1] - goals[key, 1]) <= 1e-8
            )
        if tickets[key] >= 0 and not strong[key]:
            previous = leaders[root]
            if (
                previous == -1
                or tickets[key] < tickets[previous]
                or (tickets[key] == tickets[previous] and key < previous)
            ):
                leaders[root] = key
    modes = np.zeros(size, dtype=np.int64)
    for root in range(size):
        if parents[root] != root or leaders[root] == -1:
            continue
        member = member_heads[root]
        if not pending[root]:
            while member != -1:
                if tickets[member] >= 0:
                    status[member] = 1
                member = member_links[member]
            continue
        if incompatible[root]:
            modes[root] = -1
            work[3] += 1
            continue
        interrupted = False
        while member != -1:
            neighbors = _neighbors(
                positions[member, 0],
                positions[member, 1],
                radii[member] + maximum_radius + speeds[member] + maximum_step,
                positions,
                heads,
                links,
                columns,
                rows,
                bucket_size,
                scratch,
            )
            work[0] += neighbors - 1
            for index in range(neighbors):
                other = scratch[index]
                if other == member:
                    continue
                checks += 1
                work[2] += 1
                _, _, distance = _pair_normal(member, other, positions, identity_directions)
                if distance < radii[member] + radii[other] - 1e-8 or (
                    strong[other]
                    and distance <= radii[member] + radii[other] + speeds[member] + speeds[other]
                ):
                    interrupted = True
            member = member_links[member]
        if interrupted:
            member = member_heads[root]
            while member != -1:
                if tickets[member] >= 0:
                    status[member] = 2 if recovering[member] else 1
                member = member_links[member]
            continue
        leader = leaders[root]
        modes[root] = 2 if released[leader] and direct[root] else 1
        if modes[root] == 1 and not recovering[leader]:
            # A routed follower needs its own opening request after the first
            # body arrives; straight anchor interpolation cannot cross a wall.
            leaders[root] = -1
            member = member_heads[root]
            while member != -1:
                if recovering[member] and (
                    leaders[root] == -1
                    or tickets[member] < tickets[leaders[root]]
                    or (tickets[member] == tickets[leaders[root]] and member < leaders[root])
                ):
                    leaders[root] = member
                member = member_links[member]
    return parents, modes, leaders, member_links, checks


@compile_numeric
def _normal_product(
    first: IntArray,
    second: IntArray,
    normals: FloatArray,
    vector: FloatArray,
    indices: IntArray,
    participants: IntArray,
    nodes: FloatArray,
    output: FloatArray,
    work: IntArray,
) -> None:
    """Apply J J-transpose through its incidence edges, without a matrix."""
    for key in participants:
        nodes[key, 0] = nodes[key, 1] = 0.0
        work[2] += 1
    for edge in indices:
        x, y = normals[edge, 0] * vector[edge], normals[edge, 1] * vector[edge]
        nodes[first[edge], 0] += x
        nodes[first[edge], 1] += y
        nodes[second[edge], 0] -= x
        nodes[second[edge], 1] -= y
        work[2] += 1
    for edge in indices:
        output[edge] = normals[edge, 0] * (
            nodes[first[edge], 0] - nodes[second[edge], 0]
        ) + normals[edge, 1] * (nodes[first[edge], 1] - nodes[second[edge], 1])
        work[2] += 1


@compile_numeric
def _project_anchor_gradient(
    first: IntArray,
    second: IntArray,
    normals: FloatArray,
    clearances: FloatArray,
    candidate: FloatArray,
    work: IntArray,
    binding: BoolArray,
) -> int:
    """Sparse active-set projection of a local region's joint return gradient.

    min .5*|v-g|^2 subject to Jv >= c has v=g+J-transpose*lambda.
    Nonnegative multipliers can release redundant edges. CG transmits a long
    contact chain through sparse products, rather than repeated field sweeps.
    Its dimension and operation budget are determined by this region's graph.
    The caller still certifies all physical trajectories after an early stop.
    """
    edges = len(first)
    if edges == 0:
        return 0
    size = len(candidate)
    degrees = np.zeros(size, dtype=np.int64)
    for edge in range(edges):
        degrees[first[edge]] += 1
        degrees[second[edge]] += 1
        work[2] += 1
    participants = np.flatnonzero(degrees > 0)
    degree_work = 0
    for key in participants:
        degree_work += degrees[key] * degrees[key]
        work[2] += 1
    # Equivalent local work includes visits to each modified endpoint's incident
    # edges. This bounds active-set restarts, not a fixed number of world rounds.
    budget = 8 * (len(participants) + edges + degree_work)
    beginning = work[2]
    original = candidate.copy()
    right = np.empty(edges, dtype=np.float64)
    active = np.zeros(edges, dtype=np.bool_)
    multipliers = np.zeros(edges, dtype=np.float64)
    residual = np.zeros(edges, dtype=np.float64)
    direction = np.zeros(edges, dtype=np.float64)
    product = np.zeros(edges, dtype=np.float64)
    nodes = np.zeros_like(candidate)
    checks = 0
    right_norm = 0.0
    all_edges = np.arange(edges, dtype=np.int64)
    for edge in range(edges):
        a, b = first[edge], second[edge]
        right[edge] = (
            clearances[edge]
            - normals[edge, 0] * (original[a, 0] - original[b, 0])
            - normals[edge, 1] * (original[a, 1] - original[b, 1])
        )
        active[edge] = right[edge] > 1e-11
        if active[edge]:
            multipliers[edge] = right[edge] * 0.5
        right_norm += right[edge] * right[edge]
        checks += 1
        work[2] += 1
    while work[2] - beginning < budget and bool(active.any()):
        indices = np.flatnonzero(active)
        _normal_product(
            first, second, normals, multipliers, indices, participants, nodes, product, work
        )
        squared = 0.0
        active_count = 0
        for edge in indices:
            residual[edge] = right[edge] - product[edge] if active[edge] else 0.0
            direction[edge] = residual[edge]
            squared += residual[edge] * residual[edge]
            active_count += int(active[edge])
            work[2] += 1
        dimension = min(active_count, len(participants) * 2)
        singular_release = False
        for _iteration in range(dimension):
            if squared <= 1e-22 * (1.0 + right_norm) or work[2] - beginning >= budget:
                break
            _normal_product(
                first, second, normals, direction, indices, participants, nodes, product, work
            )
            denominator = 0.0
            for edge in indices:
                denominator += direction[edge] * product[edge]
                work[2] += 1
            if denominator <= 1e-24:
                # Redundant contact rows may ask for incompatible equalities.
                # Their Gram null direction still decreases the dual energy:
                # follow it to the nonnegative boundary and release the slack
                # row, rather than mistaking a singular system for convergence.
                extent = 1e30
                for edge in indices:
                    if direction[edge] < -1e-14:
                        extent = min(extent, max(0.0, multipliers[edge]) / -direction[edge])
                    work[2] += 1
                if extent < 1e30:
                    for edge in indices:
                        multipliers[edge] = max(0.0, multipliers[edge] + extent * direction[edge])
                        if direction[edge] < -1e-14 and multipliers[edge] <= 1e-12:
                            multipliers[edge], active[edge], singular_release = 0.0, False, True
                        work[2] += 1
                break
            amount = squared / denominator
            next_squared = 0.0
            for edge in indices:
                multipliers[edge] += amount * direction[edge]
                residual[edge] -= amount * product[edge] if active[edge] else 0.0
                next_squared += residual[edge] * residual[edge]
                work[2] += 1
            ratio = next_squared / squared
            for edge in indices:
                direction[edge] = residual[edge] + ratio * direction[edge] if active[edge] else 0.0
                work[2] += 1
            squared = next_squared
        released = singular_release
        for edge in indices:
            if multipliers[edge] < -1e-12:
                multipliers[edge], active[edge], released = 0.0, False, True
            else:
                multipliers[edge] = max(0.0, multipliers[edge])
            work[2] += 1
        if released:
            continue
        _normal_product(
            first, second, normals, multipliers, all_edges, participants, nodes, product, work
        )
        added = False
        for edge in range(edges):
            if not active[edge] and right[edge] - product[edge] > 1e-10:
                active[edge], added = True, True
                multipliers[edge] = (right[edge] - product[edge]) * 0.5
            checks += 1
            work[2] += 1
        if not added:
            break
    _normal_product(
        first, second, normals, multipliers, all_edges, participants, nodes, product, work
    )
    for key in participants:
        candidate[key, 0] = original[key, 0] + nodes[key, 0]
        candidate[key, 1] = original[key, 1] + nodes[key, 1]
        work[2] += 1
    for edge in range(edges):
        binding[edge] = multipliers[edge] > 1e-12 or abs(right[edge] - product[edge]) <= 1e-9
        checks += 1
        work[2] += 1
    return checks


@compile_numeric
def _return_anchors(
    positions: FloatArray,
    goals: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    identity_directions: FloatArray,
    obstacles: BoolArray,
    regions: IntArray,
    modes: IntArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    scratch: IntArray,
    planes: FloatArray,
    seed: FloatArray,
    work: IntArray,
    recovering: BoolArray | None = None,
    stalled: BoolArray | None = None,
) -> int:
    """Blend compatible anchor gradients into the same certified joint seed.

    A straight chord between two touching legal configurations may penetrate.
    The least common radial compensation supplies the missing normal slope;
    one shared scale preserves every internal, external and static certificate.
    """
    if not bool(np.any(modes == 2)):
        return 0
    size = len(positions)
    member_heads = np.full(size, -1, dtype=np.int64)
    member_links = np.full(size, -1, dtype=np.int64)
    for key in range(size):
        root = regions[key]
        if modes[root] == 2:
            member_links[key], member_heads[root] = member_heads[root], key
    candidate = seed.copy()
    fractions = np.ones(size, dtype=np.float64)
    maximum_radius, maximum_step = float(radii.max()), float(speeds.max())
    checks = 0
    contacts = np.arange(size, dtype=np.int64)
    centers = np.zeros_like(positions)
    contact_sizes = np.zeros(size, dtype=np.int64)
    compensation = np.zeros(size, dtype=np.float64)
    for root in range(size):
        if member_heads[root] == -1:
            continue
        work[1] += 1
        rate = 1.0
        body_count = 0
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            body_count += 1
            distance = hypot(
                goals[member, 0] - positions[member, 0], goals[member, 1] - positions[member, 1]
            )
            rate = min(rate, speeds[member] * 0.5 / max(distance, 1e-9))
            member = member_links[member]
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            candidate[member, 0] = rate * (goals[member, 0] - positions[member, 0])
            candidate[member, 1] = rate * (goals[member, 1] - positions[member, 1])
            member = member_links[member]
        # Only bodies currently touching share normal compensation. The original
        # destination region still owns the phase and common trajectory scale;
        # an isolated tilted contact cannot scatter every distant returner.
        edge_first: list[int] = []
        edge_second: list[int] = []
        edge_x: list[float] = []
        edge_y: list[float] = []
        edge_clearance: list[float] = []
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            neighbors = _neighbors(
                positions[member, 0],
                positions[member, 1],
                radii[member] + maximum_radius + speeds[member] + maximum_step,
                positions,
                heads,
                links,
                columns,
                rows,
                bucket_size,
                scratch,
            )
            work[0] += neighbors - 1
            for index in range(neighbors):
                other = scratch[index]
                if other >= member or regions[other] != root:
                    continue
                checks += 1
                work[2] += 1
                dx, dy = (
                    positions[member, 0] - positions[other, 0],
                    positions[member, 1] - positions[other, 1],
                )
                if dx * dx + dy * dy <= (radii[member] + radii[other] + 1e-6) ** 2:
                    first, second = _root(contacts, member), _root(contacts, other)
                    contacts[max(first, second)] = min(first, second)
                nx, ny, distance = _pair_normal(member, other, positions, identity_directions)
                if (
                    distance
                    > radii[member] + radii[other] + (speeds[member] + speeds[other]) * 0.5 + 1e-6
                ):
                    # Every submitted member of this weak region has a half-
                    # speed cap. Triangle inequality certifies this farther pair
                    # for every point of the tick, so it cannot constrain the
                    # projection. Intermediate directions remain untrusted; the
                    # complete blend and final envelopes are still certified.
                    continue
                edge_first.append(member)
                edge_second.append(other)
                edge_x.append(nx)
                edge_y.append(ny)
                edge_clearance.append(min(0.0, radii[member] + radii[other] - distance))
            member = member_links[member]
        normals = np.empty((len(edge_first), 2), dtype=np.float64)
        for edge in range(len(edge_first)):
            work[2] += 1
            normals[edge, 0], normals[edge, 1] = edge_x[edge], edge_y[edge]
        first_array = np.asarray(edge_first, dtype=np.int64)
        second_array = np.asarray(edge_second, dtype=np.int64)
        binding = np.zeros(len(edge_first), dtype=np.bool_)
        checks += _project_anchor_gradient(
            first_array,
            second_array,
            normals,
            np.asarray(edge_clearance, dtype=np.float64),
            candidate,
            work,
            binding,
        )
        for edge in range(len(edge_first)):
            a, b = first_array[edge], second_array[edge]
            own_change = normals[edge, 0] * (candidate[a, 0] - seed[a, 0]) + normals[edge, 1] * (
                candidate[a, 1] - seed[a, 1]
            )
            other_change = normals[edge, 0] * (candidate[b, 0] - seed[b, 0]) + normals[edge, 1] * (
                candidate[b, 1] - seed[b, 1]
            )
            # A closing body may rely on its neighbor moving away even when
            # this face has slack and its dual multiplier is zero. Separate
            # scales discard that credit and can freeze a safe joint rotation
            # or translation. These ray dependencies share certification too.
            if binding[edge] or own_change * other_change > 1e-20:
                first, second = (
                    _root(contacts, first_array[edge]),
                    _root(contacts, second_array[edge]),
                )
                contacts[max(first, second)] = min(first, second)
            checks += 1
            work[2] += 1
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            contact = _root(contacts, member)
            contacts[member] = contact
            centers[contact, 0] += positions[member, 0]
            centers[contact, 1] += positions[member, 1]
            contact_sizes[contact] += 1
            member = member_links[member]
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            neighbors = _neighbors(
                positions[member, 0],
                positions[member, 1],
                radii[member] + maximum_radius + speeds[member] + maximum_step,
                positions,
                heads,
                links,
                columns,
                rows,
                bucket_size,
                scratch,
            )
            work[0] += neighbors - 1
            for index in range(neighbors):
                other = scratch[index]
                if other >= member or regions[other] != root or contacts[other] != contacts[member]:
                    continue
                checks += 1
                work[2] += 1
                nx, ny, distance = _pair_normal(member, other, positions, identity_directions)
                slope = nx * (candidate[member, 0] - candidate[other, 0]) + ny * (
                    candidate[member, 1] - candidate[other, 1]
                )
                clearance = min(0.0, radii[member] + radii[other] - distance)
                contact = contacts[member]
                compensation[contact] = max(
                    compensation[contact], max(0.0, (clearance - slope) / max(distance, 1e-12))
                )
            member = member_links[member]
        member = member_heads[root]
        while member != -1:
            work[2] += 1
            contact = contacts[member]
            alpha = compensation[contact]
            candidate[member, 0] += alpha * (
                positions[member, 0] - centers[contact, 0] / contact_sizes[contact]
            )
            candidate[member, 1] += alpha * (
                positions[member, 1] - centers[contact, 1] / contact_sizes[contact]
            )
            # The existing joint seed may already use part of this body's cap.
            # Bound the complete blend ray, not only the candidate direction.
            dx = candidate[member, 0] - seed[member, 0]
            dy = candidate[member, 1] - seed[member, 1]
            squared = dx * dx + dy * dy
            if squared > 1e-24:
                dot = seed[member, 0] * dx + seed[member, 1] * dy
                remaining = (speeds[member] * 0.5) ** 2 - (
                    seed[member, 0] ** 2 + seed[member, 1] ** 2
                )
                extent = (-dot + sqrt(max(0.0, dot * dot + squared * remaining))) / squared
                fractions[contact] = min(fractions[contact], max(0.0, extent))
            count = _static_planes(
                positions[member, 0],
                positions[member, 1],
                radii[member],
                speeds[member],
                obstacles,
                planes,
            )
            for index in range(count):
                nx, ny, clearance = planes[index]
                present = nx * seed[member, 0] + ny * seed[member, 1]
                change = nx * (candidate[member, 0] - seed[member, 0]) + ny * (
                    candidate[member, 1] - seed[member, 1]
                )
                if change < -1e-12:
                    fractions[contact] = min(
                        fractions[contact], max(0.0, (present - clearance) / -change)
                    )
            member = member_links[member]
    # All directions are now known. Allocate a closing external gap once to
    # each participating region; scaling independent regions cannot overspend it.
    for key in range(size):
        root = regions[key]
        if modes[root] != 2:
            continue
        work[2] += 1
        neighbors = _neighbors(
            positions[key, 0],
            positions[key, 1],
            radii[key] + maximum_radius + speeds[key] + maximum_step,
            positions,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            scratch,
        )
        work[0] += neighbors - 1
        for index in range(neighbors):
            other = scratch[index]
            if other == key or (contacts[other] == contacts[key] and other >= key):
                continue
            checks += 1
            work[2] += 1
            nx, ny, distance = _pair_normal(key, other, positions, identity_directions)
            present = nx * (seed[key, 0] - seed[other, 0]) + ny * (seed[key, 1] - seed[other, 1])
            gap = max(0.0, present - min(0.0, radii[key] + radii[other] - distance))
            change = nx * (candidate[key, 0] - seed[key, 0]) + ny * (
                candidate[key, 1] - seed[key, 1]
            )
            other_change = nx * (candidate[other, 0] - seed[other, 0]) + ny * (
                candidate[other, 1] - seed[other, 1]
            )
            contact = contacts[key]
            if contacts[other] == contact:
                closing = other_change - change
                if closing > 1e-12:
                    fractions[contact] = min(fractions[contact], gap / closing)
            elif change < -1e-12:
                closing = -change
                if modes[regions[other]] == 2 and other_change > 0:
                    gap *= closing / (closing + other_change)
                fractions[contact] = min(fractions[contact], gap / closing)
    decreases = np.zeros(size, dtype=np.float64)
    squares = np.zeros(size, dtype=np.float64)
    maximum_changes = np.zeros(size, dtype=np.float64)
    for key in range(size):
        if modes[regions[key]] != 2:
            continue
        work[2] += 1
        contact = contacts[key]
        root = regions[key]
        dx = fractions[contact] * (candidate[key, 0] - seed[key, 0])
        dy = fractions[contact] * (candidate[key, 1] - seed[key, 1])
        decreases[root] += (goals[key, 0] - positions[key, 0] - seed[key, 0]) * dx + (
            goals[key, 1] - positions[key, 1] - seed[key, 1]
        ) * dy
        squares[root] += dx * dx + dy * dy
    for contact in range(size):
        root = regions[contact]
        if contacts[contact] == contact and modes[root] == 2 and squares[root] > 1e-24:
            work[2] += 1
            fractions[contact] *= min(1.0, max(0.0, decreases[root] / squares[root]))
    for key in range(size):
        root = regions[key]
        if modes[root] == 2:
            work[2] += 1
            contact = contacts[key]
            maximum_changes[root] = max(
                maximum_changes[root],
                fractions[contact]
                * hypot(candidate[key, 0] - seed[key, 0], candidate[key, 1] - seed[key, 1]),
            )
    for root in range(size):
        exhausted = True
        pending = False
        member = member_heads[root]
        if recovering is not None and stalled is not None:
            while member != -1:
                work[2] += 1
                if recovering[member]:
                    pending = True
                    exhausted = exhausted and stalled[member]
                member = member_links[member]
        else:
            exhausted = False
        if modes[root] == 2 and (maximum_changes[root] <= 1e-8 or (pending and exhausted)):
            # A completed opening ticket cannot pin every still-pending return
            # indefinitely. The caller retains genuine pending ages and clears
            # fulfilled requests before selecting another local opening body.
            modes[root] = 3
            work[3] += 1
    for key in range(size):
        root = regions[key]
        if modes[root] == 2 or modes[root] == 3:
            work[2] += 1
            seed[key, 0] += fractions[contacts[key]] * (candidate[key, 0] - seed[key, 0])
            seed[key, 1] += fractions[contacts[key]] * (candidate[key, 1] - seed[key, 1])
    return checks


@compile_numeric
def _open_return_lane(
    positions: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    targets: FloatArray,
    obstacles: BoolArray,
    leader: int,
    head: int,
    member_links: IntArray,
    planes: FloatArray,
    seed: FloatArray,
    work: IntArray,
) -> bool:
    """Release a transverse lane without moving its returning leader away.

    With h perpendicular to the submitted route, s_i=(p_i-p_leader) dot h,
    v_i=beta*f(s_i)*h and monotone f give n_ij dot(v_i-v_j)>=0.
    The leader's f(0)=0 is between the two side velocities. Shared scaling
    preserves this certificate, including the one-sided wall alternatives.
    The caller still certifies external discs and every final trajectory.
    """
    dx, dy = targets[leader, 0] - positions[leader, 0], targets[leader, 1] - positions[leader, 1]
    length = hypot(dx, dy)
    if length <= 1e-9:
        return False
    hx, hy = -dy / length, dx / length
    beta = speeds[leader]
    member = head
    while member != -1:
        work[2] += 1
        if member != leader:
            beta = min(beta, speeds[member] * 0.5)
        member = member_links[member]
    for variant in range(3):
        scale = 1.0
        moving = False
        member = head
        while member != -1:
            work[2] += 1
            side = (positions[member, 0] - positions[leader, 0]) * hx + (
                positions[member, 1] - positions[leader, 1]
            ) * hy
            sign = 1.0 if side >= 0.0 else -1.0
            if member == leader or (variant == 1 and side < 0.0) or (variant == 2 and side >= 0.0):
                sign = 0.0
            vx, vy = beta * sign * hx, beta * sign * hy
            seed[member, 0], seed[member, 1] = vx, vy
            moving = moving or bool(sign)
            count = _static_planes(
                positions[member, 0],
                positions[member, 1],
                radii[member],
                speeds[member],
                obstacles,
                planes,
            )
            for index in range(count):
                nx, ny, clearance = planes[index]
                slope = nx * vx + ny * vy
                if slope < -1e-12:
                    scale = min(scale, max(0.0, clearance / slope))
            member = member_links[member]
        if moving and scale > 1e-9:
            member = head
            while member != -1:
                work[2] += 1
                seed[member, 0] *= scale
                seed[member, 1] *= scale
                member = member_links[member]
            return True
    return False


@compile_numeric
def _release_contacts(
    positions: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    active: BoolArray,
    identity_directions: FloatArray,
    obstacles: BoolArray,
    parents: IntArray,
    depths: FloatArray,
    demands: BoolArray,
    minimum_distances: FloatArray,
    coincident: BoolArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    scratch: IntArray,
    planes: FloatArray,
    work: IntArray,
    opening_leaders: BoolArray,
    opening_targets: FloatArray,
    strong: BoolArray,
) -> tuple[FloatArray, int]:
    """Construct a feasible *joint* seed for compressed contact components.

    Dilation has n_ij dot (v_i-v_j) >= 0 for every internal contact. Unlike
    certifying against unsubmitted zeros, it can release a surrounded body.
    Supporting planes choose the expansion center, and external discs limit
    the component's common scale. No pairwise non-deepening rule is relaxed.
    """
    size = len(positions)
    seed = np.zeros_like(positions)
    if not bool(np.any(depths > 1e-8)) and not bool(demands.any()):
        return seed, 0
    component_heads = np.full(size, -1, dtype=np.int64)
    component_links = np.full(size, -1, dtype=np.int64)
    component_tails = np.full(size, -1, dtype=np.int64)
    component_sizes = np.zeros(size, dtype=np.int64)
    for key in range(size):
        root = _root(parents, key)
        if component_heads[root] == -1:
            component_tails[root] = key
        component_links[key], component_heads[root] = component_heads[root], key
        component_sizes[root] += 1
    # Discover the fringe only for regions that actually need release. Ordinary
    # travel prediction retains its contact-sized neighborhood. A finite queue
    # admits each body once, including all members of a newly joined component;
    # no chain is truncated and no whole-world recovery sweeps are repeated.
    queue = np.empty(size, dtype=np.int64)
    queued = np.zeros(size, dtype=np.bool_)
    pending = 0
    for key in range(size):
        root = parents[key]
        if depths[root] > 1e-8 or demands[root]:
            queue[pending], queued[key] = key, True
            pending += 1
    cursor = 0
    maximum_radius = float(radii.max())
    maximum_step = float(speeds.max())
    checks = 0
    while cursor < pending:
        key = queue[cursor]
        cursor += 1
        work[2] += 1
        root = _root(parents, key)
        if component_sizes[root] == size:
            continue  # This component already owns every possible fringe body.
        neighbors = _neighbors(
            positions[key, 0],
            positions[key, 1],
            radii[key] + maximum_radius + (speeds[key] + maximum_step) * 0.25,
            positions,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            scratch,
        )
        work[0] += neighbors - 1
        for index in range(neighbors):
            other = scratch[index]
            root, other_root = _root(parents, key), _root(parents, other)
            if root == other_root:
                continue
            checks += 1
            _, _, length = _pair_normal(key, other, positions, identity_directions)
            clearance = radii[key] + radii[other] - length
            if clearance < -(speeds[key] + speeds[other]) * 0.25:
                continue
            member = component_heads[other_root]
            while member != -1:
                if not queued[member]:
                    queue[pending], queued[member] = member, True
                    pending += 1
                member = component_links[member]
            first, second = root, other_root
            root, joined = min(first, second), max(first, second)
            parents[joined] = root
            depths[root] = max(depths[first], depths[second], clearance)
            demands[root] = demands[first] or demands[second]
            minimum_distances[root] = min(
                minimum_distances[first],
                minimum_distances[second],
                length if length > 1e-9 else 1e30,
            )
            coincident[root] = coincident[first] or coincident[second] or length <= 1e-9
            component_links[component_tails[root]] = component_heads[joined]
            component_tails[root] = component_tails[joined]
            component_sizes[root] += component_sizes[joined]
    for key in range(size):
        parents[key] = _root(parents, key)
    # One workspace reused for the active component; never a pair matrix.
    static_capacity = (ceil((maximum_step + maximum_radius) * 2) + 2) ** 2 + 4
    center_planes = np.empty((size * static_capacity, 3), dtype=np.float64)
    for root in range(size):
        if parents[root] != root or (depths[root] <= 1e-8 and not demands[root]):
            continue
        work[1] += 1
        cx = cy = fx = fy = 0.0
        count = 0
        member = component_heads[root]
        while member != -1:
            cx += positions[member, 0]
            cy += positions[member, 1]
            fx += identity_directions[member, 0]
            fy += identity_directions[member, 1]
            count += 1
            member = component_links[member]
        cx, cy, fx, fy = cx / count, cy / count, fx / count, fy / count
        center_x = center_y = 0.0
        openings = 0
        strong_pressure = False
        member = component_heads[root]
        while member != -1:
            strong_pressure = strong_pressure or strong[member]
            if opening_leaders[member]:
                center_x += positions[member, 0] * 2 - opening_targets[member, 0]
                center_y += positions[member, 1] * 2 - opening_targets[member, 1]
                openings += 1
            member = component_links[member]
        if openings == 1 and not strong_pressure and depths[root] <= 1e-8:
            member = component_heads[root]
            leader = -1
            while member != -1:
                if opening_leaders[member]:
                    leader = member
                member = component_links[member]
            if _open_return_lane(
                positions,
                radii,
                speeds,
                opening_targets,
                obstacles,
                leader,
                component_heads[root],
                component_links,
                planes,
                seed,
                work,
            ):
                continue
        if openings and not strong_pressure and depths[root] <= 1e-8:
            # The opening body must advance into its vacant destination. An
            # expansion about the centroid instead spends its entire passive
            # cap sending that outside body away from the destination. Moving
            # the affine center preserves all relative normal certificates.
            center_x, center_y = center_x / openings - cx, center_y / openings - cy
        else:
            center_x = center_y = 0.0
        embedding = 0.0
        if coincident[root]:
            # A small ID embedding breaks exact coincidences. Its relative
            # projection cannot reverse any distinct-center contact normal.
            embedding = min(maximum_radius, minimum_distances[root] * 0.25) / (2.0 * sqrt(size))
        for axes in range(3):
            axis_embedding = embedding
            if axes and embedding:
                axis = axes - 1
                member = component_heads[root]
                while member != -1:
                    neighbors = _neighbors(
                        positions[member, 0],
                        positions[member, 1],
                        radii[member] + maximum_radius + speeds[member] + maximum_step,
                        positions,
                        heads,
                        links,
                        columns,
                        rows,
                        bucket_size,
                        scratch,
                    )
                    work[0] += neighbors - 1
                    for index in range(neighbors):
                        other = scratch[index]
                        if other >= member or parents[other] != root:
                            continue
                        checks += 1
                        nx, ny, _ = _pair_normal(member, other, positions, identity_directions)
                        normal = nx if axis == 0 else ny
                        delta = positions[member, axis] - positions[other, axis]
                        jitter = (
                            identity_directions[member, axis] - identity_directions[other, axis]
                        )
                        slope = normal * jitter
                        if slope < -1e-12:
                            axis_embedding = min(axis_embedding, normal * delta * 0.5 / -slope)
                    member = component_links[member]
            # Isotropic release first; a wall-bounded corridor can release along
            # just one axis. These are positive-semidefinite affine directions.
            rate = 1e30
            member = component_heads[root]
            while member != -1:
                work[2] += 1
                ox = (
                    positions[member, 0]
                    - cx
                    + axis_embedding * (identity_directions[member, 0] - fx)
                )
                oy = (
                    positions[member, 1]
                    - cy
                    + axis_embedding * (identity_directions[member, 1] - fy)
                )
                if axes == 1:
                    oy = 0.0
                elif axes == 2:
                    ox = 0.0
                seed[member, 0], seed[member, 1] = ox, oy
                limit = speeds[member] if active[member] else speeds[member] * 0.5
                rate = min(rate, limit / max(hypot(ox, oy), 1e-9))
                member = component_links[member]
            count = 0
            member = component_heads[root]
            while member != -1:
                local_count = _static_planes(
                    positions[member, 0],
                    positions[member, 1],
                    radii[member],
                    speeds[member],
                    obstacles,
                    planes,
                )
                for index in range(local_count):
                    nx, ny, clearance = planes[index]
                    # Translate the center by (tx,ty): v=rate*(offset-t).
                    center_planes[count] = (
                        -nx,
                        -ny,
                        clearance / rate - nx * seed[member, 0] - ny * seed[member, 1],
                    )
                    count += 1
                member = component_links[member]
            tx, ty, feasible = _solve_step(
                center_x,
                center_y,
                hypot(obstacles.shape[0], obstacles.shape[1]) * 2,
                center_planes,
                count,
            )
            if not feasible:
                continue
            scale = 1.0
            member = component_heads[root]
            while member != -1:
                vx, vy = rate * (seed[member, 0] - tx), rate * (seed[member, 1] - ty)
                seed[member, 0], seed[member, 1] = vx, vy
                limit = speeds[member] if active[member] else speeds[member] * 0.5
                scale = min(scale, limit / max(hypot(vx, vy), 1e-9))
                member = component_links[member]
            member = component_heads[root]
            while member != -1:
                seed[member, 0] *= scale
                seed[member, 1] *= scale
                member = component_links[member]
            break
        else:
            member = component_heads[root]
            while member != -1:
                seed[member, 0] = seed[member, 1] = 0.0
                member = component_links[member]
    # Certify the common seed against the full original displacement envelopes.
    # Independent components share a gap only if both move towards one another.
    scales = np.ones(size, dtype=np.float64)
    for key in range(size):
        if not (seed[key, 0] or seed[key, 1]):
            continue
        root = parents[key]
        if component_sizes[root] == size:
            continue  # No external disc constraint exists in this component.
        neighbors = _neighbors(
            positions[key, 0],
            positions[key, 1],
            radii[key] + maximum_radius + speeds[key] + maximum_step,
            positions,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            scratch,
        )
        work[0] += neighbors - 1
        for index in range(neighbors):
            other = scratch[index]
            if other == key or parents[other] == root:
                continue
            checks += 1
            nx, ny, length = _pair_normal(key, other, positions, identity_directions)
            approach = -(nx * seed[key, 0] + ny * seed[key, 1])
            if approach <= 1e-12:
                continue
            other_approach = nx * seed[other, 0] + ny * seed[other, 1]
            gap = max(0.0, length - radii[key] - radii[other])
            if other_approach > 0:
                gap *= approach / (approach + other_approach)
            scales[root] = min(scales[root], gap / approach)
    for key in range(size):
        seed[key, 0] *= scales[parents[key]]
        seed[key, 1] *= scales[parents[key]]
    for root in range(size):
        if parents[root] != root or (depths[root] <= 1e-8 and not demands[root]):
            continue
        moving = False
        member = component_heads[root]
        while member != -1:
            moving = moving or bool(seed[member, 0] or seed[member, 1])
            member = component_links[member]
        if not moving:
            work[3] += 1
    return seed, checks


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
    work: IntArray | None = None,
    stalled: BoolArray | None = None,
    recovering: BoolArray | None = None,
    recovery_ticks: IntArray | None = None,
    recovery_released: BoolArray | None = None,
    recovery_targets: FloatArray | None = None,
    recovery_tolerances: FloatArray | None = None,
    recovery_status: IntArray | None = None,
) -> tuple[FloatArray, BoolArray, int]:
    """Bounded contact prediction followed by continuous, serial certification.

    All discs can yield. Original-position buckets cover the full speed envelope, so
    corrections cannot invalidate the candidate set. Certification uses a common
    original normal for both displacements; its half-plane contains the entire
    relative trajectory. Infeasible prediction never licenses penetration.
    """
    result = positions.copy()
    pressure = np.zeros(len(positions), dtype=np.bool_)
    metrics = np.zeros(4, dtype=np.int64) if work is None else work
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
    identity_directions = np.zeros_like(positions)
    preferred = np.zeros_like(positions)
    driven = active.copy()
    for key in range(len(positions)):
        identity_directions[key, 0] = sqrt(key + 1.0) * cos(key * 2.399963229728653)
        identity_directions[key, 1] = sqrt(key + 1.0) * sin(key * 2.399963229728653)
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
    parents = np.arange(len(positions), dtype=np.int64)
    depths = np.zeros(len(positions), dtype=np.float64)
    minimum_distances = np.full(len(positions), 1e30, dtype=np.float64)
    coincident = np.zeros(len(positions), dtype=np.bool_)
    demands = np.zeros(len(positions), dtype=np.bool_) if stalled is None else stalled.copy()
    yielded = np.zeros(len(positions), dtype=np.bool_)
    regions = np.arange(len(positions), dtype=np.int64)
    recovery_modes = np.zeros(len(positions), dtype=np.int64)
    recovery_leaders = np.full(len(positions), -1, dtype=np.int64)
    geometric_leaders = np.zeros(len(positions), dtype=np.bool_)
    strong = active
    steering_stationary = stationary
    if (
        recovering is not None
        and recovery_ticks is not None
        and recovery_released is not None
        and recovery_targets is not None
        and recovery_tolerances is not None
        and recovery_status is not None
        and (bool(recovering.any()) or bool(np.any(recovery_ticks >= 0)))
    ):
        regions, recovery_modes, recovery_leaders, _, recovery_checks = _recovery_regions(
            positions,
            goals,
            recovery_targets,
            radii,
            speeds,
            active,
            stationary,
            recovering,
            recovery_ticks,
            recovery_released,
            recovery_tolerances,
            recovery_status,
            identity_directions,
            heads,
            links,
            goal_heads,
            goal_links,
            columns,
            rows,
            bucket_size,
            scratch,
            metrics,
        )
        checks += recovery_checks
        targets, active, stationary, speeds = (
            targets.copy(),
            active.copy(),
            stationary.copy(),
            speeds.copy(),
        )
        for key in range(len(positions)):
            mode = recovery_modes[regions[key]]
            if mode == 1:
                if key == recovery_leaders[regions[key]]:
                    targets[key] = recovery_targets[key]
                    speeds[key] *= 0.5
                    active[key], stationary[key] = True, False
                    geometric_leaders[key] = True
                    if (
                        hypot(
                            targets[key, 0] - positions[key, 0], targets[key, 1] - positions[key, 1]
                        )
                        <= 1e-9
                    ):
                        demands[key] = False
                else:
                    # A destination region schedules opening demand, not every
                    # harmless return. Preserve followers' original weak route;
                    # a body needed for release takes its certified seed below.
                    # Free, separated corridors need not wait for the leader.
                    demands[key] = False
            elif mode == 2:
                targets[key] = goals[key]
                demands[key] = False
            elif not active[key]:
                demands[key] = False
        driven = active.copy()
    has_stationary = bool(steering_stationary.any())
    positive = negative = False
    for key in range(len(positions)):
        if strong[key]:
            if headings[key, direction_axis] >= 0:
                positive = True
            else:
                negative = True
    for key in order:
        if recovery_modes[regions[key]] == 2:
            # Joint return owns this body's complete displacement. Its ordinary
            # route preference would only predict corrections discarded when
            # the certified joint seed replaces preferred below. Keep overlap
            # discovery and forecasts of real travel/opening intents intact.
            continue
        x, y = positions[key]
        dx, dy = targets[key, 0] - x, targets[key, 1] - y
        distance = hypot(dx, dy)
        if distance <= 1e-9:
            continue
        if stationary[key]:
            if recovery_modes[regions[key]] == -1:
                continue
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
            metrics[0] += neighbors - 1
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
            if not clear_goal:
                continue  # Preserve an incompatible anchor as a waiting intent.
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
            strong,
            steering_stationary,
            heads,
            links,
            columns,
            rows,
            bucket_size,
            direction_axis,
            positive and negative,
            has_stationary,
        )
        # A weak return leader is geometric participation, not a new travel
        # order. Keep its existing route preference through the released lane.
        if strong[key] and (opposing or idle):
            vx, vy = (vx - vy) * 0.707106781, (vx + vy) * 0.707106781
        count = _static_planes(x, y, radii[key], speeds[key], obstacles, planes)
        mx, my, feasible = _solve_step(vx, vy, speeds[key], planes, count)
        if feasible and recovery_modes[regions[key]] == 1 and not geometric_leaders[key]:
            # Followers may use an already free return corridor. A return that
            # needs another body's displacement still waits for joint release;
            # serial sidesteps must not close the lane behind its opening body.
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
            metrics[0] += neighbors - 1
            independent = True
            for index in range(neighbors):
                other = scratch[index]
                if other == key:
                    continue
                checks += 1
                metrics[2] += 1
                nx, ny, length = _pair_normal(key, other, positions, identity_directions)
                if nx * mx + ny * my < min(0.0, radii[key] + radii[other] - length) - 1e-10:
                    independent = False
            if not independent:
                continue
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
            metrics[0] += neighbors - 1
            for index in range(neighbors):
                other = scratch[index]
                if other == key or (stationary[key] == stationary[other] and other <= key):
                    continue
                if active[key] and stationary[other]:
                    continue  # The pathless endpoint owns this prediction edge.
                checks += 1
                metrics[2] += 1
                nx, ny, length = _pair_normal(key, other, positions, identity_directions)
                clearance = radii[key] + radii[other] - length
                if _iteration == 0 and clearance >= -(speeds[key] + speeds[other]) * 0.25:
                    first, second = _root(parents, key), _root(parents, other)
                    root, joined = min(first, second), max(first, second)
                    parents[joined] = root
                    depths[root] = max(depths[first], depths[second], clearance)
                    minimum_distances[root] = min(
                        minimum_distances[first],
                        minimum_distances[second],
                        length if length > 1e-9 else 1e30,
                    )
                    coincident[root] = coincident[first] or coincident[second] or length <= 1e-9
                    demands[root] = demands[first] or demands[second]
                if clearance > 1e-8:
                    # Joint release owns overlap recovery. Pairwise correction
                    # here would duplicate that solve and then be discarded by
                    # the passive coherent preference. Travel/contact prediction
                    # remains for separated incoming bodies.
                    pressure[key] = pressure[other] = True
                    continue
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
                        # Successful yielding is still incoming contact pressure.
                        # Its anchor return must observe the same quiet interval
                        # as a blocked correction, rather than undoing the lane
                        # immediately on the next tick.
                        pressure[member] = True
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

    # Start certificates from one feasible all-body trajectory, rather than
    # imposing zero motion on the unprocessed interior of an overlap component.
    seed, seed_checks = _release_contacts(
        positions,
        radii,
        speeds,
        active,
        identity_directions,
        obstacles,
        parents,
        depths,
        demands,
        minimum_distances,
        coincident,
        heads,
        links,
        columns,
        rows,
        bucket_size,
        scratch,
        planes,
        metrics,
        geometric_leaders,
        targets,
        strong,
    )
    checks += seed_checks
    checks += _return_anchors(
        positions,
        goals,
        radii,
        speeds,
        identity_directions,
        obstacles,
        regions,
        recovery_modes,
        heads,
        links,
        columns,
        rows,
        bucket_size,
        scratch,
        planes,
        seed,
        metrics,
        recovering,
        stalled,
    )
    for key in range(len(positions)):
        if recovery_modes[regions[key]] == 2 or recovery_modes[regions[key]] == 3:
            preferred[key] = seed[key]
            if (
                recovery_modes[regions[key]] == 3
                and recovery_status is not None
                and recovering is not None
            ):
                # A fulfilled request must not regain its original queue age
                # every time joint return encounters a new geometric minimum.
                # If it has since yielded again, movement requeues its unchanged
                # goal as a new request; never-fulfilled requests keep their age.
                fulfilled = recovery_released is not None and recovery_released[key]
                recovery_status[key] = 2 if recovering[key] and not fulfilled else 1
    result += seed
    # Serial intent refinement now preserves the already certified joint seed.
    # Every intermediate all-body trajectory remains feasible.
    for key in order:
        x, y = positions[key]
        vx, vy = preferred[key]
        sx, sy = seed[key]
        release = hypot(sx, sy)
        if hypot(vx, vy) <= 1e-12 and release <= 1e-12:
            if active[key]:
                pressure[key] = True
            continue  # Earlier certificates already kept clear of this resting body.
        limit = speeds[key] if active[key] else speeds[key] * 0.5
        count = _static_planes(x, y, radii[key], speeds[key], obstacles, planes)
        if release > 1e-12:
            # Retain certified recovery work while refining travel intent. A
            # passive body cannot immediately undo the component's release.
            planes[count] = (sx / release, sy / release, release)
            count += 1
            # Passive bodies take the coherent minimal release preference.
            # Summing pairwise prediction again distorts it and scatters the
            # component farther than necessary. Active travel retains intent.
            vx, vy = (vx + sx, vy + sy) if active[key] else (sx, sy)
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
        metrics[0] += neighbors - 1
        for index in range(neighbors):
            other = scratch[index]
            if other == key:
                continue
            checks += 1
            nx, ny, length = _pair_normal(key, other, positions, identity_directions)
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
        np.zeros(4, dtype=np.int64),
        np.empty(0, dtype=np.bool_),
        recovering=np.empty(0, dtype=np.bool_),
        recovery_ticks=np.empty(0, dtype=np.int64),
        recovery_released=np.empty(0, dtype=np.bool_),
        recovery_targets=empty,
        recovery_tolerances=np.empty(0),
        recovery_status=np.empty(0, dtype=np.int64),
    )
