from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from airts.commands import CreatePatrolCommand
from airts.geometry import Point, PolylineTarget
from airts.map_model import GameMap
from airts.persistence import PersistenceError, load_simulation, save_simulation
from airts.simulation import Simulation


def test_save_load_round_trip_preserves_active_runtime_and_continuation(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    simulation = Simulation(make_map(2), random_seed=42)
    simulation.execute(
        CreatePatrolCommand(
            ("unit_01", "unit_02"),
            PolylineTarget((Point(2.5, 2.5), Point(9.5, 2.5), Point(9.5, 8.5))),
        )
    )
    simulation.advance(9)
    destination = tmp_path / "state.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.snapshot() == simulation.snapshot()
    assert [event.to_dict() for event in restored.events.events] == [
        event.to_dict() for event in simulation.events.events
    ]
    assert restored.command_history == simulation.command_history

    simulation.advance(25)
    restored.advance(25)
    assert restored.snapshot() == simulation.snapshot()
    assert [event.to_dict() for event in restored.events.events] == [
        event.to_dict() for event in simulation.events.events
    ]


def test_load_rejects_an_unknown_schema(tmp_path: Path) -> None:
    destination = tmp_path / "invalid.json"
    destination.write_text(json.dumps({"schema": "future"}), encoding="utf-8")

    with pytest.raises(PersistenceError, match="unsupported save schema"):
        load_simulation(destination)
