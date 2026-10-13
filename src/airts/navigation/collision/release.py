"""Collision release algorithms; numeric inputs contain no world mutation."""

from math import ceil, hypot, sqrt

import numpy as np

from airts.navigation.collision._compile import compile_collision
from airts.navigation.collision.geometry import _pair_normal, _root, _solve_step, _static_planes
from airts.navigation.collision.neighbors import _neighbors
from airts.world._numeric import BoolArray, FloatArray, IntArray


@compile_collision
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


@compile_collision
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
