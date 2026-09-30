"""Complete-state save/load and continuation across runtime scenarios."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
    economy_combat_simulation,
    growing_factory_simulation,
)

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import load_replay, run_replay, save_replay
from airts.automations import (
    AutomationStatus,
    ProductionParameters,
)
from airts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    SetSelectionCommand,
)
from airts.geometry import Point, PointTarget, PolylineTarget, rectangle_region
from airts.simulation import Simulation
from airts.world.map_model import EntityKind, GameMap, load_example_map


def test_save_restores_spatial_state(make_map: Callable[[int], GameMap], tmp_path: Path) -> None:
    simulation = Simulation(make_map())
    region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(2, 2), Point(6, 6)), "North")
    )
    simulation.execute(SetSelectionCommand(region_ids=(region.reference_id or "",)))
    destination = tmp_path / "phase4-save.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()
    assert restored.spatial.references[region.reference_id or ""].persistent
    _assert_continues_identically(simulation, restored, 5)


def test_save_and_replay_preserve_resources_and_combat(
    tmp_path: Path,
) -> None:
    simulation = economy_combat_simulation()
    simulation.execute(CreateEconomyCommand(("generator",), 520))
    simulation.execute(AttackCommand(("tank",), "enemy"))
    simulation.advance(12)

    save_path = tmp_path / "phase5-save.json"
    replay_path = tmp_path / "phase5-replay.json"
    save_simulation(simulation, save_path)
    save_replay(simulation, replay_path)

    restored = load_simulation(save_path)
    replayed = run_replay(load_replay(replay_path))
    assert restored.export_state() == simulation.export_state()
    assert replayed.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)
    replayed.advance(10)
    assert replayed.export_state() == simulation.export_state()


def test_construction_and_batch_progress_round_trip(tmp_path: Path) -> None:
    simulation = builder_factory_simulation()
    construction = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.REPAIR_HUB,
            Point(6, 10),
            builder_ids=("builder", "builder_2"),
        )
    )
    batch = simulation.execute(
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 1), (EntityKind.BUILDER, 1)))
    )
    simulation.advance(3)
    destination = tmp_path / "builder-factory.json"
    save_simulation(simulation, destination)

    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(
        simulation,
        restored,
        max(Simulation.CONSTRUCTION_BUILD_TICKS, Simulation.PRODUCTION_BUILD_TICKS * 2),
    )

    assert (
        restored.automations[construction.automation_id or ""].status is AutomationStatus.COMPLETED
    )
    assert restored.automations[construction.automation_id or ""].entity_ids == [
        "builder",
        "builder_2",
    ]
    batch_parameters = restored.automations[batch.automation_id or ""].parameters
    assert isinstance(batch_parameters, ProductionParameters)
    assert [restored.entities[item].kind for item in batch_parameters.produced_entity_ids] == [
        EntityKind.SCOUT,
        EntityKind.BUILDER,
    ]


def test_construction_queue_round_trips_through_save_and_replay(tmp_path: Path) -> None:
    simulation = builder_factory_simulation()
    simulation.resources["player"] = 500
    first = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.RESOURCE_GENERATOR,
            Point(6, 10),
            builder_ids=("builder", "builder_2"),
            queued=True,
        )
    )
    second = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.RESOURCE_GENERATOR,
            Point(16, 16),
            builder_ids=("builder", "builder_2"),
            queued=True,
        )
    )
    simulation.advance()
    assert simulation.automations[second.automation_id or ""].reason_code == "BUILDERS_QUEUED"

    save_path = tmp_path / "construction-queue.json"
    replay_path = tmp_path / "construction-queue-replay.json"
    save_simulation(simulation, save_path)
    save_replay(simulation, replay_path)
    restored = load_simulation(save_path)
    replayed = run_replay(load_replay(replay_path))

    assert restored.export_state() == simulation.export_state()
    assert replayed.export_state() == simulation.export_state()

    simulation.resources["player"] = 10_000
    restored.resources["player"] = 10_000
    replayed.resources["player"] = 10_000
    _assert_continues_identically(simulation, restored, 200)
    replayed.advance(200)
    assert replayed.export_state() == simulation.export_state()
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[second.automation_id or ""].status is AutomationStatus.COMPLETED


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
    simulation.entities["unit_01"].congestion_stopped = True
    simulation.entities["unit_01"].collision_pressure = 3
    destination = tmp_path / "state.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.export_state() == simulation.export_state()
    assert restored.entities["unit_01"].collision_pressure == 3

    _assert_continues_identically(simulation, restored, 25)


def test_load_rejects_an_unknown_schema(tmp_path: Path) -> None:
    destination = tmp_path / "invalid.json"
    destination.write_text(json.dumps({"schema": "future"}), encoding="utf-8")

    with pytest.raises(PersistenceError, match="unsupported save schema"):
        load_simulation(destination)


def test_controller_progress_and_suspended_assignments_round_trip(
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

    assert restored.export_state() == simulation.export_state()
    assert restored.suspended_assignments["scout_01"] == patrol.automation_id

    _assert_continues_identically(simulation, restored, 40)


def test_factory_production_queue_round_trip_continues_fifo(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=11)
    first = simulation.execute(CreateProductionCommand("factory_01", EntityKind.SCOUT, 1))
    second = simulation.execute(CreateProductionCommand("factory_01", EntityKind.LIGHT_TANK, 1))
    simulation.advance(4)
    destination = tmp_path / "production-queue.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.export_state() == simulation.export_state()
    assert restored.automations[second.automation_id or ""].reason_code == "FACTORY_QUEUED"

    _assert_continues_identically(simulation, restored, 30)
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
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)


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

    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)


def _assert_continues_identically(original: Simulation, restored: Simulation, ticks: int) -> None:
    original.advance(ticks)
    restored.advance(ticks)
    assert restored.export_state() == original.export_state()


def test_growing_army_save_preserves_shared_cells_and_movement_continuation() -> None:
    simulation, _ = growing_factory_simulation()
    simulation.advance(260)
    document = {
        "schema": SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 20)
    assert "traffic_reservations" not in document["state"]


@pytest.mark.parametrize("schema", ["airts-save-v7", "airts-save-v8"])
def test_previous_save_version_repairs_consumed_waypoint_progress(schema: str) -> None:
    simulation, _ = growing_factory_simulation()
    simulation.advance(260)
    state = simulation.export_state()
    entity = next(value for value in state["entities"].values() if value["path"])
    entity["progress_target"] = [2.5, 2.5]
    entity["progress_distance"] = 10.0
    restored = load_simulation_data(
        {"schema": schema, "map": simulation.game_map.to_dict(), "state": state}
    )
    loaded_entity = restored.entities[entity["id"]]
    assert loaded_entity.progress_target == loaded_entity.path[0]
    restored.advance(5)
    assert restored.tick == 265
    assert loaded_entity.progress_target == (loaded_entity.path[0] if loaded_entity.path else None)
