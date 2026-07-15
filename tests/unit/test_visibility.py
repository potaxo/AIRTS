"""Focused tests for visibility and exploration state."""

from __future__ import annotations

from airts.commands import MoveCommand
from airts.geometry import Point
from airts.simulation import Simulation
from airts.world.map_model import load_map_data
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
