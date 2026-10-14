"""Renderer lifecycle, batching, feedback, and interpolation contracts."""

from __future__ import annotations

import os
from array import array
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pygame
import pytest
from tests.scout_scenario import head_on_scout_simulation

from airts.contracts.commands import MoveCommand
from airts.contracts.spatial import Point
from airts.presentation.app import AirtsApp
from airts.presentation.gpu_shapes import SHAPE_FLOATS
from airts.presentation.opengl_renderer import (
    OpenGLFrameBuilder,
    OpenGLRenderer,
    OpenGLRendererError,
)
from airts.presentation.palette import match_color
from airts.simulation import Simulation
from airts.world.map import GameMap, load_map_data

DISPLAY_SIZE = (3840, 2160)
UNIT_COUNT = 1_000


def _native_four_k_app(simulation: Simulation, entity_ids: tuple[str, ...]) -> AirtsApp:
    pygame.font.init()
    app = AirtsApp(simulation)
    app.resize_layout(DISPLAY_SIZE)
    app.ui.font = pygame.font.Font(None, round(24 * app.ui_scale))
    app.ui.small_font = pygame.font.Font(None, round(19 * app.ui_scale))
    app.selected_entities = set(entity_ids)
    return app


def test_wslg_opengl_prefers_wayland_unless_the_user_selected_a_video_driver(
    make_map: Callable[[int], GameMap],
) -> None:
    """Avoid WSLg's Xwayland GLX path while preserving explicit environment choices."""

    app = AirtsApp(Simulation(make_map(1)))
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


def test_default_runtime_submits_and_releases_the_opengl_renderer(
    make_map: Callable[[int], GameMap],
) -> None:
    """The normal app path must use OpenGL and release GPU objects before display shutdown."""

    app = AirtsApp(Simulation(make_map(1)))
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
    flags = set_mode.call_args.args[1]
    assert flags & pygame.OPENGL
    assert flags & pygame.DOUBLEBUF
    assert flags & pygame.RESIZABLE
    assert not flags & pygame.SCALED
    assert gl_set_attribute.call_count == 4
    renderer.render.assert_called_once_with(app, app.WINDOW_SIZE)
    renderer.release.assert_called_once_with()
    software_draw.assert_not_called()
    flip.assert_called_once_with()
    clock.tick.assert_called_once_with(1_000)
    assert app.FRAME_RATE_LIMIT == 1_000


def test_opengl_context_failure_is_diagnostic_and_never_silently_falls_back(
    make_map: Callable[[int], GameMap],
) -> None:
    """Missing OpenGL capability is an actionable startup error, not hidden software rendering."""

    app = AirtsApp(Simulation(make_map(1)))
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
        NEAREST=7,
    )
    context = MagicMock()
    context.info = {
        "GL_VENDOR": "test vendor",
        "GL_RENDERER": "test renderer",
        "GL_VERSION": "3.3 test",
    }
    context.version_code = 330
    programs = [MagicMock() for _ in range(7)]
    uniforms: dict[str, SimpleNamespace] = {}
    programs[0].__getitem__.side_effect = lambda name: uniforms.setdefault(
        name, SimpleNamespace(value=None)
    )
    context.program.side_effect = programs
    buffers = [MagicMock() for _ in range(10)]
    context.buffer.side_effect = buffers
    vertex_arrays = [MagicMock() for _ in range(8)]
    context.vertex_array.side_effect = vertex_arrays
    texture = MagicMock()
    terrain_texture = MagicMock()
    sprite_texture = MagicMock()
    context.texture.side_effect = (sprite_texture, terrain_texture, texture)
    renderer = OpenGLRenderer(module, context)
    simulation, eastbound, westbound = head_on_scout_simulation()
    entity_ids = eastbound + westbound
    app = _native_four_k_app(simulation, entity_ids)
    app.resize_layout(app.WINDOW_SIZE)
    assert simulation.execute(MoveCommand(eastbound, Point(70.5, 30.5))).accepted
    assert simulation.execute(MoveCommand(westbound, Point(9.5, 30.5))).accepted

    with patch.object(app, "_draw_opengl_overlay"):
        frame = renderer.render(app, app.WINDOW_SIZE)
        renderer.render(app, app.WINDOW_SIZE)

    terrain_draw, shape_draw, line_draw, overlay_draw = vertex_arrays[:4]
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
    # Camera transforms change uniforms, preserving every terrain primitive and
    # its already-uploaded world-coordinate buffer.
    app.zoom_camera(4, app.canvas_rect.center)
    app.pan_camera(30, -20)
    with (
        patch.object(app, "_draw_opengl_overlay"),
        patch.object(app, "_draw_opengl_partial_overlay"),
    ):
        transformed = renderer.render(app, app.WINDOW_SIZE)
    assert transformed.terrain_buffer is frame.terrain_buffer
    assert transformed.terrain_count == frame.terrain_count
    assert buffers[1].write.call_count == 1
    assert buffers[2].write.call_count == 1
    assert buffers[3].write.call_count == 1
    assert transformed.shape_buffer is frame.shape_buffer
    assert transformed.line_buffer is frame.line_buffer
    assert programs[0]["terrain_origin"].value == app.map_origin
    assert programs[0]["terrain_tile_size"].value == app.tile_size
    assert renderer.info["renderer"] == "test renderer"

    renderer.release()

    texture.release.assert_called_once_with()
    terrain_texture.release.assert_called_once_with()
    sprite_texture.release.assert_called_once_with()
    assert context.texture.call_count == 3
    assert all(resource.release.called for resource in (*vertex_arrays, *buffers, *programs))


def test_fps_sampling_does_not_invalidate_the_full_native_overlay(
    make_map: Callable[[int], GameMap],
) -> None:
    """Clock noise must not upload a 4K RGBA surface when no visible state changed."""

    app = _native_four_k_app(Simulation(make_map(1)), ("unit_01",))
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

    simulation, eastbound, westbound = head_on_scout_simulation()
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
    assert frame.shape_count == 10  # 3 aligned sprites + 6 health rectangles + 1 outer outline


@pytest.mark.parametrize("teams", [False, True], ids=["sandbox", "team-match"])
def test_large_enemy_selection_keeps_all_units_and_highlights_them(teams: bool) -> None:
    data = {
        "id": "enemy_highlights",
        "name": "Enemy highlights",
        "width": 32,
        "height": 32,
        "terrain": {"default": "grass"},
        "entities": [
            {
                "id": f"enemy_{i}",
                "kind": "scout",
                "owner": "enemy",
                "position": [1.5 + i % 16, 1.5 + i // 16],
            }
            for i in range(130)
        ]
        + [
            {"id": "player", "kind": "builder", "position": [30.5, 30.5]},
        ],
    }
    if teams:
        data["match_slots"] = [
            {"owner": "player", "controller": "player", "team": 1},
            {"owner": "enemy", "controller": "ai", "team": 2},
        ]
    app = AirtsApp(Simulation(load_map_data(data)))
    app.selected_entities = {f"enemy_{i}" for i in range(130)}
    packed, shapes, units, buildings, selected = OpenGLFrameBuilder._build_entities(app, (0, 0), 1)
    assert shapes == units == 131  # Aligned layers share one atlas entry, including both images.
    assert buildings == 0 and selected == 130
    values = array("f")
    values.frombytes(packed)
    base = match_color(app.simulation.game_map, "enemy") if teams else (218, 78, 78)
    expected = (*[min(255, component + 45) / 255 for component in base], 1)
    for i in range(130):
        assert tuple(values[i * SHAPE_FLOATS + 14 : i * SHAPE_FLOATS + 18]) == pytest.approx(
            expected
        )


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
    values = array("f")
    values.frombytes(active_combat.shape_buffer)
    filled_circles = [
        values[i : i + SHAPE_FLOATS]
        for i in range(0, len(values), SHAPE_FLOATS)
        if values[i + 18] == 1 and values[i + 17] > 0
    ]
    assert len(filled_circles) == 2 * len(simulation.projectiles)
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
        NEAREST=7,
    )
    context = MagicMock()
    programs = [MagicMock() for _ in range(7)]
    uniforms = [{} for _ in programs]
    for program, program_uniforms in zip(programs, uniforms, strict=True):
        program.__getitem__.side_effect = lambda key, values=program_uniforms: values.setdefault(
            key, SimpleNamespace(value=None)
        )
    context.program.side_effect = programs
    context.buffer.side_effect = [MagicMock() for _ in range(10)]
    context.vertex_array.side_effect = [MagicMock() for _ in range(8)]
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


def test_immutable_projectile_traces_reuse_world_geometry_and_expire() -> None:
    simulation = Simulation(
        load_map_data(
            {
                "id": "trace_cache",
                "name": "Trace cache",
                "width": 12,
                "height": 12,
                "terrain": {"default": "grass"},
                "entities": [
                    {"id": "scout", "kind": "scout", "position": [3.5, 3.5]},
                    {"id": "enemy", "kind": "scout", "owner": "enemy", "position": [6.5, 3.5]},
                ],
            }
        )
    )
    simulation.advance(4)
    assert simulation.projectile_traces
    app = _native_four_k_app(simulation, ())
    builder = OpenGLFrameBuilder()
    first = builder.build(app, DISPLAY_SIZE)
    cached = dict(builder._trace_buffers)
    assert cached
    app.inspected_entity_id = "scout"
    inspected = builder.build(app, DISPLAY_SIZE)
    assert inspected is not first
    assert inspected.line_buffer == first.line_buffer
    assert all(builder._trace_buffers[key] is value for key, value in cached.items())
    for entity in simulation.entities.values():
        entity.attack_cooldown = 1000
    expires = max(trace.expires_tick for trace in simulation.projectile_traces)
    simulation.advance(expires - simulation.tick + 1)
    assert not simulation.projectile_traces
    expired = builder.build(app, DISPLAY_SIZE)
    assert not builder._trace_buffers
    assert not expired.line_buffer
