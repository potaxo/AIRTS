"""Expected native-4K OpenGL behavior for the 1,000-scout workload."""

from __future__ import annotations

import os
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pygame
import pytest

from airts.app import AirtsApp
from airts.commands import MoveCommand
from airts.geometry import Point
from airts.map_model import load_map_data
from airts.opengl_renderer import OpenGLFrameBuilder, OpenGLRenderer, OpenGLRendererError
from airts.simulation import Simulation

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
        patch("airts.app.pygame.display.init"),
        patch("airts.app.pygame.font.init"),
        patch("airts.app.pygame.display.gl_set_attribute") as gl_set_attribute,
        patch("airts.app.pygame.display.set_mode", return_value=Mock()) as set_mode,
        patch("airts.app.OpenGLRenderer.from_active_context", return_value=renderer),
        patch("airts.app.pygame.display.set_caption"),
        patch("airts.app.pygame.font.Font", side_effect=(Mock(), Mock())),
        patch("airts.app.pygame.time.Clock", return_value=clock),
        patch("airts.app.pygame.event.get", return_value=()),
        patch("airts.app.pygame.event.clear"),
        patch("airts.app.pygame.font.quit"),
        patch("airts.app.pygame.display.quit"),
        patch("airts.app.pygame.quit"),
        patch("airts.app.pygame.display.flip") as flip,
        patch.object(app, "_draw") as software_draw,
    ):
        app.run(max_frames=1)

    set_mode.assert_called_once_with(app.WINDOW_SIZE, app.OPENGL_DISPLAY_FLAGS)
    assert gl_set_attribute.call_count == 4
    renderer.render.assert_called_once_with(app, app.WINDOW_SIZE)
    renderer.release.assert_called_once_with()
    software_draw.assert_not_called()
    flip.assert_called_once_with()


def test_opengl_context_failure_is_diagnostic_and_never_silently_falls_back() -> None:
    """Missing OpenGL capability is an actionable startup error, not hidden software rendering."""

    app = AirtsApp(_head_on_scout_simulation()[0])
    with (
        patch.object(app, "_configure_opengl_video_backend"),
        patch("airts.app.pygame.display.init"),
        patch("airts.app.pygame.font.init"),
        patch("airts.app.pygame.display.gl_set_attribute"),
        patch("airts.app.pygame.display.set_mode", return_value=Mock()),
        patch(
            "airts.app.OpenGLRenderer.from_active_context",
            side_effect=OpenGLRendererError("OpenGL 3.3 context failed"),
        ),
        patch("airts.app.pygame.event.clear"),
        patch("airts.app.pygame.font.quit"),
        patch("airts.app.pygame.display.quit"),
        patch("airts.app.pygame.quit"),
        patch("airts.app.pygame.display.flip") as flip,
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
        patch("airts.opengl_renderer._load_moderngl", return_value=module),
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


def test_native_4k_opengl_submission_cpu_work_fits_the_100fps_interval() -> None:
    """Commands, collisions, and GPU-frame preparation must fit one real-time second."""

    simulation, eastbound, westbound = _head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    builder = OpenGLFrameBuilder()
    initial_positions = {
        entity_id: simulation.entities[entity_id].position for entity_id in entity_ids
    }

    started = perf_counter()
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
    elapsed = perf_counter() - started

    progressing = sum(
        simulation.entities[entity_id].position != initial_positions[entity_id]
        for entity_id in entity_ids
    )
    assert simulation.tick == MEASURED_TICKS
    assert frame.unit_count == UNIT_COUNT
    assert frame.framebuffer_size == DISPLAY_SIZE
    assert maximum_collision_checks > 0
    assert progressing >= 750
    assert elapsed <= 1.0, f"native-4K OpenGL submission work took {elapsed:.3f}s"


def test_native_4k_hardware_opengl_sustains_100fps_end_to_end() -> None:
    """A real hardware context must rasterize the complete native-4K scenario at 100 FPS."""

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

        started = perf_counter()
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
        elapsed = perf_counter() - started

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
    achieved_fps = MEASURED_FRAMES / elapsed
    assert achieved_fps >= TARGET_FPS, (
        f"{renderer_name} achieved {achieved_fps:.1f} native-4K FPS "
        f"({elapsed:.3f}s for {MEASURED_FRAMES} frames)"
    )
