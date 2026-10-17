"""Complete-state save/load and continuation across runtime scenarios."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
    economy_combat_simulation,
    gated_attack_simulation,
    growing_factory_simulation,
    hold_contact_simulation,
    injected_overlap_simulation,
    runtime_map,
)

from airts.adapters.persistence import (
    SAVE_SCHEMA,
    PersistenceError,
    capture_save,
    load_simulation,
    load_simulation_data,
    save_simulation,
    write_save,
)
from airts.adapters.replay import (
    capture_replay,
    load_replay,
    run_replay,
    save_replay,
)
from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
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
    RemoveEntityCommand,
    SetDeveloperModeCommand,
    SetSelectionCommand,
)
from airts.contracts.spatial import Point, PointTarget, PolylineTarget, rectangle_region
from airts.navigation.collision.geometry import collision_radius
from airts.navigation.spatial_index import SpatialIndex
from airts.simulation import Simulation
from airts.world.entities import UnitState
from airts.world.map import GameMap, load_example_map, load_map_data
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize("match", [False, True], ids=["sandbox", "match"])
def test_developer_mode_save_retains_partial_second_and_replays(
    match: bool, tmp_path: Path
) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "developer_mode_save",
                "name": "Developer mode save",
                "width": 32,
                "height": 16,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "player_center", "kind": "command_center", "position": [0, 0]},
                    {
                        "id": "enemy_center",
                        "kind": "command_center",
                        "owner": "enemy",
                        "position": [24, 0],
                    },
                ],
                **(
                    {
                        "match_slots": [
                            {"owner": "player", "controller": "player", "team": 1},
                            {"owner": "enemy", "controller": "ai", "team": 2},
                        ]
                    }
                    if match
                    else {}
                ),
            }
        )
    )
    simulation.advance(3)
    assert simulation.execute(SetDeveloperModeCommand(True)).accepted
    simulation.advance(7)
    balance = simulation.resources["player"]
    path = tmp_path / "developer-mode.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    assert restored.developer_mode
    assert restored.developer_income_ticks == 7
    assert restored.export_state() == simulation.export_state()
    for instance in (simulation, restored):
        instance.advance(2)
        assert instance.resources["player"] == balance
        instance.advance()
        assert instance.resources["player"] == balance + 2000
        assert instance.execute(SetDeveloperModeCommand(False)).accepted
        instance.advance(20)
        assert instance.resources["player"] == balance + 2000
    assert restored.export_state() == simulation.export_state()
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


@pytest.mark.parametrize(
    ("mode", "ticks"),
    [(1, 0), (None, 0), (True, True), (True, -1), (True, 10), (True, 6), (False, 1)],
)
def test_save_rejects_invalid_developer_income_state(mode: object, ticks: object) -> None:
    simulation = builder_factory_simulation()
    simulation.advance(5)
    state = simulation.export_state()
    state.update(developer_mode=mode, developer_income_ticks=ticks)
    with pytest.raises(PersistenceError, match="developer"):
        load_simulation_data(
            {
                "schema": SAVE_SCHEMA,
                "map": simulation.game_map.to_dict(),
                "state": state,
            }
        )


def test_save_capture_defers_packed_history_and_isolated_state_survives_play(
    tmp_path: Path,
) -> None:
    simulation = builder_factory_simulation()
    simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    simulation.events.record_path(
        simulation.tick, "builder", source="test", cost=2, cells=((2, 2), (3, 2), (4, 2))
    )
    expected = simulation.export_state()
    # Capturing must not expand historical paths on the application's thread.
    with patch("airts.contracts.events.Event.to_dict", side_effect=AssertionError("expanded")):
        snapshot = capture_save(simulation)
    simulation.advance(3)
    simulation.resources["player"] = 1
    simulation.execute(HoldPositionCommand(("builder",)))
    path = tmp_path / "captured.json"
    write_save(snapshot, path)
    restored = load_simulation(path)
    assert restored.export_state() == expected
    assert restored.tick < simulation.tick
    restored.advance(3)
    assert restored.tick == simulation.tick


@pytest.mark.parametrize("failure", ["encoding", "replace"])
def test_atomic_save_failure_preserves_previous_file_and_cleans_temporary(
    tmp_path: Path, failure: str
) -> None:
    simulation = builder_factory_simulation()
    path = tmp_path / "existing.json"
    save_simulation(simulation, path)
    before = path.read_bytes()
    simulation.resources["player"] = 1
    target = "json.dumps" if failure == "encoding" else "os.replace"
    with patch(f"airts.adapters.persistence.{target}", side_effect=OSError("disk failure")):
        with pytest.raises(OSError, match="disk failure"):
            save_simulation(simulation, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_blocked_attack_continues_after_save_load_and_gate_removal(tmp_path: Path) -> None:
    simulation, blocked, reachable = gated_attack_simulation()
    assert simulation.execute(AttackCommand(blocked + reachable, "target")).accepted
    simulation.advance(8)
    path = tmp_path / "blocked-attack.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    assert restored.export_state() == simulation.export_state()
    for current in (simulation, restored):
        current._routes.clear()
        assert current.execute(RemoveEntityCommand("gate")).accepted
        current.advance(30)
    assert restored.export_state() == simulation.export_state()
    dispatched = {
        event.subject_id
        for event in simulation.events.events
        if event.event_type.value == "path_computed"
    }
    assert set(blocked + reachable) <= dispatched
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


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


@pytest.mark.parametrize("unit_count", [100, 400])
def test_injected_coincident_recovery_save_continues_through_internal_squad_exit(
    unit_count: int, tmp_path: Path
) -> None:
    """Injection is test setup; complete saves preserve a recovery already in progress."""
    simulation, entity_ids, selected = injected_overlap_simulation(unit_count, 0.0)
    initial_positions = {key: simulation.entities[key].position for key in entity_ids}
    assert simulation.execute(MoveCommand(selected, Point(75, 48))).accepted
    destinations = {key: simulation.entities[key].move_target for key in selected}
    destination_envelope = max(
        target.distance_to(Point(75, 48)) for target in destinations.values() if target is not None
    )
    simulation.advance(4)
    assert any(simulation.entities[key].position != initial_positions[key] for key in entity_ids), (
        "save must be taken during actual body recovery"
    )
    assert any(simulation.entities[key].path for key in selected)
    destination = tmp_path / f"coincident-recovery-{unit_count}.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()

    for _ in range(40):
        _assert_continues_identically(simulation, restored, 10)
    for key, target in destinations.items():
        assert target is not None
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == key and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(target) <= 1e-8
        # An early arrival becomes Idle and may yield to the remaining squad.
        assert simulation.entities[key].position.distance_to(Point(75, 48)) <= (
            destination_envelope + 0.6
        )
        assert not simulation.entities[key].path
        assert simulation.entities[key].position.distance_to(initial_positions[key]) > 20
    assert set(simulation.entities) == set(entity_ids)
    index = SpatialIndex({key: entity.position for key, entity in simulation.entities.items()})
    for first, second in index.candidate_pairs(0.6):
        a, b = simulation.entities[first], simulation.entities[second]
        assert a.position.distance_to(b.position) >= (
            collision_radius(a.kind) + collision_radius(b.kind) - 1e-6
        )
    rested = {key: simulation.entities[key].position for key in entity_ids}
    _assert_continues_identically(simulation, restored, 40)
    assert all(
        simulation.entities[key].position.distance_to(position) <= 1e-6
        for key, position in rested.items()
    ), "recovered bodies must settle after the squad has completed its exit"


def test_thousand_unit_dense_recovery_save_preserves_bounded_continuation(tmp_path: Path) -> None:
    """The largest recovery also rebuilds all disposable caches without trajectory drift."""
    simulation, entity_ids, selected = injected_overlap_simulation(1_000, 0.3)
    assert simulation.execute(HoldPositionCommand(entity_ids)).accepted
    anchors = {
        key: simulation.entities[key].hold_anchor for key in entity_ids if key not in selected
    }
    assert simulation.execute(MoveCommand(selected, Point(75, 48))).accepted
    destinations = {key: simulation.entities[key].move_target for key in selected}
    simulation.advance(4)
    destination = tmp_path / "thousand-dense-recovery.json"
    save_simulation(simulation, destination)
    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 40)
    assert all(simulation.entities[key].hold_anchor == anchor for key, anchor in anchors.items())
    assert all(simulation.entities[key].state is UnitState.HOLDING for key in anchors)
    while simulation.tick < 600 and any(simulation.entities[key].path for key in selected):
        _assert_continues_identically(simulation, restored, min(10, 600 - simulation.tick))
    for key, target in destinations.items():
        assert target is not None
        assert not simulation.entities[key].path, f"{key} did not complete within 600 ticks"
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == key and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(target) <= 1e-8
    passive_frontier = max(
        simulation.entities[key].position.x + collision_radius(simulation.entities[key].kind)
        for key in anchors
    )
    assert all(
        simulation.entities[key].position.x - collision_radius(simulation.entities[key].kind)
        > passive_frontier
        for key in selected
    )
    index = SpatialIndex({key: entity.position for key, entity in simulation.entities.items()})
    maximum_radius = max(collision_radius(entity.kind) for entity in simulation.entities.values())
    for first, second in index.candidate_pairs(maximum_radius * 2):
        assert (
            simulation.entities[first].position.distance_to(simulation.entities[second].position)
            >= collision_radius(simulation.entities[first].kind)
            + collision_radius(simulation.entities[second].kind)
            - 1e-6
        )
    assert all(simulation.entities[key].hold_anchor == anchor for key, anchor in anchors.items())
    assert all(simulation.entities[key].state is UnitState.HOLDING for key in anchors)
    rested = {key: simulation.entities[key].position for key in entity_ids}
    _assert_continues_identically(simulation, restored, 40)
    assert all(
        simulation.entities[key].position.distance_to(position) <= 0.08
        for key, position in rested.items()
    ), "conflicting anchors must wait safely after the selected squad has left"


@pytest.mark.parametrize(
    ("request_tick", "released"), [(None, False), (0, False), (0, True), (3, True)]
)
def test_current_save_round_trips_return_request_age_and_release_phase(
    request_tick: int | None, released: bool
) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    simulation.tick = 3
    holder = simulation.entities["holder"]
    holder.recovery_request_tick = request_tick
    holder.recovery_released = released
    restored = load_simulation_data(
        {
            "schema": SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    assert restored.export_state() == simulation.export_state()


def test_current_save_preserves_pending_hold_progress_and_continues_without_a_player_route() -> (
    None
):
    """The internal weak return tracker affects the saved deterministic trajectory."""
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    holder = simulation.entities["holder"]
    # Prepare a displaced, physically separated holder and an aged weak request.
    holder.position = Point(6.5, 5.5)
    simulation.occupancy.move("holder", holder.occupied_cells)
    simulation.tick = 40
    holder.recovery_request_tick = 0
    holder.progress_target = holder.hold_anchor
    holder.progress_distance = 1.0
    holder.no_progress_ticks = 31
    holder.route_ticks = 62
    restored = load_simulation_data(
        {
            "schema": SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    assert restored.export_state() == simulation.export_state()
    assert not restored.entities["holder"].path
    assert restored.entities["holder"].route_ticks == 62
    _assert_continues_identically(simulation, restored, 10)
    assert holder.position.distance_to(Point(5.5, 5.5)) <= 0.05


@pytest.mark.parametrize("fault", ["wrong-anchor", "missing-request"])
def test_hold_internal_progress_requires_the_current_saved_request_owner(fault: str) -> None:
    simulation = hold_contact_simulation()
    assert simulation.execute(HoldPositionCommand(("holder",))).accepted
    holder = simulation.entities["holder"]
    holder.recovery_request_tick = 0
    holder.progress_target = Point(6.5, 5.5) if fault == "wrong-anchor" else holder.hold_anchor
    holder.progress_distance = 1.0
    holder.no_progress_ticks = 31
    holder.route_ticks = 62
    if fault == "missing-request":
        holder.clear_recovery_request()
    with pytest.raises(PersistenceError, match="progress target must match its next waypoint"):
        load_simulation_data(
            {
                "schema": SAVE_SCHEMA,
                "map": simulation.game_map.to_dict(),
                "state": simulation.export_state(),
            }
        )


@pytest.mark.parametrize("field", ["recovery_request_tick", "recovery_released"])
def test_current_save_requires_explicit_return_phase_fields(field: str) -> None:
    simulation = hold_contact_simulation()
    state = simulation.export_state()
    state["entities"]["holder"].pop(field)
    with pytest.raises(PersistenceError, match="required in v11"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize("owner", ["idle", "move", "building", "unarrived-defend", "paused"])
def test_current_save_rejects_return_requests_without_a_weak_business_owner(owner: str) -> None:
    simulation = Simulation(runtime_map()) if owner == "building" else hold_contact_simulation()
    key = "factory" if owner == "building" else "holder"
    entity = simulation.entities[key]
    if owner == "move":
        assert simulation.execute(MoveCommand((key,), Point(12.5, 5.5))).accepted
    elif owner in {"unarrived-defend", "paused"}:
        created = simulation.execute(CreateDefendCommand((key,), PointTarget(Point(8.5, 5.5))))
        assert created.accepted
        if owner == "paused":
            assert simulation.execute(PauseAutomationCommand(created.automation_id or "")).accepted
            entity.state = UnitState.DEFENDING
            entity.defend_arrived = True
    entity.recovery_request_tick = 0
    with pytest.raises(PersistenceError, match="no weak return owner"):
        load_simulation_data(
            {
                "schema": SAVE_SCHEMA,
                "map": simulation.game_map.to_dict(),
                "state": simulation.export_state(),
            }
        )


def test_current_save_preserves_arrived_defense_release_phase_at_its_station() -> None:
    simulation = hold_contact_simulation()
    entity = simulation.entities["holder"]
    created = simulation.execute(CreateDefendCommand(("holder",), PointTarget(entity.position)))
    assert created.accepted
    entity.defend_arrived = True
    entity.recovery_request_tick = 0
    entity.recovery_released = True
    restored = load_simulation_data(
        {
            "schema": SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    assert restored.export_state() == simulation.export_state()


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
        ("recovery_request_tick", -1),
        ("recovery_request_tick", True),
        ("recovery_request_tick", 0.0),
        ("recovery_request_tick", 1),
        ("recovery_released", 1),
        ("recovery_released", "true"),
        ("recovery_released", None),
        ("recovery_released", True),
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


@pytest.mark.parametrize(
    "entity_id,field,value",
    [
        ("builder", "move_queue", None),
        ("builder", "move_queue", [[-1, 4]]),
        ("builder", "move_queue", [[20.5, 20.5]]),
        ("builder", "move_queue", [[float("nan"), 4]]),
        ("factory", "move_queue", [[12, 12]]),
        ("scout", "attack_gather_point", [12, 12]),
        ("builder", "attack_gather_point", [12, 12]),
    ],
)
def test_save_rejects_invalid_movement_queue_and_attack_gathering_intent(
    entity_id: str, field: str, value: object
) -> None:
    simulation = builder_factory_simulation()
    state = simulation.export_state()
    state["entities"][entity_id][field] = value
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize("value", [True, -1, 0.5, 1, "0"])
def test_save_rejects_invalid_player_control_timestamp(value: object) -> None:
    simulation = builder_factory_simulation()
    state = simulation.export_state()
    state["entities"]["scout"]["last_player_control_tick"] = value
    with pytest.raises(PersistenceError, match="last_player_control_tick"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


@pytest.mark.parametrize("subject", ["missing", "builder", "factory", "enemy"])
def test_save_requires_player_control_timestamp_to_match_its_unit(subject: str) -> None:
    simulation = builder_factory_simulation()
    state = simulation.export_state()
    if subject == "missing":
        state["entities"]["scout"].pop("last_player_control_tick")
    elif subject == "enemy":
        state["entities"]["scout"]["owner"] = "enemy"
        state["entities"]["scout"]["last_player_control_tick"] = 0
    else:
        state["entities"][subject]["last_player_control_tick"] = 0
    with pytest.raises(PersistenceError, match="last_player_control_tick|player control timestamp"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


def test_save_rejects_direct_queue_conflicting_with_automation_control() -> None:
    simulation = builder_factory_simulation()
    assert simulation.execute(CreatePatrolCommand(("scout",), PointTarget(Point(12, 12)))).accepted
    state = simulation.export_state()
    state["entities"]["scout"]["move_queue"] = [[14, 14]]
    with pytest.raises(PersistenceError, match="conflicts with automation control"):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


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


@pytest.mark.parametrize(
    ("field", "value", "diagnostic"),
    [
        ("build_ticks", 5, "build_ticks differs from its unit profile"),
        ("progress_ticks", 21, "progress exceeds its build time"),
    ],
)
def test_load_rejects_inconsistent_production_timing(
    field: str,
    value: int,
    diagnostic: str,
) -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    simulation.advance(3)
    state = simulation.export_state()
    state["automations"][result.automation_id]["parameters"][field] = value
    with pytest.raises(PersistenceError, match=diagnostic):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


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
    construction_parameters = simulation.automations[construction.automation_id or ""].parameters
    assert isinstance(construction_parameters, ConstructionParameters)
    site = simulation.entities[construction_parameters.site_entity_id or ""]
    site.health -= 17
    save_simulation(simulation, destination)

    restored = load_simulation(destination)
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(
        simulation,
        restored,
        max(
            Simulation.CONSTRUCTION_BUILD_TICKS,
            EntityKind.SCOUT.profile.production_ticks + EntityKind.BUILDER.profile.production_ticks,
        ),
    )

    assert (
        restored.automations[construction.automation_id or ""].status is AutomationStatus.COMPLETED
    )
    assert restored.automations[construction.automation_id or ""].entity_ids == [
        "builder",
        "builder_2",
    ]
    assert restored.entities[site.entity_id].health == site.kind.profile.max_health - 17
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
    simulation.entities["unit_01"].collision_pressure = 3
    destination = tmp_path / "state.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.export_state() == simulation.export_state()
    assert restored.entities["unit_01"].collision_pressure == 3
    assert [item.automation_id for item in restored.live_automations] == [
        item.automation_id for item in simulation.live_automations
    ]

    _assert_continues_identically(simulation, restored, 25)


def test_terrain_speed_continues_after_save_and_replay(tmp_path: Path) -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "saved_terrain_speed",
                "name": "Saved terrain speed",
                "width": 24,
                "height": 12,
                "terrain": {
                    "default": "grass",
                    "rectangles": [[4, 0, 3, 12, "forest"], [7, 0, 4, 12, "road"]],
                },
                "entities": [{"id": "unit", "kind": "scout", "position": [2.5, 5.5]}],
            }
        )
    )
    destination = Point(20.5, 5.5)
    assert simulation.execute(MoveCommand(("unit",), destination)).accepted
    simulation.advance(7)
    path = tmp_path / "terrain-speed.json"
    save_simulation(simulation, path)
    restored = load_simulation(path)
    for current in (simulation, restored):
        unit = current.entities["unit"]
        start = unit.position
        assert current.game_map.terrain_at(start).value == "forest"
        current.advance()
        assert unit.position.distance_to(start) == pytest.approx(1 / 3)
    _assert_continues_identically(simulation, restored, 40)
    assert simulation.entities["unit"].position == destination
    assert not simulation.entities["unit"].path
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()


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

    _assert_continues_identically(
        simulation,
        restored,
        EntityKind.SCOUT.profile.production_ticks + EntityKind.LIGHT_TANK.profile.production_ticks,
    )
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
    simulation.advance(EntityKind.LIGHT_TANK.profile.production_ticks * 2 + 1)
    destination = tmp_path / "continuous-defense.json"

    save_simulation(simulation, destination)
    restored = load_simulation(destination)

    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 10)


@pytest.mark.parametrize(
    "stage", ["traveling", "waiting_for_money", "paused_site", "destroyed_builder"]
)
@pytest.mark.parametrize("building_kind", [EntityKind.REPAIR_HUB, EntityKind.TURRET])
def test_blueprint_and_unfinished_site_state_continues_after_loading(
    stage: str, building_kind: EntityKind
) -> None:
    simulation = builder_factory_simulation()
    simulation.resources["player"] = 0 if stage in {"traveling", "waiting_for_money"} else 500
    result = simulation.execute(
        CreateConstructionCommand("builder", building_kind, Point(16, 14), queued=True)
    )
    simulation.execute(
        CreateConstructionCommand(
            "builder", EntityKind.RESOURCE_EXTRACTOR, Point(6, 10), queued=True
        )
    )
    simulation.advance(1 if stage == "traveling" else 40)
    if stage == "paused_site":
        assert simulation.execute(PauseAutomationCommand(result.automation_id or "")).accepted
    elif stage == "destroyed_builder":
        simulation.remove_entity("builder")
    elif stage == "waiting_for_money":
        assert (
            simulation.automations[result.automation_id or ""].reason_code
            == "INSUFFICIENT_RESOURCES"
        )
    restored = load_simulation_data(
        {
            "schema": SAVE_SCHEMA,
            "map": simulation.game_map.to_dict(),
            "state": simulation.export_state(),
        }
    )
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 80)


@pytest.mark.parametrize(
    "corruption",
    [
        "negative_progress",
        "complete_progress",
        "wrong_site",
        "unpaid_site",
        "mismatched_progress",
        "missing_site_field",
    ],
)
def test_save_rejects_inconsistent_construction_site(corruption: str) -> None:
    simulation = builder_factory_simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    )
    simulation.advance()
    state = simulation.export_state()
    parameters = state["automations"][result.automation_id]["parameters"]
    site = state["entities"][parameters["site_entity_id"]]
    if corruption == "negative_progress":
        site["construction_progress"] = -1
    elif corruption == "complete_progress":
        site["construction_progress"] = 1
    elif corruption == "wrong_site":
        parameters["site_entity_id"] = "factory"
    elif corruption == "unpaid_site":
        parameters["cost_paid"] = False
    elif corruption == "mismatched_progress":
        parameters["construction_value"] += 1
    else:
        parameters.pop("site_entity_id")
    with pytest.raises(PersistenceError):
        load_simulation_data(
            {"schema": SAVE_SCHEMA, "map": simulation.game_map.to_dict(), "state": state}
        )


def _assert_continues_identically(original: Simulation, restored: Simulation, ticks: int) -> None:
    original.advance(ticks)
    restored.advance(ticks)
    assert restored.export_state() == original.export_state()


@pytest.mark.parametrize("gathering", [False, True], ids=["preserve", "gather"])
@pytest.mark.usefixtures("dense_production")
def test_growing_army_save_preserves_shared_cells_and_movement_continuation(
    gathering: bool,
) -> None:
    simulation, _ = growing_factory_simulation()
    simulation.advance(260)
    if gathering:
        selected = tuple(key for key, entity in simulation.entities.items() if entity.is_movable)
        assert simulation.execute(MoveCommand(selected, Point(35, 35), gathering=True)).accepted
        simulation.advance(5)
    document = {
        "schema": SAVE_SCHEMA,
        "map": simulation.game_map.to_dict(),
        "state": simulation.export_state(),
    }
    restored = load_simulation_data(document)
    assert restored.export_state() == simulation.export_state()
    _assert_continues_identically(simulation, restored, 20)
    assert "traffic_reservations" not in document["state"]
