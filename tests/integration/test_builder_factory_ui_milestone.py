"""Integration contract for the builder, factory, and responsive UI milestone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pygame
import pytest

from airts.adapters.persistence import load_simulation, save_simulation
from airts.adapters.replay import load_replay, run_replay, save_replay
from airts.automations import (
    AutomationKind,
    AutomationStatus,
    ConstructionParameters,
    EconomyParameters,
    ProductionParameters,
)
from airts.commands import (
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
)
from airts.geometry import Point, PolylineTarget, rectangle_region
from airts.presentation.app import (
    REAL_FPS_FRAME_TIME_PERCENTILE,
    AirtsApp,
    PresentationProfiler,
    real_fps_from_frame_times,
)
from airts.simulation import Simulation
from airts.world.map_model import EntityKind, load_map_data
from airts.world.projectiles import Projectile


def _simulation() -> Simulation:
    game_map = load_map_data(
        {
            "id": "builder_factory_milestone",
            "name": "Builder and factory test map",
            "width": 24,
            "height": 24,
            "terrain": {
                "default": "grass",
                "rectangles": [[20, 20, 2, 2, "water"]],
            },
            "entities": [
                {"id": "factory", "kind": "factory", "position": [1, 1]},
                {"id": "factory_2", "kind": "factory", "position": [17, 1]},
                {"id": "builder", "kind": "builder", "position": [8.5, 8.5]},
                {"id": "builder_2", "kind": "builder", "position": [9.5, 8.5]},
                {"id": "scout", "kind": "scout", "position": [10.5, 8.5]},
                {"id": "tank", "kind": "light_tank", "position": [11.5, 8.5]},
            ],
        }
    )
    simulation = Simulation(game_map)
    simulation.resources["player"] = 10_000
    return simulation


def test_factory_can_produce_every_mobile_unit_kind_through_shared_logic() -> None:
    simulation = _simulation()

    for kind in (
        EntityKind.SCOUT,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
        EntityKind.BUILDER,
    ):
        before = simulation.resources["player"]
        result = simulation.execute(CreateProductionCommand("factory", kind, 1))
        assert result.accepted
        simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
        production = simulation.automations[result.automation_id or ""]
        parameters = production.parameters
        assert isinstance(parameters, ProductionParameters)
        assert production.status is AutomationStatus.COMPLETED
        assert simulation.entities[parameters.produced_entity_ids[0]].kind is kind
        assert simulation.resources["player"] == before - kind.profile.production_cost


def test_factory_loop_button_broadcasts_to_same_type_selection() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    button = pygame.Rect(10, 10, 80, 30)
    app._command_buttons = [(button, "loop:heavy_tank")]

    app._handle_command_click(button.center)

    loops = [app.simulation.continuous_production(item) for item in ("factory", "factory_2")]
    assert all(loop is not None for loop in loops)
    assert all(
        isinstance(loop.parameters, ProductionParameters)
        and loop.parameters.unit_kind is EntityKind.HEAVY_TANK
        for loop in loops
        if loop is not None
    )
    assert "2 factories" in app.notice


def test_ordered_factory_queue_broadcasts_to_same_type_selection() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    app.production_sequence = [(EntityKind.SCOUT, 2), (EntityKind.BUILDER, 1)]
    button = pygame.Rect(10, 10, 80, 30)
    app._command_buttons = [(button, "start_queue")]

    app._handle_command_click(button.center)

    for factory_id in ("factory", "factory_2"):
        queue = app.simulation.production_queue(factory_id)
        assert len(queue) == 1
        parameters = queue[0].parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.sequence == ((EntityKind.SCOUT, 2), (EntityKind.BUILDER, 1))
    assert not app.production_sequence
    assert "2 factories" in app.notice


def test_produce_and_defend_broadcasts_to_every_selected_factory_loop() -> None:
    simulation = _simulation()
    loops = [
        simulation.execute(
            CreateProductionCommand(factory_id, EntityKind.LIGHT_TANK, 1, continuous=True)
        )
        for factory_id in ("factory", "factory_2")
    ]
    app = AirtsApp(simulation)
    app.selected_entities = {"factory", "factory_2"}
    app._selection_changed()
    app.active_target = PolylineTarget((Point(6, 14), Point(18, 14)))

    app._create_production()

    for result in loops:
        production = simulation.automations[result.automation_id or ""]
        parameters = production.parameters
        assert isinstance(parameters, ProductionParameters)
        assert parameters.defend_target == app.active_target
    assert "2 factories" in app.notice


def test_economy_button_applies_to_every_selected_resource_generator() -> None:
    game_map = load_map_data(
        {
            "id": "multi_generator_ui",
            "name": "Multi-generator UI test map",
            "width": 12,
            "height": 12,
            "terrain": {"default": "grass"},
            "entities": [
                {"id": "generator_1", "kind": "resource_generator", "position": [1, 1]},
                {"id": "generator_2", "kind": "resource_generator", "position": [5, 1]},
            ],
        }
    )
    app = AirtsApp(Simulation(game_map))
    app.selected_entities = {"generator_1", "generator_2"}
    app._selection_changed()
    button = pygame.Rect(10, 10, 80, 30)
    app._command_buttons = [(button, "economy")]

    app._handle_command_click(button.center)

    automation = app.simulation.automations[app.selected_automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, EconomyParameters)
    assert parameters.generator_ids == ["generator_1", "generator_2"]


def test_finite_factory_batch_obeys_order_and_exact_quantities_then_leaves_live_list() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateProductionBatchCommand(
            "factory",
            ((EntityKind.LIGHT_TANK, 2), (EntityKind.HEAVY_TANK, 1), (EntityKind.SCOUT, 3)),
        )
    )
    assert result.accepted

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 6)

    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ProductionParameters)
    assert [simulation.entities[item].kind for item in parameters.produced_entity_ids] == [
        EntityKind.LIGHT_TANK,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
        EntityKind.SCOUT,
        EntityKind.SCOUT,
        EntityKind.SCOUT,
    ]
    assert automation.status is AutomationStatus.COMPLETED
    assert automation not in simulation.live_automations


def test_continuous_factory_production_repeats_one_kind_until_canceled() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.BUILDER, 1, continuous=True)
    )
    assert result.accepted

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 3)
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.produced_count == 3
    assert all(
        simulation.entities[item].kind is EntityKind.BUILDER
        for item in parameters.produced_entity_ids
    )
    assert automation.status is AutomationStatus.ACTIVE

    simulation.execute(CancelAutomationCommand(automation.automation_id))
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 2)
    assert parameters.produced_count == 3
    assert automation not in simulation.live_automations


def test_finite_player_queue_preempts_then_resumes_continuous_loop() -> None:
    simulation = _simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    simulation.advance(2)
    loop = simulation.automations[loop_result.automation_id or ""]
    loop_parameters = loop.parameters
    assert isinstance(loop_parameters, ProductionParameters)
    assert loop_parameters.progress_ticks == 2

    queue_result = simulation.execute(
        CreateProductionBatchCommand("factory", ((EntityKind.SCOUT, 1), (EntityKind.BUILDER, 1)))
    )
    assert queue_result.accepted
    assert simulation.production_queue("factory")[0].automation_id == queue_result.automation_id

    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS * 2)
    queue = simulation.automations[queue_result.automation_id or ""]
    queue_parameters = queue.parameters
    assert isinstance(queue_parameters, ProductionParameters)
    assert [simulation.entities[item].kind for item in queue_parameters.produced_entity_ids] == [
        EntityKind.SCOUT,
        EntityKind.BUILDER,
    ]
    assert queue.status is AutomationStatus.COMPLETED
    assert loop.status is AutomationStatus.ACTIVE
    assert loop_parameters.progress_ticks == 2


def test_produce_and_defend_attaches_area_to_current_loop_kind() -> None:
    simulation = _simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.HEAVY_TANK, 1, continuous=True)
    )
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
    loop = simulation.automations[loop_result.automation_id or ""]
    parameters = loop.parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.defend_target is None
    app = AirtsApp(simulation)
    app.selected_entities = {"factory"}
    app._selection_changed()
    app.active_target = rectangle_region(Point(12, 12), Point(18, 18))

    app._create_production()

    assert (
        len([item for item in simulation.automations.values() if item.kind.value == "production"])
        == 1
    )
    assert parameters.unit_kind is EntityKind.HEAVY_TANK
    assert parameters.defend_target == app.active_target
    assert parameters.defend_automation_id is not None
    defender = simulation.entities[parameters.produced_entity_ids[0]]
    assert simulation.assignments[defender.entity_id] == parameters.defend_automation_id


def test_automation_inspector_can_attach_current_loop_to_defense_area() -> None:
    simulation = _simulation()
    loop_result = simulation.execute(
        CreateProductionCommand("factory", EntityKind.BUILDER, 1, continuous=True)
    )
    app = AirtsApp(simulation)
    app.selected_automation_id = loop_result.automation_id
    app.active_target = rectangle_region(Point(12, 12), Point(18, 18))

    app._apply_target_to_automation()

    parameters = simulation.automations[loop_result.automation_id or ""].parameters
    assert isinstance(parameters, ProductionParameters)
    assert parameters.defend_target == app.active_target


def test_builder_construction_is_paid_timed_and_places_building_on_completion() -> None:
    simulation = _simulation()
    before = simulation.resources["player"]
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(6, 10))
    )
    assert result.accepted
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ConstructionParameters)

    simulation.advance(Simulation.CONSTRUCTION_BUILD_TICKS - 1)
    assert parameters.constructed_entity_id is None
    assert (
        simulation.resources["player"]
        == before - EntityKind.RESOURCE_GENERATOR.profile.construction_cost
    )

    simulation.advance()
    assert automation.status is AutomationStatus.COMPLETED
    assert parameters.constructed_entity_id is not None
    building = simulation.entities[parameters.constructed_entity_id]
    assert building.kind is EntityKind.RESOURCE_GENERATOR
    assert building.position == Point(6, 10)
    assert automation not in simulation.live_automations


def test_builder_rejects_non_buildings_forbidden_command_center_and_invalid_footprints() -> None:
    simulation = _simulation()
    cases = (
        (EntityKind.LIGHT_TANK, Point(12, 12), "UNSUPPORTED_CONSTRUCTION_KIND"),
        (EntityKind.COMMAND_CENTER, Point(12, 12), "UNSUPPORTED_CONSTRUCTION_KIND"),
        (EntityKind.FACTORY, Point(2, 2), "BUILDING_OVERLAP"),
        (EntityKind.FACTORY, Point(22, 22), "FOOTPRINT_OUTSIDE_MAP"),
        (EntityKind.RESOURCE_GENERATOR, Point(20, 20), "BUILDING_TERRAIN_BLOCKED"),
    )
    for kind, position, reason in cases:
        result = simulation.execute(CreateConstructionCommand("builder", kind, position))
        assert not result.accepted
        assert reason in result.reason


def test_construction_and_batch_progress_round_trip(tmp_path: Path) -> None:
    simulation = _simulation()
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
    restored.advance(
        max(Simulation.CONSTRUCTION_BUILD_TICKS, Simulation.PRODUCTION_BUILD_TICKS * 2)
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


def test_ui_state_exposes_two_side_panels_scrolling_popovers_and_camera_pan() -> None:
    app = AirtsApp(_simulation())

    assert app.LEFT_PANEL_WIDTH > 0
    assert app.RIGHT_PANEL_WIDTH > 0
    assert app.automation_scroll == 0
    assert not app.settings_open
    assert not app.help_open
    assert app.camera_offset == Point(0, 0)

    app.pan_camera(120, -80)
    assert app.camera_offset != Point(0, 0)
    app.scroll_automations(4, visible_rows=2, total_rows=9)
    assert app.automation_scroll == 4


def test_automation_panel_backfills_rows_after_canceling_at_bottom() -> None:
    simulation = _simulation()
    created_ids = []
    for _ in range(8):
        result = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
        assert result.accepted
        assert result.automation_id is not None
        created_ids.append(result.automation_id)
    app = AirtsApp(simulation)
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(app.WINDOW_SIZE)

    app._draw_panel(surface)
    app.automation_scroll = 2
    canceled = simulation.execute(CancelAutomationCommand(created_ids[-1]))
    assert canceled.accepted
    app._draw_panel(surface)

    panel_rows = app._automation_panel_rows()
    visible_ids = [
        automation_id for _, action, automation_id in app._automation_buttons if action == "inspect"
    ]
    assert app.automation_scroll == 1
    assert len(visible_ids) == app._automation_visible_rows
    assert visible_ids == [
        item.automation_id for item in panel_rows[1 : 1 + app._automation_visible_rows]
    ]
    pygame.font.quit()


def test_factory_production_and_linked_area_defense_share_one_panel_item() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateProductionCommand(
            "factory",
            EntityKind.SCOUT,
            1,
            continuous=True,
            defend_target=rectangle_region(Point(5, 5), Point(9, 9)),
        )
    )
    assert result.accepted
    simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
    assert {item.kind for item in simulation.live_automations} == {
        AutomationKind.PRODUCTION,
        AutomationKind.DEFEND,
    }

    rows = AirtsApp(simulation)._automation_panel_rows()

    assert len(rows) == 1
    assert rows[0].kind is AutomationKind.PRODUCTION


def test_automation_panel_exposes_a_prominent_clickable_scrollbar() -> None:
    simulation = _simulation()
    for _ in range(8):
        result = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
        assert result.accepted
    app = AirtsApp(simulation)
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(app.WINDOW_SIZE)
    app._draw_panel(surface)

    track = app._automation_scrollbar_track
    thumb = app._automation_scrollbar_thumb
    assert track is not None
    assert thumb is not None
    assert track.width == app.AUTOMATION_SCROLLBAR_WIDTH
    assert thumb.height >= app.AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT
    assert thumb.height < track.height

    app._handle_mouse_down(1, (track.centerx, track.bottom - 1))

    assert app.automation_scroll == len(app._automation_panel_rows()) - app._automation_visible_rows
    app._draw_panel(surface)
    thumb = app._automation_scrollbar_thumb
    assert thumb is not None
    app._handle_mouse_down(1, thumb.center)
    app._handle_mouse_motion((track.centerx, track.top), (True, False, False))
    app._handle_mouse_up(1, (track.centerx, track.top))

    assert app.automation_scroll == 0
    assert app._automation_scroll_drag_offset is None
    pygame.font.quit()


def test_resizing_reflows_panels_and_scales_canvas_coordinates() -> None:
    app = AirtsApp(_simulation())
    original_tile_size = app.tile_size

    app.resize_layout((1000, 700))

    assert app.canvas_rect.left == app.left_panel_rect.right
    assert app.canvas_rect.right == app.right_panel_rect.left
    assert app.command_bar_rect.top == app.canvas_rect.bottom
    assert app.tile_size < original_tile_size
    point = Point(12.0, 7.5)
    screen_point = app._screen_point(point)
    restored = app._map_point(screen_point)
    assert restored.distance_to(point) <= 1 / app.tile_size


def test_single_kind_selection_opens_details_and_type_choice_filters_selection() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    assert app.inspected_kind is EntityKind.BUILDER

    app.selected_entities = {"builder", "builder_2", "scout", "tank"}
    app._selection_changed()
    assert app.inspected_kind is None
    app._filter_selection_to_kind(EntityKind.BUILDER)

    assert app.selected_entities == {"builder", "builder_2"}
    assert app.inspected_kind is EntityKind.BUILDER
    assert app.simulation.selection.entity_ids == ("builder", "builder_2")


def test_escape_returns_to_select_mode_and_detaches_every_selection() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app.selected_regions = {"stale-region"}
    app.placement_kind = EntityKind.FACTORY
    app.inspected_kind = EntityKind.BUILDER
    app.mode = app.mode.RECTANGLE

    app._handle_key(pygame.K_ESCAPE)

    assert app.mode is app.mode.SELECT
    assert not app.selected_entities
    assert not app.selected_points
    assert not app.selected_routes
    assert not app.selected_regions
    assert app.placement_kind is None
    assert app.inspected_kind is None
    assert app.simulation.selection.entity_ids == ()


def test_double_click_selection_helper_selects_same_kind_in_current_view() -> None:
    app = AirtsApp(_simulation())
    app.resize_layout((1100, 760))

    app._select_all_visible_kind(EntityKind.BUILDER)

    assert app.selected_entities == {"builder", "builder_2"}
    assert app.inspected_kind is EntityKind.BUILDER


def test_double_click_entity_selects_all_visible_friendly_entities_of_that_kind() -> None:
    app = AirtsApp(_simulation())
    builder = app.simulation.entities["builder"]
    with patch("airts.presentation.app.pygame.time.get_ticks", side_effect=(1000, 1200)):
        app._select_entities(builder.position, builder.position)
        app._select_entities(builder.position, builder.position)

    assert app.selected_entities == {"builder", "builder_2"}
    assert app.inspected_kind is EntityKind.BUILDER


def test_settings_menu_clicks_save_load_and_new_game(tmp_path: Path) -> None:
    app = AirtsApp(_simulation())
    app.quick_save_path = tmp_path / "quicksave.json"
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface((1200, 780))
    app.resize_layout(surface.get_size())
    app.settings_open = True
    app._draw(surface)
    buttons = {action: rectangle for rectangle, action in app._settings_buttons}

    app._handle_mouse_down(1, buttons["save"].center)
    assert app.quick_save_path.exists()
    app.simulation.resources["player"] = 123
    app.settings_open = True
    app._draw(surface)
    buttons = {action: rectangle for rectangle, action in app._settings_buttons}
    app._handle_mouse_down(1, buttons["load"].center)
    assert app.simulation.resources["player"] == 10_000
    app.simulation.resources["player"] = 123
    app.settings_open = True
    app._draw(surface)
    buttons = {action: rectangle for rectangle, action in app._settings_buttons}
    app._handle_mouse_down(1, buttons["new"].center)
    assert app.simulation.resources["player"] == 500
    pygame.font.quit()


def test_settings_menu_requests_a_higher_explicit_resolution() -> None:
    app = AirtsApp(_simulation())
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface((1280, 720))
    app.resize_layout(surface.get_size())
    app.settings_open = True
    app._draw(surface)
    buttons = {action: rectangle for rectangle, action in app._settings_buttons}

    app._handle_mouse_down(1, buttons["resolution_higher"].center)

    assert app._pending_window_size == app.WINDOW_SIZE
    assert not app.settings_open
    assert "1428 x 872" in app.notice
    pygame.font.quit()


def test_presentation_profiler_reports_present_wait_and_frame_pacing() -> None:
    profiler = PresentationProfiler()
    metrics = profiler.metrics
    for frame in range(100):
        metrics = profiler.record(
            presented_at=frame * 0.005,
            frame_ms=5.0,
            render_ms=1.5,
            present_ms=2.5,
            simulation_ms=0.4 if frame % 10 == 0 else 0.0,
        )

    assert metrics.submit_fps == pytest.approx(200.0)
    assert metrics.one_percent_low_fps == pytest.approx(200.0)
    assert metrics.frame_p95_ms == 5.0
    assert metrics.render_p95_ms == 1.5
    assert metrics.present_p95_ms == 2.5
    assert metrics.simulation_p95_ms == 0.4


def test_multiple_builders_share_one_job_and_reduce_construction_time() -> None:
    single = _simulation()
    single_result = single.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(6, 10))
    )
    single.advance(Simulation.CONSTRUCTION_BUILD_TICKS // 2)
    single_parameters = single.automations[single_result.automation_id or ""].parameters
    assert isinstance(single_parameters, ConstructionParameters)
    assert single_parameters.constructed_entity_id is None
    assert single_parameters.construction_value == single_parameters.required_value / 2

    team = _simulation()
    team_result = team.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.REPAIR_HUB,
            Point(6, 10),
            builder_ids=("builder", "builder_2"),
        )
    )
    assert team_result.accepted
    team.advance(Simulation.CONSTRUCTION_BUILD_TICKS // 2)
    team_automation = team.automations[team_result.automation_id or ""]
    team_parameters = team_automation.parameters
    assert isinstance(team_parameters, ConstructionParameters)
    assert team_parameters.constructed_entity_id is not None
    assert team_parameters.construction_value == team_parameters.required_value
    assert team_automation.entity_ids == ["builder", "builder_2"]
    assert team.resources["player"] == 10_000 - EntityKind.REPAIR_HUB.profile.construction_cost


def test_builder_group_placement_submits_every_selected_builder() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    app._handle_mouse_down(1, app._screen_point(Point(14, 14)))

    construction = next(
        automation
        for automation in app.simulation.automations.values()
        if isinstance(automation.parameters, ConstructionParameters)
    )
    assert construction.entity_ids == ["builder", "builder_2"]
    assert app.placement_kind is None


def test_builder_profile_drives_incremental_construction_value() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateConstructionCommand(
            "builder",
            EntityKind.RESOURCE_GENERATOR,
            Point(6, 10),
            builder_ids=("builder", "builder_2"),
        )
    )
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    assert EntityKind.BUILDER.profile.build_speed > 0

    simulation.advance()

    assert parameters.construction_value == EntityKind.BUILDER.profile.build_speed * 2
    assert parameters.construction_value < parameters.required_value


def test_construction_preview_reports_validity_before_placement() -> None:
    app = AirtsApp(_simulation())
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    valid = app._construction_preview_at(Point(14.2, 14.8))
    blocked = app._construction_preview_at(Point(2.2, 2.2))

    assert valid is not None and valid[0] == Point(14, 14) and valid[1]
    assert blocked is not None and blocked[0] == Point(2, 2) and not blocked[1]


def test_presentation_profiler_reports_stutter_sensitive_real_fps() -> None:
    profiler = PresentationProfiler()
    metrics = profiler.metrics
    presented_at = 0.0
    for frame in range(200):
        frame_ms = 30.0 if frame % 20 == 0 else 5.0
        presented_at += frame_ms / 1000.0
        metrics = profiler.record(
            presented_at=presented_at,
            frame_ms=frame_ms,
            render_ms=1.5,
            present_ms=2.5,
            simulation_ms=0.0,
        )

    assert metrics.submit_fps > 150.0
    assert metrics.one_percent_low_fps == pytest.approx(1000.0 / 30.0)
    assert metrics.one_percent_low_fps < metrics.submit_fps / 4.0


def test_real_fps_acceptance_rule_remains_inverse_p99_frame_time() -> None:
    frame_times_ms = [5.0] * 190 + [30.0] * 10

    assert REAL_FPS_FRAME_TIME_PERCENTILE == 0.99
    assert real_fps_from_frame_times(frame_times_ms) == pytest.approx(1000.0 / 30.0)


def test_info_panel_displays_stutter_sensitive_real_fps() -> None:
    app = AirtsApp(_simulation())
    app.real_fps = 41.6
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(app.WINDOW_SIZE)

    with patch.object(app, "_small_text", wraps=app._small_text) as small_text:
        app._draw_panel(surface)

    rendered = [call.args[1] for call in small_text.call_args_list]
    assert any("Real FPS   42" in line for line in rendered)
    pygame.font.quit()


def test_projectile_visual_size_is_small_and_stable_across_ui_scales() -> None:
    app = AirtsApp(_simulation())
    projectile = Projectile(
        projectile_id="projectile_visual",
        source_entity_id="tank",
        target_entity_id="scout",
        owner_id="player",
        weapon_kind=EntityKind.LIGHT_TANK,
        position=Point(10.5, 8.5),
        destination=Point(10.5, 8.5),
        damage=EntityKind.LIGHT_TANK.profile.attack_damage,
        speed=10.0,
    )
    app.simulation.projectiles[projectile.projectile_id] = projectile
    observed: list[tuple[int, int]] = []

    for size in ((1000, 700), (3840, 2160)):
        app.resize_layout(size)
        surface = pygame.Surface(size)
        with patch("airts.presentation.app.pygame.draw.circle") as circle:
            app._draw_projectiles(surface)
        observed.append(tuple(call.args[3] for call in circle.call_args_list[-2:]))

    assert observed == [(3, 2), (3, 2)]


def test_builder_must_enter_build_range_before_contributing_work() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(16, 14))
    )
    parameters = simulation.automations[result.automation_id or ""].parameters
    assert isinstance(parameters, ConstructionParameters)
    initial_distance = simulation.entities["builder"].position.distance_to(Point(16, 14))

    simulation.advance()

    assert EntityKind.BUILDER.profile.build_range > 0
    assert parameters.construction_value == 0
    assert simulation.entities["builder"].path

    for _ in range(100):
        simulation.advance()
        if parameters.construction_value > 0:
            break

    assert parameters.construction_value > 0
    assert simulation.entities["builder"].position.distance_to(Point(16, 14)) < initial_distance


def test_shift_construction_appends_fifo_jobs_and_keeps_placement_active() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR

    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, app._screen_point(Point(12, 12)))
        app._handle_mouse_down(1, app._screen_point(Point(16, 16)))

    constructions = [
        automation
        for automation in app.simulation.automations.values()
        if automation.kind.value == "construction"
    ]
    assert len(constructions) == 2
    assert constructions[0].status is AutomationStatus.ACTIVE
    assert constructions[1].status is AutomationStatus.WAITING
    assert constructions[1].reason_code == "BUILDERS_QUEUED"
    assert app.placement_kind is EntityKind.RESOURCE_GENERATOR
    assert app.simulation.command_history[-1]["command"]["queued"] is True

    for _ in range(100):
        app.simulation.advance()
        if constructions[0].status is AutomationStatus.COMPLETED:
            break

    assert constructions[0].status is AutomationStatus.COMPLETED
    assert constructions[1].status is AutomationStatus.ACTIVE


def test_construction_queue_round_trips_through_save_and_replay(tmp_path: Path) -> None:
    simulation = _simulation()
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

    assert restored.snapshot() == simulation.snapshot()
    assert replayed.snapshot() == simulation.snapshot()

    simulation.resources["player"] = 10_000
    restored.resources["player"] = 10_000
    simulation.advance(200)
    restored.advance(200)
    assert restored.snapshot() == simulation.snapshot()
    assert simulation.automations[first.automation_id or ""].status is AutomationStatus.COMPLETED
    assert simulation.automations[second.automation_id or ""].status is AutomationStatus.COMPLETED


def test_completed_construction_waits_for_builder_to_leave_footprint() -> None:
    simulation = _simulation()
    result = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.RESOURCE_GENERATOR, Point(12, 12))
    )
    automation = simulation.automations[result.automation_id or ""]
    parameters = automation.parameters
    assert isinstance(parameters, ConstructionParameters)
    parameters.construction_value = (
        parameters.required_value - EntityKind.BUILDER.profile.build_speed
    )
    simulation.occupancy.move("builder", frozenset({(12, 12)}))
    simulation.entities["builder"].position = Point(12.5, 12.5)

    simulation.advance()

    assert parameters.constructed_entity_id is None
    assert automation.status is AutomationStatus.ACTIVE
    assert simulation.entities["builder"].path

    for _ in range(50):
        simulation.advance()
        if parameters.constructed_entity_id is not None:
            break

    assert parameters.constructed_entity_id is not None
    building = simulation.entities[parameters.constructed_entity_id]
    assert not building.occupied_cells.intersection(simulation.entities["builder"].occupied_cells)


def test_right_click_exits_shift_construction_without_moving_builders() -> None:
    app = AirtsApp(_simulation())
    app.selected_entities = {"builder", "builder_2"}
    app._selection_changed()
    app.placement_kind = EntityKind.RESOURCE_GENERATOR
    with patch("airts.presentation.app.pygame.key.get_mods", return_value=pygame.KMOD_SHIFT):
        app._handle_mouse_down(1, app._screen_point(Point(6, 10)))
        app._handle_mouse_down(1, app._screen_point(Point(16, 16)))
    assignments = dict(app.simulation.assignments)
    command_count = len(app.simulation.command_history)

    app._handle_mouse_down(3, app._screen_point(Point(20, 20)))

    assert app.placement_kind is None
    assert app.simulation.assignments == assignments
    assert len(app.simulation.command_history) == command_count
    assert all(app.simulation.entities[item].move_target is None for item in assignments)
