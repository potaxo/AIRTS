"""Geometric certificates for coherent recovery trajectories."""

from math import ceil, floor, sqrt

import numpy as np

from airts.navigation.collision.returning import _project_anchor_gradient, _return_anchors


def test_sparse_projection_releases_redundant_singular_contact_row() -> None:
    # Three radius-.5 discs are legally touching on the x axis. Their goals are
    # the same legal arrangement rotated 90 degrees about the middle disc.
    positions = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
    goals = np.array([[1.0, -1.0], [1.0, 0.0], [1.0, 1.0]])
    candidate = goals - positions
    first = np.array([0, 1, 0], dtype=np.int64)
    second = np.array([1, 2, 2], dtype=np.int64)
    normals = np.array([[-1.0, 0.0], [-1.0, 0.0], [-1.0, 0.0]])
    assert np.linalg.norm(goals[first] - goals[second], axis=1).min() >= 1.0
    # x0 <= x1 <= x2 projects (1, 0, -1) uniquely to zero; y is unconstrained
    # and retains (-1, 0, 1). The outer pair has one unit of spare distance.
    # Treating all three initially violated faces as equalities is inconsistent;
    # the singular dual system must release the outer pair's redundant row.
    expected = np.array([[0.0, -1.0], [0.0, 0.0], [0.0, 1.0]])
    work = np.zeros(4, dtype=np.int64)
    binding = np.zeros(3, dtype=np.bool_)
    checks = _project_anchor_gradient(
        first, second, normals, np.array([0.0, 0.0, -1.0]), candidate, work, binding
    )
    np.testing.assert_allclose(candidate, expected, atol=1e-10)
    assert binding.tolist() == [True, True, False]
    assert 0 < checks <= 64 and 0 < work[2] <= 512
    # Certify the entire relative trajectory independently of the solver's planes.
    for edge in range(3):
        offset = positions[first[edge]] - positions[second[edge]]
        relative = candidate[first[edge]] - candidate[second[edge]]
        squared = float(relative @ relative)
        fraction = min(1.0, max(0.0, -float(offset @ relative) / squared)) if squared else 0.0
        assert np.linalg.norm(offset + fraction * relative) >= 1.0 - 1e-10


def test_anchor_gradient_shares_speed_cap_with_nonzero_certified_seed() -> None:
    # Both layouts are legal touching formations. A corner return needs a small
    # radial compensation, while an existing common side displacement has already
    # consumed most of each passive body's speed circle.
    positions = np.array([[49.4, 50.0], [50.0, 50.0], [50.6, 50.0]])
    goals = np.array([[49.4, 50.6], [49.4, 50.0], [50.0, 50.0]])
    radii, speeds = np.full(3, 0.3), np.full(3, 0.35)
    seed = np.array([[-0.17, 0.0], [-0.17, 0.0], [-0.17, 0.0]])
    bucket_size = float(speeds.max() * 2 + radii.max() * 2)
    columns = rows = ceil(96 / bucket_size)
    heads = np.full(columns * rows, -1, dtype=np.int64)
    links = np.full(3, -1, dtype=np.int64)
    for key, (x, y) in enumerate(positions):
        bucket = floor(y / bucket_size) * columns + floor(x / bucket_size)
        links[key], heads[bucket] = heads[bucket], key
    work = np.zeros(4, dtype=np.int64)
    checks = _return_anchors(
        positions,
        goals,
        radii,
        speeds,
        np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]),
        np.zeros((96, 96), dtype=np.bool_),
        np.zeros(3, dtype=np.int64),
        np.array([2, 0, 0], dtype=np.int64),
        heads,
        links,
        columns,
        rows,
        bucket_size,
        np.empty(3, dtype=np.int64),
        np.empty((100, 3)),
        seed,
        work,
    )
    assert checks > 0 and work[0] > 0 and work[2] > 0
    assert seed[0, 1] > 0.01  # The certified blend still advances the corner return.
    assert np.linalg.norm(seed, axis=1).max() <= 0.175 + 1e-10
    # Independent swept-distance checks cover the whole joint trajectory, rather
    # than merely accepting the solver's final positions or its normal planes.
    for first in range(3):
        for second in range(first):
            offset = positions[first] - positions[second]
            relative = seed[first] - seed[second]
            squared = float(relative @ relative)
            fraction = min(1.0, max(0.0, -float(offset @ relative) / squared)) if squared else 0.0
            assert np.linalg.norm(offset + fraction * relative) >= 0.6 - 1e-10


def test_closed_microgap_dependencies_preserve_safe_joint_anchor_rotation() -> None:
    # Three radius-.3 bodies form a legal equilateral triangle with .00001 spare
    # distance on every edge. Adding a 90-degree tangent offset expands every
    # destination edge by sqrt(2), so these original anchors are also compatible.
    # The common straight motion rotates and expands the triangle safely. Each
    # body individually approaches one neighbor while that neighbor moves away;
    # allocating each gap as if the departing neighbor stayed still pins all three.
    side = 0.60001
    positions = np.array(
        [[5.0, 5.0], [5.0 + side, 5.0], [5.0 + side * 0.5, 5.0 + side * sqrt(3) * 0.5]]
    )
    offsets = positions - positions.mean(axis=0)
    goals = positions + np.column_stack((-offsets[:, 1], offsets[:, 0]))
    radii, speeds = np.full(3, 0.3), np.full(3, 0.4)
    for first in range(3):
        for second in range(first):
            assert np.linalg.norm(positions[first] - positions[second]) >= 0.6
            assert np.linalg.norm(goals[first] - goals[second]) >= 0.6
    bucket_size = float(speeds.max() * 2 + radii.max() * 2)
    columns = rows = ceil(96 / bucket_size)
    heads = np.full(columns * rows, -1, dtype=np.int64)
    links = np.full(3, -1, dtype=np.int64)
    for key, (x, y) in enumerate(positions):
        bucket = floor(y / bucket_size) * columns + floor(x / bucket_size)
        links[key], heads[bucket] = heads[bucket], key
    seed = np.zeros_like(positions)
    work = np.zeros(4, dtype=np.int64)
    checks = _return_anchors(
        positions,
        goals,
        radii,
        speeds,
        np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]),
        np.zeros((96, 96), dtype=np.bool_),
        np.zeros(3, dtype=np.int64),
        np.array([2, 0, 0], dtype=np.int64),
        heads,
        links,
        columns,
        rows,
        bucket_size,
        np.empty(3, dtype=np.int64),
        np.empty((100, 3)),
        seed,
        work,
    )
    assert np.all(np.isfinite(seed))
    steps = np.linalg.norm(seed, axis=1)
    assert np.all(steps >= 0.19)
    assert np.all(steps <= speeds * 0.5 + 1e-10)
    assert np.all(
        np.linalg.norm(goals - positions, axis=1) - np.linalg.norm(goals - positions - seed, axis=1)
        >= 0.19
    )
    assert 0 < checks <= 128 and 0 < work[2] <= 1_024
    # Certify every pair's whole relative segment independently of active sets,
    # contact grouping, internal normals or the solver's final endpoint alone.
    for first in range(3):
        for second in range(first):
            offset = positions[first] - positions[second]
            relative = seed[first] - seed[second]
            squared = float(relative @ relative)
            fraction = min(1.0, max(0.0, -float(offset @ relative) / squared)) if squared else 0.0
            assert np.linalg.norm(offset + fraction * relative) >= 0.6 - 1e-10
