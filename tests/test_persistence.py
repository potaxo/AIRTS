from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from airts.commands import (
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
)
from airts.geometry import Point, PointTarget, PolylineTarget, rectangle_region
from airts.map_model import EntityKind, GameMap, load_example_map
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


def test_phase3_controller_progress_and_suspended_assignments_round_trip(
    tmp_path: Path,
) -> None:
    simulation = Simulation(load_example_map(), random_seed=9)
    patrol = simulation.execute(CreatePatrolCommand(("scout_01",), PointTarget(Point(20, 28))))
    simulation.entities["scout_01"].health = 10
    simulation.execute(
        CreateRepairAndReturnCommand(("scout_01",), health_threshold=0.5, repair_rate=10)
    )
    simulation.execute(CreateProductionCommand("factory_01", EntityKind.SCOUT, 2))
    simulation.advance(4)
    destination = tmp_path / "phase3-state.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.snapshot() == simulation.snapshot()
    assert restored.suspended_assignments["scout_01"] == patrol.automation_id

    simulation.advance(40)
    restored.advance(40)
    assert restored.snapshot() == simulation.snapshot()
    assert [event.to_dict() for event in restored.events.events] == [
        event.to_dict() for event in simulation.events.events
    ]


def test_factory_production_queue_round_trip_continues_fifo(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=11)
    first = simulation.execute(CreateProductionCommand("factory_01", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory_01", EntityKind.LIGHT_TANK, 1))
    simulation.advance(4)
    destination = tmp_path / "production-queue.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.snapshot() == simulation.snapshot()
    assert restored.automations[second.automation_id or ""].reason_code == "FACTORY_QUEUED"

    simulation.advance(30)
    restored.advance(30)
    assert restored.snapshot() == simulation.snapshot()
    assert simulation.automations[first.automation_id or ""].status.value == "completed"
    assert simulation.automations[second.automation_id or ""].status.value == "completed"


def test_ambient_enemy_reinforcements_continue_after_save_load(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    simulation = Simulation(
        make_map(1),
        random_seed=37,
        ambient_enemy_spawns=True,
        enemy_spawn_interval_ticks=7,
        enemy_spawn_cap=12,
    )
    simulation.advance(12)
    destination = tmp_path / "ambient-enemies.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.ambient_enemy_spawns
    assert restored.enemy_spawn_interval_ticks == 7
    assert restored.enemy_spawn_cap == 12
    assert restored.snapshot() == simulation.snapshot()
    simulation.advance(10)
    restored.advance(10)
    assert restored.snapshot() == simulation.snapshot()


def test_continuous_production_compact_defense_link_round_trips(
    tmp_path: Path,
) -> None:
    simulation = Simulation(load_example_map(), random_seed=13)
    simulation.resources["player"] = 10_000
    simulation.execute(
        CreateProductionCommand(
            "factory_01",
            EntityKind.LIGHT_TANK,
            1,
            continuous=True,
            defend_target=rectangle_region(Point(32, 38), Point(43, 48)),
        )
    )
    simulation.advance(11)
    destination = tmp_path / "continuous-defense.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.snapshot() == simulation.snapshot()
    simulation.advance(10)
    restored.advance(10)
    assert restored.snapshot() == simulation.snapshot()
