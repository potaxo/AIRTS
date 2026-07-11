from __future__ import annotations

from airts.app import AirtsApp, InputMode
from airts.automations import AutomationStatus
from airts.commands import (
    CreatePatrolCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    MoveCommand,
    SetSelectionCommand,
    command_from_dict,
    command_to_dict,
)
from airts.geometry import Point, PolylineTarget, rectangle_region
from airts.map_model import load_map_data
from airts.simulation import Simulation


def _interaction_simulation() -> Simulation:
    return Simulation(
        load_map_data(
            {
                "id": "interaction",
                "name": "Interaction",
                "width": 16,
                "height": 16,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {"id": "unit", "kind": "scout", "owner": "player", "position": [2.5, 2.5]},
                    {"id": "tank", "kind": "light_tank", "owner": "player", "position": [4.5, 2.5]},
                    {"id": "factory", "kind": "factory", "owner": "player", "position": [3, 3]},
                    {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [12.5, 12.5]},
                    {
                        "id": "generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [10, 3],
                    },
                ],
            }
        ),
        random_seed=7,
    )


def test_drag_selection_chooses_only_friendly_units_and_keeps_grounding() -> None:
    simulation = _interaction_simulation()
    app = AirtsApp(simulation)
    region = simulation.execute(
        CreateSpatialReferenceCommand(rectangle_region(Point(1, 1), Point(8, 8)))
    )
    app.selected_regions = {region.reference_id or ""}

    app._select_entities(Point(1, 1), Point(8, 8))

    assert app.selected_entities == {"unit", "tank"}
    assert app.selected_regions == {region.reference_id}
    assert simulation.selection.entity_ids == ("tank", "unit")
    assert simulation.selection.region_ids == (region.reference_id,)


def test_line_finishes_with_right_click_and_preserves_selected_units() -> None:
    simulation = _interaction_simulation()
    app = AirtsApp(simulation)
    app.selected_entities = {"unit"}
    app.mode = InputMode.LINE
    app._handle_mouse_down(1, (80, 80))
    app._handle_mouse_down(1, (120, 120))

    app._handle_mouse_down(3, (140, 140))

    assert app.mode is InputMode.SELECT
    assert app.selected_entities == {"unit"}
    assert simulation.selection.entity_ids == ("unit",)
    assert simulation.selection.route_ids == ("route_001",)
    assert simulation.spatial.references["route_001"].geometry == PolylineTarget(
        (app._map_point((80, 80)), app._map_point((120, 120)))
    )


def test_line_enter_does_not_finish_and_escape_cancels() -> None:
    app = AirtsApp(_interaction_simulation())
    app.mode = InputMode.LINE
    app._handle_mouse_down(1, (80, 80))
    app._handle_mouse_down(1, (120, 120))

    app._handle_key(13)

    assert app.mode is InputMode.LINE
    assert len(app.line_points) == 2

    app._handle_key(27)

    assert app.mode is InputMode.LINE
    assert not app.line_points


def test_region_deletion_is_replayable_and_cancels_affected_automation() -> None:
    simulation = _interaction_simulation()
    target = rectangle_region(Point(7, 7), Point(10, 10))
    region = simulation.execute(CreateSpatialReferenceCommand(target, "Defense"))
    patrol = simulation.execute(CreatePatrolCommand(("unit",), target))
    simulation.execute(SetSelectionCommand(("unit",), region_ids=(region.reference_id or "",)))
    command = DeleteRegionCommand(region.reference_id or "")

    result = simulation.execute(command)

    assert result.accepted
    assert "CANCELED" in result.reason
    assert region.reference_id not in simulation.spatial.references
    assert simulation.automations[patrol.automation_id or ""].status is AutomationStatus.CANCELED
    assert simulation.selection.entity_ids == ("unit",)
    assert not simulation.selection.region_ids
    assert command_from_dict(command_to_dict(command)) == command


def test_resource_generators_produce_without_an_automation() -> None:
    simulation = _interaction_simulation()

    simulation.advance(20)

    assert simulation.resources["player"] == 520


def test_crossing_units_recover_and_are_deterministic() -> None:
    first = _interaction_simulation()
    second = _interaction_simulation()
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


def test_dense_patrol_assigns_distributed_motion() -> None:
    simulation = _interaction_simulation()
    result = simulation.execute(
        CreatePatrolCommand(("unit", "tank"), rectangle_region(Point(7, 7), Point(10, 10)))
    )

    simulation.advance(80)

    assert result.accepted
    assert simulation.entities["unit"].position != simulation.entities["tank"].position
    assert simulation.automations[result.automation_id or ""].status is AutomationStatus.ACTIVE
