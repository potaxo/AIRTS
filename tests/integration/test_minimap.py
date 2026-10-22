"""Tactical overview pixels, camera input, caching and GPU dirty-region integration."""

from unittest.mock import patch

import pygame
import pytest

from airts.contracts.commands import CreateDefendCommand, MoveCommand
from airts.contracts.spatial import Point, rectangle_region
from airts.presentation.app import AirtsApp, InputMode
from airts.presentation.opengl_renderer import OpenGLRenderer
from airts.presentation.palette import TEAM_COLORS, TERRAIN_COLORS, match_color
from airts.presentation.ui import automations, selection, theme
from airts.simulation import Simulation
from airts.world.map import Terrain, load_map_data
from airts.world.units.catalog import EntityKind


def overview_app() -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(
        Simulation(
            load_map_data(
                {
                    "id": "overview",
                    "name": "Overview",
                    "width": 80,
                    "height": 40,
                    "terrain": {
                        "default": "grass",
                        "rectangles": [[38, 0, 4, 40, "water"], [38, 18, 4, 4, "bridge"]],
                    },
                    "resource_sites": [[20, 30]],
                    "entities": [
                        {"id": "scout", "kind": "scout", "position": [10.5, 10.5]},
                        {
                            "id": "enemy",
                            "kind": "scout",
                            "owner": "enemy",
                            "position": [65.5, 30.5],
                        },
                        {"id": "base", "kind": "factory", "position": [15, 20]},
                    ],
                }
            )
        )
    )
    app.ui.font = pygame.font.Font(None, 24)
    app.ui.small_font = pygame.font.Font(None, 19)
    return app


def test_overview_shows_terrain_contacts_resources_and_selection_without_mutating_world() -> None:
    app = overview_app()
    app.selected_entities = {"scout"}
    before = app.simulation.export_state()
    screen = pygame.Surface(app.window_size)
    app.minimap.draw(app, screen)
    game_map = app.simulation.game_map

    def point(x: float, y: float) -> tuple[int, int]:
        return app.minimap.screen_point(Point(x, y), game_map)

    assert screen.get_at(point(40, 8))[:3] == TERRAIN_COLORS[Terrain.WATER]
    assert screen.get_at(point(40, 20))[:3] == TERRAIN_COLORS[Terrain.BRIDGE]
    assert screen.get_at(point(5, 32))[:3] == TERRAIN_COLORS[Terrain.GRASS]
    assert screen.get_at(point(10.5, 10.5))[:3] == TEAM_COLORS[1]
    assert screen.get_at(point(65.5, 30.5))[:3] == TEAM_COLORS[2]
    base = app.minimap.screen_point(app.simulation.entities["base"].selection_position, game_map)
    assert screen.get_at((base[0] + 2, base[1] + 2))[:3] == TEAM_COLORS[1]
    selected = point(10.5, 10.5)
    assert screen.get_at((selected[0] - 2, selected[1]))[:3] == theme.TEXT
    resource = point(21, 31)
    assert screen.get_at((resource[0] + 1, resource[1]))[:3] == theme.ACCENT
    assert app.minimap.viewport_rect(app) == app.minimap.map_rect
    assert app.minimap.map_rect.width == app.minimap.map_rect.height * 2
    assert app.simulation.export_state() == before


def test_minimap_click_and_captured_drag_move_only_camera_and_work_while_paused() -> None:
    app = overview_app()
    app.zoom_camera(5, app.canvas_rect.center)
    app.paused = True
    app.selected_entities = {"scout"}
    app.mode = InputMode.RECTANGLE
    app.placement_kind = app.simulation.entities["base"].kind
    before = app.simulation.export_state()
    rectangle = app.minimap.map_rect
    target = (rectangle.x + rectangle.width // 4, rectangle.centery)
    app._handle_mouse_down(1, target)
    assert app._minimap_dragging
    world_target = app.minimap.world_point(target, app.simulation.game_map)
    center = app._map_point(app.canvas_rect.center)
    assert center.distance_to(world_target) < 0.2
    camera_box = app.minimap.viewport_rect(app)
    assert camera_box.width < rectangle.width
    assert abs(camera_box.centerx - target[0]) <= 1
    app._handle_mouse_motion((rectangle.right + 500, rectangle.bottom + 500), (True, False, False))
    # The pan bounds put the map's far corner at the canvas corner.
    origin = app.map_origin
    assert origin[0] + app.map_pixel_size[0] == app.canvas_rect.right
    assert origin[1] + app.map_pixel_size[1] == app.canvas_rect.bottom
    app._handle_mouse_up(1, target)
    assert not app._minimap_dragging
    assert app.drag_start is None
    assert app.selected_entities == {"scout"}
    assert app.simulation.export_state() == before
    app._handle_mouse_down(3, target)
    assert app.simulation.export_state() == before
    assert app.paused


@pytest.mark.parametrize("blocked", ["settings_open", "help_open", "naming_reference_id"])
def test_minimap_respects_modal_input_and_focus_loss(blocked: str) -> None:
    app = overview_app()
    app.zoom_camera(5, app.canvas_rect.center)
    initial = app.camera.offset
    setattr(app, blocked, "region" if blocked == "naming_reference_id" else True)
    app._handle_mouse_down(1, app.minimap.map_rect.topleft)
    assert app.camera.offset == initial
    assert not app._minimap_dragging
    setattr(app, blocked, None if blocked == "naming_reference_id" else False)
    # The thin outer border must not move the camera.
    app._handle_mouse_down(1, (app.minimap.panel.centerx, app.minimap.panel.top))
    assert app.camera.offset == initial
    app._handle_mouse_down(1, app.minimap.map_rect.center)
    app._handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    assert not app._minimap_dragging


def test_minimap_camera_partial_refresh_matches_full_overlay_and_reuses_contacts() -> None:
    app = overview_app()
    surface = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(surface)
    terrain = app.minimap._terrain
    contacts = app.minimap._contacts
    old_key = app._opengl_overlay_key()
    app.zoom_camera(5, app.canvas_rect.center)
    app.pan_camera(-80, 70)
    regions = app._opengl_partial_overlay_regions(old_key, app._opengl_overlay_key())
    assert regions == (app.minimap.panel,)
    with patch.object(app, "_draw_interface") as draw_interface:
        app._draw_opengl_partial_overlay(surface, regions)
        draw_interface.assert_not_called()
    full = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(full)
    assert pygame.image.tobytes(surface, "RGBA") == pygame.image.tobytes(full, "RGBA")
    assert app.minimap._terrain is terrain
    assert app.minimap._contacts is contacts
    old_position = app.simulation.entities["scout"].position
    assert app.simulation.execute(MoveCommand(("scout",), Point(20.5, 10.5))).accepted
    app.simulation.advance(10)
    app.minimap.draw(app, surface)
    assert app.minimap._contacts is not contacts
    position = app.simulation.entities["scout"].position
    assert position.distance_to(old_position) > 1
    point = app.minimap.screen_point(position, app.simulation.game_map)
    assert surface.get_at(point)[:3] == TEAM_COLORS[1]
    app.simulation.entities.pop("enemy")
    app.minimap.draw(app, surface)
    point = app.minimap.screen_point(Point(65.5, 30.5), app.simulation.game_map)
    assert surface.get_at(point)[:3] == TERRAIN_COLORS[Terrain.GRASS]


def test_minimap_resizes_and_replaces_map_sources_using_match_team_colors() -> None:
    app = overview_app()
    app.minimap.draw(app, pygame.Surface(app.window_size))
    previous_terrain = app.minimap._terrain
    app.start_match((("player", 1), ("ai", 1), ("ai", 2), ("ai", 3)))
    assert app.minimap.map_rect.width == app.minimap.map_rect.height
    for size in ((1280, 720), (3840, 2160), (760, 520)):
        app.resize_layout(size)
        assert app.right_panel_rect.contains(app.minimap.panel)
        assert app.minimap.panel == app.minimap.map_rect.inflate(2, 2)
        assert not app.minimap.panel.colliderect(app.canvas_rect)
        assert app.selection_panel_rect.top > app.minimap.panel.bottom
        assert app.right_panel_rect.contains(app.selection_panel_rect)
        screen = pygame.Surface(app.window_size)
        app.minimap.draw(app, screen)
        for entity in app.simulation.entities.values():
            point = app.minimap.screen_point(entity.selection_position, app.simulation.game_map)
            assert screen.get_at(point)[:3] == match_color(app.simulation.game_map, entity.owner_id)
    assert app.minimap._terrain is not previous_terrain


def test_minimap_navigation_stays_separate_from_automation_details() -> None:
    app = overview_app()
    app.resize_layout((760, 520))
    assert app.simulation.execute(
        CreateDefendCommand(("scout",), rectangle_region(Point(4, 4), Point(15, 15)))
    ).accepted
    app.automation_panel.selected_id = next(iter(app.simulation.automations))
    app.automation_panel.group = None
    app.automation_panel.details_open = True
    app.zoom_camera(4, app.canvas_rect.center)
    surface = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(surface)
    details = automations.details_rect(app)
    assert details is not None and not details.colliderect(app.minimap.panel)
    old_key = app._opengl_overlay_key()
    before = app.simulation.command_count
    target = app.minimap.map_rect.topleft
    app._handle_mouse_down(1, target)
    assert app._minimap_dragging
    app._handle_mouse_up(1, target)
    assert app.simulation.command_count == before
    regions = app._opengl_partial_overlay_regions(old_key, app._opengl_overlay_key())
    assert regions == (app.minimap.panel,)
    buttons = app._command_buttons.copy()
    app._draw_opengl_partial_overlay(surface, regions)
    assert app._command_buttons == buttons
    full = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(full)
    assert pygame.image.tobytes(surface, "RGBA") == pygame.image.tobytes(full, "RGBA")


def test_short_sidebar_scrolls_details_without_covering_minimap_or_leaking_click_targets() -> None:
    app = overview_app()
    app.resize_layout((760, 520))
    app.selected_entities = {"base"}
    app._selection_changed()
    app.production_detail_kind = EntityKind.ARTILLERY
    surface = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(surface)
    minimap_pixels = pygame.image.tobytes(surface.subsurface(app.minimap.panel), "RGBA")
    old_key = app._opengl_overlay_key()
    with patch("pygame.mouse.get_pos", return_value=app.selection_panel_rect.center):
        app._handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=-20))
    assert app.selection_scroll > 0
    regions = app._opengl_partial_overlay_regions(old_key, app._opengl_overlay_key())
    assert regions == (app.selection_panel_rect,)
    app._draw_opengl_partial_overlay(surface, regions)
    assert pygame.image.tobytes(surface.subsurface(app.minimap.panel), "RGBA") == minimap_pixels
    assert all(
        selection.content_rect(app).contains(rectangle)
        for rectangle, _ in app._command_buttons
        if rectangle.x >= app.right_panel_rect.x
    )
    full = pygame.Surface(app.window_size, pygame.SRCALPHA)
    app._draw_opengl_overlay(full)
    assert pygame.image.tobytes(surface, "RGBA") == pygame.image.tobytes(full, "RGBA")
    before = (app.selection_scroll, app.camera.zoom, app.simulation.command_count)
    with patch("pygame.mouse.get_pos", return_value=app.minimap.map_rect.center):
        app._handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1))
    assert (app.selection_scroll, app.camera.zoom, app.simulation.command_count) == before
    app.selected_entities = {"scout"}
    app._selection_changed()
    app._draw_opengl_overlay(surface)
    assert app.selection_scroll == 0


@pytest.mark.performance
def test_native_minimap_pixels_and_camera_refresh_preserve_world_gpu_buffers() -> None:
    app = overview_app()
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(app.window_size)
    try:
        renderer.render(app, app.window_size, framebuffer=target.framebuffer)
        terrain = renderer._terrain_texture
        contacts = app.minimap._contacts
        app.zoom_camera(5, app.canvas_rect.center)
        with patch.object(
            renderer._builder, "_build_entities", wraps=renderer._builder._build_entities
        ) as build:
            renderer.render(app, app.window_size, framebuffer=target.framebuffer)
            build.assert_not_called()
        renderer.finish()
        assert app.minimap._contacts is contacts
        image = pygame.image.frombytes(
            target.framebuffer.read(components=3), app.window_size, "RGB", True
        )
        software = pygame.Surface(app.window_size)
        app.minimap.draw(app, software)
        for point in (Point(10.5, 10.5), Point(65.5, 30.5), Point(40, 8), Point(40, 20)):
            pixel = app.minimap.screen_point(point, app.simulation.game_map)
            assert image.get_at(pixel)[:3] == software.get_at(pixel)[:3]
        corner = app.minimap.viewport_rect(app).topleft
        assert image.get_at(corner)[:3] == theme.TEXT
        assert renderer._terrain_texture is terrain
    finally:
        target.release()
        renderer.release()
