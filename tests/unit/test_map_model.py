"""Focused tests for map loading and validation."""

from __future__ import annotations

import pytest

from airts.contracts.spatial import Point
from airts.world.map import (
    EntityCategory,
    MapValidationError,
    Terrain,
    load_example_map,
    load_map_data,
)


def test_bundled_map_is_a_valid_64_by_64_playable_map() -> None:
    game_map = load_example_map()

    assert (game_map.width, game_map.height) == (64, 64)
    assert game_map.map_version == 3
    assert len(game_map.entities) == 13
    assert (
        sum(spec.kind.profile.category is EntityCategory.BUILDING for spec in game_map.entities)
        == 5
    )
    assert game_map.terrain_at(Point(31, 20)) is Terrain.WATER
    assert game_map.terrain_at(Point(31, 31)) is Terrain.BRIDGE
    assert game_map.is_passable(Point(31, 31))
    assert not game_map.is_passable(Point(31, 20))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        pytest.param(
            {
                "entities": [
                    {"id": "unit_01", "kind": "scout", "position": [1, 1]},
                    {"id": "unit_01", "kind": "scout", "position": [2, 2]},
                ]
            },
            "duplicate entity ID",
            id="duplicate-id",
        ),
        pytest.param(
            {"terrain": {"default": "water", "rectangles": []}},
            "impassable terrain",
            id="impassable-terrain",
        ),
        pytest.param(
            {"entities": [{"id": "unit", "kind": "scout", "position": [-0.2, 1]}]},
            "outside the map",
            id="negative-position",
        ),
        pytest.param(
            {"terrain": {"default": "grass", "rectangles": [[3, 3, 2, 2, "rock"]]}},
            "outside the map",
            id="out-of-bounds-patch",
        ),
        pytest.param(
            {
                "entities": [
                    {"id": "factory", "kind": "factory", "position": [0, 0]},
                    {"id": "repair", "kind": "repair_hub", "position": [1, 1]},
                ]
            },
            "overlaps",
            id="overlapping-footprints",
        ),
    ],
)
def test_map_rejects_invalid_data(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(MapValidationError, match=message):
        load_map_data(_valid_map_data() | overrides)


def test_map_round_trips_normalized_rows_and_entity_ownership() -> None:
    game_map = load_example_map()

    restored = load_map_data(game_map.to_dict())

    assert restored == game_map


def _valid_map_data() -> dict[str, object]:
    return {
        "id": "small",
        "name": "Small",
        "width": 4,
        "height": 4,
        "terrain": {"default": "grass", "rectangles": []},
        "entities": [{"id": "unit_01", "kind": "scout", "position": [1, 1]}],
    }
