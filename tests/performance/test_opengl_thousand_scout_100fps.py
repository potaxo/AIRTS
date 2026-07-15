"""Performance contract for native-4K OpenGL with 1,000 scouts."""

from __future__ import annotations

import os
from array import array
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pygame
import pytest
from tests.performance.frame_pacing import RealFpsProbe, assert_real_fps

from airts.commands import MoveCommand
from airts.geometry import Point
from airts.presentation.app import AirtsApp
from airts.presentation.opengl_renderer import (
    SHAPE_FLOATS,
    OpenGLFrameBuilder,
    OpenGLRenderer,
    OpenGLRendererError,
)
from airts.simulation import Simulation
from airts.world.map_model import load_map_data

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000
GROUP_SIZE = UNIT_COUNT // 2
TARGET_FPS = 100
MEASURED_FRAMES = 100
MEASURED_TICKS = Simulation.TICKS_PER_SECOND
SIMULATION_INTERVAL_FRAMES = TARGET_FPS // Simulation.TICKS_PER_SECOND


def _head_on_scout_simulation() -> tuple[Simulation, tuple[str, ...], tuple[str, ...]]:
    eastbound = tuple(f"east_{index:04d}" for index in range(GROUP_SIZE))
    westbound = tuple(f"west_{index:04d}" for index in range(GROUP_SIZE))
    simulation = Simulation(
        load_map_data(
            {
                "id": "native_four_k_opengl_scouts",
                "name": "Native 4K OpenGL Thousand Scouts",
                "width": 80,
                "height": 60,
                "terrain": {
                    "default": "grass",
                    "rectangles": [
                        [0, 14, 80, 4, "road"],
                        [0, 42, 80, 4, "road"],
                        [31, 20, 3, 20, "forest"],
                    ],
                },
                "entities": [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [5.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(eastbound)
                ]
                + [
                    {
                        "id": entity_id,
                        "kind": "scout",
                        "owner": "player",
                        "position": [35.5 + index % 25, 20.5 + index // 25],
                    }
                    for index, entity_id in enumerate(westbound)
                ]
                + [
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [68, 4],
                    },
                    {
                        "id": "repair_hub",
                        "kind": "repair_hub",
                        "owner": "player",
                        "position": [68, 12],
                    },
                    {
                        "id": "command_center",
                        "kind": "command_center",
                        "owner": "player",
                        "position": [68, 48],
                    },
                    {
                        "id": "resource_generator",
                        "kind": "resource_generator",
                        "owner": "player",
                        "position": [4, 48],
                    },
                ],
            }
        ),
        random_seed=97,
    )
    return simulation, eastbound, westbound


def _native_four_k_app(simulation: Simulation, entity_ids: tuple[str, ...]) -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(simulation)
    app.resize_layout(DISPLAY_SIZE)
    app._font = pygame.font.Font(None, round(24 * app.ui_scale))
    app._small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    app.selected_entities = set(entity_ids)
    return app


def test_opengl_runtime_requests_a_native_resizable_double_buffer() -> None:
    """The default GPU path must render at native pixels rather than scaling a small Surface."""

    flags = AirtsApp.OPENGL_DISPLAY_FLAGS
    assert flags & pygame.OPENGL
    assert flags & pygame.DOUBLEBUF
    assert flags & pygame.RESIZABLE
    assert not flags & pygame.SCALED


def test_wslg_opengl_prefers_wayland_unless_the_user_selected_a_video_driver() -> None:
    """Avoid WSLg's Xwayland GLX path while preserving explicit environment choices."""

    app = AirtsApp(_head_on_scout_simulation()[0])
    with patch.dict(
        os.environ,
        {"WSL_DISTRO_NAME": "Ubuntu-24.04", "WAYLAND_DISPLAY": "wayland-0"},
        clear=True,
    ):
        app._configure_opengl_video_backend()
        assert os.environ["SDL_VIDEODRIVER"] == "wayland"

    with patch.dict(
        os.environ,
        {
            "WSL_DISTRO_NAME": "Ubuntu-24.04",
            "WAYLAND_DISPLAY": "wayland-0",
            "SDL_VIDEODRIVER": "x11",
        },
        clear=True,
    ):
        app._configure_opengl_video_backend()
        assert os.environ["SDL_VIDEODRIVER"] == "x11"


def test_default_runtime_submits_and_releases_the_opengl_renderer() -> None:
    """The normal app path must use OpenGL and release GPU objects before display shutdown."""

    app = AirtsApp(_head_on_scout_simulation()[0])
    renderer = Mock()
    clock = Mock()
    clock.tick.return_value = 0

    with (
        patch.object(app, "_configure_opengl_video_backend"),
        patch("airts.presentation.app.pygame.display.init"),
        patch("airts.presentation.app.pygame.font.init"),
        patch("airts.presentation.app.pygame.display.gl_set_attribute") as gl_set_attribute,
        patch("airts.presentation.app.pygame.display.set_mode", return_value=Mock()) as set_mode,
        patch("airts.presentation.app.OpenGLRenderer.from_active_context", return_value=renderer),
        patch("airts.presentation.app.pygame.display.set_caption"),
        patch("airts.presentation.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.presentation.app.pygame.time.Clock", return_value=clock),
        patch("airts.presentation.app.pygame.event.get", return_value=()),
        patch("airts.presentation.app.pygame.event.clear"),
        patch("airts.presentation.app.pygame.font.quit"),
        patch("airts.presentation.app.pygame.display.quit"),
        patch("airts.presentation.app.pygame.quit"),
        patch("airts.presentation.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as software_draw,
    ):
        app.run(max_frames=1)

    set_mode.assert_called_once_with(app.WINDOW_SIZE, app.OPENGL_DISPLAY_FLAGS, vsync=0)
    assert gl_set_attribute.call_count == 4
    renderer.render.assert_called_once_with(app, app.WINDOW_SIZE)
    renderer.release.assert_called_once_with()
    software_draw.assert_not_called()
    flip.assert_called_once_with()
    clock.tick.assert_called_once_with(1_000)
    assert app.FRAME_RATE_LIMIT == 1_000


def test_opengl_context_failure_is_diagnostic_and_never_silently_falls_back() -> None:
    """Missing OpenGL capability is an actionable startup error, not hidden software rendering."""

    app = AirtsApp(_head_on_scout_simulation()[0])
    with (
        patch.object(app, "_configure_opengl_video_backend"),
        patch("airts.presentation.app.pygame.display.init"),
        patch("airts.presentation.app.pygame.font.init"),
        patch("airts.presentation.app.pygame.display.gl_set_attribute"),
        patch("airts.presentation.app.pygame.display.set_mode", return_value=Mock()),
        patch(
            "airts.presentation.app.OpenGLRenderer.from_active_context",
            side_effect=OpenGLRendererError("OpenGL 3.3 context failed"),
        ),
        patch("airts.presentation.app.pygame.event.clear"),
        patch("airts.presentation.app.pygame.font.quit"),
        patch("airts.presentation.app.pygame.display.quit"),
        patch("airts.presentation.app.pygame.quit"),
        patch("airts.presentation.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as software_draw,
    ):
        with pytest.raises(OpenGLRendererError, match="OpenGL 3.3 context failed"):
            app.run(max_frames=1)

    software_draw.assert_not_called()
    flip.assert_not_called()


def test_standalone_opengl_context_uses_the_platform_backend_by_default() -> None:
    """Hardware verification must use WGL on Windows instead of forcing Linux EGL."""

    module = MagicMock()
    context = MagicMock()
    module.create_context.return_value = context

    with (
        patch("airts.presentation.opengl_renderer._load_moderngl", return_value=module),
        patch.object(OpenGLRenderer, "__init__", return_value=None) as initialize,
    ):
        renderer = OpenGLRenderer.from_standalone_context()

    module.create_context.assert_called_once_with(require=330, standalone=True)
    initialize.assert_called_once_with(module, context)
    assert isinstance(renderer, OpenGLRenderer)


def test_opengl_renderer_batches_scene_primitives_and_releases_gpu_resources() -> None:
    """A complete scene uses one instanced terrain draw, one entity draw, and bounded lines."""

    module = SimpleNamespace(
        BLEND=1,
        SRC_ALPHA=2,
        ONE_MINUS_SRC_ALPHA=3,
        TRIANGLE_STRIP=4,
        LINES=5,
        LINEAR=6,
    )
    context = MagicMock()
    context.info = {
        "GL_VENDOR": "test vendor",
        "GL_RENDERER": "test renderer",
        "GL_VERSION": "3.3 test",
    }
    context.version_code = 330
    programs = [MagicMock() for _ in range(3)]
    context.program.side_effect = programs
    buffers = [MagicMock() for _ in range(5)]
    context.buffer.side_effect = buffers
    vertex_arrays = [MagicMock() for _ in range(4)]
    context.vertex_array.side_effect = vertex_arrays
    texture = MagicMock()
    context.texture.return_value = texture
    renderer = OpenGLRenderer(module, context)
    simulation, eastbound, westbound = _head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    app.resize_layout(app.WINDOW_SIZE)
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted

    with patch.object(app, "_draw_opengl_overlay"):
        frame = renderer.render(app, app.WINDOW_SIZE)
        renderer.render(app, app.WINDOW_SIZE)

    terrain_draw, shape_draw, line_draw, overlay_draw = vertex_arrays
    terrain_draw.render.assert_called_with(
        mode=module.TRIANGLE_STRIP,
        vertices=4,
        instances=frame.terrain_shape_count,
    )
    shape_draw.render.assert_called_with(
        mode=module.TRIANGLE_STRIP,
        vertices=4,
        instances=frame.shape_count,
    )
    line_draw.render.assert_called_with(
        mode=module.LINES,
        vertices=frame.line_vertex_count,
    )
    overlay_draw.render.assert_called_with(mode=module.TRIANGLE_STRIP, vertices=4)
    assert terrain_draw.render.call_count == 2
    assert shape_draw.render.call_count == 2
    assert line_draw.render.call_count == 2
    assert overlay_draw.render.call_count == 2
    assert buffers[1].write.call_count == 1
    assert buffers[2].write.call_count == 1
    assert buffers[3].write.call_count == 1
    texture.write.assert_called_once()
    assert renderer.info["renderer"] == "test renderer"

    renderer.release()

    texture.release.assert_called_once_with()
    assert all(resource.release.called for resource in (*vertex_arrays, *buffers, *programs))


def test_fps_sampling_does_not_invalidate_the_full_native_overlay() -> None:
    """Clock noise must not upload a 4K RGBA surface when no visible state changed."""

    simulation, eastbound, _ = _head_on_scout_simulation()
    app = _native_four_k_app(simulation, eastbound)
    app.real_fps = 96.0
    initial_key = app._opengl_overlay_key()

    app.real_fps = 104.0

    assert app._opengl_overlay_key() == initial_key


def test_native_overlay_status_refreshes_in_bounded_tick_buckets() -> None:
    """CPU-rendered status text is coalesced while GPU world batches update every tick."""

    simulation = Simulation(
        load_map_data(
            {
                "id": "overlay_refresh",
                "name": "Overlay Refresh",
                "width": 4,
                "height": 4,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [1.5, 1.5],
                    }
                ],
            }
        )
    )
    app = _native_four_k_app(simulation, ())
    initial_key = app._opengl_overlay_key()

    simulation.advance(AirtsApp.OPENGL_OVERLAY_REFRESH_TICKS - 1)
    assert app._opengl_overlay_key() == initial_key

    simulation.advance()
    assert app._opengl_overlay_key() != initial_key


def test_opengl_renderer_releases_partial_resources_when_initialization_fails() -> None:
    """A shader or vertex-array failure must not strand already-created GPU objects."""

    module = SimpleNamespace(
        BLEND=1,
        SRC_ALPHA=2,
        ONE_MINUS_SRC_ALPHA=3,
    )
    context = MagicMock()
    programs = [MagicMock() for _ in range(3)]
    context.program.side_effect = programs
    buffers = [MagicMock() for _ in range(5)]
    context.buffer.side_effect = buffers
    vertex_arrays = [MagicMock(), MagicMock()]
    context.vertex_array.side_effect = [
        *vertex_arrays,
        RuntimeError("vertex layout rejected"),
    ]

    with pytest.raises(OpenGLRendererError, match="failed to initialize OpenGL resources"):
        OpenGLRenderer(module, context)

    assert all(resource.release.called for resource in (*vertex_arrays, *buffers, *programs))
    context.release.assert_not_called()


def test_opengl_frame_contains_every_native_4k_scenario_element() -> None:
    """GPU submission must retain all units, buildings, terrain, selection, and routes."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted

    frame = OpenGLFrameBuilder().build(app, DISPLAY_SIZE)

    assert frame.framebuffer_size == DISPLAY_SIZE
    assert frame.pixel_scale == 1.0
    assert frame.terrain_count == 80 * 60
    assert frame.unit_count == UNIT_COUNT
    assert frame.building_count == 4
    assert frame.selected_unit_count == UNIT_COUNT
    assert 1 <= frame.path_count <= AirtsApp.MAX_SELECTED_PATHS
    assert frame.shape_buffer
    assert frame.line_buffer


def test_opengl_normal_scene_preserves_full_health_bars_and_outer_selection_outline() -> None:
    """Small selections retain the detailed health and individual outline feedback."""

    simulation = Simulation(
        load_map_data(
            {
                "id": "opengl_detail_scene",
                "name": "OpenGL Detail Scene",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "selected_scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [3.5, 3.5],
                    },
                    {
                        "id": "enemy_scout",
                        "kind": "scout",
                        "owner": "enemy",
                        "position": [7.5, 7.5],
                    },
                    {
                        "id": "factory",
                        "kind": "factory",
                        "owner": "player",
                        "position": [8, 2],
                    },
                ],
            }
        )
    )
    app = _native_four_k_app(simulation, ("selected_scout",))

    frame = OpenGLFrameBuilder().build(app, DISPLAY_SIZE)

    assert frame.unit_count == 2
    assert frame.building_count == 1
    assert frame.selected_unit_count == 1
    assert frame.shape_count == 10  # 3 entities + 6 health rectangles + 1 outer outline


def test_opengl_frame_batches_live_projectile_feedback_on_the_gpu() -> None:
    """OpenGL must not route active combat effects through the CPU overlay texture."""

    simulation = Simulation(
        load_map_data(
            {
                "id": "opengl_projectile_scene",
                "name": "OpenGL Projectile Scene",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "player_scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [3.5, 3.5],
                    },
                    {
                        "id": "enemy_scout",
                        "kind": "scout",
                        "owner": "enemy",
                        "position": [6.5, 3.5],
                    },
                ],
            }
        )
    )
    app = _native_four_k_app(simulation, ("player_scout",))
    builder = OpenGLFrameBuilder()
    initial = builder.build(app, DISPLAY_SIZE)

    simulation.advance()
    active_combat = builder.build(app, DISPLAY_SIZE)

    assert simulation.projectiles
    assert active_combat.shape_count == initial.shape_count + 2 * len(simulation.projectiles)
    assert active_combat.line_vertex_count > initial.line_vertex_count


def test_gpu_interpolates_fixed_tick_motion_without_rebuilding_the_frame() -> None:
    """A 100+ FPS frontend must present distinct positions between 10 Hz simulation ticks."""

    simulation = Simulation(
        load_map_data(
            {
                "id": "gpu_interpolation",
                "name": "GPU Interpolation",
                "width": 20,
                "height": 12,
                "terrain": {"default": "grass", "rectangles": []},
                "entities": [
                    {
                        "id": "scout",
                        "kind": "scout",
                        "owner": "player",
                        "position": [2.5, 5.5],
                    }
                ],
            }
        )
    )
    app = _native_four_k_app(simulation, ("scout",))
    assert simulation.execute(MoveCommand(("scout",), Point(17.5, 5.5))).accepted
    app._advance_presentation_tick()
    app.render_alpha = 0.5
    builder = OpenGLFrameBuilder()
    first = builder.build(app, DISPLAY_SIZE)
    second = builder.build(app, DISPLAY_SIZE)
    values = array("f")
    values.frombytes(first.shape_buffer)
    current_center = tuple(values[0:2])
    previous_center = tuple(values[2:4])

    assert first is second
    assert len(values) % SHAPE_FLOATS == 0
    assert current_center != previous_center
    midpoint = tuple(
        previous + (current - previous) * app.render_alpha
        for previous, current in zip(previous_center, current_center, strict=True)
    )
    assert midpoint != previous_center
    assert midpoint != current_center

    module = SimpleNamespace(
        BLEND=1,
        SRC_ALPHA=2,
        ONE_MINUS_SRC_ALPHA=3,
        TRIANGLE_STRIP=4,
        LINES=5,
        LINEAR=6,
    )
    context = MagicMock()
    programs = [MagicMock() for _ in range(3)]
    uniforms = [{}, {}, {}]
    for program, program_uniforms in zip(programs, uniforms, strict=True):
        program.__getitem__.side_effect = lambda key, values=program_uniforms: values.setdefault(
            key, SimpleNamespace(value=None)
        )
    context.program.side_effect = programs
    context.buffer.side_effect = [MagicMock() for _ in range(5)]
    context.vertex_array.side_effect = [MagicMock() for _ in range(4)]
    context.texture.return_value = MagicMock()
    renderer = OpenGLRenderer(module, context)
    try:
        with patch.object(app, "_draw_opengl_overlay"):
            rendered = renderer.render(app, DISPLAY_SIZE)
            app.render_alpha = 0.9
            rerendered = renderer.render(app, DISPLAY_SIZE)
    finally:
        renderer.release()

    assert rendered is rerendered
    assert uniforms[0]["interpolation_alpha"].value == pytest.approx(0.9)


def test_native_4k_opengl_submission_cpu_work_sustains_100_real_fps() -> None:
    """Commands, collisions, and GPU-frame preparation must sustain 100 Real FPS."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
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

    simulation, eastbound, westbound = _head_on_scout_simulation()
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
