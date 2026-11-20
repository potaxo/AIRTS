"""Full nearest-hostile queries keep the geometry and stable-ID tie rule."""

from math import hypot

import numpy as np
import pytest

from airts.systems.opponent_ai import (
    PlacementTerrain,
    _minimum_distances,
    _nearest_enemy_index,
    _producer_nearby,
    _valid_placement_origins,
)
from airts.world.map import load_map_data


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


@pytest.mark.parametrize("resource", [False, True])
@pytest.mark.parametrize("footprint", [(1, 1), (2, 4), (5, 5)])
def test_batched_placement_matches_complete_cell_validation(resource, footprint) -> None:
    game_map = load_map_data(
        {
            "id": "placement",
            "name": "Placement",
            "width": 24,
            "height": 20,
            "terrain": {
                "default": "grass",
                "rectangles": [
                    [3, 4, 7, 3, "road"],
                    [14, 6, 3, 8, "water"],
                    [5, 14, 8, 2, "bridge"],
                ],
            },
            "entities": [{"id": "observer", "kind": "scout", "position": [1.5, 1.5]}],
        }
    )
    terrain = PlacementTerrain(game_map)
    ground = terrain.resource if resource else terrain.ordinary
    blocked = np.zeros(ground.shape, dtype=np.bool_)
    blocked[8:10, 5:12] = True
    points = np.random.default_rng(83).uniform([-3, -3], [26, 22], (1000, 2))
    width, height = footprint
    expected = []
    for px, py in points:
        x, y = int(px), int(py)
        expected.append(
            all(
                0 <= cx < 24 and 0 <= cy < 20 and ground[cy, cx] and not blocked[cy, cx]
                for cy in range(y, y + height)
                for cx in range(x, x + width)
            )
        )
    assert _valid_placement_origins(points, ground, blocked, width, height).tolist() == expected
