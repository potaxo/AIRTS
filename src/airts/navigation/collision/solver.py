"""Collision solver algorithms; numeric inputs contain no world mutation."""

from math import ceil, cos, floor, hypot, sin, sqrt

import numpy as np

from airts.navigation.collision._compile import compile_collision
from airts.navigation.collision.geometry import _pair_normal, _root, _solve_step, _static_planes
from airts.navigation.collision.neighbors import _neighbors, _steering_obstacles
from airts.navigation.collision.release import _release_contacts
from airts.navigation.collision.returning import (
    _probe_opening_return,
    _recovery_regions,
    _return_anchors,
)
from airts.world._numeric import BoolArray, FloatArray, IntArray


@compile_collision
def advance_positions(
    positions: FloatArray,
    targets: FloatArray,
    goals: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    masses: FloatArray,
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
    joint_probe = np.zeros(len(positions), dtype=np.bool_)
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
        regions, recovery_modes, recovery_leaders, _, joint_probe, recovery_checks = (
            _recovery_regions(
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
                if (
                    clearance < 0.0
                    and active[key]
                    and active[other]
                    and (masses[key] == masses[other] or not (strong[key] or strong[other]))
                ):
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
                            # Opening a passing lane is voluntary geometric
                            # avoidance, not an impact impulse. Mass biases the
                            # normal correction below; it must not slow this
                            # maneuver and fold a held formation across anchors.
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
                weighted = masses[key] != masses[other] and (strong[key] or strong[other])
                first_weight = 0.15 if driven[key] and not yielded[key] else 1.0
                second_weight = 0.15 if driven[other] and not yielded[other] else 1.0
                if weighted:
                    first_weight /= masses[key]
                    second_weight /= masses[other]
                fraction = first_weight / (first_weight + second_weight)
                remaining = deficit
                for member, sign, amount in ((key, 1.0, fraction), (other, -1.0, 1.0 - fraction)):
                    allocation = remaining if weighted and member == other else deficit * amount
                    old_x, old_y = preferred[member, 0], preferred[member, 1]
                    vx = old_x + nx * allocation * sign
                    vy = old_y + ny * allocation * sign
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
                        remaining -= sign * (nx * (mx - old_x) + ny * (my - old_y))
                        changed = True
                if weighted and remaining > 1e-9:
                    # Walls/speed circles can exhaust the lighter body's share.
                    # Transfer only its unfulfilled correction in one bounded
                    # capacity pass instead of pinning both at that contact.
                    checks += 1
                    metrics[2] += 1
                    for member, sign in ((key, 1.0), (other, -1.0)):
                        if remaining <= 1e-9:
                            break
                        old_x, old_y = preferred[member, 0], preferred[member, 1]
                        count = _static_planes(
                            positions[member, 0],
                            positions[member, 1],
                            radii[member],
                            speeds[member],
                            obstacles,
                            planes,
                        )
                        limit = speeds[member] if active[member] else speeds[member] * 0.5
                        mx, my, feasible = _solve_step(
                            old_x + nx * remaining * sign,
                            old_y + ny * remaining * sign,
                            limit,
                            planes,
                            count,
                        )
                        if feasible:
                            preferred[member, 0], preferred[member, 1] = mx, my
                            remaining -= sign * (nx * (mx - old_x) + ny * (my - old_y))
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
    probe_checks, probe_accepted = _probe_opening_return(
        positions,
        goals,
        radii,
        speeds,
        identity_directions,
        obstacles,
        regions,
        recovery_leaders,
        joint_probe,
        heads,
        links,
        columns,
        rows,
        bucket_size,
        scratch,
        planes,
        seed,
        metrics,
    )
    checks += probe_checks
    if probe_accepted:
        for root in range(len(positions)):
            if joint_probe[root]:
                recovery_modes[root] = 2
                leader = recovery_leaders[root]
                if geometric_leaders[leader]:
                    # The certified trial owns this weak body's complete step.
                    # Undo opening-only promotion without touching business intent.
                    speeds[leader] *= 2.0
                    active[leader], stationary[leader] = False, True
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
