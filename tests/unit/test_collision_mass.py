"""Relative mass changes contact response without weakening geometric certificates."""

import numpy as np
import pytest

from airts.navigation.collision.solver import advance_positions


@pytest.mark.parametrize("moving_other", [False, True], ids=["passive", "opposing-orders"])
def test_heavier_body_resists_contact_with_equal_geometry_and_speed(moving_other: bool) -> None:
    positions = np.array([[5.0, 5.0], [5.8, 5.0]])
    targets = np.array([[6.0, 5.0], [4.8 if moving_other else 5.8, 5.0]])
    outcomes = []
    for masses in ([9.0, 1.0], [1.0, 9.0]):
        result, _, checks = advance_positions(
            positions,
            targets,
            targets,
            np.full(2, 0.4),
            np.full(2, 0.25),
            np.array(masses),
            np.array([0, 1], dtype=np.int64),
            np.array([True, moving_other]),
            np.array([False, not moving_other]),
            np.zeros((12, 12), dtype=np.bool_),
            0,
        )
        displacement = result - positions
        assert np.all(np.isfinite(result))
        assert np.linalg.norm(displacement[0]) <= 0.25 + 1e-9
        assert np.linalg.norm(displacement[1]) <= (0.25 if moving_other else 0.125) + 1e-9
        offset = positions[0] - positions[1]
        relative = displacement[0] - displacement[1]
        squared = float(relative @ relative)
        fraction = min(1.0, max(0.0, -float(offset @ relative) / squared)) if squared else 0.0
        assert np.linalg.norm(offset + fraction * relative) >= 0.8 - 1e-8
        assert 0 < checks <= 8
        outcomes.append(displacement)
    if moving_other:
        assert outcomes[0][0, 0] > outcomes[1][0, 0] + 0.1
        assert outcomes[0][1, 0] > outcomes[1][1, 0] + 0.1
    else:
        # Voluntary side-stepping can consume the same passive speed allowance;
        # compare displacement along the collision normal, not total movement.
        assert outcomes[0][1, 0] > outcomes[1][1, 0] + 0.03
