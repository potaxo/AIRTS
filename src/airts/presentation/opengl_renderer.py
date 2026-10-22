"""Native-pixel OpenGL presentation adapter for AIRTS."""

from __future__ import annotations

from array import array
from dataclasses import dataclass, replace
from importlib import import_module
from typing import Any, Protocol, cast

import pygame

from airts.contracts.automations import AutomationKind, DefendParameters, target_center
from airts.contracts.spatial import Point
from airts.presentation.gpu_feedback import FeedbackView, GPUFeedback
from airts.presentation.gpu_shapes import SHAPE_FLOATS, FloatColor, append_shape
from airts.presentation.lobby import Lobby
from airts.presentation.palette import (
    PROJECTILE_COLORS,
    TERRAIN_COLORS,
    Color,
)
from airts.presentation.projectile_effects import (
    SHELL_CORE_RADIUS,
    SHELL_RIM_RADIUS,
    blast_circles,
    shell_visual_position,
)
from airts.presentation.rendering import SceneView, interaction_range, order_feedback
from airts.presentation.ui.minimap import Minimap
from airts.presentation.ui.saves import SavePanelState
from airts.presentation.ui.theme import BACKGROUND
from airts.presentation.visuals.assets import atlas_pixels
from airts.presentation.visuals.catalog import ATLAS_COLUMNS, TILE_PIXELS
from airts.presentation.visuals.gpu import (
    ENTITY_ATTRIBUTES,
    ENTITY_GEOMETRY_SHADER,
    ENTITY_VERTEX_SHADER,
    pack_entities,
)
from airts.presentation.visuals.terrain import DETAIL_MIN_TILE, GRID_COLOR, fragment_shader
from airts.simulation import Simulation
from airts.world.entities import ProjectileTrace
from airts.world.units.schema import EntityCategory

LINE_FLOATS = 6

_INITIAL_TERRAIN_BUFFER_BYTES = 512 * 1024
_INITIAL_SHAPE_BUFFER_BYTES = 256 * 1024
_INITIAL_LINE_BUFFER_BYTES = 256 * 1024

_GPU_RESOURCE_ATTRIBUTES = (
    "_entity_vertex_array",
    "_entity_buffer",
    "_entity_program",
    "_overlay_texture",
    "_terrain_texture",
    "_sprite_texture",
    "_overlay_vertex_array",
    "_line_vertex_array",
    "_shape_vertex_array",
    "_terrain_vertex_array",
    "_overlay_quad_buffer",
    "_line_buffer",
    "_shape_buffer",
    "_terrain_buffer",
    "_quad_buffer",
    "_overlay_program",
    "_line_program",
    "_shape_program",
)


class OpenGLRenderState(SceneView, FeedbackView, Protocol):
    """The UI-owned state needed to build a GPU frame without importing the application."""

    render_alpha: float
    minimap: Minimap
    settings_open: bool
    help_open: bool
    save_panel: SavePanelState
    lobby: Lobby | None

    def previous_entity_position(self, entity_id: str) -> Point: ...

    def previous_projectile_position(self, projectile_id: str) -> Point: ...

    def _prune_removed_entities(self) -> None: ...

    def _opengl_overlay_key(self) -> tuple[object, ...]: ...

    def _draw_opengl_overlay(self, screen: pygame.Surface) -> None: ...

    def _draw_opengl_partial_overlay(
        self,
        screen: pygame.Surface,
        regions: tuple[pygame.Rect, ...],
    ) -> None: ...

    def _opengl_partial_overlay_regions(
        self,
        previous: tuple[object, ...],
        current: tuple[object, ...],
    ) -> tuple[pygame.Rect, ...] | None: ...

    def _opengl_dynamic_overlay_regions(self) -> tuple[pygame.Rect, ...]: ...


@dataclass(frozen=True, slots=True)
class OpenGLFrame:
    """Packed native-pixel primitives ready for OpenGL buffer upload."""

    framebuffer_size: tuple[int, int]
    pixel_scale: float
    tick: int
    terrain_count: int
    terrain_shape_count: int
    unit_count: int
    building_count: int
    selected_unit_count: int
    path_count: int
    shape_count: int
    line_vertex_count: int
    terrain_buffer: bytes
    terrain_pixels: bytes
    terrain_size: tuple[int, int]
    terrain_origin: tuple[int, int]
    terrain_tile_size: float
    shape_buffer: bytes
    line_buffer: bytes
    entity_buffer: bytes


@dataclass(slots=True)
class OpenGLOffscreenTarget:
    """Renderer-owned helper target for native-resolution GPU verification."""

    size: tuple[int, int]
    framebuffer: Any
    texture: Any

    def read_pixel(self, position: tuple[int, int]) -> bytes:
        """Read one RGBA pixel for correctness checks outside timed rendering."""

        x, y = position
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            raise ValueError("pixel position is outside the framebuffer")
        return cast(
            bytes,
            self.framebuffer.read(
                viewport=(x, y, 1, 1),
                components=4,
                alignment=1,
            ),
        )

    def release(self) -> None:
        self.framebuffer.release()
        self.texture.release()


class OpenGLFrameBuilder:
    """Build and cache exact scene batches in physical framebuffer coordinates."""

    def __init__(self) -> None:
        self._terrain_key: tuple[object, ...] | None = None
        self._terrain_buffer = b""
        self._terrain_pixels = b""
        self._terrain_count = 0
        self._terrain_shape_count = 0
        self._frame_key: tuple[object, ...] | None = None
        self._frame: OpenGLFrame | None = None
        self._selection_key: frozenset[str] | None = None
        self._trace_buffers: dict[str, tuple[ProjectileTrace, bytes]] = {}
        self._deferred_tick: int | None = None
        self._frame_entity_ids: frozenset[str] = frozenset()

    def staged_frame(
        self, app: OpenGLRenderState, framebuffer_size: tuple[int, int]
    ) -> OpenGLFrame | None:
        """Hold the previous current endpoint for one presentation call after a tick.

        As with software scene composition, pack the new world on the following
        call rather than adding that work to the simulation tick's CPU tail.
        This adds at most one presentation interval to interpolation latency.
        Commands, selection changes, births and skipped ticks rebuild at once. Deaths
        can retain their old endpoint for that one call; no living unit is omitted.
        """
        frame, key = self._frame, self._frame_key
        simulation = app.simulation
        if (
            frame is None
            or key is None
            or self._deferred_tick == simulation.tick
            or frame.tick != simulation.tick - 1
            or key[0] != id(simulation)
            or key[2] != simulation.command_count
            or self._selection_key is None
            or self._selection_key.intersection(simulation.entities) != app.selected_entities
            or not simulation.entities.keys() <= self._frame_entity_ids
        ):
            return None
        self._deferred_tick = simulation.tick
        if (
            frame.framebuffer_size != framebuffer_size
            or frame.terrain_origin != app.map_origin
            or frame.terrain_tile_size != app.tile_size
        ):
            return replace(
                frame,
                framebuffer_size=framebuffer_size,
                terrain_origin=app.map_origin,
                terrain_tile_size=app.tile_size,
            )
        return frame

    def build(
        self,
        app: OpenGLRenderState,
        framebuffer_size: tuple[int, int],
    ) -> OpenGLFrame:
        """Return one native-resolution frame, reusing buffers between simulation ticks."""

        if framebuffer_size[0] <= 0 or framebuffer_size[1] <= 0:
            raise ValueError("framebuffer dimensions must be positive")
        simulation = app.simulation
        origin = app.map_origin
        tile_size = app.tile_size
        terrain_key: tuple[object, ...] = (id(simulation.game_map),)
        if terrain_key != self._terrain_key:
            self._terrain_key = terrain_key
            (
                self._terrain_buffer,
                self._terrain_pixels,
                self._terrain_count,
                self._terrain_shape_count,
            ) = self._build_terrain(
                simulation,
            )

        selection_key = self._selection_key
        if selection_key is None or selection_key != app.selected_entities:
            selection_key = frozenset(app.selected_entities)
            self._selection_key = selection_key
        frame_key: tuple[object, ...] = (
            id(simulation),
            simulation.tick,
            simulation.command_count,
            selection_key,
            app.inspected_entity_id,
            len(simulation.entities),
        )
        if frame_key == self._frame_key and self._frame is not None:
            if (
                self._frame.framebuffer_size != framebuffer_size
                or self._frame.terrain_origin != origin
                or self._frame.terrain_tile_size != tile_size
            ):
                self._frame = replace(
                    self._frame,
                    framebuffer_size=framebuffer_size,
                    terrain_origin=origin,
                    terrain_tile_size=tile_size,
                )
            return self._frame

        shape_buffer, shape_count = self._build_entities(app, (0, 0), 1.0)
        entity_buffer, unit_count, building_count, selected_unit_count = pack_entities(app)
        line_buffer, line_vertex_count, path_count = self._build_paths(app, (0, 0), 1.0)
        (
            projectile_shape_buffer,
            projectile_shape_count,
            projectile_line_buffer,
            projectile_line_vertex_count,
        ) = self._build_projectiles(app, (0, 0), 1.0)
        shape_buffer += projectile_shape_buffer
        shape_count += projectile_shape_count
        line_buffer += projectile_line_buffer
        line_vertex_count += projectile_line_vertex_count
        self._frame_key = frame_key
        self._frame_entity_ids = frozenset(simulation.entities)
        self._frame = OpenGLFrame(
            framebuffer_size=framebuffer_size,
            pixel_scale=1.0,
            tick=simulation.tick,
            terrain_count=self._terrain_count,
            terrain_shape_count=self._terrain_shape_count,
            unit_count=unit_count,
            building_count=building_count,
            selected_unit_count=selected_unit_count,
            path_count=path_count,
            shape_count=shape_count,
            line_vertex_count=line_vertex_count,
            terrain_buffer=self._terrain_buffer,
            terrain_pixels=self._terrain_pixels,
            terrain_size=(simulation.game_map.width, simulation.game_map.height),
            terrain_origin=origin,
            terrain_tile_size=tile_size,
            shape_buffer=shape_buffer,
            line_buffer=line_buffer,
            entity_buffer=entity_buffer,
        )
        return self._frame

    @staticmethod
    def _build_terrain(
        simulation: Simulation,
    ) -> tuple[bytes, bytes, int, int]:
        values = array("f")
        map_width = simulation.game_map.width
        map_height = simulation.game_map.height
        palette = {terrain: bytes(color) for terrain, color in TERRAIN_COLORS.items()}
        pixels = b"".join(
            palette[terrain] for row in simulation.game_map.terrain for terrain in row
        )
        append_shape(
            values,
            center=(map_width / 2, map_height / 2),
            half_size=(map_width / 2, map_height / 2),
            color=(1.0, 1.0, 1.0, 1.0),
            circle=False,
        )
        values[-6] = -4.0  # One texel per exact cell; GPU clips the world rectangle.
        count = map_width * map_height
        grid_color = _float_color(GRID_COLOR)
        for cell in range(0, simulation.game_map.width + 1, 8):
            append_shape(
                values,
                center=(cell, map_height / 2),
                half_size=(0.0, map_height / 2),
                color=grid_color,
                circle=False,
            )
            values[-6] = -1.0  # Vertical grid: keep its width in screen pixels.
        for cell in range(0, simulation.game_map.height + 1, 8):
            append_shape(
                values,
                center=(map_width / 2, cell),
                half_size=(map_width / 2, 0.0),
                color=grid_color,
                circle=False,
            )
            values[-6] = -2.0  # Horizontal grid.
        return values.tobytes(), pixels, count, len(values) // SHAPE_FLOATS

    @staticmethod
    def _build_entities(
        app: OpenGLRenderState,
        origin: tuple[int, int],
        tile_size: float,
    ) -> tuple[bytes, int]:
        values = array("f")
        simulation = app.simulation
        selected_entities = app.selected_entities
        origin_x, origin_y = origin

        inspected = simulation.entities.get(app.inspected_entity_id or "")
        radius, work_range = interaction_range(inspected) if inspected is not None else (0.0, False)
        if inspected is not None and radius > 0 and len(selected_entities) <= 1:
            range_radius = radius * tile_size
            previous_position = app.previous_entity_position(inspected.entity_id)
            previous_selection_position = (
                previous_position
                if inspected.category is EntityCategory.UNIT
                else inspected.selection_position
            )
            append_shape(
                values,
                center=(
                    origin_x + inspected.selection_position.x * tile_size,
                    origin_y + inspected.selection_position.y * tile_size,
                ),
                previous_center=(
                    origin_x + previous_selection_position.x * tile_size,
                    origin_y + previous_selection_position.y * tile_size,
                ),
                half_size=(range_radius, range_radius),
                color=(0.0, 0.0, 0.0, 0.0),
                circle=True,
                outline_width=1.0,
                outline_color=_float_color((105, 232, 172) if work_range else (255, 218, 100)),
            )

        for automation in simulation.live_automations:
            if (
                automation.kind is not AutomationKind.DEFEND
                or not isinstance(automation.parameters, DefendParameters)
                or not automation.parameters.gathering_point
                or not automation.entity_ids
            ):
                continue
            parameters = automation.parameters
            world_center = target_center(parameters.target)
            center = (
                origin_x + world_center.x * tile_size,
                origin_y + world_center.y * tile_size,
            )
            radius = (parameters.assembly_radius + 0.8) * tile_size
            append_shape(
                values,
                center=center,
                half_size=(radius, radius),
                minimum_half_size=(8.0, 8.0),
                color=(92 / 255, 177 / 255, 1.0, 20 / 255),
                circle=True,
                outline_width=2.0,
                outline_color=(135 / 255, 205 / 255, 1.0, 72 / 255),
            )
            append_shape(
                values,
                center=center,
                half_size=(radius, radius),
                minimum_half_size=(8.0, 8.0),
                pixel_padding=(-5.0, -5.0),
                color=(0.0, 0.0, 0.0, 0.0),
                circle=True,
                outline_width=2.0,
                outline_color=(190 / 255, 230 / 255, 1.0, 35 / 255),
            )

        return (
            values.tobytes(),
            len(values) // SHAPE_FLOATS,
        )

    @staticmethod
    def _build_paths(
        app: OpenGLRenderState,
        origin: tuple[int, int],
        tile_size: float,
    ) -> tuple[bytes, int, int]:
        values = array("f")
        origin_x, origin_y = origin
        path_count = 0
        for entity_id in app.representative_path_entity_ids():
            entity = app.simulation.entities[entity_id]
            drawn = False
            for points, rgb in order_feedback(app, entity):
                color = _float_color(rgb)
                if len(points) < 2:
                    continue
                for first, second in zip(points, points[1:], strict=False):
                    _append_line_vertex(
                        values,
                        origin_x + first.x * tile_size,
                        origin_y + first.y * tile_size,
                        color,
                    )
                    _append_line_vertex(
                        values,
                        origin_x + second.x * tile_size,
                        origin_y + second.y * tile_size,
                        color,
                    )
                drawn = True
            path_count += int(drawn)
        return values.tobytes(), len(values) // LINE_FLOATS, path_count

    def _build_projectiles(
        self,
        app: OpenGLRenderState,
        origin: tuple[int, int],
        tile_size: float,
    ) -> tuple[bytes, int, bytes, int]:
        """Pack projectile feedback into the normal GPU shape and line batches."""

        shapes = array("f")
        lines = array("f")
        simulation = app.simulation
        origin_x, origin_y = origin

        def append_trajectory(points: tuple[Point, ...] | list[Point], color: FloatColor) -> None:
            for first, second in zip(points, points[1:], strict=False):
                _append_line_vertex(
                    lines,
                    origin_x + first.x * tile_size,
                    origin_y + first.y * tile_size,
                    color,
                )
                _append_line_vertex(
                    lines,
                    origin_x + second.x * tile_size,
                    origin_y + second.y * tile_size,
                    color,
                )

        trace_chunks = []
        retained = {}
        for trace in simulation.projectile_traces:
            if trace.weapon_kind.profile.splash_radius > 0:
                for point, radius, blast_color, width in blast_circles(trace, simulation.tick):
                    append_shape(
                        shapes,
                        center=(origin_x + point.x * tile_size, origin_y + point.y * tile_size),
                        half_size=(radius * tile_size, radius * tile_size),
                        minimum_half_size=(1.0, 1.0),
                        color=_float_color(blast_color) if width == 0 else (0.0, 0.0, 0.0, 0.0),
                        circle=True,
                        outline_width=float(width),
                        outline_color=_float_color(blast_color),
                    )
                continue
            cached = self._trace_buffers.get(trace.projectile_id)
            if cached is None or cached[0] is not trace:
                append_trajectory(trace.points, _float_color(PROJECTILE_COLORS[trace.weapon_kind]))
                cached = (trace, lines.tobytes())
                del lines[:]
            retained[trace.projectile_id] = cached
            trace_chunks.append(cached[1])
        self._trace_buffers = retained
        for projectile in simulation.projectiles.values():
            raw_color = PROJECTILE_COLORS[projectile.weapon_kind]
            color = _float_color(raw_color)
            shell = projectile.weapon_kind.profile.splash_radius > 0
            if not shell:
                append_trajectory(projectile.trajectory, color)
            destination_color = _float_color(
                (raw_color[0] // 2, raw_color[1] // 2, raw_color[2] // 2)
            )
            if not shell:
                _append_line_vertex(
                    lines,
                    origin_x + projectile.position.x * tile_size,
                    origin_y + projectile.position.y * tile_size,
                    destination_color,
                )
                _append_line_vertex(
                    lines,
                    origin_x + projectile.destination.x * tile_size,
                    origin_y + projectile.destination.y * tile_size,
                    destination_color,
                )
            center = (
                origin_x + projectile.position.x * tile_size,
                origin_y + projectile.position.y * tile_size,
            )
            previous_position = app.previous_projectile_position(projectile.projectile_id)
            previous_center = (
                origin_x + previous_position.x * tile_size,
                origin_y + previous_position.y * tile_size,
            )
            if shell:
                append_shape(
                    shapes,
                    center=center,
                    previous_center=previous_center,
                    half_size=(0.0, 0.0),
                    minimum_half_size=(3.0, 3.0),
                    color=_float_color((45, 39, 32)),
                    circle=True,
                )
                visual = shell_visual_position(projectile, projectile.position)
                previous_visual = shell_visual_position(projectile, previous_position)
                center = (origin_x + visual.x * tile_size, origin_y + visual.y * tile_size)
                previous_center = (
                    origin_x + previous_visual.x * tile_size,
                    origin_y + previous_visual.y * tile_size,
                )
            append_shape(
                shapes,
                center=center,
                previous_center=previous_center,
                half_size=(0.0, 0.0),
                minimum_half_size=(SHELL_RIM_RADIUS, SHELL_RIM_RADIUS) if shell else (3.0, 3.0),
                color=_float_color((255, 226, 150) if shell else (255, 255, 255)),
                circle=True,
            )
            append_shape(
                shapes,
                center=center,
                previous_center=previous_center,
                half_size=(0.0, 0.0),
                minimum_half_size=(SHELL_CORE_RADIUS, SHELL_CORE_RADIUS) if shell else (2.0, 2.0),
                color=color,
                circle=True,
            )
        line_buffer = b"".join((*trace_chunks, lines.tobytes()))
        return (
            shapes.tobytes(),
            len(shapes) // SHAPE_FLOATS,
            line_buffer,
            len(line_buffer) // (4 * LINE_FLOATS),
        )


class OpenGLRendererError(RuntimeError):
    """Raised when AIRTS cannot create or operate its required OpenGL renderer."""


class OpenGLRenderer:
    """Render native-pixel AIRTS batches with OpenGL 3.3 instancing and shaders."""

    def __init__(self, module: Any, context: Any) -> None:
        self._module = module
        self._context = context
        self._builder = OpenGLFrameBuilder()
        self._feedback: GPUFeedback | None = None
        self._uploaded_frame: OpenGLFrame | None = None
        self._uploaded_terrain: bytes | None = None
        self._overlay_key: tuple[object, ...] | None = None
        self._overlay_tick: int | None = None
        self._overlay_regions: tuple[pygame.Rect, ...] = ()
        self._overlay_surface: pygame.Surface | None = None
        self._overlay_texture: Any = None
        self._terrain_texture: Any = None
        self._sprite_texture: Any = None
        self._overlay_vertex_array: Any = None
        self._line_vertex_array: Any = None
        self._shape_vertex_array: Any = None
        self._terrain_vertex_array: Any = None
        self._overlay_quad_buffer: Any = None
        self._line_buffer: Any = None
        self._shape_buffer: Any = None
        self._terrain_buffer: Any = None
        self._quad_buffer: Any = None
        self._overlay_program: Any = None
        self._line_program: Any = None
        self._shape_program: Any = None
        self._entity_program: Any = None
        self._entity_buffer: Any = None
        self._entity_vertex_array: Any = None
        try:
            self._shape_program = context.program(
                vertex_shader=_SHAPE_VERTEX_SHADER,
                fragment_shader=_SHAPE_FRAGMENT_SHADER,
            )
            self._line_program = context.program(
                vertex_shader=_LINE_VERTEX_SHADER,
                fragment_shader=_LINE_FRAGMENT_SHADER,
            )
            self._overlay_program = context.program(
                vertex_shader=_OVERLAY_VERTEX_SHADER,
                fragment_shader=_OVERLAY_FRAGMENT_SHADER,
            )
            self._entity_program = context.program(
                vertex_shader=ENTITY_VERTEX_SHADER,
                geometry_shader=ENTITY_GEOMETRY_SHADER,
                fragment_shader=_SHAPE_FRAGMENT_SHADER,
            )
            self._quad_buffer = context.buffer(
                array("f", (-1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)).tobytes()
            )
            self._terrain_buffer = context.buffer(
                reserve=_INITIAL_TERRAIN_BUFFER_BYTES,
                dynamic=True,
            )
            self._shape_buffer = context.buffer(
                reserve=_INITIAL_SHAPE_BUFFER_BYTES,
                dynamic=True,
            )
            self._line_buffer = context.buffer(
                reserve=_INITIAL_LINE_BUFFER_BYTES,
                dynamic=True,
            )
            self._overlay_quad_buffer = context.buffer(
                array(
                    "f",
                    (
                        -1.0,
                        -1.0,
                        0.0,
                        0.0,
                        1.0,
                        -1.0,
                        1.0,
                        0.0,
                        -1.0,
                        1.0,
                        0.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                    ),
                ).tobytes()
            )
            instance_format = "2f 2f 2f 2f 2f 2f 2f 4f 2f 4f /i"
            instance_attributes = (
                "in_center",
                "in_previous_center",
                "in_half_size",
                "in_minimum_half_size",
                "in_pixel_offset",
                "in_pixel_padding",
                "in_size_fraction",
                "in_color",
                "in_shape_outline",
                "in_outline_color",
            )
            self._terrain_vertex_array = context.vertex_array(
                self._shape_program,
                [
                    (self._quad_buffer, "2f", "in_corner"),
                    (self._terrain_buffer, instance_format, *instance_attributes),
                ],
            )
            self._shape_vertex_array = context.vertex_array(
                self._shape_program,
                [
                    (self._quad_buffer, "2f", "in_corner"),
                    (self._shape_buffer, instance_format, *instance_attributes),
                ],
            )
            self._line_vertex_array = context.vertex_array(
                self._line_program,
                [(self._line_buffer, "2f 4f", "in_position", "in_color")],
            )
            self._overlay_vertex_array = context.vertex_array(
                self._overlay_program,
                [(self._overlay_quad_buffer, "2f 2f", "in_position", "in_uv")],
            )
            self._entity_buffer = context.buffer(reserve=_INITIAL_SHAPE_BUFFER_BYTES, dynamic=True)
            self._entity_vertex_array = context.vertex_array(
                self._entity_program,
                [(self._entity_buffer, "4f 4f 4f 4f 4f 4f 4f 4f", *ENTITY_ATTRIBUTES)],
            )
            self._overlay_program["overlay_texture"].value = 0
            atlas_size, pixels = atlas_pixels()
            self._sprite_texture = context.texture(atlas_size, 4, data=pixels, alignment=1)
            self._sprite_texture.filter = module.NEAREST, module.NEAREST
            self._shape_program["sprite_texture"].value = 2
            self._entity_program["sprite_texture"].value = 2
            self._feedback = GPUFeedback(module, context)
            context.enable(module.BLEND)
            context.blend_func = (module.SRC_ALPHA, module.ONE_MINUS_SRC_ALPHA)
        except Exception as error:
            self._release_gpu_resources()
            raise OpenGLRendererError(f"failed to initialize OpenGL resources: {error}") from error

    @classmethod
    def from_active_context(cls) -> OpenGLRenderer:
        """Attach to the OpenGL context created by the active Pygame window."""

        module = _load_moderngl()
        try:
            context = module.create_context(require=330)
        except Exception as error:
            raise OpenGLRendererError(
                "OpenGL 3.3 context creation failed; verify the GPU driver and WSLg/SDL setup: "
                f"{error}"
            ) from error
        return cls(module, context)

    @classmethod
    def from_standalone_context(cls, *, backend: str | None = None) -> OpenGLRenderer:
        """Create a headless OpenGL context for native-resolution rendering tests."""

        module = _load_moderngl()
        settings = {"backend": backend} if backend is not None else {}
        try:
            context = module.create_context(require=330, standalone=True, **settings)
        except Exception as error:
            backend_name = repr(backend) if backend is not None else "the platform default"
            raise OpenGLRendererError(
                f"standalone OpenGL 3.3 context creation failed with {backend_name}: {error}"
            ) from error
        return cls(module, context)

    @property
    def info(self) -> dict[str, object]:
        """Expose driver identity for diagnostics without leaking the mutable context."""

        return {
            "version_code": self._context.version_code,
            "vendor": self._context.info.get("GL_VENDOR", "unknown"),
            "renderer": self._context.info.get("GL_RENDERER", "unknown"),
            "version": self._context.info.get("GL_VERSION", "unknown"),
        }

    def render(
        self,
        app: OpenGLRenderState,
        framebuffer_size: tuple[int, int],
        *,
        framebuffer: Any | None = None,
    ) -> OpenGLFrame:
        """Draw one complete native-resolution frame and return its submitted batch metadata."""

        app._prune_removed_entities()
        frame = self._builder.staged_frame(app, framebuffer_size)
        if frame is None:
            frame = self._builder.build(app, framebuffer_size)
        target = framebuffer if framebuffer is not None else self._context.screen
        if target is None:
            raise OpenGLRendererError("the OpenGL context has no screen framebuffer")
        try:
            target.use()
            width, height = framebuffer_size
            self._context.viewport = (0, 0, width, height)
            self._context.clear(BACKGROUND[0] / 255, BACKGROUND[1] / 255, BACKGROUND[2] / 255, 1.0)
            self._shape_program["viewport_size"].value = framebuffer_size
            self._shape_program["interpolation_alpha"].value = (
                1.0 if frame.tick != app.simulation.tick else max(0.0, min(1.0, app.render_alpha))
            )
            self._line_program["viewport_size"].value = framebuffer_size
            self._line_program["origin"].value = frame.terrain_origin
            self._line_program["tile"].value = frame.terrain_tile_size
            self._upload_frame(frame)
            self._sprite_texture.use(location=2)
            if frame.terrain_shape_count:
                self._shape_program["terrain_origin"].value = frame.terrain_origin
                self._shape_program["terrain_tile_size"].value = frame.terrain_tile_size
                self._shape_program["terrain_texture"].value = 1
                self._shape_program["terrain_size"].value = frame.terrain_size
                self._terrain_texture.use(location=1)
                self._terrain_vertex_array.render(
                    mode=self._module.TRIANGLE_STRIP,
                    vertices=4,
                    instances=frame.terrain_shape_count,
                )
            if frame.line_vertex_count:
                self._line_vertex_array.render(
                    mode=self._module.LINES,
                    vertices=frame.line_vertex_count,
                )
            if frame.shape_count:
                self._draw_entities(app, frame, framebuffer_size)
                self._shape_vertex_array.render(
                    mode=self._module.TRIANGLE_STRIP,
                    vertices=4,
                    instances=frame.shape_count,
                )
            else:
                self._draw_entities(app, frame, framebuffer_size)
            assert self._feedback is not None
            self._feedback.draw(
                app,
                framebuffer_size,
                interpolation_alpha=0.0 if frame.tick != app.simulation.tick else app.render_alpha,
            )
            self._draw_overlay(app, frame)
            self._draw_minimap(app, frame, framebuffer_size)
        except OpenGLRendererError:
            raise
        except Exception as error:
            raise OpenGLRendererError(f"OpenGL frame submission failed: {error}") from error
        return frame

    def finish(self) -> None:
        """Wait for submitted GPU work, used only by performance verification."""

        self._context.finish()

    def create_offscreen_target(
        self,
        framebuffer_size: tuple[int, int],
    ) -> OpenGLOffscreenTarget:
        """Create a color framebuffer used by native-resolution GPU tests."""

        if framebuffer_size[0] <= 0 or framebuffer_size[1] <= 0:
            raise ValueError("framebuffer dimensions must be positive")
        texture = self._context.texture(framebuffer_size, 4)
        framebuffer = self._context.framebuffer(color_attachments=(texture,))
        return OpenGLOffscreenTarget(framebuffer_size, framebuffer, texture)

    def release(self) -> None:
        """Release renderer-owned GPU resources before the Pygame context is destroyed."""

        self._release_gpu_resources()
        self._overlay_surface = None
        self._overlay_tick = None
        self._overlay_regions = ()
        self._uploaded_frame = None
        self._uploaded_terrain = None

    def _release_gpu_resources(self) -> None:
        """Release every resource created so far and make repeated cleanup harmless."""

        if self._feedback is not None:
            self._feedback.release()
            self._feedback = None
        for attribute in _GPU_RESOURCE_ATTRIBUTES:
            resource = getattr(self, attribute)
            if resource is not None:
                resource.release()
                setattr(self, attribute, None)

    def _upload_frame(self, frame: OpenGLFrame) -> None:
        if frame.terrain_buffer is not self._uploaded_terrain:
            _replace_buffer(self._terrain_buffer, frame.terrain_buffer)
            texture = self._context.texture(
                frame.terrain_size, 3, data=frame.terrain_pixels, alignment=1
            )
            previous_texture = self._terrain_texture
            self._terrain_texture = texture
            if previous_texture is not None:
                previous_texture.release()
            texture.filter = self._module.NEAREST, self._module.NEAREST
            self._uploaded_terrain = frame.terrain_buffer
        if frame is self._uploaded_frame:
            return
        if (
            self._uploaded_frame is None
            or frame.shape_buffer is not self._uploaded_frame.shape_buffer
        ):
            _replace_buffer(self._shape_buffer, frame.shape_buffer)
        if (
            self._uploaded_frame is None
            or frame.line_buffer is not self._uploaded_frame.line_buffer
        ):
            _replace_buffer(self._line_buffer, frame.line_buffer)
        if (
            self._uploaded_frame is None
            or frame.entity_buffer is not self._uploaded_frame.entity_buffer
        ):
            _replace_buffer(self._entity_buffer, frame.entity_buffer)
        self._uploaded_frame = frame

    def _draw_entities(
        self, app: OpenGLRenderState, frame: OpenGLFrame, size: tuple[int, int]
    ) -> None:
        program = self._entity_program
        program["viewport_size"].value = size
        program["terrain_origin"].value = frame.terrain_origin
        program["terrain_tile_size"].value = frame.terrain_tile_size
        program["interpolation_alpha"].value = (
            1.0 if frame.tick != app.simulation.tick else max(0.0, min(1.0, app.render_alpha))
        )
        program["minimap_mode"].value = 0
        if frame.unit_count + frame.building_count:
            self._entity_vertex_array.render(
                mode=self._module.POINTS,
                vertices=frame.unit_count + frame.building_count,
            )

    def _draw_minimap(
        self, app: OpenGLRenderState, frame: OpenGLFrame, size: tuple[int, int]
    ) -> None:
        if app.settings_open or app.help_open or app.save_panel.open or app.lobby is not None:
            return
        rect = app.minimap.map_rect
        program = self._entity_program
        program["minimap_rect"].value = tuple(rect)
        program["world_size"].value = (
            app.simulation.game_map.width,
            app.simulation.game_map.height,
        )
        program["minimap_viewport"].value = tuple(app.minimap.viewport_rect(app))
        self._context.scissor = (rect.x, size[1] - rect.bottom, rect.width, rect.height)
        try:
            for mode in (1, 2):
                program["minimap_mode"].value = mode
                if frame.unit_count + frame.building_count:
                    self._entity_vertex_array.render(
                        mode=self._module.POINTS,
                        vertices=frame.unit_count + frame.building_count,
                    )
            program["minimap_mode"].value = 3
            self._entity_vertex_array.render(mode=self._module.POINTS, vertices=1)
        finally:
            self._context.scissor = None

    def _draw_overlay(self, app: OpenGLRenderState, frame: OpenGLFrame) -> None:
        overlay_key = (frame.framebuffer_size, app._opengl_overlay_key())
        if (
            self._overlay_surface is None
            or self._overlay_surface.get_size() != frame.framebuffer_size
        ):
            self._overlay_surface = pygame.Surface(frame.framebuffer_size, pygame.SRCALPHA, 32)
            if self._overlay_texture is not None:
                self._overlay_texture.release()
            self._overlay_texture = self._context.texture(frame.framebuffer_size, 4)
            self._overlay_texture.filter = (self._module.LINEAR, self._module.LINEAR)
            self._overlay_texture.repeat_x = False
            self._overlay_texture.repeat_y = False
            self._overlay_key = None
            self._overlay_tick = None
        defer_tick_refresh = (
            self._overlay_key is not None
            and self._overlay_tick != app.simulation.tick
            and app.render_alpha <= 0.0
        )
        if overlay_key != self._overlay_key and not defer_tick_refresh:
            assert self._overlay_surface is not None
            assert self._overlay_texture is not None
            partial_regions = _partial_overlay_regions(app, self._overlay_key, overlay_key)
            if partial_regions is not None:
                dirty_regions = _merge_overlay_regions((*self._overlay_regions, *partial_regions))
                app._draw_opengl_partial_overlay(self._overlay_surface, dirty_regions)
                _write_overlay_regions(
                    self._overlay_texture,
                    self._overlay_surface,
                    dirty_regions,
                    frame.framebuffer_size,
                )
            else:
                self._overlay_surface.fill((0, 0, 0, 0))
                app._draw_opengl_overlay(self._overlay_surface)
                self._overlay_texture.write(
                    pygame.image.tobytes(self._overlay_surface, "RGBA", True)
                )
            self._overlay_regions = app._opengl_dynamic_overlay_regions()
            self._overlay_key = overlay_key
            self._overlay_tick = app.simulation.tick
        assert self._overlay_texture is not None
        self._overlay_texture.use(location=0)
        self._overlay_vertex_array.render(mode=self._module.TRIANGLE_STRIP, vertices=4)


def _load_moderngl() -> Any:
    try:
        return cast(Any, import_module("moderngl"))
    except ModuleNotFoundError as error:
        raise OpenGLRendererError(
            "ModernGL is required for the default GPU renderer; reinstall AIRTS from "
            "pyproject.toml or run the explicit software renderer"
        ) from error


def _replace_buffer(buffer: Any, data: bytes) -> None:
    required_size = max(4, len(data))
    current_size = buffer.size
    if not isinstance(current_size, int) or current_size < required_size:
        current_size = current_size if isinstance(current_size, int) else 4
        buffer.orphan(max(required_size, current_size * 2, 4_096))
    if data:
        buffer.write(data)


def _partial_overlay_regions(
    app: OpenGLRenderState,
    previous: tuple[object, ...] | None,
    current: tuple[object, ...],
) -> tuple[pygame.Rect, ...] | None:
    if previous is None or len(previous) != 2 or len(current) != 2:
        return None
    previous_key = previous[1]
    current_key = current[1]
    if not isinstance(previous_key, tuple) or not isinstance(current_key, tuple):
        return None
    return app._opengl_partial_overlay_regions(previous_key, current_key)


def _merge_overlay_regions(regions: tuple[pygame.Rect, ...]) -> tuple[pygame.Rect, ...]:
    """Coalesce overlapping dirty rectangles before CPU conversion and GPU upload."""

    merged: list[pygame.Rect] = []
    for region in regions:
        candidate = region.copy()
        index = 0
        while index < len(merged):
            if candidate.colliderect(merged[index]) or candidate.contains(merged[index]):
                candidate.union_ip(merged.pop(index))
                index = 0
            else:
                index += 1
        merged.append(candidate)
    return tuple(merged)


def _write_overlay_regions(
    texture: Any,
    surface: pygame.Surface,
    regions: tuple[pygame.Rect, ...],
    framebuffer_size: tuple[int, int],
) -> None:
    bounds = surface.get_rect()
    for region in regions:
        clipped = region.clip(bounds)
        if not clipped.width or not clipped.height:
            continue
        pixels = pygame.image.tobytes(surface.subsurface(clipped), "RGBA", True)
        texture.write(
            pixels,
            viewport=(
                clipped.x,
                framebuffer_size[1] - clipped.bottom,
                clipped.width,
                clipped.height,
            ),
        )


def _float_color(color: Color) -> FloatColor:
    return color[0] / 255, color[1] / 255, color[2] / 255, 1.0


def _append_line_vertex(
    values: array[float],
    x: float,
    y: float,
    color: FloatColor,
) -> None:
    values.extend((x, y, *color))


_SHAPE_VERTEX_SHADER = """
#version 330
uniform vec2 viewport_size;
uniform float interpolation_alpha;
uniform vec2 terrain_origin;
uniform float terrain_tile_size;
in vec2 in_corner;
in vec2 in_center;
in vec2 in_previous_center;
in vec2 in_half_size;
in vec2 in_minimum_half_size;
in vec2 in_pixel_offset;
in vec2 in_pixel_padding;
in vec2 in_size_fraction;
in vec4 in_color;
in vec2 in_shape_outline;
in vec4 in_outline_color;
out vec2 local_position;
out vec2 world_position;
flat out vec2 half_size;
flat out vec4 fill_color;
flat out float circle_shape;
flat out float outline_width;
flat out vec4 outline_color;

void main() {
    vec2 center = mix(in_previous_center, in_center, interpolation_alpha);
    vec2 size = max(in_half_size * terrain_tile_size, in_minimum_half_size) + in_pixel_padding;
    center = terrain_origin + center * terrain_tile_size + in_pixel_offset;
    if (in_shape_outline.x < -0.5) {
        vec2 padding = in_shape_outline.x < -2.5 ? vec2(0.5)
            : in_shape_outline.x < -1.5 ? vec2(0.0, 0.5) : vec2(0.5, 0.0);
        size += padding;
    }
    if (in_shape_outline.x > 1.5 && in_shape_outline.x < 2.5) {
        center.x += size.x * (in_size_fraction.x - 1.0);
        // Signed world height anchors bars above/below sprites; thickness stays in pixels.
        center.y += sign(in_half_size.y) * max(abs(in_half_size.y) * terrain_tile_size, 7.0);
        size.y = in_minimum_half_size.y;
    }
    size *= in_size_fraction;
    vec2 offset = in_corner * size;
    if (in_shape_outline.x > 2.5) {
        float angle = mix(in_outline_color.x, in_outline_color.y, interpolation_alpha);
        float c = cos(angle), s = sin(angle);
        offset = vec2(c * offset.x - s * offset.y, s * offset.x + c * offset.y);
    }
    vec2 pixel_position = center + offset;
    vec2 normalized = vec2(
        pixel_position.x * 2.0 / viewport_size.x - 1.0,
        1.0 - pixel_position.y * 2.0 / viewport_size.y
    );
    gl_Position = vec4(normalized, 0.0, 1.0);
    world_position = (pixel_position - terrain_origin) / terrain_tile_size;
    local_position = in_corner;
    half_size = size;
    fill_color = in_color;
    circle_shape = in_shape_outline.x;
    outline_width = in_shape_outline.y;
    outline_color = in_outline_color;
}
"""

_SHAPE_FRAGMENT_SHADER = (
    """
#version 330
in vec2 local_position;
in vec2 world_position;
uniform sampler2D terrain_texture;
uniform float terrain_tile_size;
uniform sampler2D sprite_texture;
uniform ivec2 terrain_size;
flat in vec2 half_size;
flat in vec4 fill_color;
flat in float circle_shape;
flat in float outline_width;
flat in vec4 outline_color;
out vec4 fragment_color;

TERRAIN_DETAIL
void main() {
    if (circle_shape > 4.5) {
        if (outline_width > 0.0 && min((1.0-abs(local_position.x))*half_size.x,
            (1.0-abs(local_position.y))*half_size.y) > outline_width) discard;
        fragment_color = outline_width > 0.0 ? outline_color : fill_color;
        return;
    }
    if (circle_shape < -3.5) {
        ivec2 cell = clamp(ivec2(floor(world_position)), ivec2(0), terrain_size - ivec2(1));
        vec3 base = texelFetch(terrain_texture, cell, 0).rgb;
        if (terrain_tile_size >= DETAIL_MIN_TILE) {
            base = terrainPixel(base, cell, world_position);
        }
        fragment_color = vec4(base, 1.0);
        return;
    }
    if (circle_shape > 2.5) {
        int index = int(outline_width + 0.5);
        ivec2 cell = ivec2(index % SPRITE_COLUMNS, index / SPRITE_COLUMNS) * SPRITE_SIDE;
        ivec2 pixel = clamp(ivec2(floor((local_position + 1.0) * (float(SPRITE_SIDE) / 2.0))), ivec2(0), ivec2(SPRITE_SIDE - 1));
        fragment_color = texelFetch(sprite_texture, cell + pixel, 0) * fill_color;
        if (fragment_color.a < 0.01) discard;
        return;
    }
    float signed_distance;
    if (circle_shape > 0.5 && circle_shape < 1.5) {
        signed_distance = (1.0 - length(local_position)) * min(half_size.x, half_size.y);
    } else {
        vec2 edge_distance = (1.0 - abs(local_position)) * half_size;
        signed_distance = min(edge_distance.x, edge_distance.y);
    }
    float antialias_width = max(fwidth(signed_distance), 0.75);
    float outer_alpha = smoothstep(-antialias_width, antialias_width, signed_distance);
    float inner_alpha = outline_width > 0.0
        ? smoothstep(-antialias_width, antialias_width, signed_distance - outline_width)
        : outer_alpha;
    float outline_band = max(0.0, outer_alpha - inner_alpha);
    vec4 color = fill_color;
    float fill_alpha = color.a * inner_alpha;
    float outline_alpha = outline_color.a * outline_band;
    float combined_alpha = fill_alpha + outline_alpha;
    vec3 premultiplied_color = color.rgb * fill_alpha
        + outline_color.rgb * outline_alpha;
    fragment_color = vec4(
        premultiplied_color / max(combined_alpha, 0.001),
        combined_alpha
    );
    if (combined_alpha <= 0.001) {
        discard;
    }
}
""".replace("SPRITE_COLUMNS", str(ATLAS_COLUMNS))
    .replace("SPRITE_SIDE", str(TILE_PIXELS))
    .replace("TERRAIN_DETAIL", fragment_shader())
    .replace("DETAIL_MIN_TILE", f"{DETAIL_MIN_TILE}.0")
)


_LINE_VERTEX_SHADER = """
#version 330
uniform vec2 viewport_size;
uniform vec2 origin;
uniform float tile;
in vec2 in_position;
in vec4 in_color;
out vec4 line_color;

void main() {
    vec2 position = origin + in_position * tile;
    vec2 normalized = vec2(
        position.x * 2.0 / viewport_size.x - 1.0,
        1.0 - position.y * 2.0 / viewport_size.y
    );
    gl_Position = vec4(normalized, 0.0, 1.0);
    line_color = in_color;
}
"""

_LINE_FRAGMENT_SHADER = """
#version 330
in vec4 line_color;
out vec4 fragment_color;

void main() {
    fragment_color = line_color;
}
"""

_OVERLAY_VERTEX_SHADER = """
#version 330
in vec2 in_position;
in vec2 in_uv;
out vec2 texture_coordinate;

void main() {
    gl_Position = vec4(in_position, 0.0, 1.0);
    texture_coordinate = in_uv;
}
"""

_OVERLAY_FRAGMENT_SHADER = """
#version 330
uniform sampler2D overlay_texture;
in vec2 texture_coordinate;
out vec4 fragment_color;

void main() {
    fragment_color = texture(overlay_texture, texture_coordinate);
}
"""
