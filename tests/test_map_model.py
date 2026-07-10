from __future__ import annotations

import pytest

from airts.geometry import Point
from airts.map_model import MapValidationError, Terrain, load_example_map, load_map_data


def test_bundled_map_is_a_valid_64_by_64_playable_map() -> None:
    game_map = load_example_map()

    assert (game_map.width, game_map.height) == (64, 64)
    assert len(game_map.entities) == 6
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


def test_map_rejects_out_of_bounds_terrain_patches() -> None:
    data = _valid_map_data()
    data["terrain"] = {"default": "grass", "rectangles": [[3, 3, 2, 2, "rock"]]}

    with pytest.raises(MapValidationError, match="outside the map"):
        load_map_data(data)


def _valid_map_data() -> dict[str, object]:
    return {
        "id": "small",
        "name": "Small",
        "width": 4,
        "height": 4,
        "terrain": {"default": "grass", "rectangles": []},
        "entities": [{"id": "unit_01", "kind": "scout", "position": [1, 1]}],
    }
