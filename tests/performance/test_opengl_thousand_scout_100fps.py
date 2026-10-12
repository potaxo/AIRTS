"""Performance contract for native-4K OpenGL with 1,000 scouts."""

from __future__ import annotations

import pygame
import pytest
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps
from tests.scout_scenario import head_on_scout_simulation

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import (
    OpenGLFrameBuilder,
    OpenGLRenderer,
)
from airts.presentation.palette import TERRAIN_COLORS
from airts.simulation import Simulation
from airts.world.skirmish import create_skirmish_map

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000
TARGET_FPS = 100
MEASURED_FRAMES = 100
MEASURED_TICKS = Simulation.TICKS_PER_SECOND
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND


def _native_four_k_app(simulation: Simulation, entity_ids: tuple[str, ...]) -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(simulation)
    app.resize_layout(DISPLAY_SIZE)
    app._font = pygame.font.Font(None, round(24 * app.ui_scale))
    app._small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    app.selected_entities = set(entity_ids)
    return app


def test_native_4k_opengl_submission_cpu_work_sustains_100_real_fps(check_timing: bool) -> None:
    """Commands, collisions, and GPU-frame preparation must sustain 100 Real FPS."""

    simulation, eastbound, westbound = head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    builder = OpenGLFrameBuilder()
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    probe = RealFpsProbe()
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted
    frame = builder.build(app, DISPLAY_SIZE)
    maximum_collision_checks = 0
    for index in range(MEASURED_FRAMES):
        if index % SIMULATION_INTERVAL_FRAMES == 0:
            simulation.advance()
            maximum_collision_checks = max(
                maximum_collision_checks,
                simulation.collision_pair_check_count,
            )
        frame = builder.build(app, DISPLAY_SIZE)
        probe.frame_completed()

    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert simulation.tick == MEASURED_TICKS
    assert frame.unit_count == UNIT_COUNT
    assert frame.framebuffer_size == DISPLAY_SIZE
    assert maximum_collision_checks > 0
    assert progressing >= 750
    if check_timing:
        assert_real_fps(probe, TARGET_FPS, "native-4K OpenGL submission work")


@pytest.mark.performance
def test_native_4k_hardware_opengl_sustains_100_real_fps_end_to_end() -> None:
    """A hardware context must rasterize the native-4K scenario at 100 Real FPS."""

    simulation, eastbound, westbound = head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(DISPLAY_SIZE)
    try:
        renderer_name = str(renderer.info["renderer"])
        software_renderers = (
            "llvmpipe",
            "softpipe",
            "software rasterizer",
            "swiftshader",
            "gdi generic",
            "microsoft basic render driver",
        )
        assert not any(name in renderer_name.lower() for name in software_renderers), (
            f"expected a hardware OpenGL renderer, got {renderer_name!r}"
        )
        renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
        renderer.finish()
        initial_positions = {
            entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
        }

        probe = RealFpsProbe()
        assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
        assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted
        maximum_collision_checks = 0
        for index in range(MEASURED_FRAMES):
            if index % SIMULATION_INTERVAL_FRAMES == 0:
                simulation.advance()
                maximum_collision_checks = max(
                    maximum_collision_checks,
                    simulation.collision_pair_check_count,
                )
            renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
            renderer.finish()
            probe.frame_completed()

        progressing = sum(
            simulation.entities[entity_id].position != initial_positions[entity_id]
            for entity_id in entity_ids
        )
        center_pixel = target.read_pixel(
            (app.canvas_rect.centerx, DISPLAY_SIZE[1] - app.canvas_rect.centery)
        )
    finally:
        target.release()
        renderer.release()

    assert simulation.tick == MEASURED_TICKS
    assert maximum_collision_checks > 0
    assert progressing >= 750
    assert center_pixel != bytes((18, 22, 28, 255))
    assert_real_fps(probe, TARGET_FPS, f"{renderer_name} native-4K rendering")


@pytest.mark.performance
def test_native_4k_large_map_terrain_texture_preserves_cells_through_camera_changes() -> None:
    simulation = Simulation(create_skirmish_map())
    app = _native_four_k_app(simulation, ())
    renderer = OpenGLRenderer.from_standalone_context()
    target = renderer.create_offscreen_target(DISPLAY_SIZE)
    try:
        renderer_name = str(renderer.info["renderer"])
        assert not any(
            name in renderer_name.lower()
            for name in (
                "llvmpipe",
                "softpipe",
                "software rasterizer",
                "swiftshader",
                "gdi generic",
                "microsoft basic render driver",
            )
        ), f"expected a hardware OpenGL renderer, got {renderer_name!r}"
        frame = renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
        renderer.finish()
        texture = frame.terrain_pixels
        assert frame.terrain_count == 320 * 320
        assert frame.terrain_shape_count == 1 + 41 + 41
        assert len(texture) == 320 * 320 * 3
        for y, row in enumerate(simulation.game_map.terrain):
            for x, terrain in enumerate(row):
                offset = (y * 320 + x) * 3
                assert texture[offset : offset + 3] == bytes(TERRAIN_COLORS[terrain])
        for zoom in (0, 4, 4):
            app.zoom_camera(zoom, app.canvas_rect.center)
            app.pan_camera(15, -10)
            frame = renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
            renderer.finish()
            assert frame.terrain_pixels is texture
            for x, y in ((161, 161), (157, 149), (169, 161)):
                screen_x = int(app.map_origin[0] + (x + 0.5) * app.tile_size)
                screen_y = int(app.map_origin[1] + (y + 0.5) * app.tile_size)
                assert app.canvas_rect.collidepoint(screen_x, screen_y)
                pixel = target.read_pixel((screen_x, DISPLAY_SIZE[1] - 1 - screen_y))
                assert tuple(pixel[:3]) == TERRAIN_COLORS[simulation.game_map.terrain[y][x]]
        probe = RealFpsProbe()
        for index in range(MEASURED_FRAMES):
            app.pan_camera(4 if index % 2 else -4, 0)
            frame = renderer.render(app, DISPLAY_SIZE, framebuffer=target.framebuffer)
            renderer.finish()
            probe.frame_completed()
        assert frame.terrain_pixels is texture
        assert frame.framebuffer_size == DISPLAY_SIZE
        assert_real_fps(
            probe, TARGET_FPS, f"{renderer.info['renderer']} native-4K 320-cell map camera"
        )
    finally:
        target.release()
        renderer.release()
