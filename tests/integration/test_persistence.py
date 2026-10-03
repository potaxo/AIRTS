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
    hold_contact_simulation,
)

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    load_simulation,
    load_simulation_data,
    save_simulation,
)
from airts.adapters.replay import (
    ReplayError,
    capture_replay,
    load_replay,
    load_replay_data,
    run_replay,
    save_replay,
)
from airts.contracts.automations import (
    AutomationStatus,
    DefendParameters,
    ProductionParameters,
)
from airts.contracts.commands import (
    AttackCommand,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    SetSelectionCommand,
)
from airts.contracts.spatial import Point, PointTarget, PolylineTarget, rectangle_region
from airts.simulation import Simulation
from airts.world.entities import UnitState
from airts.world.map import EntityKind, GameMap, load_example_map


def test_displaced_hold_round_trips_and_replays_before_anchor_recovery(tmp_path: Path) -> None:
    """Save during actual contact, so rebuilding an anchor from position cannot pass."""
    simulation = hold_contact_simulation()
    anchor = simulation.entities["holder"].position
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    result = simulation.execute(MoveCommand(("mover",), Point(12.5, 5.5)))
    assert result.accepted, (
        f"held bodies must not reject a statically reachable route: {result.reason}"
    )
    for _ in range(40):
        simulation.advance()
        if simulation.entities["holder"].position.distance_to(anchor) > 0.1:
            break
    assert simulation.entities["holder"].position.distance_to(anchor) > 0.1
    assert simulation.entities["holder"].state is UnitState.HOLDING
    assert simulation.entities["holder"].hold_anchor == anchor
    destination = tmp_path / "displaced-hold.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    replayed = run_replay(capture_replay(simulation))
    assert restored.export_state() == simulation.export_state()
    assert replayed.export_state() == simulation.export_state()

    for instance in (simulation, restored, replayed):
        assert instance.execute(MoveCommand(("mover",), Point(12.5, 9.5))).accepted
    for _ in range(20):
        _assert_continues_identically(simulation, restored, 5)
        replayed.advance(5)
        assert replayed.export_state() == simulation.export_state()
    assert simulation.entities["holder"].position.distance_to(anchor) <= 0.05
    assert simulation.entities["holder"].state is UnitState.HOLDING
    # The complete command-driven run must also reproduce the recovered state and events.
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


@pytest.mark.parametrize("schema", ["airts-save-v7", "airts-save-v8", "airts-save-v9"])
def test_legacy_hold_save_migrates_only_the_recorded_body_position(schema: str) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    state = simulation.export_state()
    saved_position = [7.5, 5.5]
    state["entities"]["holder"]["position"] = saved_position
    for entity in state["entities"].values():
        for field in (
            "hold_anchor",
            "contact_wait_ticks",
            "recovery_path",
            "recovery_retry_tick",
            "defend_arrived",
            "recovery_blocked_cells",
        ):
            entity.pop(field)
    restored = load_simulation_data(
        {"schema": schema, "map": simulation.game_map.to_dict(), "state": state}
    )
    assert restored.entities["holder"].hold_anchor == Point(*saved_position)
    restored.advance(40)
    assert restored.entities["holder"].position == Point(*saved_position)
    assert SAVE_SCHEMA == "airts-save-v10"


@pytest.mark.parametrize(
    "field,value",
    [
        ("hold_anchor", None),
        ("hold_anchor", [-1.0, 5.5]),
        ("contact_wait_ticks", -1),
        ("contact_wait_ticks", 9),
        ("recovery_retry_tick", -1),
        ("recovery_path", [[-1.0, 5.5]]),
        ("defend_arrived", 1),
        ("recovery_blocked_cells", [[-1, 5]]),
    ],
)
def test_new_save_rejects_corrupted_recovery_state(field: str, value: object) -> None:
    simulation = hold_contact_simulation()
    simulation.execute(HoldPositionCommand(("holder",)))
    state = simulation.export_state()
    state["entities"]["holder"][field] = value
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


def test_previous_replay_schema_is_explicitly_rejected() -> None:
    document = capture_replay(hold_contact_simulation()).to_dict()
    document["schema"] = "airts-replay-v8"
    with pytest.raises(ReplayError, match="unsupported replay schema"):
        load_replay_data(document)


def test_modified_automation_round_trips_without_a_new_lifecycle_transition(
    make_map: Callable[[int], GameMap], tmp_path: Path
) -> None:
    simulation = Simulation(make_map())
    created = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(5, 5))))
    simulation.advance(3)
    assert simulation.execute(
        ModifyAutomationCommand(
            created.automation_id or "",
            title="Edited",
            priority=4,
            target=PointTarget(Point(6, 6)),
        )
    ).accepted
    automation = simulation.automations[created.automation_id or ""]
    assert automation.modified_tick > automation.transition_history[-1].tick
    destination = tmp_path / "modified.json"
    save_simulation(simulation, destination)

    restored = load_simulation(destination)
    replayed = run_replay(capture_replay(simulation))
    assert restored.export_state() == simulation.export_state()
    assert replayed.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)
    replayed.advance(10)
    assert replayed.export_state() == simulation.export_state()


def test_load_rejects_modified_tick_before_the_last_transition(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map())
    created = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(5, 5))))
    simulation.advance(3)
    simulation.execute(PauseAutomationCommand(created.automation_id or ""))
    state = simulation.export_state()
    state["automations"][created.automation_id]["modified_tick"] = 2
    with pytest.raises(PersistenceError, match="modified tick"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


def test_expanded_ordinary_defense_round_trips_and_continues(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    created = simulation.execute(
        CreateDefendCommand(("unit_01", "unit_02"), rectangle_region(Point(5, 5), Point(5.1, 5.1)))
    )
    assert created.accepted
    parameters = simulation.automations[created.automation_id or ""].parameters
    assert isinstance(parameters, DefendParameters)
    assert not parameters.gathering_point
    assert len(parameters.deployment_slots) == 2
    simulation.advance(3)
    restored = load_simulation_data(
        {
            "schema": SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    assert restored.export_state() == simulation.export_state()
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)


@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        pytest.param("duplicate", "deployment slots are invalid", id="duplicate-slots"),
        pytest.param("outside", "impassable deployment slot", id="outside-map"),
        pytest.param("radius", "radius excludes a station", id="insufficient-radius"),
    ],
)
def test_load_rejects_malformed_expanded_defense(
    make_map: Callable[[int], GameMap], corruption: str, reason: str
) -> None:
    simulation = Simulation(make_map(2))
    created = simulation.execute(
        CreateDefendCommand(("unit_01", "unit_02"), rectangle_region(Point(5, 5), Point(5.1, 5.1)))
    )
    assert created.accepted
    state = simulation.export_state()
    parameters = state["automations"][created.automation_id]["parameters"]
    if corruption == "duplicate":
        parameters["deployment_slots"][1] = parameters["deployment_slots"][0]
    elif corruption == "outside":
        parameters["deployment_slots"][0] = [-1, -1]
    else:
        parameters["assembly_radius"] = 0.0
    with pytest.raises(PersistenceError, match=reason):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


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
