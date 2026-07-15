"""Integration tests for deterministic command replay."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from airts.adapters.replay import ReplayData, ReplayError, load_replay, run_replay, save_replay
from airts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    MoveCommand,
)
from airts.geometry import Point, PointTarget
from airts.simulation import Simulation
from airts.world.map_model import EntityKind, GameMap, load_example_map


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


def test_replay_reproduces_phase3_controller_commands(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=23)
    simulation.execute(CreateDefendCommand(("tank_01",), PointTarget(Point(20, 30), radius=3)))
    simulation.execute(
        CreateProductionCommand("factory_01", EntityKind.LIGHT_TANK, 1, Point(20, 35))
    )
    simulation.advance(25)
    destination = tmp_path / "phase3-replay.json"

    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))

    assert replayed.snapshot() == simulation.snapshot()


def test_replay_records_authoritative_entity_removal(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=29)
    production = simulation.execute(CreateProductionCommand("factory_01", EntityKind.SCOUT, 2))
    simulation.remove_entity("factory_01", "DESTROYED")
    destination = tmp_path / "removal-replay.json"

    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))

    assert replayed.snapshot() == simulation.snapshot()
    assert replayed.automations[production.automation_id or ""].status.value == "failed"
