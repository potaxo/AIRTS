"""Skirmish map symmetry, resource sites and opening configuration."""

from __future__ import annotations

import pytest

from airts.contracts.spatial import Point
from airts.world.map import Terrain, load_map_data
from airts.world.skirmish import create_skirmish_map


def test_skirmish_geometry_and_opening_entities_are_exactly_symmetric() -> None:
    game_map = create_skirmish_map()
    assert game_map.width == game_map.height == 320
    for row in game_map.terrain:
        assert row == tuple(reversed(row))
    assert game_map.terrain == tuple(reversed(game_map.terrain))
    sites = set(game_map.resource_sites or ())
    assert len(sites) == 12
    assert {Point(318 - point.x, point.y) for point in sites} == sites
    assert {Point(point.x, 318 - point.y) for point in sites} == sites
    assert game_map.terrain[160][160] is Terrain.BRIDGE
    assert game_map.terrain[46][160] is Terrain.BRIDGE
    assert game_map.terrain[80][160] is Terrain.WATER
    assert load_map_data(game_map.to_dict()) == game_map
    positions = {(entity.kind, entity.position) for entity in game_map.entities}
    assert all(
        (
            entity.kind,
            Point(
                320
                - entity.position.x
                - (entity.kind.profile.footprint[0] if not entity.kind.profile.movable else 0),
                entity.position.y,
            ),
        )
        in positions
        for entity in game_map.entities
    )


def test_closed_slots_keep_resources_and_configuration_validation() -> None:
    game_map = create_skirmish_map((("closed", 1), ("player", 1), ("ai", 2), ("closed", 2)))
    assert len(game_map.entities) == 10
    assert len(game_map.resource_sites or ()) == 12
    assert {slot.owner_id for slot in game_map.match_slots} == {"player", "ai_c"}
    with pytest.raises(ValueError, match="human"):
        create_skirmish_map((("ai", 1),) * 4)
    with pytest.raises(ValueError, match="opposing"):
        create_skirmish_map((("player", 1), ("ai", 1), ("closed", 2), ("closed", 2)))
