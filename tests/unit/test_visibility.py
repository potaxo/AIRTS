"""Focused tests for visibility and exploration state."""

from __future__ import annotations

from unittest.mock import patch

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.world import visibility as visibility_module
from airts.world.map import load_map_data
from airts.world.visibility import VisibilityState


def test_visibility_is_player_specific_and_exploration_persists() -> None:
    game_map = load_map_data(
        {
            "id": "visibility",
            "name": "Visibility",
            "width": 30,
            "height": 10,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "player_scout", "kind": "scout", "owner": "player", "position": [2.5, 5.5]},
                {"id": "enemy_scout", "kind": "scout", "owner": "enemy", "position": [27.5, 5.5]},
            ],
        }
    )
    simulation = Simulation(game_map)
    player = simulation.visibility.for_player("player")
    enemy = simulation.visibility.for_player("enemy")

    assert player.state_at((2, 5)) is VisibilityState.VISIBLE
    assert player.state_at((27, 5)) is VisibilityState.UNEXPLORED
    assert enemy.state_at((27, 5)) is VisibilityState.VISIBLE
    assert enemy.state_at((2, 5)) is VisibilityState.UNEXPLORED

    simulation.execute(MoveCommand(("player_scout",), Point(20.5, 5.5)))
    simulation.advance(40)

    assert player.state_at((2, 5)) is VisibilityState.EXPLORED
    assert player.last_observed_tick[(2, 5)] < simulation.tick
    assert player.state_at((20, 5)) is VisibilityState.VISIBLE


def test_large_visibility_union_matches_cell_center_geometry_across_word_boundaries() -> None:
    from random import Random

    rng = Random(53)
    cells = rng.sample([(x, y) for y in range(24) for x in range(140)], 150)
    game_map = load_map_data(
        {
            "id": "wide_visibility",
            "name": "Wide visibility",
            "width": 140,
            "height": 24,
            "terrain": {"default": "grass"},
            "entities": [
                {
                    "id": f"unit_{index:03d}",
                    "kind": "scout" if index % 3 else "light_tank",
                    "owner": "player" if index % 2 else "enemy",
                    "position": [x + rng.uniform(0.1, 0.9), y + rng.uniform(0.1, 0.9)],
                }
                for index, (x, y) in enumerate(cells)
            ],
        }
    )
    simulation = Simulation(game_map)
    for owner in ("player", "enemy"):
        sources = [entity for entity in simulation.entities.values() if entity.owner_id == owner]
        expected = {
            (x, y)
            for y in range(game_map.height)
            for x in range(game_map.width)
            if any(
                (x + 0.5 - e.position.x) ** 2 + (y + 0.5 - e.position.y) ** 2 <= e.vision_range**2
                for e in sources
            )
        }
        assert simulation.visibility.for_player(owner).visible == expected


def test_grouped_visibility_reuses_static_unions_but_refreshes_observation_and_ownership() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "static_visibility",
                "name": "Static visibility",
                "width": 140,
                "height": 30,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": f"u{i}", "kind": "scout", "position": [2.5 + i % 65, 2.5 + i // 65]}
                    for i in range(130)
                ],
            }
        )
    )
    visibility = simulation.visibility
    expected = set(visibility.for_player("player").visible)
    with patch.object(
        visibility_module, "_visibility_words", wraps=visibility_module._visibility_words
    ) as calculate:
        visibility.update(simulation.entities, 1)
        visibility.update(simulation.entities, 2)
        assert calculate.call_count == 0
        assert visibility.for_player("player").visible == expected
        assert set(visibility.for_player("player").last_observed_tick.values()) == {2}
        # A source changing owner must invalidate both unions, even at the same position.
        entity = simulation.entities["u0"]
        entity.owner_id = "enemy"
        visibility.update(simulation.entities, 3)
        assert calculate.call_count == 2
        enemy = visibility.for_player("enemy")
        assert simulation.game_map.cell_for(entity.position) in enemy.visible
        entity.owner_id = "player"
        visibility.update(simulation.entities, 4)
        assert not enemy.visible and enemy.explored
        assert visibility.for_player("player").visible == expected
    # Crossing back to the small-source implementation must discard derived grouped data.
    reduced = dict(list(simulation.entities.items())[:128])
    visibility.update(reduced, 5)
    visibility.update(simulation.entities, 6)
    assert visibility.for_player("player").visible == expected
    assert set(visibility.for_player("player").last_observed_tick.values()) == {6}
