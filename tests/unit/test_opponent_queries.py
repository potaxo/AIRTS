"""Full nearest-hostile queries keep the geometry and stable-ID tie rule."""

from math import hypot

import numpy as np
import pytest

from airts.systems.opponent_ai import _minimum_distances, _nearest_enemy_index, _producer_nearby


@pytest.mark.parametrize("point", [(0.0, 0.0), (100.5, 23.7), (319.9, 319.9)])
def test_complete_nearest_query_matches_distance_oracle(point: tuple[float, float]) -> None:
    coordinates = np.random.default_rng(29).uniform(0, 320, (3000, 2))
    x, y = point
    expected = min(
        range(len(coordinates)),
        key=lambda i: (hypot(coordinates[i, 0] - x, coordinates[i, 1] - y), i),
    )
    assert _nearest_enemy_index(coordinates, x, y) == expected


def test_nearest_query_handles_empty_and_tied_targets() -> None:
    assert _nearest_enemy_index(np.empty((0, 2)), 0.0, 0.0) == -1
    positions = np.asarray([[-1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    assert _nearest_enemy_index(positions, 0.0, 0.0) == 0


def test_batched_regional_distances_match_the_complete_scalar_oracle() -> None:
    random = np.random.default_rng(31)
    queries = random.uniform(-40, 320, (144, 2))
    references = random.uniform(0, 320, (2000, 2))
    expected = [min(hypot(x - a, y - b) for a, b in references) for x, y in queries]
    assert _minimum_distances(queries, references, 18.0) == pytest.approx(expected, rel=1e-14)
    assert _minimum_distances(queries, np.empty((0, 2)), 18.0) == pytest.approx(
        [18.0] * len(queries)
    )
    assert _minimum_distances(np.empty((0, 2)), references, 0.0).size == 0


@pytest.mark.parametrize("point", [(4, 4), (0, 4), (0.001, 4), (6, 11), (10, 4), (100, 100)])
def test_numeric_producer_clearance_keeps_rectangles_and_the_strict_boundary(point) -> None:
    boxes = np.asarray([[3.0, 4.0, 4.0, 4.0], [20.0, 14.0, 5.0, 5.0]])
    x, y = point
    expected = any(
        hypot(max(a - x, 0, x - a - width), max(b - y, 0, y - b - height)) < 3
        for a, b, width, height in boxes
    )
    assert _producer_nearby(boxes, float(x), float(y)) is expected
    assert not _producer_nearby(np.empty((0, 4)), float(x), float(y))
