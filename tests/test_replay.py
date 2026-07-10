from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from airts.commands import CreatePatrolCommand, MoveCommand
from airts.geometry import Point, PointTarget
from airts.map_model import GameMap
from airts.replay import ReplayData, ReplayError, load_replay, run_replay, save_replay
from airts.simulation import Simulation


def test_recorded_commands_reproduce_state_and_events(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    simulation = Simulation(make_map(2), random_seed=17)
    simulation.execute(MoveCommand(("unit_01",), Point(5.5, 1.5)))
    simulation.advance(12)
    simulation.execute(CreatePatrolCommand(("unit_02",), PointTarget(Point(7.5, 7.5))))
    simulation.advance(30)
    destination = tmp_path / "replay.json"

    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))

    assert replayed.snapshot() == simulation.snapshot()
    assert [event.to_dict() for event in replayed.events.events] == [
        event.to_dict() for event in simulation.events.events
    ]


def test_replay_verification_detects_state_divergence(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    simulation.advance(2)
    replay = ReplayData(
        game_map=simulation.game_map,
        random_seed=0,
        commands=(),
        final_tick=2,
        expected_snapshot={"invalid": True},
        expected_events=tuple(event.to_dict() for event in simulation.events.events),
    )

    with pytest.raises(ReplayError, match="final state"):
        run_replay(replay)
