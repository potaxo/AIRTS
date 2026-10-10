"""Software presentation, input state, window lifecycle, and resource cleanup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, call, patch

import pygame
import pytest
from tests.integration.scenarios import (
    builder_factory_simulation,
    interaction_simulation,
    runtime_map,
)

from airts.contracts.automations import (
    AutomationKind,
    AutomationStatus,
    DefendParameters,
    RepairParameters,
)
from airts.contracts.commands import (
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreateProductionCommand,
)
from airts.contracts.spatial import Point, rectangle_region
from airts.navigation.collision import collision_radius
from airts.presentation.app import AirtsApp
from airts.presentation.rendering import RendererBackend
from airts.simulation import Simulation
from airts.systems.movement import position_clear
from airts.world.entities import Projectile
from airts.world.map import EntityKind, load_map_data


@pytest.mark.parametrize("health", [49, 50, 51], ids=["below-half", "exactly-half", "above-half"])
def test_repair_shortcut_only_sends_selected_units_below_half_health(health: int) -> None:
    simulation = Simulation(runtime_map())
    app = AirtsApp(simulation)
    simulation.entities["unit_02"].health = health
    simulation.entities["unit_01"].health = 1  # An unselected wounded unit stays untouched.
    app.selected_entities = {"unit_02", "factory"}

    app._handle_key(pygame.K_r)

    assert "unit_01" not in simulation.assignments
    assert "factory" not in simulation.assignments
    if health < 50:
        automation = simulation.automations[simulation.assignments["unit_02"]]
        assert automation.entity_ids == ["unit_02"]
        assert isinstance(automation.parameters, RepairParameters)
        assert automation.parameters.health_threshold == 0.5
    else:
        assert "unit_02" not in simulation.assignments
        assert not simulation.live_automations


def test_extractors_produce_without_an_economy_button_or_hotkey() -> None:
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
    pygame.font.init()
    app._font = pygame.font.Font(None, 22)
    app._small_font = pygame.font.Font(None, 18)
    app.selected_entities = {"generator_1", "generator_2"}
    app._selection_changed()
    app._draw_context_panel(pygame.Surface(app.window_size))
    assert all(action != "economy" for _, action in app._command_buttons)
    app._handle_key(pygame.K_g)
    assert not app.simulation.automations
    before = app.simulation.resources["player"]
    app.simulation.advance(10)
    assert app.simulation.resources["player"] == before + 2000


def test_automation_panel_backfills_rows_after_canceling_at_bottom() -> None:
    simulation = builder_factory_simulation()
    created_ids = []
    for _ in range(30):
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
    maximum = len(app._automation_panel_rows()) - app._automation_visible_rows
    app.automation_scroll = maximum
    canceled = simulation.execute(CancelAutomationCommand(created_ids[-1]))
    assert canceled.accepted
    app._draw_panel(surface)

    panel_rows = app._automation_panel_rows()
    visible_ids = [
        automation_id for _, action, automation_id in app._automation_buttons if action == "inspect"
    ]
    assert app.automation_scroll == maximum - 1
    assert len(visible_ids) == app._automation_visible_rows
    assert visible_ids == [
        item.automation_id
        for item in panel_rows[maximum - 1 : maximum - 1 + app._automation_visible_rows]
    ]
    pygame.font.quit()


def test_automation_panel_exposes_a_prominent_clickable_scrollbar() -> None:
    simulation = builder_factory_simulation()
    for _ in range(30):
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


@pytest.mark.parametrize("size", [(1280, 720), AirtsApp.WINDOW_SIZE, (3840, 2160)])
@pytest.mark.parametrize("producer_kind", [EntityKind.FACTORY, EntityKind.COMMAND_CENTER])
def test_compact_production_rows_inspect_cost_without_starting_production(
    size: tuple[int, int],
    producer_kind: EntityKind,
) -> None:
    game_map = builder_factory_simulation().game_map.to_dict()
    entities = game_map["entities"]
    assert isinstance(entities, list)
    entities[0]["kind"] = producer_kind.value
    app = AirtsApp(Simulation(load_map_data(game_map)))
    app.resize_layout(size)
    app.selected_entities = {"factory"}
    app._selection_changed()
    pygame.font.init()
    app._font = pygame.font.Font(None, round(24 * app.ui_scale))
    app._small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    surface = pygame.Surface(app.window_size)
    try:
        with patch.object(app, "_text", wraps=app._text) as text:
            app._draw_interface(surface)
        assert all(
            call.args[2][0] >= app.right_panel_rect.x
            for call in text.call_args_list
            if call.args[1] == "Selection"
        )
        assert all(action != "start_queue" for _, action in app._command_buttons)
        for kind in producer_kind.production_kinds:
            buttons = [
                rect
                for rect, action in app._command_buttons
                if action
                in {
                    f"unit_details:{kind.value}",
                    f"queue:{kind.value}",
                    f"reduce:{kind.value}",
                    f"loop:{kind.value}",
                }
            ]
            assert len(buttons) == 4
            assert len({rect.y for rect in buttons}) == 1
            assert all(app.right_panel_rect.contains(rect) for rect in buttons)
            assert all(
                not left.colliderect(right)
                for index, left in enumerate(buttons)
                for right in buttons[index + 1 :]
            )
        inspected_kind = producer_kind.production_kinds[-1]
        inspect = next(
            rect
            for rect, action in app._command_buttons
            if action == f"unit_details:{inspected_kind.value}"
        )
        app._handle_panel_click(inspect.center)
        assert not app.simulation.automations
        with patch.object(app, "_small_text", wraps=app._small_text) as text:
            app._draw_interface(surface)
        assert any(
            f"Cost {inspected_kind.profile.production_cost}" in call.args[1]
            for call in text.call_args_list
        )
        plus = next(
            rect
            for rect, action in app._command_buttons
            if action == f"queue:{inspected_kind.value}"
        )
        app._handle_panel_click(plus.center)
        assert len(app.simulation.production_queue("factory")) == 1
    finally:
        pygame.font.quit()


def test_automation_categories_keep_controls_on_one_line_and_show_details_on_demand() -> None:
    simulation = builder_factory_simulation()
    production = simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1))
    construction = simulation.execute(
        CreateConstructionCommand("builder", EntityKind.REPAIR_HUB, Point(4, 14))
    )
    defense = simulation.execute(
        CreateDefendCommand(("scout",), rectangle_region(Point(10, 10), Point(16, 16)))
    )
    assert production.accepted and construction.accepted and defense.accepted
    app = AirtsApp(simulation)
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(app.window_size)
    try:
        for group, command, expected in (
            (AutomationKind.PRODUCTION, "production", production.automation_id),
            (AutomationKind.CONSTRUCTION, "construction", construction.automation_id),
            (None, "units", defense.automation_id),
        ):
            app._draw_interface(surface)
            tab = next(
                rect
                for rect, action in app._command_buttons
                if action == f"automation_group:{command}"
            )
            app._handle_panel_click(tab.center)
            app._draw_interface(surface)
            assert app.automation_group is group
            assert [item.automation_id for item in app._automation_panel_rows()] == [expected]
            assert len(app._automation_buttons) == 3
            assert len({rect.y for rect, _, _ in app._automation_buttons}) == 1
            details = next(
                rect for rect, action, _ in app._automation_buttons if action == "inspect"
            )
            app._handle_panel_click(details.center)
            assert app.automation_details_open
            assert app.selected_automation_id == expected
            app._draw_interface(surface)
            toggle = next(rect for rect, action, _ in app._automation_buttons if action == "pause")
            app._handle_panel_click(toggle.center)
            assert simulation.automations[expected or ""].status is AutomationStatus.PAUSED
            app._draw_interface(surface)
            resume = next(rect for rect, action, _ in app._automation_buttons if action == "resume")
            app._handle_panel_click(resume.center)
            assert simulation.automations[expected or ""].status is AutomationStatus.ACTIVE
    finally:
        pygame.font.quit()


def test_visible_pause_button_stops_ticks_and_space_resumes_without_catchup() -> None:
    app = AirtsApp(interaction_simulation(), renderer_backend=RendererBackend.SOFTWARE)
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    screen = pygame.Surface(app.window_size)
    app._draw_interface(screen)
    pause = next(rect for rect, action in app._command_buttons if action == "pause_game")
    ticks = []
    times = iter(index * 0.2 for index in range(100))
    with (
        patch("airts.presentation.app.pygame.display.init"),
        patch("airts.presentation.app.pygame.display.set_mode", return_value=screen),
        patch("airts.presentation.app.pygame.display.set_caption"),
        patch("airts.presentation.app.pygame.display.flip"),
        patch("airts.presentation.app.pygame.event.clear"),
        patch("airts.presentation.app.pygame.time.Clock", return_value=Mock()),
        patch("airts.presentation.app.perf_counter", side_effect=lambda: next(times)),
        patch(
            "airts.presentation.app.pygame.event.get",
            side_effect=(
                (pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pause.center),),
                (),
                (pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE),),
            ),
        ),
        patch.object(app, "_draw", side_effect=lambda _: ticks.append(app.simulation.tick)),
    ):
        app.run(max_frames=3)
    assert ticks[:2] == [0, 0]
    assert 1 <= ticks[2] <= 2
    assert not app.paused


def test_partial_overlay_erases_completed_rows_and_refreshes_right_selection() -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"factory"}
    app._selection_changed()
    assert app.simulation.execute(CreateProductionCommand("factory", EntityKind.SCOUT, 1)).accepted
    pygame.font.init()
    app._font = pygame.font.Font(None, 24)
    app._small_font = pygame.font.Font(None, 19)
    surface = pygame.Surface(app.window_size, pygame.SRCALPHA)
    try:
        app._draw_opengl_overlay(surface)
        uploaded = surface.copy()
        previous = app._opengl_overlay_key()
        app.simulation.advance(Simulation.PRODUCTION_BUILD_TICKS)
        regions = app._opengl_partial_overlay_regions(previous, app._opengl_overlay_key())
        assert regions is not None
        app._draw_opengl_partial_overlay(surface, regions)
        for region in regions:
            uploaded.blit(surface, region, region)
        full = pygame.Surface(app.window_size, pygame.SRCALPHA)
        app._draw_opengl_overlay(full)
        assert pygame.image.tobytes(uploaded, "RGBA") == pygame.image.tobytes(full, "RGBA")
    finally:
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

    # The full-map view is centered and cannot be dragged beyond the map.
    app.pan_camera(120, -80)
    assert app.camera_offset == Point(0, 0)
    app.zoom_camera(5, app.canvas_rect.center)
    screen_point = app.screen_point(point)
    app.pan_camera(120, -80)
    panned_point = app.screen_point(point)
    assert panned_point[0] == screen_point[0] + 120
    assert abs(app.map_origin[1] + app.map_pixel_size[1] - app.canvas_rect.bottom) <= 1
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


@pytest.mark.parametrize(
    ("delay_ms", "offset_pixels", "change_selection", "gathering"),
    [
        pytest.param(200, 0, False, True, id="same-target"),
        pytest.param(400, 4, False, True, id="small-pointer-jitter"),
        pytest.param(401, 0, False, False, id="too-slow"),
        pytest.param(200, 20, False, False, id="different-target"),
        pytest.param(200, 0, True, False, id="different-selection"),
    ],
)
def test_right_click_preserves_formation_and_double_click_gathers(
    delay_ms: int, offset_pixels: int, change_selection: bool, gathering: bool
) -> None:
    app = AirtsApp(builder_factory_simulation())
    app.selected_entities = {"builder", "builder_2"}
    target = Point(15.5, 10.5)
    screen = app.screen_point(target)
    origin_x, origin_y = app.map_origin
    local = (screen[0] - origin_x, screen[1] - origin_y)
    with patch("airts.presentation.app.pygame.time.get_ticks", side_effect=(1000, 1000 + delay_ms)):
        app._handle_mouse_down(3, local)
        first = app.simulation.command_history[-1]["command"]
        assert isinstance(first, dict)
        assert first["type"] == "move"
        assert not first.get("gathering", False)
        if change_selection:
            app.selected_entities = {"builder"}
            app._selection_changed()
        app._handle_mouse_down(3, (local[0] + offset_pixels, local[1]))
    second = app.simulation.command_history[-1]["command"]
    assert isinstance(second, dict)
    assert second["type"] == "move"
    assert second.get("gathering", False) is gathering
    if gathering:
        assert second["target"] == first["target"]


def test_double_right_click_on_enemy_keeps_attack_behavior() -> None:
    app = AirtsApp(interaction_simulation())
    app.selected_entities = {"unit", "tank"}
    screen = app.screen_point(app.simulation.entities["enemy"].position)
    origin_x, origin_y = app.map_origin
    local = (screen[0] - origin_x, screen[1] - origin_y)
    for _ in range(2):
        app._handle_mouse_down(3, local)
        command = app.simulation.command_history[-1]["command"]
        assert isinstance(command, dict)
        assert command["type"] == "attack"
        assert command["target_entity_id"] == "enemy"
        assert app._last_move_click is None


@pytest.mark.parametrize("friendly_id", ["unit", "factory"], ids=["unit", "building"])
def test_right_click_on_friendly_is_not_stolen_by_a_nearby_enemy(friendly_id: str) -> None:
    app = AirtsApp(interaction_simulation())
    app.selected_entities = {"tank"}
    target = app.simulation.entities[friendly_id].selection_position
    enemy = app.simulation.entities["enemy"]
    enemy.position = Point(target.x + 2, target.y)
    screen = app.screen_point(target)
    origin_x, origin_y = app.map_origin
    local = (screen[0] - origin_x, screen[1] - origin_y)
    with patch("airts.presentation.app.pygame.time.get_ticks", side_effect=(1000, 1200)):
        app._handle_mouse_down(3, local)
        first = app.simulation.command_history[-1]["command"]
        assert isinstance(first, dict)
        assert first["type"] == "move"
        app._handle_mouse_down(3, local)
        second = app.simulation.command_history[-1]["command"]
        assert isinstance(second, dict)
        assert second["type"] == "move"
        assert second["gathering"] is True
    entity = app.simulation.entities["tank"]
    assert entity.move_target is not None
    assert position_clear(
        entity.move_target,
        collision_radius(entity.kind),
        app.simulation.game_map.width,
        app.simulation.game_map.height,
        app.simulation._building_cells(),
    )


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


def test_software_runtime_keeps_a_logical_framebuffer_when_the_window_resizes_to_4k() -> None:
    app = AirtsApp(interaction_simulation(), renderer_backend=RendererBackend.SOFTWARE)
    screen = pygame.Surface(app.WINDOW_SIZE)
    physical_size = (3840, 2160)
    clock = Mock()
    clock.tick.return_value = 0
    app.paused = True
    with (
        patch("airts.presentation.app.pygame.display.init"),
        patch("airts.presentation.app.pygame.font.init"),
        patch("airts.presentation.app.pygame.display.set_mode", return_value=screen) as set_mode,
        patch("airts.presentation.app.pygame.display.get_surface", return_value=screen),
        patch("airts.presentation.app.pygame.display.set_caption"),
        patch("airts.presentation.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.presentation.app.pygame.time.Clock", return_value=clock),
        patch(
            "airts.presentation.app.pygame.event.get",
            return_value=(pygame.event.Event(pygame.VIDEORESIZE, size=physical_size),),
        ),
        patch("airts.presentation.app.pygame.event.clear"),
        patch("airts.presentation.app.pygame.font.quit"),
        patch("airts.presentation.app.pygame.display.quit"),
        patch("airts.presentation.app.pygame.quit"),
        patch("airts.presentation.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as draw,
    ):
        app.run(max_frames=1)

    set_mode.assert_called_once_with(screen.get_size(), app.DISPLAY_FLAGS)
    flags = set_mode.call_args.args[1]
    assert flags & pygame.SCALED and flags & pygame.RESIZABLE
    assert not flags & pygame.OPENGL
    assert app.window_size == screen.get_size() == app.WINDOW_SIZE
    assert all(
        logical < physical
        for logical, physical in zip(screen.get_size(), physical_size, strict=True)
    )
    draw.assert_called_once_with(screen)
    flip.assert_called_once_with()


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
