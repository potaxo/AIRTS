"""Performance contract for native-4K OpenGL with 1,000 scouts."""

from __future__ import annotations

import pygame
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps
from tests.scout_scenario import head_on_scout_simulation

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import (
    OpenGLFrameBuilder,
    OpenGLRenderer,
)
from airts.simulation import Simulation

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


def test_native_4k_opengl_submission_cpu_work_sustains_100_real_fps() -> None:
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
    assert_real_fps(probe, TARGET_FPS, "native-4K OpenGL submission work")


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
