"""Integration tests for deterministic command replay."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from airts.adapters.replay import (
    ReplayData,
    ReplayError,
    capture_replay,
    load_replay,
    load_replay_data,
    run_replay,
    save_replay,
)
from airts.contracts.automations import AutomationStatus, ProductionParameters
from airts.contracts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    HoldPositionCommand,
    MoveCommand,
)
from airts.contracts.spatial import Point, PointTarget
from airts.navigation.spatial_index import SpatialIndex
from airts.simulation import Simulation
from airts.world.map import GameMap, load_example_map, load_map_data
from airts.world.units.catalog import EntityKind


def test_internal_squad_exit_from_normal_hold_formation_replays(tmp_path: Path) -> None:
    """Legal initial discs and ordinary commands reproduce actual passage and final events."""
    entity_ids = tuple(f"scout_{index:03d}" for index in range(100))
    simulation = Simulation(
        load_map_data(
            {
                "id": "normal_hold_formation_replay",
                "name": "Normal Hold formation replay",
                "width": 64,
                "height": 48,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "position": [16.5 + index % 10, 16.5 + index // 10],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=193,
    )
    selected = tuple(entity_ids[index] for index in (44, 45, 54, 55))
    assert simulation.execute(HoldPositionCommand(entity_ids)).accepted
    assert simulation.execute(MoveCommand(selected, Point(45, 21))).accepted
    destinations = {key: simulation.entities[key].move_target for key in selected}
    simulation.advance(350)
    for key, target in destinations.items():
        assert target is not None
        completed = next(
            event
            for event in reversed(simulation.events.events)
            if event.subject_id == key and event.event_type.value == "movement_completed"
        )
        assert Point(*completed.details["position"]).distance_to(target) <= 1e-8
        assert simulation.entities[key].position.distance_to(target) <= 0.6
        assert not simulation.entities[key].path
    destination = tmp_path / "normal-internal-squad.json"
    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))
    assert replayed.export_state() == simulation.export_state()


def test_map_defined_overlapping_mobile_discs_recover_and_replay(tmp_path: Path) -> None:
    """Legacy map cell validity permits disc penetration across adjacent cell borders.

    These 25 independent four-body clusters are artificial initial geometry, not a
    normally produced army pile. Unlike runtime injection, their full setup is in
    the replay's map and can therefore be reproduced through the public adapter.
    """
    entity_ids = tuple(f"scout_{index:03d}" for index in range(100))
    simulation = Simulation(
        load_map_data(
            {
                "id": "initial_overlap_replay",
                "name": "Map-defined overlap recovery replay",
                "width": 64,
                "height": 48,
                "terrain": {"default": "grass"},
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "position": [
                            16 + (index // 4) % 5 * 3 + (-0.05 if index % 2 == 0 else 0.05),
                            16 + (index // 4) // 5 * 3 + (-0.05 if index % 4 < 2 else 0.05),
                        ],
                    }
                    for index, entity_id in enumerate(entity_ids)
                ],
            }
        ),
        random_seed=197,
    )
    assert (
        simulation.entities[entity_ids[0]].position.distance_to(
            simulation.entities[entity_ids[1]].position
        )
        < 0.2 * 0.6
    )
    selected = entity_ids[48:52]
    assert simulation.execute(MoveCommand(selected, Point(48, 22))).accepted
    destinations = {key: simulation.entities[key].move_target for key in selected}
    simulation.advance(4)
    # Verify an in-progress recovery replay as well as the completed run.
    assert run_replay(capture_replay(simulation)).export_state() == simulation.export_state()
    simulation.advance(300)
    for key, target in destinations.items():
        assert target is not None
        assert simulation.entities[key].position.distance_to(target) <= 0.05
    index = SpatialIndex({key: entity.position for key, entity in simulation.entities.items()})
    for first, second in index.candidate_pairs(0.6):
        assert (
            simulation.entities[first].position.distance_to(simulation.entities[second].position)
            >= 0.6 - 1e-6
        )
    destination = tmp_path / "map-overlap-recovery.json"
    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))
    assert replayed.export_state() == simulation.export_state()


@pytest.mark.parametrize("gathering", [False, True], ids=["preserve", "gather"])
def test_recorded_commands_reproduce_state_and_events(
    make_map: Callable[[int], GameMap], tmp_path: Path, gathering: bool
) -> None:
    simulation = Simulation(make_map(2), random_seed=17)
    assert simulation.execute(
        MoveCommand(("unit_01", "unit_02"), Point(5.5, 3.5), gathering=gathering)
    ).accepted
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
    assert replayed.export_state() == simulation.export_state()


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


def test_replay_preserves_defense_and_completed_production(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=23)
    initial_position = simulation.entities["tank_01"].position
    defense = simulation.execute(
        CreateDefendCommand(("tank_01",), PointTarget(Point(20, 30), radius=3))
    )
    production = simulation.execute(
        CreateProductionCommand("factory_01", EntityKind.LIGHT_TANK, 1, Point(20, 35))
    )
    assert defense.accepted and defense.automation_id is not None
    assert production.accepted and production.automation_id is not None
    simulation.advance(25)
    assert simulation.assignments["tank_01"] == defense.automation_id
    assert simulation.entities["tank_01"].position != initial_position
    automation = simulation.automations[production.automation_id]
    assert automation.status is AutomationStatus.COMPLETED
    parameters = automation.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.produced_count == len(parameters.produced_entity_ids) == 1
    produced = simulation.entities[parameters.produced_entity_ids[0]]
    assert produced.kind is EntityKind.LIGHT_TANK and produced.owner_id == "player"
    destination = tmp_path / "defense-production-replay.json"

    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))

    assert replayed.export_state() == simulation.export_state()


def test_replay_records_authoritative_entity_removal(tmp_path: Path) -> None:
    simulation = Simulation(load_example_map(), random_seed=29)
    production = simulation.execute(CreateProductionCommand("factory_01", EntityKind.SCOUT, 2))
    simulation.remove_entity("factory_01", "DESTROYED")
    destination = tmp_path / "removal-replay.json"

    save_replay(simulation, destination)
    replayed = run_replay(load_replay(destination))

    assert replayed.snapshot() == simulation.snapshot()
    assert replayed.automations[production.automation_id or ""].status.value == "failed"


@pytest.mark.parametrize("version", [6, 7, 8, 9, 10, 11, 12], ids=lambda version: f"v{version}")
def test_replay_rejects_unsupported_previous_schemas(version: int) -> None:
    schema = f"airts-replay-v{version}"
    with pytest.raises(ReplayError, match=f"unsupported replay schema: {schema}"):
        load_replay_data({"schema": schema})
