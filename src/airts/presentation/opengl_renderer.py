"""Native-pixel OpenGL presentation adapter for AIRTS."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol, cast

import pygame

from airts.automations import AutomationKind, DefendParameters, target_center
from airts.geometry import Point
from airts.simulation import Simulation
from airts.world.entities import Entity
from airts.world.map_model import EntityCategory, EntityKind, Terrain

type Color = tuple[int, int, int]
type FloatColor = tuple[float, float, float, float]

SHAPE_FLOATS = 16
LINE_FLOATS = 6

_INITIAL_TERRAIN_BUFFER_BYTES = 512 * 1024
_INITIAL_SHAPE_BUFFER_BYTES = 256 * 1024
_INITIAL_LINE_BUFFER_BYTES = 256 * 1024

_GPU_RESOURCE_ATTRIBUTES = (
    "_overlay_texture",
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

_TERRAIN_COLORS: dict[Terrain, Color] = {
    Terrain.GRASS: (64, 102, 60),
    Terrain.ROAD: (119, 106, 77),
    Terrain.FOREST: (43, 78, 48),
    Terrain.WATER: (42, 91, 132),
    Terrain.ROCK: (66, 69, 72),
    Terrain.BRIDGE: (148, 126, 82),
}

_ENTITY_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (82, 211, 237),
    EntityKind.LIGHT_TANK: (235, 221, 93),
    EntityKind.HEAVY_TANK: (230, 139, 75),
    EntityKind.BUILDER: (99, 220, 176),
    EntityKind.FACTORY: (112, 142, 181),
    EntityKind.REPAIR_HUB: (110, 178, 151),
    EntityKind.COMMAND_CENTER: (155, 129, 190),
    EntityKind.RESOURCE_GENERATOR: (198, 168, 88),
}

_ENTITY_FLOAT_COLORS: dict[EntityKind, FloatColor] = {
    kind: (color[0] / 255, color[1] / 255, color[2] / 255, 1.0)
    for kind, color in _ENTITY_COLORS.items()
}
_LARGE_SELECTED_ENTITY_FLOAT_COLORS: dict[EntityKind, FloatColor] = {
    kind: (
        min(255, color[0] + 45) / 255,
        min(255, color[1] + 45) / 255,
        min(255, color[2] + 45) / 255,
        1.0,
    )
    for kind, color in _ENTITY_COLORS.items()
}
_ENEMY_FLOAT_COLOR: FloatColor = (218 / 255, 78 / 255, 78 / 255, 1.0)

_PROJECTILE_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (120, 225, 255),
    EntityKind.LIGHT_TANK: (255, 232, 105),
    EntityKind.HEAVY_TANK: (255, 135, 70),
}


class OpenGLRenderState(Protocol):
    """The UI-owned state needed to build a GPU frame without importing Pygame."""

    MAX_SELECTED_PATHS: int
    simulation: Simulation
    selected_entities: set[str]
    inspected_entity_id: str | None
    render_alpha: float

    @property
    def tile_size(self) -> float: ...

    @property
    def map_origin(self) -> tuple[int, int]: ...

    def _representative_path_entity_ids(self) -> tuple[str, ...]: ...

    def previous_entity_position(self, entity_id: str) -> Point: ...

    def previous_projectile_position(self, projectile_id: str) -> Point: ...

    @staticmethod
    def _simplified_path(entity: Entity) -> tuple[Point, ...]: ...

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
    shape_buffer: bytes
    line_buffer: bytes


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
        self._terrain_count = 0
        self._terrain_shape_count = 0
        self._frame_key: tuple[object, ...] | None = None
        self._frame: OpenGLFrame | None = None
        self._selection_key: frozenset[str] | None = None

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
        terrain_key: tuple[object, ...] = (
            id(simulation.game_map),
            framebuffer_size,
            origin,
            tile_size,
        )
        if terrain_key != self._terrain_key:
            self._terrain_key = terrain_key
            (
                self._terrain_buffer,
                self._terrain_count,
                self._terrain_shape_count,
            ) = self._build_terrain(
                simulation,
                origin,
                tile_size,
            )

        selection_key = self._selection_key
        if selection_key is None or selection_key != app.selected_entities:
            selection_key = frozenset(app.selected_entities)
            self._selection_key = selection_key
        frame_key: tuple[object, ...] = (
            id(simulation),
            simulation.tick,
            simulation.command_count,
            framebuffer_size,
            origin,
            tile_size,
            selection_key,
            app.inspected_entity_id,
            len(simulation.entities),
        )
        if frame_key == self._frame_key and self._frame is not None:
            return self._frame

        shape_buffer, shape_count, unit_count, building_count, selected_unit_count = (
            self._build_entities(app, origin, tile_size)
        )
        line_buffer, line_vertex_count, path_count = self._build_paths(app, origin, tile_size)
        (
            projectile_shape_buffer,
            projectile_shape_count,
            projectile_line_buffer,
            projectile_line_vertex_count,
        ) = self._build_projectiles(app, origin, tile_size)
        shape_buffer += projectile_shape_buffer
        shape_count += projectile_shape_count
        line_buffer += projectile_line_buffer
        line_vertex_count += projectile_line_vertex_count
        self._frame_key = frame_key
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
            shape_buffer=shape_buffer,
            line_buffer=line_buffer,
        )
        return self._frame

    @staticmethod
    def _build_terrain(
        simulation: Simulation,
        origin: tuple[int, int],
        tile_size: float,
    ) -> tuple[bytes, int, int]:
        values = array("f")
        origin_x, origin_y = origin
        half_size = tile_size / 2 + 0.5
        count = 0
        for y, row in enumerate(simulation.game_map.terrain):
            for x, terrain in enumerate(row):
                _append_shape(
                    values,
                    center=(origin_x + (x + 0.5) * tile_size, origin_y + (y + 0.5) * tile_size),
                    half_size=(half_size, half_size),
                    color=_float_color(_TERRAIN_COLORS[terrain]),
                    circle=False,
                )
                count += 1
        map_width = simulation.game_map.width * tile_size
        map_height = simulation.game_map.height * tile_size
        grid_color = _float_color((44, 65, 49))
        for cell in range(0, simulation.game_map.width + 1, 8):
            pixel = origin_x + cell * tile_size
            _append_shape(
                values,
                center=(pixel, origin_y + map_height / 2),
                half_size=(0.5, map_height / 2),
                color=grid_color,
                circle=False,
            )
        for cell in range(0, simulation.game_map.height + 1, 8):
            pixel = origin_y + cell * tile_size
            _append_shape(
                values,
                center=(origin_x + map_width / 2, pixel),
                half_size=(map_width / 2, 0.5),
                color=grid_color,
                circle=False,
            )
        return values.tobytes(), count, len(values) // SHAPE_FLOATS

    @staticmethod
    def _build_entities(
        app: OpenGLRenderState,
        origin: tuple[int, int],
        tile_size: float,
    ) -> tuple[bytes, int, int, int, int]:
        values = array("f")
        simulation = app.simulation
        selected_entities = app.selected_entities
        large_selection = len(selected_entities) > 128
        show_full_health_bars = not large_selection
        origin_x, origin_y = origin
        unit_radius = max(5.0, tile_size * 0.42)
        unit_count = 0
        building_count = 0
        selected_unit_count = 0

        for entity_id, entity in simulation.entities.items():
            selected = entity_id in selected_entities
            color = (
                _LARGE_SELECTED_ENTITY_FLOAT_COLORS[entity.kind]
                if entity.owner_id == "player"
                and selected
                and large_selection
                and entity.category is EntityCategory.UNIT
                else _ENTITY_FLOAT_COLORS[entity.kind]
                if entity.owner_id == "player"
                else _ENEMY_FLOAT_COLOR
            )
            if entity.category is EntityCategory.BUILDING:
                width, height = entity.kind.profile.footprint
                half_width = width * tile_size / 2
                half_height = height * tile_size / 2
                center = (
                    origin_x + (entity.position.x + width / 2) * tile_size,
                    origin_y + (entity.position.y + height / 2) * tile_size,
                )
                previous_center = center
                _append_shape(
                    values,
                    center=center,
                    half_size=(half_width, half_height),
                    color=color,
                    circle=False,
                    outline_width=2.0,
                    outline_color=_float_color((35, 42, 49)),
                )
                if selected:
                    _append_shape(
                        values,
                        center=center,
                        half_size=(half_width + 3, half_height + 3),
                        color=(0.0, 0.0, 0.0, 0.0),
                        circle=False,
                        outline_width=2.0,
                        outline_color=_float_color((255, 255, 255)),
                    )
                building_count += 1
            else:
                previous_position = app.previous_entity_position(entity_id)
                center = (
                    origin_x + entity.position.x * tile_size,
                    origin_y + entity.position.y * tile_size,
                )
                previous_center = (
                    origin_x + previous_position.x * tile_size,
                    origin_y + previous_position.y * tile_size,
                )
                _append_shape(
                    values,
                    center=center,
                    previous_center=previous_center,
                    half_size=(unit_radius, unit_radius),
                    color=color,
                    circle=True,
                )
                if (selected and not large_selection) or (
                    entity_id == app.inspected_entity_id and not selected
                ):
                    _append_shape(
                        values,
                        center=center,
                        previous_center=previous_center,
                        half_size=(unit_radius + 3, unit_radius + 3),
                        color=(0.0, 0.0, 0.0, 0.0),
                        circle=True,
                        outline_width=2.0,
                        outline_color=_float_color((255, 255, 255) if selected else (255, 210, 90)),
                    )
                unit_count += 1
                if selected:
                    selected_unit_count += 1

            if show_full_health_bars or entity_id == app.inspected_entity_id:
                maximum_width = max(12.0, tile_size * 1.4)
                health_width = maximum_width * entity.health / entity.kind.profile.max_health
                health_y = center[1] - 10.5
                _append_shape(
                    values,
                    center=(center[0], health_y),
                    previous_center=(previous_center[0], previous_center[1] - 10.5),
                    half_size=(maximum_width / 2, 1.5),
                    color=_float_color((70, 35, 35)),
                    circle=False,
                )
                _append_shape(
                    values,
                    center=(center[0] - (maximum_width - health_width) / 2, health_y),
                    previous_center=(
                        previous_center[0] - (maximum_width - health_width) / 2,
                        previous_center[1] - 10.5,
                    ),
                    half_size=(health_width / 2, 1.5),
                    color=_float_color((74, 218, 111)),
                    circle=False,
                )

        inspected = simulation.entities.get(app.inspected_entity_id or "")
        interaction_range = (
            inspected.kind.profile.build_range
            if inspected is not None and inspected.kind is EntityKind.BUILDER
            else inspected.kind.profile.attack_range
            if inspected is not None
            else 0.0
        )
        if inspected is not None and interaction_range > 0 and len(selected_entities) <= 1:
            range_radius = interaction_range * tile_size
            previous_position = app.previous_entity_position(inspected.entity_id)
            previous_selection_position = (
                previous_position
                if inspected.category is EntityCategory.UNIT
                else inspected.selection_position
            )
            _append_shape(
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
                outline_color=_float_color(
                    (105, 232, 172) if inspected.kind is EntityKind.BUILDER else (255, 218, 100)
                ),
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
            radius = max(8.0, (parameters.assembly_radius + 0.8) * tile_size)
            _append_shape(
                values,
                center=center,
                half_size=(radius, radius),
                color=(92 / 255, 177 / 255, 1.0, 20 / 255),
                circle=True,
                outline_width=2.0,
                outline_color=(135 / 255, 205 / 255, 1.0, 72 / 255),
            )
            _append_shape(
                values,
                center=center,
                half_size=(max(1.0, radius - 5.0), max(1.0, radius - 5.0)),
                color=(0.0, 0.0, 0.0, 0.0),
                circle=True,
                outline_width=2.0,
                outline_color=(190 / 255, 230 / 255, 1.0, 35 / 255),
            )

        return (
            values.tobytes(),
            len(values) // SHAPE_FLOATS,
            unit_count,
            building_count,
            selected_unit_count,
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
        color = _float_color((225, 225, 225))
        for entity_id in app._representative_path_entity_ids():
            entity = app.simulation.entities[entity_id]
            points = app._simplified_path(entity)
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
            path_count += 1
        return values.tobytes(), len(values) // LINE_FLOATS, path_count

    @staticmethod
    def _build_projectiles(
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

        for trace in simulation.projectile_traces:
            append_trajectory(trace.points, _float_color(_PROJECTILE_COLORS[trace.weapon_kind]))
        for projectile in simulation.projectiles.values():
            raw_color = _PROJECTILE_COLORS[projectile.weapon_kind]
            color = _float_color(raw_color)
            append_trajectory(projectile.trajectory, color)
            destination_color = _float_color(
                (raw_color[0] // 2, raw_color[1] // 2, raw_color[2] // 2)
            )
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
            _append_shape(
                shapes,
                center=center,
                previous_center=previous_center,
                half_size=(3.0, 3.0),
                color=_float_color((255, 255, 255)),
                circle=True,
            )
            _append_shape(
                shapes,
                center=center,
                previous_center=previous_center,
                half_size=(2.0, 2.0),
                color=color,
                circle=True,
            )
        return (
            shapes.tobytes(),
            len(shapes) // SHAPE_FLOATS,
            lines.tobytes(),
            len(lines) // LINE_FLOATS,
        )


class OpenGLRendererError(RuntimeError):
    """Raised when AIRTS cannot create or operate its required OpenGL renderer."""


class OpenGLRenderer:
    """Render native-pixel AIRTS batches with OpenGL 3.3 instancing and shaders."""

    def __init__(self, module: Any, context: Any) -> None:
        self._module = module
        self._context = context
        self._builder = OpenGLFrameBuilder()
        self._uploaded_frame: OpenGLFrame | None = None
        self._uploaded_terrain: bytes | None = None
        self._overlay_key: tuple[object, ...] | None = None
        self._overlay_tick: int | None = None
        self._overlay_regions: tuple[pygame.Rect, ...] = ()
        self._overlay_surface: pygame.Surface | None = None
        self._overlay_texture: Any = None
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
            instance_format = "2f 2f 2f 4f 2f 4f /i"
            instance_attributes = (
                "in_center",
                "in_previous_center",
                "in_half_size",
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
            self._overlay_program["overlay_texture"].value = 0
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
        frame = self._builder.build(app, framebuffer_size)
        target = framebuffer if framebuffer is not None else self._context.screen
        if target is None:
            raise OpenGLRendererError("the OpenGL context has no screen framebuffer")
        try:
            target.use()
            width, height = framebuffer_size
            self._context.viewport = (0, 0, width, height)
            self._context.clear(18 / 255, 22 / 255, 28 / 255, 1.0)
            self._shape_program["viewport_size"].value = framebuffer_size
            self._shape_program["interpolation_alpha"].value = max(
                0.0,
                min(1.0, app.render_alpha),
            )
            self._line_program["viewport_size"].value = framebuffer_size
            self._upload_frame(frame)
            if frame.terrain_shape_count:
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
                self._shape_vertex_array.render(
                    mode=self._module.TRIANGLE_STRIP,
                    vertices=4,
                    instances=frame.shape_count,
                )
            self._draw_overlay(app, frame)
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

        for attribute in _GPU_RESOURCE_ATTRIBUTES:
            resource = getattr(self, attribute)
            if resource is not None:
                resource.release()
                setattr(self, attribute, None)

    def _upload_frame(self, frame: OpenGLFrame) -> None:
        if frame.terrain_buffer is not self._uploaded_terrain:
            _replace_buffer(self._terrain_buffer, frame.terrain_buffer)
            self._uploaded_terrain = frame.terrain_buffer
        if frame is self._uploaded_frame:
            return
        _replace_buffer(self._shape_buffer, frame.shape_buffer)
        _replace_buffer(self._line_buffer, frame.line_buffer)
        self._uploaded_frame = frame

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
            and self._overlay_tick != frame.tick
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
            self._overlay_tick = frame.tick
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


def _append_shape(
    values: array[float],
    *,
    center: tuple[float, float],
    half_size: tuple[float, float],
    color: FloatColor,
    circle: bool,
    previous_center: tuple[float, float] | None = None,
    outline_width: float = 0.0,
    outline_color: FloatColor = (0.0, 0.0, 0.0, 0.0),
) -> None:
    previous = center if previous_center is None else previous_center
    values.extend(
        (
            center[0],
            center[1],
            previous[0],
            previous[1],
            half_size[0],
            half_size[1],
            *color,
            1.0 if circle else 0.0,
            outline_width,
            *outline_color,
        )
    )


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
in vec2 in_corner;
in vec2 in_center;
in vec2 in_previous_center;
in vec2 in_half_size;
in vec4 in_color;
in vec2 in_shape_outline;
in vec4 in_outline_color;
out vec2 local_position;
flat out vec2 half_size;
flat out vec4 fill_color;
flat out float circle_shape;
flat out float outline_width;
flat out vec4 outline_color;

void main() {
    vec2 center = mix(in_previous_center, in_center, interpolation_alpha);
    vec2 pixel_position = center + in_corner * in_half_size;
    vec2 normalized = vec2(
        pixel_position.x * 2.0 / viewport_size.x - 1.0,
        1.0 - pixel_position.y * 2.0 / viewport_size.y
    );
    gl_Position = vec4(normalized, 0.0, 1.0);
    local_position = in_corner;
    half_size = in_half_size;
    fill_color = in_color;
    circle_shape = in_shape_outline.x;
    outline_width = in_shape_outline.y;
    outline_color = in_outline_color;
}
"""

_SHAPE_FRAGMENT_SHADER = """
#version 330
in vec2 local_position;
flat in vec2 half_size;
flat in vec4 fill_color;
flat in float circle_shape;
flat in float outline_width;
flat in vec4 outline_color;
out vec4 fragment_color;

void main() {
    float signed_distance;
    if (circle_shape > 0.5) {
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
    float fill_alpha = fill_color.a * inner_alpha;
    float outline_alpha = outline_color.a * outline_band;
    float combined_alpha = fill_alpha + outline_alpha;
    vec3 premultiplied_color = fill_color.rgb * fill_alpha
        + outline_color.rgb * outline_alpha;
    fragment_color = vec4(
        premultiplied_color / max(combined_alpha, 0.001),
        combined_alpha
    );
    if (combined_alpha <= 0.001) {
        discard;
    }
}
"""

_LINE_VERTEX_SHADER = """
#version 330
uniform vec2 viewport_size;
in vec2 in_position;
in vec4 in_color;
out vec4 line_color;

void main() {
    vec2 normalized = vec2(
        in_position.x * 2.0 / viewport_size.x - 1.0,
        1.0 - in_position.y * 2.0 / viewport_size.y
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
