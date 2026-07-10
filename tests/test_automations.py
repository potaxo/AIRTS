from __future__ import annotations

from collections.abc import Callable

import pytest

from airts.automations import AutomationStatus, build_patrol_waypoints
from airts.commands import (
    CancelAutomationCommand,
    CreatePatrolCommand,
    MoveCommand,
    PauseAutomationCommand,
    ResumeAutomationCommand,
)
from airts.events import EventType
from airts.geometry import Point, PointTarget, PolygonRegion, PolylineTarget, SpatialTarget
from airts.map_model import GameMap
from airts.simulation import Simulation


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


def test_area_waypoints_are_deterministic_and_inside_region(
    make_map: Callable[[int], GameMap],
) -> None:
    game_map = make_map(1)
    region = PolygonRegion((Point(2, 2), Point(9, 2), Point(9, 8), Point(2, 8)))

    first = build_patrol_waypoints(region, game_map)
    second = build_patrol_waypoints(region, game_map)

    assert first == second
    assert 1 < len(first) <= 24
    assert all(region.contains(point) for point in first)


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


def test_manual_move_detaches_unit_and_records_override(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(2))
    created = simulation.execute(
        CreatePatrolCommand(("unit_01", "unit_02"), PointTarget(Point(6, 6)))
    )
    automation_id = created.automation_id or ""

    moved = simulation.execute(MoveCommand(("unit_01",), Point(3, 3)))

    assert moved.accepted
    assert "unit_01" not in simulation.assignments
    assert simulation.assignments["unit_02"] == automation_id
    assert simulation.automations[automation_id].entity_ids == ["unit_02"]
    assert any(event.event_type is EventType.MANUAL_OVERRIDE for event in simulation.events.events)


def test_override_of_last_unit_cancels_empty_patrol(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    created = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(6, 6))))
    automation_id = created.automation_id or ""

    simulation.execute(MoveCommand(("unit_01",), Point(3, 3)))

    automation = simulation.automations[automation_id]
    assert automation.status is AutomationStatus.CANCELED
    assert automation.reason_code == "NO_ASSIGNED_ENTITIES"


def test_invalid_patrol_geometry_cannot_mutate_world_state(
    make_map: Callable[[int], GameMap],
) -> None:
    simulation = Simulation(make_map(1))
    before = simulation.snapshot()

    result = simulation.execute(CreatePatrolCommand(("unit_01",), PointTarget(Point(30, 30))))

    assert not result.accepted
    assert simulation.snapshot() == before
    assert not simulation.automations
