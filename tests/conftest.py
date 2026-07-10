from __future__ import annotations

from collections.abc import Callable

import pytest

from airts.map_model import GameMap, load_map_data


@pytest.fixture
def make_map() -> Callable[[int], GameMap]:
    def factory(entity_count: int = 1) -> GameMap:
        entities = [
            {
                "id": f"unit_{index + 1:02d}",
                "kind": "scout" if index == 0 else "light_tank",
                "position": [1.5, 1.5 + index],
            }
            for index in range(entity_count)
        ]
        return load_map_data(
            {
                "id": "test_map",
                "name": "Test Map",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": entities,
            }
        )

    return factory
