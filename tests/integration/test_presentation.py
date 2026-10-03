"""Software presentation, input state, window lifecycle, and resource cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, call, patch

import pygame
import pytest
from tests.integration.scenarios import builder_factory_simulation, interaction_simulation

from airts.contracts.automations import (
    DefendParameters,
    EconomyParameters,
)
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateDefendCommand,
    CreateProductionCommand,
)
from airts.contracts.spatial import Point, rectangle_region
from airts.presentation.app import AirtsApp
from airts.presentation.rendering import RendererBackend
from airts.simulation import Simulation
from airts.world.entities import Projectile
from airts.world.map import EntityKind, load_map_data


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


def test_automation_panel_backfills_rows_after_canceling_at_bottom() -> None:
    simulation = builder_factory_simulation()
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


def test_automation_panel_exposes_a_prominent_clickable_scrollbar() -> None:
    simulation = builder_factory_simulation()
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
    app = AirtsApp(builder_factory_simulation())
    original_tile_size = app.tile_size

    app.resize_layout((1000, 700))

    assert app.canvas_rect.left == app.left_panel_rect.right
    assert app.canvas_rect.right == app.right_panel_rect.left
    assert app.command_bar_rect.top == app.canvas_rect.bottom
    assert app.tile_size < original_tile_size
    point = Point(12.0, 7.5)
    screen_point = app.screen_point(point)
    restored = app._map_point(screen_point)
    assert restored.distance_to(point) <= 1 / app.tile_size

    app.pan_camera(120, -80)
    panned_point = app.screen_point(point)
    assert panned_point == (screen_point[0] + 120, screen_point[1] - 80)
    assert app._map_point(panned_point).distance_to(point) <= 1 / app.tile_size


def test_single_kind_selection_opens_details_and_type_choice_filters_selection() -> None:
    app = AirtsApp(builder_factory_simulation())
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
    app = AirtsApp(builder_factory_simulation())
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


def test_double_click_entity_selects_all_visible_friendly_entities_of_that_kind() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.resize_layout((1100, 760))
    builder = app.simulation.entities["builder"]
    with patch("airts.presentation.app.pygame.time.get_ticks", side_effect=(1000, 1200)):
        app._select_entities(builder.position, builder.position)
        app._select_entities(builder.position, builder.position)

    assert app.selected_entities == {"builder", "builder_2"}
    assert app.inspected_kind is EntityKind.BUILDER


def test_settings_menu_clicks_save_load_and_new_game(tmp_path: Path) -> None:
    app = AirtsApp(builder_factory_simulation())
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
    assert app._software_renderer._map_surface is None
    app.simulation.resources["player"] = 123
    app.settings_open = True
    app._draw(surface)
    buttons = {action: rectangle for rectangle, action in app._settings_buttons}
    app._handle_mouse_down(1, buttons["new"].center)
    assert app.simulation.resources["player"] == 500
    assert app._software_renderer._map_surface is None
    pygame.font.quit()


def test_settings_menu_requests_a_higher_explicit_resolution() -> None:
    app = AirtsApp(builder_factory_simulation())
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


def test_info_panel_displays_stutter_sensitive_real_fps() -> None:
    app = AirtsApp(builder_factory_simulation())
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
    app = AirtsApp(builder_factory_simulation())
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
    before = app.simulation.export_state()
    observed: list[tuple[int, int]] = []

    for size in ((1000, 700), (3840, 2160)):
        app.resize_layout(size)
        surface = pygame.Surface(size)
        with patch("airts.presentation.rendering.pygame.draw.circle") as circle:
            app._software_renderer.draw_projectiles(app, surface)
        observed.append(tuple(call.args[3] for call in circle.call_args_list[-2:]))
        assert app.simulation.export_state() == before

    assert observed == [(3, 2), (3, 2)]


def test_gathering_glow_radius_grows_with_the_authoritative_assembly_radius() -> None:
    simulation = interaction_simulation()
    target = rectangle_region(Point(8, 8), Point(12, 12))
    created = simulation.execute(
        CreateDefendCommand(("unit", "tank"), target, gathering_point=True)
    )
    automation = simulation.automations[created.automation_id or ""]
    assert isinstance(automation.parameters, DefendParameters)
    app = AirtsApp(simulation)
    screen = pygame.Surface(app.WINDOW_SIZE)

    with patch(
        "airts.presentation.app.pygame.draw.circle", wraps=pygame.draw.circle
    ) as draw_circle:
        app._draw_assembly_glows(screen)
    initial_radius = max(call.args[3] for call in draw_circle.call_args_list)

    automation.parameters.assembly_radius += 4
    with patch(
        "airts.presentation.app.pygame.draw.circle", wraps=pygame.draw.circle
    ) as draw_circle:
        app._draw_assembly_glows(screen)
    expanded_radius = max(call.args[3] for call in draw_circle.call_args_list)

    assert expanded_radius > initial_radius


def test_window_close_exits_before_another_tick_or_render_and_releases_resources() -> None:
    simulation = interaction_simulation()
    app = AirtsApp(simulation, renderer_backend=RendererBackend.SOFTWARE)
    lifecycle = Mock()
    clock = Mock()
    clock.tick.return_value = 100
    screen = Mock()

    with (
        patch("airts.presentation.app.pygame.init") as global_init,
        patch("airts.presentation.app.pygame.display.init", side_effect=lifecycle.display_init),
        patch("airts.presentation.app.pygame.font.init", side_effect=lifecycle.font_init),
        patch("airts.presentation.app.pygame.display.set_mode", return_value=screen),
        patch("airts.presentation.app.pygame.display.set_caption"),
        patch("airts.presentation.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.presentation.app.pygame.time.Clock", return_value=clock),
        patch(
            "airts.presentation.app.pygame.event.get",
            return_value=(pygame.event.Event(pygame.QUIT),),
        ),
        patch("airts.presentation.app.pygame.event.clear", side_effect=lifecycle.event_clear),
        patch("airts.presentation.app.pygame.font.quit", side_effect=lifecycle.font_quit),
        patch("airts.presentation.app.pygame.display.quit", side_effect=lifecycle.display_quit),
        patch("airts.presentation.app.pygame.quit", side_effect=lifecycle.pygame_quit),
        patch("airts.presentation.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as draw,
    ):
        app.run()

    global_init.assert_not_called()
    draw.assert_not_called()
    flip.assert_not_called()
    assert simulation.tick == 0
    assert app._font is None
    assert app._small_font is None
    assert app._software_renderer._map_surface is None
    assert lifecycle.mock_calls == [
        call.display_init(),
        call.font_init(),
        call.event_clear(),
        call.font_quit(),
        call.display_quit(),
        call.pygame_quit(),
    ]


def test_render_failure_still_releases_pygame_resources_in_order() -> None:
    app = AirtsApp(
        interaction_simulation(),
        renderer_backend=RendererBackend.SOFTWARE,
    )
    lifecycle = Mock()
    clock = Mock()
    clock.tick.return_value = 0

    with (
        patch("airts.presentation.app.pygame.init"),
        patch("airts.presentation.app.pygame.display.init", side_effect=lifecycle.display_init),
        patch("airts.presentation.app.pygame.font.init", side_effect=lifecycle.font_init),
        patch("airts.presentation.app.pygame.display.set_mode", return_value=Mock()),
        patch("airts.presentation.app.pygame.display.set_caption"),
        patch("airts.presentation.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.presentation.app.pygame.time.Clock", return_value=clock),
        patch("airts.presentation.app.pygame.event.get", return_value=()),
        patch("airts.presentation.app.pygame.event.clear", side_effect=lifecycle.event_clear),
        patch("airts.presentation.app.pygame.font.quit", side_effect=lifecycle.font_quit),
        patch("airts.presentation.app.pygame.display.quit", side_effect=lifecycle.display_quit),
        patch("airts.presentation.app.pygame.quit", side_effect=lifecycle.pygame_quit),
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
