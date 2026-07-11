from __future__ import annotations

import pytest

from airts.geometry import Point
from airts.map_model import (
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


def test_map_rejects_duplicate_entity_ids() -> None:
    data = _valid_map_data()
    entities = data["entities"]
    assert isinstance(entities, list)
    entities.append({"id": "unit_01", "kind": "scout", "position": [2, 2]})

    with pytest.raises(MapValidationError, match="duplicate entity ID"):
        load_map_data(data)


def test_map_rejects_entities_on_impassable_terrain() -> None:
    data = _valid_map_data()
    data["terrain"] = {"default": "water", "rectangles": []}

    with pytest.raises(MapValidationError, match="impassable terrain"):
        load_map_data(data)


def test_map_rejects_negative_fractional_entity_positions() -> None:
    data = _valid_map_data()
    data["entities"] = [{"id": "unit", "kind": "scout", "position": [-0.2, 1]}]

    with pytest.raises(MapValidationError, match="outside the map"):
        load_map_data(data)


def test_map_rejects_out_of_bounds_terrain_patches() -> None:
    data = _valid_map_data()
    data["terrain"] = {"default": "grass", "rectangles": [[3, 3, 2, 2, "rock"]]}

    with pytest.raises(MapValidationError, match="outside the map"):
        load_map_data(data)


def test_map_rejects_overlapping_building_footprints() -> None:
    data = _valid_map_data()
    entities = data["entities"]
    assert isinstance(entities, list)
    entities.extend(
        [
            {"id": "factory", "kind": "factory", "position": [0, 0]},
            {"id": "repair", "kind": "repair_hub", "position": [1, 1]},
        ]
    )

    with pytest.raises(MapValidationError, match="overlaps"):
        load_map_data(data)


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
