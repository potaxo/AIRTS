"""Terrain detail, camera cache lifetime and shared hardware/software pixels."""

import pygame
import pytest

from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import OpenGLRenderer
from airts.presentation.palette import TERRAIN_COLORS
from airts.presentation.visuals.terrain import DETAIL_SIDE, SHORE_COLOR, detailed_surface
from airts.simulation import Simulation
from airts.world.map import Terrain, load_map_data


def terrain_app() -> AirtsApp:
    return AirtsApp(
        Simulation(
            load_map_data(
                {
                    "id": "terrain-appearance",
                    "name": "Terrain appearance",
                    "width": 24,
                    "height": 20,
                    "terrain": {
                        "default": "grass",
                        "rectangles": [
                            [index * 4, 0, 4, 20, terrain.value]
                            for index, terrain in enumerate(Terrain)
                        ],
                    },
                    "entities": [{"id": "scout", "kind": "scout", "position": [1.5, 1.5]}],
                }
            )
        )
    )


def set_tile(app: AirtsApp, tile: int) -> None:
    fit = min(app.canvas_rect.width / 24, app.canvas_rect.height / 20)
    app.camera.zoom = tile / fit


def test_terrain_detail_is_lazy_reused_and_reset_without_changing_world() -> None:
    app = terrain_app()
    app.resize_layout((1280, 720))
    renderer = app._software_renderer
    screen = pygame.Surface(app.window_size)
    screen.set_clip(app.canvas_rect)
    before = app.simulation.export_state()
    set_tile(app, 4)
    renderer.draw_map(app, screen)
    assert renderer._map_detail_surface is None
    base = renderer._map_surface
    set_tile(app, 16)
    renderer.draw_map(app, screen)
    detail = renderer._map_detail_surface
    assert detail is not None and detail.get_size() == (24 * DETAIL_SIDE, 20 * DETAIL_SIDE)
    viewport = renderer._scaled_map_surface
    renderer.draw_map(app, screen)
    assert renderer._scaled_map_surface is viewport
    for tile in (32, 4, 16):
        set_tile(app, tile)
        renderer.draw_map(app, screen)
        assert renderer._map_surface is base
        assert renderer._map_detail_surface is detail
        crop = renderer._scaled_map_surface
        assert crop is not None
        assert crop.get_width() <= app.canvas_rect.width + tile * 2
        assert crop.get_height() <= app.canvas_rect.height + tile * 2
    assert app.simulation.export_state() == before
    renderer.reset_map()
    assert renderer._map_surface is renderer._map_detail_surface is None


def test_water_banks_follow_real_neighbors_and_keep_cell_centers() -> None:
    app = terrain_app()
    game_map = app.simulation.game_map
    image = detailed_surface(game_map)
    water_x = list(Terrain).index(Terrain.WATER) * 4
    assert tuple(image.get_at((water_x * 8, 2 * 8 + 3))[:3]) == SHORE_COLOR
    assert tuple(image.get_at(((water_x + 4) * 8 - 1, 2 * 8 + 3))[:3]) == SHORE_COLOR
    assert tuple(image.get_at(((water_x + 1) * 8, 2 * 8 + 3))[:3]) != SHORE_COLOR
    for y, row in enumerate(game_map.terrain):
        for x, terrain in enumerate(row):
            assert tuple(image.get_at((x * 8 + 4, y * 8 + 4))[:3]) == TERRAIN_COLORS[terrain]


@pytest.mark.performance
def test_native_terrain_detail_matches_software_through_zoom_without_reupload() -> None:
    """Full material/variant/shore readback catches drift between CPU and GLSL patterns."""
    pygame.font.init()
    app = terrain_app()
    app.resize_layout((1280, 720))
    app.ui.font = pygame.font.Font(None, 22)
    app.ui.small_font = pygame.font.Font(None, 16)
    source = detailed_surface(app.simulation.game_map)
    before = app.simulation.export_state()
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(app.window_size)
    texture = None
    try:
        for tile in (16, 4, 16):
            set_tile(app, tile)
            frame = renderer.render(app, app.window_size, framebuffer=target.framebuffer)
            renderer.finish()
            if texture is None:
                texture = frame.terrain_pixels
            assert frame.terrain_pixels is texture
            image = pygame.image.frombytes(
                target.framebuffer.read(components=3), app.window_size, "RGB", True
            )
            origin_x, origin_y = app.map_origin
            for y, row in enumerate(app.simulation.game_map.terrain):
                for x, terrain in enumerate(row):
                    if x < 3 and y < 3:
                        continue  # The map's initial scout and its health bar overlay terrain.
                    for py in range(8) if tile == 16 else (4,):
                        for px in range(8) if tile == 16 else (4,):
                            sx = int(origin_x + (x + (px + 0.5) / 8) * tile)
                            sy = int(origin_y + (y + (py + 0.5) / 8) * tile)
                            if app.minimap.panel.collidepoint((sx, sy)):
                                continue  # The tactical overview intentionally covers this terrain.
                            # Eight-cell grid primitives intentionally overlay detail.
                            if (
                                min(
                                    abs(sx + 0.5 - (origin_x + grid * tile))
                                    for grid in (0, 8, 16, 24)
                                )
                                < 1.5
                            ):
                                continue
                            if (
                                min(abs(sy + 0.5 - (origin_y + grid * tile)) for grid in (0, 8, 16))
                                < 1.5
                            ):
                                continue
                            expected = (
                                tuple(source.get_at((x * 8 + px, y * 8 + py))[:3])
                                if tile == 16
                                else TERRAIN_COLORS[terrain]
                            )
                            assert tuple(image.get_at((sx, sy))[:3]) == expected, (
                                tile,
                                x,
                                y,
                                px,
                                py,
                            )
        assert app.simulation.export_state() == before
    finally:
        target.release()
        renderer.release()
