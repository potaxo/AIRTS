from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from airts.world.map import GameMap, load_map_data
from airts.world.units import catalog


@pytest.fixture
def dense_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep five-tick spawns in crowd stress cases independently of gameplay balance."""

    for kind in catalog.EntityKind:
        if kind.profile.movable:
            monkeypatch.setitem(
                catalog._ENTITY_PROFILES, kind, replace(kind.profile, production_ticks=5)
            )


@pytest.fixture
def established_economy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise AI strategy/placement with 25/s income, independently of opening balance."""
    monkeypatch.setitem(
        catalog._ENTITY_PROFILES,
        catalog.EntityKind.RESOURCE_GENERATOR,
        replace(catalog.EntityKind.RESOURCE_GENERATOR.profile, income_per_second=25),
    )


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
