from __future__ import annotations

from unittest.mock import Mock, call, patch

import pygame
import pytest

from airts.app import AirtsApp, InputMode
from airts.automations import AutomationStatus, DefendParameters, ProductionParameters
from airts.commands import (
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateSpatialReferenceCommand,
    DeleteRegionCommand,
    DeleteSpatialReferenceCommand,
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


def test_delete_control_removes_selected_route_and_cancels_its_patrol() -> None:
    simulation = _interaction_simulation()
    app = AirtsApp(simulation)
    target = PolylineTarget((Point(7, 7), Point(12, 7)))
    route = simulation.execute(CreateSpatialReferenceCommand(target))
    patrol = simulation.execute(CreatePatrolCommand(("unit",), target))
    app._select_reference(route.reference_id)

    app._delete_selected_reference()

    assert route.reference_id not in simulation.spatial.references
    assert simulation.automations[patrol.automation_id or ""].status is AutomationStatus.CANCELED
    assert not app.selected_routes
    command = DeleteSpatialReferenceCommand("route_001")
    assert command_from_dict(command_to_dict(command)) == command


def test_resource_generators_produce_without_an_automation() -> None:
    simulation = _interaction_simulation()

    simulation.advance(20)

    assert simulation.resources["player"] == 2500


def test_factory_and_area_interaction_creates_continuous_defense_production() -> None:
    simulation = _interaction_simulation()
    app = AirtsApp(simulation)
    target = rectangle_region(Point(8, 8), Point(13, 13))
    created_region = simulation.execute(CreateSpatialReferenceCommand(target, "Front Line"))
    factory_position = simulation.entities["factory"].selection_position

    app._select_entities(factory_position, factory_position)
    app._select_entities(Point(9, 9), Point(9, 9))

    app._create_production()

    assert app.selected_entities == {"factory"}
    assert app.selected_regions == {created_region.reference_id}
    automation = simulation.automations[app.selected_automation_id or ""]
    assert isinstance(automation.parameters, ProductionParameters)
    assert automation.parameters.continuous
    assert automation.parameters.defend_target == target


def test_gathering_glow_radius_grows_with_the_authoritative_assembly_radius() -> None:
    simulation = _interaction_simulation()
    target = rectangle_region(Point(8, 8), Point(12, 12))
    created = simulation.execute(
        CreateDefendCommand(("unit", "tank"), target, gathering_point=True)
    )
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    app = AirtsApp(simulation)
    screen = pygame.Surface(app.WINDOW_SIZE)

    with patch("airts.app.pygame.draw.circle", wraps=pygame.draw.circle) as draw_circle:
        app._draw_assembly_glows(screen)
    initial_radius = max(call.args[3] for call in draw_circle.call_args_list)

    automation.parameters.assembly_radius += 4
    with patch("airts.app.pygame.draw.circle", wraps=pygame.draw.circle) as draw_circle:
        app._draw_assembly_glows(screen)
    expanded_radius = max(call.args[3] for call in draw_circle.call_args_list)

    assert expanded_radius > initial_radius


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


def test_window_close_exits_before_another_tick_or_render_and_releases_resources() -> None:
    simulation = _interaction_simulation()
    app = AirtsApp(simulation)
    lifecycle = Mock()
    clock = Mock()
    clock.tick.return_value = 100
    screen = Mock()

    with (
        patch("airts.app.pygame.init") as global_init,
        patch("airts.app.pygame.display.init", side_effect=lifecycle.display_init),
        patch("airts.app.pygame.font.init", side_effect=lifecycle.font_init),
        patch("airts.app.pygame.display.set_mode", return_value=screen),
        patch("airts.app.pygame.display.set_caption"),
        patch("airts.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.app.pygame.time.Clock", return_value=clock),
        patch(
            "airts.app.pygame.event.get",
            return_value=(pygame.event.Event(pygame.QUIT),),
        ),
        patch("airts.app.pygame.event.clear", side_effect=lifecycle.event_clear),
        patch("airts.app.pygame.font.quit", side_effect=lifecycle.font_quit),
        patch("airts.app.pygame.display.quit", side_effect=lifecycle.display_quit),
        patch("airts.app.pygame.quit", side_effect=lifecycle.pygame_quit),
        patch("airts.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as draw,
    ):
        app.run()

    global_init.assert_not_called()
    draw.assert_not_called()
    flip.assert_not_called()
    assert simulation.tick == 0
    assert app._font is None
    assert app._small_font is None
    assert app._map_surface is None
    assert lifecycle.mock_calls == [
        call.display_init(),
        call.font_init(),
        call.event_clear(),
        call.font_quit(),
        call.display_quit(),
        call.pygame_quit(),
    ]


def test_render_failure_still_releases_pygame_resources_in_order() -> None:
    app = AirtsApp(_interaction_simulation())
    lifecycle = Mock()
    clock = Mock()
    clock.tick.return_value = 0

    with (
        patch("airts.app.pygame.init"),
        patch("airts.app.pygame.display.init", side_effect=lifecycle.display_init),
        patch("airts.app.pygame.font.init", side_effect=lifecycle.font_init),
        patch("airts.app.pygame.display.set_mode", return_value=Mock()),
        patch("airts.app.pygame.display.set_caption"),
        patch("airts.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.app.pygame.time.Clock", return_value=clock),
        patch("airts.app.pygame.event.get", return_value=()),
        patch("airts.app.pygame.event.clear", side_effect=lifecycle.event_clear),
        patch("airts.app.pygame.font.quit", side_effect=lifecycle.font_quit),
        patch("airts.app.pygame.display.quit", side_effect=lifecycle.display_quit),
        patch("airts.app.pygame.quit", side_effect=lifecycle.pygame_quit),
        patch.object(app, "_draw", side_effect=RuntimeError("render failed")),
    ):
        with pytest.raises(RuntimeError, match="render failed"):
            app.run(max_frames=1)

    assert lifecycle.mock_calls == [
        call.display_init(),
        call.font_init(),
        call.event_clear(),
        call.font_quit(),
        call.display_quit(),
        call.pygame_quit(),
    ]
