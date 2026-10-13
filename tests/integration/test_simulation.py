"""Integration tests for the public simulation facade."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from tests.integration.scenarios import interaction_simulation

from airts.contracts.automations import AutomationStatus
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreatePatrolCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.contracts.events import EventType
from airts.contracts.spatial import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialTarget,
)
from airts.simulation import Simulation
from airts.world.map import GameMap, load_map_data


def test_crossing_units_recover_and_are_deterministic() -> None:
    first = interaction_simulation()
    second = interaction_simulation()
    commands = (
        MoveCommand(("unit",), Point(9.5, 2.5)),
        MoveCommand(("tank",), Point(2.5, 8.5)),
    )
    for simulation in (first, second):
        for command in commands:
            assert simulation.execute(command).accepted
        simulation.advance(120)

    assert not first.entities["unit"].path
    assert not first.entities["tank"].path
    assert first.snapshot() == second.snapshot()


def test_manual_movement_reaches_target_on_fixed_ticks(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    assert simulation.occupancy.cells_for("unit_01") == frozenset({(1, 1)})

    result = simulation.execute(MoveCommand(("unit_01",), Point(6.5, 1.5)))
    simulation.advance(10)

    assert result.accepted
    assert simulation.entities["unit_01"].position == Point(6.5, 1.5)
    assert simulation.occupancy.cells_for("unit_01") == frozenset({(6, 1)})
    assert "unit_01" not in simulation.occupancy.occupants((1, 1))
    assert any(
        event.event_type is EventType.MOVEMENT_COMPLETED for event in simulation.events.events
    )


def test_unreachable_move_is_rejected_without_partial_movement() -> None:
    game_map = load_map_data(
        {
            "id": "obstacle",
            "name": "Obstacle",
            "width": 8,
            "height": 6,
            "terrain": {"default": "grass", "rectangles": [[3, 0, 1, 6, "water"]]},
            "entities": [{"id": "unit", "kind": "scout", "position": [1.5, 2.5]}],
        }
    )
    simulation = Simulation(game_map)

    result = simulation.execute(MoveCommand(("unit",), Point(5.5, 2.5)))
    assert not result.accepted
    assert result.reason == "NO_PATH"
    assert simulation.entities["unit"].position == Point(1.5, 2.5)
    assert any(
        event.event_type is EventType.PATHFINDING_FAILED for event in simulation.events.events
    )


def test_invalid_command_does_not_mutate_world_state(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    before = simulation.snapshot()

    result = simulation.execute(MoveCommand(("missing",), Point(4, 4)))

    assert not result.accepted
    assert result.reason == "UNKNOWN_ENTITY:missing"
    assert simulation.snapshot() == before
    assert simulation.events.events[-1].event_type is EventType.COMMAND_REJECTED


def test_building_cannot_receive_a_movement_command() -> None:
    game_map = load_map_data(
        {
            "id": "building",
            "name": "Building",
            "width": 10,
            "height": 10,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [{"id": "factory", "kind": "factory", "position": [2, 2]}],
        }
    )
    simulation = Simulation(game_map)

    result = simulation.execute(MoveCommand(("factory",), Point(8, 8)))

    assert not result.accepted
    assert result.reason == "ENTITY_NOT_MOVABLE:factory"


@pytest.mark.parametrize(
    "target",
    [
        PointTarget(Point(5, 5), radius=2),
        PolylineTarget((Point(2, 2), Point(6, 2), Point(6, 6))),
        PolygonRegion((Point(3, 3), Point(8, 3), Point(8, 8), Point(3, 8))),
    ],
    ids=["point", "line", "area"],
)
def test_each_patrol_geometry_runs_across_ticks(
    make_map: Callable[[int], GameMap], target: SpatialTarget
) -> None:
    simulation = Simulation(make_map(1))
    start = simulation.entities["unit_01"].position

    result = simulation.execute(CreatePatrolCommand(("unit_01",), target))
    simulation.advance(30)

    assert result.accepted
    assert simulation.entities["unit_01"].position != start
    assert simulation.assignments["unit_01"] == result.automation_id
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.ACTIVE


def test_patrol_can_be_paused_resumed_and_canceled(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    created = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(5, 5))))
    automation_id = created.automation_id or ""

    assert simulation.execute(PauseAutomationCommand(automation_id)).accepted
    paused_position = simulation.entities["unit_01"].position
    simulation.advance(10)
    assert simulation.entities["unit_01"].position == paused_position
    assert simulation.automations[automation_id].status is AutomationStatus.PAUSED

    assert simulation.execute(ResumeAutomationCommand(automation_id)).accepted
    simulation.advance(10)
    assert simulation.entities["unit_01"].position != paused_position

    assert simulation.execute(CancelAutomationCommand(automation_id)).accepted
    assert simulation.automations[automation_id].status is AutomationStatus.CANCELED
    assert "unit_01" not in simulation.assignments


@pytest.mark.parametrize("entity_count", [1, 2], ids=["last-member", "partial-group"])
def test_manual_move_releases_patrol_membership(
    make_map: Callable[[int], GameMap], entity_count: int
) -> None:
    simulation = Simulation(make_map(entity_count))
    entity_ids = tuple(simulation.entities)
    created = simulation.execute(CreatePatrolCommand(entity_ids, PointTarget(Point(6, 6))))
    assert created.accepted
    assert created.automation_id is not None
    automation = simulation.automations[created.automation_id]

    assert simulation.execute(MoveCommand(("unit_01",), Point(3, 3))).accepted

    assert "unit_01" not in simulation.assignments
    assert automation.entity_ids == list(entity_ids[1:])
    overrides = simulation.events.query(
        event_types=frozenset({EventType.MANUAL_OVERRIDE}), subject_id="unit_01"
    )
    assert len(overrides) == 1
    if entity_count == 1:
        assert automation.status is AutomationStatus.CANCELED
        assert automation.reason_code == "NO_ASSIGNED_ENTITIES"
    else:
        assert simulation.assignments["unit_02"] == created.automation_id
        assert automation.status is AutomationStatus.ACTIVE


def test_invalid_patrol_geometry_cannot_mutate_world_state(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    before = simulation.snapshot()

    result = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(30, 30))))

    assert not result.accepted
    assert simulation.snapshot() == before
    assert not simulation.automations


def test_group_move_allocates_distinct_destination_cells(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))

    result = simulation.execute(MoveCommand(("unit_01", "unit_02"), Point(7.5, 7.5)))
    simulation.advance(30)

    assert result.accepted
    assert simulation.occupancy.cells_for("unit_01") != simulation.occupancy.cells_for("unit_02")


def test_building_footprint_is_a_hard_pathfinding_obstacle() -> None:
    game_map = load_map_data(
        {
            "id": "building_path",
            "name": "Building Path",
            "width": 13,
            "height": 9,
            "terrain": {"default": "grass", "rectangles": []},
            "entities": [
                {"id": "unit", "kind": "scout", "position": [1.5, 4.5]},
                {"id": "factory", "kind": "factory", "position": [4, 2]},
            ],
        }
    )
    simulation = Simulation(game_map)

    result = simulation.execute(MoveCommand(("unit",), Point(10.5, 4.5)))
    path_cells = {(int(point.x), int(point.y)) for point in simulation.entities["unit"].path}

    assert result.accepted
    assert path_cells.isdisjoint(simulation.occupancy.cells_for("factory"))
