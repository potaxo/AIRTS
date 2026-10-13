"""Collision returning algorithms; numeric inputs contain no world mutation."""

from math import hypot, sqrt

import numpy as np

from airts.navigation.collision._compile import compile_collision
from airts.navigation.collision.geometry import _pair_normal, _root, _static_planes
from airts.navigation.collision.neighbors import _neighbors
from airts.world._numeric import BoolArray, FloatArray, IntArray


@compile_collision
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
) -> tuple[IntArray, IntArray, IntArray, IntArray, BoolArray, int]:
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
    joint_probe = np.zeros(size, dtype=np.bool_)
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
        # A newly displaced weighted formation can have a useful joint return
        # before the oldest body reaches its anchor. Probe the existing
        # certified gradient during opening rather than
        # spending hundreds of ticks opening one body at a time. Tickets remain
        # pending until actual arrival; no strong travel or overlap is admitted.
        joint_probe[root] = direct[root] and not released[leader]
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
    return parents, modes, leaders, member_links, joint_probe, checks


@compile_collision
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


@compile_collision
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


@compile_collision
def _probe_opening_return(
    positions: FloatArray,
    goals: FloatArray,
    radii: FloatArray,
    speeds: FloatArray,
    identity_directions: FloatArray,
    obstacles: BoolArray,
    regions: IntArray,
    leaders: IntArray,
    probe: BoolArray,
    heads: IntArray,
    links: IntArray,
    columns: int,
    rows: int,
    bucket_size: float,
    scratch: IntArray,
    planes: FloatArray,
    seed: FloatArray,
    work: IntArray,
) -> tuple[int, bool]:
    """Try a certified joint direction without prematurely ending an opening.

    A trial must improve every opening leader over its existing release
    seed. Otherwise retain the original all-body seed. Commit or reject the
    whole trial so independently restored regions cannot overspend a shared gap.
    """
    if not bool(probe.any()):
        return 0, False
    modes = np.zeros(len(positions), dtype=np.int64)
    for root in range(len(positions)):
        if probe[root]:
            modes[root] = 2
    candidate = seed.copy()
    checks = _return_anchors(
        positions,
        goals,
        radii,
        speeds,
        identity_directions,
        obstacles,
        regions,
        modes,
        heads,
        links,
        columns,
        rows,
        bucket_size,
        scratch,
        planes,
        candidate,
        work,
    )
    for root in range(len(positions)):
        if not probe[root]:
            continue
        work[2] += 1
        leader = leaders[root]
        dx = goals[leader, 0] - positions[leader, 0]
        dy = goals[leader, 1] - positions[leader, 1]
        original_error = hypot(dx - seed[leader, 0], dy - seed[leader, 1])
        trial_error = hypot(dx - candidate[leader, 0], dy - candidate[leader, 1])
        # A barely positive gradient can be a geometric minimum in a large
        # touching formation. It must not replace a useful opening with
        # thousands of microscopic steps. Require half the trial allowance,
        # or the complete remaining error when the anchor is closer.
        required_progress = min(hypot(dx, dy), speeds[leader] * 0.25)
        if (
            modes[root] != 2
            or trial_error >= original_error - 1e-8
            or trial_error > hypot(dx, dy) - required_progress + 1e-8
        ):
            return checks, False
    seed[:] = candidate
    return checks, True


@compile_collision
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
