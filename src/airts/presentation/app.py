"""Pygame RTS interaction, inspection, and software-rendering adapter."""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import ceil, hypot
from pathlib import Path
from time import perf_counter

import pygame

from airts.adapters.persistence import PersistenceError, load_simulation, save_simulation
from airts.automations import (
    Automation,
    AutomationKind,
    AutomationStatus,
    ConstructionParameters,
    DefendParameters,
    ProductionParameters,
    target_center,
)
from airts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionBatchCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    DeleteSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    RenameRegionCommand,
    ResumeAutomationCommand,
    SetSelectionCommand,
    StopCommand,
)
from airts.geometry import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialTarget,
    rectangle_region,
    simplify_freehand,
)
from airts.presentation.opengl_renderer import OpenGLRenderer, OpenGLRendererError
from airts.simulation import Simulation
from airts.spatial import SpatialKind
from airts.world.entities import Entity
from airts.world.map_model import EntityCategory, EntityKind, Terrain


class InputMode(StrEnum):
    SELECT = "select"
    LINE = "line"
    RECTANGLE = "rectangle"
    FREEHAND = "freehand"


class RendererBackend(StrEnum):
    OPENGL = "opengl"
    SOFTWARE = "software"


REAL_FPS_FRAME_TIME_PERCENTILE = 0.99


def real_fps_from_frame_times(frame_times_ms: Iterable[float]) -> float:
    """Return AIRTS's permanent stutter-sensitive FPS acceptance metric."""

    frame_p99_ms = _percentile(frame_times_ms, REAL_FPS_FRAME_TIME_PERCENTILE)
    return 1000.0 / frame_p99_ms if frame_p99_ms > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class PresentationMetrics:
    """Rolling measurements for the application-to-compositor presentation path."""

    submit_fps: float = 0.0
    one_percent_low_fps: float = 0.0
    frame_p95_ms: float = 0.0
    render_p95_ms: float = 0.0
    present_p95_ms: float = 0.0
    simulation_p95_ms: float = 0.0


class PresentationProfiler:
    """Measure frame pacing without adding another limiter or profiler dependency."""

    SAMPLE_SECONDS = 2.0
    MAX_SAMPLE_COUNT = 20_000
    REFRESH_SECONDS = 0.25

    def __init__(self) -> None:
        self._presented_at: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._frame_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._render_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._present_ms: deque[float] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._simulation_samples: deque[tuple[float, float]] = deque(maxlen=self.MAX_SAMPLE_COUNT)
        self._last_refresh = 0.0
        self.metrics = PresentationMetrics()

    def record(
        self,
        *,
        presented_at: float,
        frame_ms: float,
        render_ms: float,
        present_ms: float,
        simulation_ms: float,
    ) -> PresentationMetrics:
        self._presented_at.append(presented_at)
        self._frame_ms.append(frame_ms)
        self._render_ms.append(render_ms)
        self._present_ms.append(present_ms)
        if simulation_ms > 0.0:
            self._simulation_samples.append((presented_at, simulation_ms))
        while (
            len(self._presented_at) > 1
            and presented_at - self._presented_at[0] > self.SAMPLE_SECONDS
        ):
            self._presented_at.popleft()
            self._frame_ms.popleft()
            self._render_ms.popleft()
            self._present_ms.popleft()
        while (
            self._simulation_samples
            and presented_at - self._simulation_samples[0][0] > self.SAMPLE_SECONDS
        ):
            self._simulation_samples.popleft()
        if presented_at - self._last_refresh < self.REFRESH_SECONDS:
            return self.metrics
        self._last_refresh = presented_at
        elapsed = (
            self._presented_at[-1] - self._presented_at[0] if len(self._presented_at) > 1 else 0.0
        )
        submit_fps = (len(self._presented_at) - 1) / elapsed if elapsed > 0.0 else 0.0
        self.metrics = PresentationMetrics(
            submit_fps=submit_fps,
            one_percent_low_fps=real_fps_from_frame_times(self._frame_ms),
            frame_p95_ms=_percentile(self._frame_ms, 0.95),
            render_p95_ms=_percentile(self._render_ms, 0.95),
            present_p95_ms=_percentile(self._present_ms, 0.95),
            simulation_p95_ms=_percentile(
                deque(value for _, value in self._simulation_samples),
                0.95,
            ),
        )
        return self.metrics


class AirtsApp:
    FRAME_RATE_LIMIT = 1_000
    OPENGL_OVERLAY_REFRESH_TICKS = 3
    MAX_SELECTED_PATHS = 32
    AUTOMATION_ROW_HEIGHT = 70
    AUTOMATION_SCROLLBAR_WIDTH = 12
    AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT = 32
    OPENGL_DISPLAY_FLAGS = pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
    DISPLAY_FLAGS = pygame.RESIZABLE | pygame.SCALED
    MAP_PIXELS = 768
    LEFT_PANEL_WIDTH = 280
    RIGHT_PANEL_WIDTH = 380
    PANEL_WIDTH = RIGHT_PANEL_WIDTH
    COMMAND_BAR_HEIGHT = 104
    WINDOW_SIZE = (
        LEFT_PANEL_WIDTH + MAP_PIXELS + RIGHT_PANEL_WIDTH,
        MAP_PIXELS + COMMAND_BAR_HEIGHT,
    )
    RESOLUTION_PRESETS = (
        (1280, 720),
        WINDOW_SIZE,
        (1600, 900),
        (1920, 1080),
        (2560, 1440),
        (3840, 2160),
    )
    BACKGROUND = (18, 22, 28)
    PANEL_BACKGROUND = (27, 32, 40)
    ENTITY_CLICK_RADIUS = 1.5
    ENEMY_CLICK_RADIUS = 2.5

    def __init__(
        self,
        simulation: Simulation,
        renderer_backend: RendererBackend = RendererBackend.OPENGL,
    ) -> None:
        self.simulation = simulation
        self.renderer_backend = renderer_backend
        self.mode = InputMode.SELECT
        self.selected_entities: set[str] = set()
        self.selected_points: set[str] = set()
        self.selected_routes: set[str] = set()
        self.selected_regions: set[str] = set()
        self.active_target: SpatialTarget | None = None
        self.active_reference_id: str | None = None
        self.editing_reference_id: str | None = None
        self.selected_automation_id: str | None = None
        self.inspected_entity_id: str | None = None
        self.naming_reference_id: str | None = None
        self.naming_buffer = ""
        self.line_points: list[Point] = []
        self.freehand_points: list[Point] = []
        self.drag_start: Point | None = None
        self.camera_offset = Point(0, 0)
        self._camera_drag_position: tuple[int, int] | None = None
        self.automation_scroll = 0
        self._automation_visible_rows = 6
        self._automation_scrollbar_track: pygame.Rect | None = None
        self._automation_scrollbar_thumb: pygame.Rect | None = None
        self._automation_scroll_drag_offset: int | None = None
        self.settings_open = False
        self.help_open = False
        self.placement_kind: EntityKind | None = None
        self.production_sequence: list[tuple[EntityKind, int]] = []
        self.paused = False
        self.real_fps = 0.0
        self.presentation_metrics = PresentationMetrics()
        self.render_alpha = 1.0
        self._previous_entity_positions: dict[str, Point] = {}
        self._previous_projectile_positions: dict[str, Point] = {}
        self.window_size = self.WINDOW_SIZE
        self._pending_window_size: tuple[int, int] | None = None
        self.notice = "Select units, draw a target, then press A to patrol."
        self._automation_buttons: list[tuple[pygame.Rect, str, str]] = []
        self._command_buttons: list[tuple[pygame.Rect, str]] = []
        self._settings_buttons: list[tuple[pygame.Rect, str]] = []
        self._type_buttons: list[tuple[pygame.Rect, EntityKind]] = []
        self.inspected_kind: EntityKind | None = None
        self._last_entity_click: tuple[str, int] | None = None
        self._initial_map = simulation.game_map
        self._initial_seed = simulation.random_seed
        self._initial_ambient_enemy_spawns = simulation.ambient_enemy_spawns
        self._initial_enemy_spawn_interval_ticks = simulation.enemy_spawn_interval_ticks
        self._initial_enemy_spawn_cap = simulation.enemy_spawn_cap
        self.quick_save_path = Path("airts-quicksave.json")
        self._font: pygame.font.Font | None = None
        self._small_font: pygame.font.Font | None = None
        self._map_surface: pygame.Surface | None = None
        self._scaled_map_surface: pygame.Surface | None = None
        self._scaled_map_size: tuple[int, int] | None = None
        self._frame_selected_entities: tuple[Entity, ...] | None = None
        self._unit_sprite_cache: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}
        self._large_unit_render_key: tuple[object, ...] | None = None
        self._large_unit_blits: tuple[tuple[pygame.Surface, tuple[int, int]], ...] = ()
        self._large_building_draws: tuple[tuple[pygame.Rect, tuple[int, int, int], bool], ...] = ()
        self._large_unit_selected_bounds: pygame.Rect | None = None
        self._large_unit_health_bars: tuple[tuple[pygame.Rect, int], ...] = ()
        self._large_unit_inspected_ring: tuple[tuple[int, int], int] | None = None
        self._large_unit_renderable = False
        self._path_render_key: tuple[object, ...] | None = None
        self._path_render_points: tuple[tuple[tuple[int, int], ...], ...] = ()
        self._software_draw_key: tuple[object, ...] | None = None
        self._software_draw_tick: int | None = None
        self._software_deferred_tick: int | None = None
        self._software_frame_surface: pygame.Surface | None = None
        self._software_cache_pending = False
        self._scaled_display_active = False
        self._frame_tile_size: float | None = None
        self._frame_map_pixel_size: tuple[int, int] | None = None
        self._frame_map_origin: tuple[int, int] | None = None
        self._reset_presentation_history()
        self.resize_layout(self.WINDOW_SIZE)

    @property
    def tile_size(self) -> float:
        if self._frame_tile_size is not None:
            return self._frame_tile_size
        return min(
            self.canvas_rect.width / self.simulation.game_map.width,
            self.canvas_rect.height / self.simulation.game_map.height,
        )

    @property
    def map_pixel_size(self) -> tuple[int, int]:
        if self._frame_map_pixel_size is not None:
            return self._frame_map_pixel_size
        return (
            round(self.simulation.game_map.width * self.tile_size),
            round(self.simulation.game_map.height * self.tile_size),
        )

    @property
    def map_origin(self) -> tuple[int, int]:
        if self._frame_map_origin is not None:
            return self._frame_map_origin
        width, height = self.map_pixel_size
        return (
            self.canvas_rect.centerx - width // 2 + round(self.camera_offset.x),
            self.canvas_rect.centery - height // 2 + round(self.camera_offset.y),
        )

    def resize_layout(self, size: tuple[int, int]) -> None:
        width = max(760, size[0])
        height = max(520, size[1])
        self.window_size = (width, height)
        self.ui_scale = max(
            0.8, min(1.45, min(width / self.WINDOW_SIZE[0], height / self.WINDOW_SIZE[1]))
        )
        left_width = max(200, round(self.LEFT_PANEL_WIDTH * self.ui_scale))
        right_width = max(280, round(self.RIGHT_PANEL_WIDTH * self.ui_scale))
        command_height = max(82, round(self.COMMAND_BAR_HEIGHT * self.ui_scale))
        center_width = max(280, width - left_width - right_width)
        self.left_panel_rect = pygame.Rect(0, 0, left_width, height)
        self.right_panel_rect = pygame.Rect(width - right_width, 0, right_width, height)
        self.canvas_rect = pygame.Rect(left_width, 0, center_width, height - command_height)
        self.command_bar_rect = pygame.Rect(
            left_width, height - command_height, center_width, command_height
        )
        self.camera_offset = Point(0, 0)
        if self._font is not None and pygame.font.get_init():
            self._font = pygame.font.Font(None, round(24 * self.ui_scale))
            self._small_font = pygame.font.Font(None, round(19 * self.ui_scale))

    def _reset_presentation_history(self) -> None:
        """Make the current authoritative state both interpolation endpoints."""

        self._previous_entity_positions = {
            entity_id: entity.position for entity_id, entity in self.simulation.entities.items()
        }
        self._previous_projectile_positions = {
            projectile_id: projectile.position
            for projectile_id, projectile in self.simulation.projectiles.items()
        }
        self.render_alpha = 1.0

    def _advance_presentation_tick(self) -> None:
        """Capture the previous state before advancing the authoritative simulation."""

        self._previous_entity_positions = {
            entity_id: entity.position for entity_id, entity in self.simulation.entities.items()
        }
        self._previous_projectile_positions = {
            projectile_id: projectile.position
            for projectile_id, projectile in self.simulation.projectiles.items()
        }
        self.simulation.advance()

    def previous_entity_position(self, entity_id: str) -> Point:
        entity = self.simulation.entities[entity_id]
        return self._previous_entity_positions.get(entity_id, entity.position)

    def previous_projectile_position(self, projectile_id: str) -> Point:
        projectile = self.simulation.projectiles[projectile_id]
        return self._previous_projectile_positions.get(projectile_id, projectile.position)

    def _request_resolution_step(self, direction: int) -> None:
        current = self._pending_window_size or self.window_size
        nearest = min(
            range(len(self.RESOLUTION_PRESETS)),
            key=lambda index: (
                abs(self.RESOLUTION_PRESETS[index][0] - current[0])
                + abs(self.RESOLUTION_PRESETS[index][1] - current[1])
            ),
        )
        target_index = max(0, min(len(self.RESOLUTION_PRESETS) - 1, nearest + direction))
        target = self.RESOLUTION_PRESETS[target_index]
        if target == current:
            self.notice = f"Resolution is already {target[0]} x {target[1]}."
            return
        self._pending_window_size = target
        self.settings_open = False
        self.notice = f"Changing resolution to {target[0]} x {target[1]}."

    def pan_camera(self, dx: float, dy: float) -> None:
        limit = min(self.map_pixel_size) * 0.45
        self.camera_offset = Point(
            max(-limit, min(limit, self.camera_offset.x + dx)),
            max(-limit, min(limit, self.camera_offset.y + dy)),
        )

    def scroll_automations(self, delta: int, *, visible_rows: int, total_rows: int) -> None:
        maximum = max(0, total_rows - visible_rows)
        self.automation_scroll = max(0, min(maximum, self.automation_scroll + delta))

    def _automation_panel_rows(self) -> tuple[Automation, ...]:
        live_automations = self.simulation.live_automations
        linked_defense_ids = {
            parameters.defend_automation_id
            for automation in live_automations
            if automation.kind is AutomationKind.PRODUCTION
            and isinstance((parameters := automation.parameters), ProductionParameters)
            and parameters.defend_automation_id is not None
        }
        return tuple(
            automation
            for automation in live_automations
            if automation.automation_id not in linked_defense_ids
        )

    def _clamp_automation_scroll(self, *, visible_rows: int, total_rows: int) -> None:
        self.scroll_automations(0, visible_rows=visible_rows, total_rows=total_rows)

    def _set_automation_scroll_from_pointer(self, pointer_y: int) -> None:
        track = self._automation_scrollbar_track
        thumb = self._automation_scrollbar_thumb
        rows = self._automation_panel_rows()
        maximum = max(0, len(rows) - self._automation_visible_rows)
        if track is None or thumb is None or maximum == 0:
            self.automation_scroll = 0
            return
        travel = track.height - thumb.height
        if travel <= 0:
            self.automation_scroll = 0
            return
        offset = self._automation_scroll_drag_offset
        thumb_top = pointer_y - (thumb.height // 2 if offset is None else offset)
        fraction = max(0.0, min(1.0, (thumb_top - track.top) / travel))
        self.automation_scroll = round(fraction * maximum)

    def _in_canvas(self, position: tuple[int, int]) -> bool:
        return self.canvas_rect.collidepoint(position)

    def run(self, max_frames: int | None = None) -> None:
        display_initialized = False
        font_initialized = False
        opengl_renderer: OpenGLRenderer | None = None
        try:
            if self.renderer_backend is RendererBackend.OPENGL:
                self._configure_opengl_video_backend()
            pygame.display.init()
            display_initialized = True
            pygame.font.init()
            font_initialized = True
            if self.renderer_backend is RendererBackend.OPENGL:
                pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
                pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
                pygame.display.gl_set_attribute(
                    pygame.GL_CONTEXT_PROFILE_MASK,
                    pygame.GL_CONTEXT_PROFILE_CORE,
                )
                pygame.display.gl_set_attribute(pygame.GL_DOUBLEBUFFER, 1)
                try:
                    screen = pygame.display.set_mode(
                        self.window_size,
                        self.OPENGL_DISPLAY_FLAGS,
                        vsync=0,
                    )
                except pygame.error as error:
                    raise OpenGLRendererError(
                        "SDL could not create an OpenGL 3.3 window; verify the GPU driver and "
                        "WSLg setup, or explicitly use --renderer software for headless CI: "
                        f"{error}"
                    ) from error
                opengl_renderer = OpenGLRenderer.from_active_context()
                self._scaled_display_active = False
            else:
                screen = pygame.display.set_mode(self.window_size, self.DISPLAY_FLAGS)
                self._scaled_display_active = bool(self.DISPLAY_FLAGS & pygame.SCALED)
            self.resize_layout(self.window_size)
            pygame.display.set_caption("AIRTS — Phase 5")
            self._font = pygame.font.Font(None, 24)
            self._small_font = pygame.font.Font(None, 19)
            clock = pygame.time.Clock()
            profiler = PresentationProfiler()
            accumulator = 0.0
            running = True
            frames = 0
            last_presented_at = perf_counter()
            previous_loop_at = last_presented_at
            while running and (max_frames is None or frames < max_frames):
                clock.tick(self.FRAME_RATE_LIMIT)
                loop_started_at = perf_counter()
                elapsed = min(loop_started_at - previous_loop_at, 0.25)
                previous_loop_at = loop_started_at
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                        break
                    else:
                        self._handle_event(event)
                if not running:
                    break
                if self._pending_window_size is not None:
                    target_size = self._pending_window_size
                    self._pending_window_size = None
                    if opengl_renderer is not None:
                        opengl_renderer.release()
                        opengl_renderer = None
                        screen = pygame.display.set_mode(
                            target_size,
                            self.OPENGL_DISPLAY_FLAGS,
                            vsync=0,
                        )
                        opengl_renderer = OpenGLRenderer.from_active_context()
                    else:
                        screen = pygame.display.set_mode(target_size, self.DISPLAY_FLAGS)
                    self.resize_layout(target_size)
                if not self.paused:
                    accumulator += elapsed
                    simulation_started = perf_counter()
                    while accumulator >= Simulation.TICK_SECONDS:
                        self._advance_presentation_tick()
                        accumulator -= Simulation.TICK_SECONDS
                    simulation_ms = (perf_counter() - simulation_started) * 1000.0
                    self.render_alpha = max(
                        0.0,
                        min(1.0, accumulator / Simulation.TICK_SECONDS),
                    )
                else:
                    simulation_ms = 0.0
                    self.render_alpha = 1.0
                render_started = perf_counter()
                if opengl_renderer is not None:
                    opengl_renderer.render(
                        self,
                        (self.right_panel_rect.right, self.left_panel_rect.bottom),
                    )
                else:
                    self._draw(screen)
                render_ms = (perf_counter() - render_started) * 1000.0
                present_started = perf_counter()
                pygame.display.flip()
                presented_at = perf_counter()
                present_ms = (presented_at - present_started) * 1000.0
                frame_ms = (presented_at - last_presented_at) * 1000.0
                last_presented_at = presented_at
                self.presentation_metrics = profiler.record(
                    presented_at=presented_at,
                    frame_ms=frame_ms,
                    render_ms=render_ms,
                    present_ms=present_ms,
                    simulation_ms=simulation_ms,
                )
                self.real_fps = self.presentation_metrics.one_percent_low_fps
                frames += 1
        finally:
            if opengl_renderer is not None:
                opengl_renderer.release()
            self._shutdown_pygame(display_initialized, font_initialized)

    @staticmethod
    def _configure_opengl_video_backend() -> None:
        """Prefer native WSLg Wayland for OpenGL unless the user selected an SDL driver."""

        if (
            "SDL_VIDEODRIVER" not in os.environ
            and os.environ.get("WSL_DISTRO_NAME")
            and os.environ.get("WAYLAND_DISPLAY")
        ):
            os.environ["SDL_VIDEODRIVER"] = "wayland"

    def _shutdown_pygame(self, display_initialized: bool, font_initialized: bool) -> None:
        """Release UI resources in dependency order, including exceptional exits."""

        self._font = None
        self._small_font = None
        self._map_surface = None
        self._scaled_map_surface = None
        self._scaled_map_size = None
        self._unit_sprite_cache.clear()
        self._large_unit_render_key = None
        self._large_unit_blits = ()
        self._large_building_draws = ()
        self._large_unit_health_bars = ()
        self._large_unit_selected_bounds = None
        self._large_unit_inspected_ring = None
        self._large_unit_renderable = False
        self._path_render_key = None
        self._path_render_points = ()
        self._scaled_display_active = False
        try:
            if display_initialized:
                pygame.event.clear()
        finally:
            try:
                if font_initialized:
                    pygame.font.quit()
            finally:
                try:
                    if display_initialized:
                        pygame.display.quit()
                finally:
                    pygame.quit()

    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if self.naming_reference_id is not None:
                self._handle_name_key(event)
            else:
                self._handle_key(event.key)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            self._handle_mouse_down(event.button, event.pos)
        elif event.type == pygame.MOUSEMOTION:
            self._handle_mouse_motion(event.pos, event.buttons)
        elif event.type == pygame.MOUSEBUTTONUP:
            self._handle_mouse_up(event.button, event.pos)
        elif event.type == pygame.MOUSEWHEEL:
            mouse = pygame.mouse.get_pos()
            if self.left_panel_rect.collidepoint(mouse):
                rows = self._automation_panel_rows()
                self.scroll_automations(
                    -event.y,
                    visible_rows=self._automation_visible_rows,
                    total_rows=len(rows),
                )
        elif event.type in {pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED}:
            display_surface = pygame.display.get_surface()
            size = (
                display_surface.get_size()
                if self._scaled_display_active and display_surface is not None
                else event.size
                if hasattr(event, "size")
                else (event.x, event.y)
            )
            self.resize_layout(tuple(size))

    def _handle_key(self, key: int) -> None:
        mode_keys = {
            pygame.K_1: InputMode.SELECT,
            pygame.K_2: InputMode.LINE,
            pygame.K_3: InputMode.RECTANGLE,
            pygame.K_4: InputMode.FREEHAND,
        }
        if key in mode_keys:
            self.mode = mode_keys[key]
            self._clear_draft()
            self.notice = f"Input mode: {self.mode.value}"
        elif key == pygame.K_a:
            self._create_patrol()
        elif key == pygame.K_d:
            self._create_defend()
        elif key == pygame.K_p:
            self._create_production()
        elif key == pygame.K_r:
            self._create_repair()
        elif key == pygame.K_g:
            self._create_economy()
        elif key == pygame.K_s:
            self._stop_selected()
        elif key == pygame.K_h:
            self._hold_selected()
        elif key == pygame.K_DELETE:
            self._delete_selected_reference()
        elif key == pygame.K_F5:
            self._save_game()
        elif key == pygame.K_F9:
            self._load_game()
        elif key == pygame.K_F2:
            self._new_game()
        elif key == pygame.K_n:
            self._name_selected_region()
        elif key == pygame.K_e and self.active_reference_id is not None:
            self.editing_reference_id = self.active_reference_id
            self.notice = "Redraw the selected spatial object to replace its geometry."
        elif key == pygame.K_u:
            self._apply_target_to_automation()
        elif key in {pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET}:
            self._change_automation_priority(-1 if key == pygame.K_LEFTBRACKET else 1)
        elif key == pygame.K_SPACE:
            self.paused = not self.paused
            self.notice = "Simulation paused." if self.paused else "Simulation resumed."
        elif key == pygame.K_ESCAPE:
            self._clear_draft()
            self._clear_selection_state()
            self.mode = InputMode.SELECT
            self.placement_kind = None
            self.settings_open = False
            self.help_open = False
            self._commit_selection()
            self.notice = "Selection and active tools cleared."

    def _handle_mouse_down(self, button: int, position: tuple[int, int]) -> None:
        # Direct headless interaction tests use historical canvas-local pixels.
        if self._font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if button == 1 and self.settings_open:
            if self._handle_settings_click(position):
                return
            self.settings_open = False
            return
        if button == 2 and self._in_canvas(position):
            self._camera_drag_position = position
            return
        if (
            button == 1
            and self._automation_scrollbar_track is not None
            and self._automation_scrollbar_track.collidepoint(position)
        ):
            thumb = self._automation_scrollbar_thumb
            self._automation_scroll_drag_offset = (
                position[1] - thumb.top
                if thumb is not None and thumb.collidepoint(position)
                else None
            )
            self._set_automation_scroll_from_pointer(position[1])
            return
        if self.left_panel_rect.collidepoint(position) or self.right_panel_rect.collidepoint(
            position
        ):
            if button == 1:
                self._handle_panel_click(position)
            return
        if self.command_bar_rect.collidepoint(position):
            if button == 1:
                self._handle_command_click(position)
            return
        point = self._map_point(position)
        if button == 3 and self.placement_kind is not None:
            self.placement_kind = None
            self.notice = "Building placement closed. Queued construction is unchanged."
            return
        if button == 1 and self.placement_kind is not None:
            builders = [
                entity_id
                for entity_id in sorted(self.selected_entities)
                if self.simulation.entities[entity_id].kind is EntityKind.BUILDER
            ]
            if not builders:
                self.notice = "Select one or more builders."
                return
            snapped = Point(float(int(point.x)), float(int(point.y)))
            try:
                queued = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            except pygame.error:
                queued = False
            result = self.simulation.execute(
                CreateConstructionCommand(
                    builders[0],
                    self.placement_kind,
                    snapped,
                    builder_ids=tuple(builders),
                    queued=queued,
                )
            )
            if result.accepted:
                self.notice = "Construction queued." if queued else "Construction started."
            else:
                self.notice = result.reason
            if result.accepted and not queued:
                self.placement_kind = None
            return
        if button == 3:
            if self.mode is InputMode.LINE:
                if len(self.line_points) < 2:
                    self.notice = "A line needs at least two points before finishing."
                    return
                self._finish_target(PolylineTarget(tuple(self.line_points)))
                return
            enemies = sorted(
                (_entity_hit_distance(entity, point), entity.entity_id)
                for entity in self.simulation.entities.values()
                if entity.owner_id != "player"
                and _entity_hit_distance(entity, point) <= self.ENEMY_CLICK_RADIUS
            )
            command = (
                AttackCommand(tuple(sorted(self.selected_entities)), enemies[0][1])
                if enemies
                else MoveCommand(tuple(sorted(self.selected_entities)), point)
            )
            result = self.simulation.execute(command)
            self.notice = "Command issued." if result.accepted else result.reason
            return
        if button != 1:
            return
        if self.mode is InputMode.LINE:
            self.line_points.append(point)
            self.notice = "Left-click to add vertices; right-click to finish."
        elif self.mode is InputMode.FREEHAND:
            self.freehand_points = [point]
        else:
            self.drag_start = point

    def _handle_mouse_motion(
        self, position: tuple[int, int], buttons: tuple[bool, bool, bool]
    ) -> None:
        if self._font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if self._automation_scroll_drag_offset is not None and buttons[0]:
            self._set_automation_scroll_from_pointer(position[1])
            return
        if (
            self.mode is InputMode.FREEHAND
            and buttons[0]
            and self._in_canvas(position)
            and self.freehand_points
        ):
            point = self._map_point(position)
            if point.distance_to(self.freehand_points[-1]) >= 0.2:
                self.freehand_points.append(point)
        if self._camera_drag_position is not None and buttons[1]:
            previous = self._camera_drag_position
            self.pan_camera(position[0] - previous[0], position[1] - previous[1])
            self._camera_drag_position = position

    def _handle_mouse_up(self, button: int, position: tuple[int, int]) -> None:
        if self._font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if button == 2:
            self._camera_drag_position = None
            return
        if button == 1 and self._automation_scroll_drag_offset is not None:
            self._automation_scroll_drag_offset = None
            return
        if button != 1 or not self._in_canvas(position):
            return
        point = self._map_point(position)
        if self.mode is InputMode.FREEHAND and self.freehand_points:
            self.freehand_points.append(point)
            try:
                self._finish_target(simplify_freehand(tuple(self.freehand_points)))
            except ValueError as error:
                self.notice = str(error)
            finally:
                self.freehand_points.clear()
            return
        if self.drag_start is None:
            return
        start = self.drag_start
        self.drag_start = None
        if self.mode is InputMode.SELECT:
            self._select_entities(start, point, bool(pygame.key.get_mods() & pygame.KMOD_SHIFT))
        elif self.mode is InputMode.RECTANGLE:
            try:
                self._finish_target(rectangle_region(start, point))
            except ValueError as error:
                self.notice = str(error)

    def _select_entities(self, start: Point, end: Point, additive: bool = False) -> None:
        if start.distance_to(end) < 0.3:
            candidates = sorted(
                (
                    (_entity_hit_distance(entity, end), entity_id)
                    for entity_id, entity in self.simulation.entities.items()
                    if _entity_hit_distance(entity, end)
                    <= (
                        self.ENTITY_CLICK_RADIUS
                        if entity.owner_id == "player"
                        else self.ENEMY_CLICK_RADIUS
                    )
                )
            )
            clicked = candidates[0][1] if candidates else None
            if clicked is not None and self.simulation.entities[clicked].owner_id != "player":
                self.inspected_entity_id = clicked
                if not additive:
                    self.selected_entities.clear()
                self._commit_selection()
                self.notice = f"Inspecting enemy {clicked}; commands disabled."
                return
            found = {clicked} if clicked is not None else set()
            if clicked is not None:
                click_tick = pygame.time.get_ticks()
                if (
                    self._last_entity_click is not None
                    and self._last_entity_click[0] == clicked
                    and click_tick - self._last_entity_click[1] <= 400
                ):
                    self._select_all_visible_kind(self.simulation.entities[clicked].kind)
                    self._last_entity_click = None
                    return
                self._last_entity_click = (clicked, click_tick)
            if not found:
                found_points = {
                    reference.reference_id
                    for reference in self.simulation.spatial.references.values()
                    if isinstance(reference.geometry, PointTarget)
                    and reference.geometry.point.distance_to(end) <= reference.geometry.radius
                }
                found_routes = {
                    reference.reference_id
                    for reference in self.simulation.spatial.references.values()
                    if isinstance(reference.geometry, PolylineTarget)
                    and _distance_to_polyline(end, reference.geometry.points) <= 0.4
                }
                found_regions = {
                    reference.reference_id
                    for reference in self.simulation.spatial.references.values()
                    if reference.kind is SpatialKind.REGION
                    and isinstance(reference.geometry, PolygonRegion)
                    and reference.geometry.contains(end)
                }
                if additive:
                    self.selected_points.symmetric_difference_update(found_points)
                    self.selected_routes.symmetric_difference_update(found_routes)
                    self.selected_regions.symmetric_difference_update(found_regions)
                else:
                    self.selected_points = found_points
                    self.selected_routes = found_routes
                    self.selected_regions = found_regions
                if found_points or found_routes or found_regions:
                    reference_id = next(iter(found_points | found_routes | found_regions))
                    reference = self.simulation.spatial.references[reference_id]
                    self.active_reference_id = reference_id
                    self.active_target = reference.geometry
                self._commit_selection()
                return
        else:
            left, right = sorted((start.x, end.x))
            top, bottom = sorted((start.y, end.y))
            found = {
                entity_id
                for entity_id, entity in self.simulation.entities.items()
                if entity.owner_id == "player"
                if entity.category is EntityCategory.UNIT
                if left <= entity.selection_position.x <= right
                and top <= entity.selection_position.y <= bottom
            }
        if additive:
            self.selected_entities.symmetric_difference_update(found)
        else:
            self.selected_entities = found
        self.inspected_entity_id = next(iter(found)) if len(found) == 1 else None
        self._selection_changed()
        self.notice = f"Selected {len(self.selected_entities)} unit(s)."

    def _finish_target(self, target: SpatialTarget) -> None:
        if self.editing_reference_id is not None:
            result = self.simulation.execute(
                EditSpatialReferenceCommand(self.editing_reference_id, target)
            )
            self.editing_reference_id = None
        else:
            result = self.simulation.execute(CreateSpatialReferenceCommand(target))
        if result.accepted:
            self.active_target = target
            self.active_reference_id = result.reference_id
            self._select_reference(result.reference_id)
            self.notice = f"Spatial object {result.reference_id} ready."
        else:
            self.notice = result.reason
        self.mode = InputMode.SELECT
        self._clear_draft()

    def _select_reference(self, reference_id: str | None) -> None:
        if reference_id is None:
            return
        reference = self.simulation.spatial.references[reference_id]
        self.selected_points = {reference_id} if reference.kind is SpatialKind.POINT else set()
        self.selected_routes = {reference_id} if reference.kind is SpatialKind.ROUTE else set()
        self.selected_regions = {reference_id} if reference.kind is SpatialKind.REGION else set()
        self.active_reference_id = reference_id
        self.active_target = reference.geometry
        self._commit_selection()

    def _commit_selection(self) -> None:
        self.simulation.execute(
            SetSelectionCommand(
                tuple(sorted(self.selected_entities)),
                tuple(sorted(self.selected_points)),
                tuple(sorted(self.selected_routes)),
                tuple(sorted(self.selected_regions)),
            )
        )

    def _selection_changed(self) -> None:
        kinds = {
            self.simulation.entities[entity_id].kind
            for entity_id in self.selected_entities
            if entity_id in self.simulation.entities
        }
        self.inspected_kind = next(iter(kinds)) if len(kinds) == 1 else None
        self._commit_selection()

    def _filter_selection_to_kind(self, kind: EntityKind) -> None:
        self.selected_entities = {
            entity_id
            for entity_id in self.selected_entities
            if self.simulation.entities[entity_id].kind is kind
        }
        self.inspected_entity_id = (
            next(iter(self.selected_entities)) if len(self.selected_entities) == 1 else None
        )
        self._selection_changed()
        self.notice = f"Selected {len(self.selected_entities)} {kind.value} unit(s)."

    def _select_all_visible_kind(self, kind: EntityKind) -> None:
        self.selected_entities = {
            entity_id
            for entity_id, entity in self.simulation.entities.items()
            if entity.owner_id == "player"
            and entity.kind is kind
            and self.canvas_rect.collidepoint(self._screen_point(entity.selection_position))
        }
        self.inspected_entity_id = (
            next(iter(self.selected_entities)) if len(self.selected_entities) == 1 else None
        )
        self._selection_changed()
        self.notice = f"Selected all visible {kind.value} units."

    def _name_selected_region(self) -> None:
        if len(self.selected_regions) != 1:
            self.notice = "Select exactly one region before naming it."
            return
        reference_id = next(iter(self.selected_regions))
        self.naming_reference_id = reference_id
        self.naming_buffer = self.simulation.spatial.references[reference_id].name or ""
        self.notice = "Type a unique region name, then press Enter."

    def _handle_name_key(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_ESCAPE:
            self.naming_reference_id = None
            self.naming_buffer = ""
            self.notice = "Region naming canceled."
        elif event.key == pygame.K_BACKSPACE:
            self.naming_buffer = self.naming_buffer[:-1]
        elif event.key in {pygame.K_RETURN, pygame.K_KP_ENTER}:
            assert self.naming_reference_id is not None
            result = self.simulation.execute(
                RenameRegionCommand(self.naming_reference_id, self.naming_buffer)
            )
            if result.accepted:
                self.naming_reference_id = None
                self.naming_buffer = ""
                self.notice = "Region name saved."
            else:
                self.notice = result.reason
        elif event.unicode.isprintable() and len(self.naming_buffer) < 40:
            self.naming_buffer += event.unicode
            self.notice = f"Region name: {self.naming_buffer}"

    def _apply_target_to_automation(self) -> None:
        if self.selected_automation_id is None or self.active_target is None:
            self.notice = "Select an automation and spatial target first."
            return
        result = self.simulation.execute(
            ModifyAutomationCommand(self.selected_automation_id, target=self.active_target)
        )
        self.notice = "Automation target updated." if result.accepted else result.reason

    def _change_automation_priority(self, delta: int) -> None:
        if self.selected_automation_id is None:
            self.notice = "Select an automation first."
            return
        automation = self.simulation.automations[self.selected_automation_id]
        result = self.simulation.execute(
            ModifyAutomationCommand(automation.automation_id, priority=automation.priority + delta)
        )
        self.notice = "Automation priority updated." if result.accepted else result.reason

    def _create_patrol(self) -> None:
        if self.active_target is None:
            self.notice = "Draw a point, line, rectangle, or freehand area first."
            return
        result = self.simulation.execute(
            CreatePatrolCommand(tuple(sorted(self.selected_entities)), self.active_target)
        )
        if result.accepted:
            self.selected_automation_id = result.automation_id
            self.notice = f"Created {result.automation_id}."
        else:
            self.notice = result.reason

    def _create_defend(self) -> None:
        if self.active_target is None:
            self.notice = "Draw a spatial target before creating a defense automation."
            return
        result = self.simulation.execute(
            CreateDefendCommand(tuple(sorted(self.selected_entities)), self.active_target)
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.selected_automation_id = result.automation_id

    def _create_production(self) -> None:
        factories = self._selected_factory_ids()
        if not factories:
            self.notice = "Select one or more factories for production."
            return
        if not isinstance(self.active_target, PolygonRegion | PolylineTarget):
            self.notice = "Select a defense line or area before using Produce + Defend."
            return
        loops = tuple(self.simulation.continuous_production(factory_id) for factory_id in factories)
        if any(loop is None for loop in loops):
            self.notice = "Start a Loop on every selected factory before Produce + Defend."
            return
        results = [
            self.simulation.execute(
                ModifyAutomationCommand(loop.automation_id, target=self.active_target)
            )
            for loop in loops
            if loop is not None
        ]
        accepted = sum(result.accepted for result in results)
        if accepted == len(factories):
            self.notice = f"Production defense assigned to {accepted} factories."
            self.selected_automation_id = next(
                loop.automation_id for loop in reversed(loops) if loop is not None
            )
        else:
            failure = next(result.reason for result in results if not result.accepted)
            self.notice = f"Updated {accepted}/{len(factories)} factories: {failure}"

    def _selected_factory_ids(self) -> tuple[str, ...]:
        return tuple(
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].owner_id == "player"
            and self.simulation.entities[entity_id].kind is EntityKind.FACTORY
        )

    def _create_repair(self) -> None:
        units = tuple(
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].is_movable
        )
        result = self.simulation.execute(CreateRepairAndReturnCommand(units))
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.selected_automation_id = result.automation_id

    def _create_economy(self) -> None:
        generators = tuple(
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].owner_id == "player"
            and self.simulation.entities[entity_id].kind is EntityKind.RESOURCE_GENERATOR
        )
        result = self.simulation.execute(
            CreateEconomyCommand(generators, self.simulation.resources.get("player", 0) + 100)
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.selected_automation_id = result.automation_id

    def _stop_selected(self) -> None:
        result = self.simulation.execute(StopCommand(tuple(sorted(self.selected_entities))))
        self.notice = "Units stopped." if result.accepted else result.reason

    def _hold_selected(self) -> None:
        result = self.simulation.execute(HoldPositionCommand(tuple(sorted(self.selected_entities))))
        self.notice = "Units holding position." if result.accepted else result.reason

    def _delete_selected_reference(self) -> None:
        selected = self.selected_routes | self.selected_regions
        if len(selected) != 1:
            self.notice = "Select exactly one route or region to delete."
            return
        reference_id = next(iter(selected))
        result = self.simulation.execute(DeleteSpatialReferenceCommand(reference_id))
        if result.accepted:
            self.selected_routes.discard(reference_id)
            self.selected_regions.clear()
            self.active_reference_id = None
            self.active_target = None
            self.notice = result.reason
        else:
            self.notice = result.reason

    def _save_game(self) -> None:
        try:
            save_simulation(self.simulation, self.quick_save_path)
        except OSError as error:
            self.notice = f"Save failed: {error}"
        else:
            self.notice = f"Saved {self.quick_save_path}."

    def _load_game(self) -> None:
        try:
            simulation = load_simulation(self.quick_save_path)
        except (OSError, PersistenceError) as error:
            self.notice = f"Load failed: {error}"
            return
        self.simulation = simulation
        self._map_surface = None
        self._scaled_map_surface = None
        self._scaled_map_size = None
        self._reset_presentation_history()
        self._clear_selection_state()
        self.notice = f"Loaded {self.quick_save_path}."

    def _new_game(self) -> None:
        self.simulation = Simulation(
            self._initial_map,
            self._initial_seed,
            ambient_enemy_spawns=self._initial_ambient_enemy_spawns,
            enemy_spawn_interval_ticks=self._initial_enemy_spawn_interval_ticks,
            enemy_spawn_cap=self._initial_enemy_spawn_cap,
        )
        self._map_surface = None
        self._scaled_map_surface = None
        self._scaled_map_size = None
        self._reset_presentation_history()
        self._clear_selection_state()
        self.notice = "New game started."

    def _clear_selection_state(self) -> None:
        self.selected_entities.clear()
        self.selected_points.clear()
        self.selected_routes.clear()
        self.selected_regions.clear()
        self.inspected_entity_id = None
        self.active_reference_id = None
        self.active_target = None
        self.selected_automation_id = None
        self.inspected_kind = None
        self.placement_kind = None

    def _handle_command_click(self, position: tuple[int, int]) -> None:
        for rectangle, action in self._command_buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "settings":
                self.settings_open = not self.settings_open
                return
            if action == "help":
                self.help_open = not self.help_open
                return
            if action.startswith("build:"):
                self.placement_kind = EntityKind(action.partition(":")[2])
                self.notice = "Click a clear grid location to place the building."
                return
            if action.startswith("queue:"):
                kind = EntityKind(action.partition(":")[2])
                if self.production_sequence and self.production_sequence[-1][0] is kind:
                    previous_kind, quantity = self.production_sequence[-1]
                    self.production_sequence[-1] = (previous_kind, quantity + 1)
                else:
                    self.production_sequence.append((kind, 1))
                self.notice = "Unit added to the staged production queue."
                return
            if action.startswith("loop:"):
                factories = self._selected_factory_ids()
                if not factories:
                    self.notice = "Select one or more factories."
                    return
                kind = EntityKind(action.partition(":")[2])
                results = [
                    self.simulation.execute(
                        CreateProductionCommand(
                            factory_id,
                            kind,
                            1,
                            title=f"Continuous {kind.value.replace('_', ' ').title()}",
                            continuous=True,
                        )
                    )
                    for factory_id in factories
                ]
                accepted = sum(result.accepted for result in results)
                if accepted == len(factories):
                    self.notice = (
                        f"Continuous {kind.value.replace('_', ' ')} started for "
                        f"{accepted} factories."
                    )
                    self.selected_automation_id = next(
                        result.automation_id for result in reversed(results) if result.accepted
                    )
                else:
                    failure = next(result.reason for result in results if not result.accepted)
                    self.notice = f"Started {accepted}/{len(factories)} factories: {failure}"
                return
            if action == "start_queue":
                factories = self._selected_factory_ids()
                if not factories or not self.production_sequence:
                    self.notice = "Select factories and stage at least one unit."
                    return
                sequence = tuple(self.production_sequence)
                results = [
                    self.simulation.execute(CreateProductionBatchCommand(factory_id, sequence))
                    for factory_id in factories
                ]
                accepted = sum(result.accepted for result in results)
                if accepted == len(factories):
                    self.production_sequence.clear()
                    self.notice = f"Production queue started for {accepted} factories."
                    self.selected_automation_id = next(
                        result.automation_id for result in reversed(results) if result.accepted
                    )
                else:
                    failure = next(result.reason for result in results if not result.accepted)
                    self.notice = f"Queued {accepted}/{len(factories)} factories: {failure}"
                return
            {
                "stop": self._stop_selected,
                "hold": self._hold_selected,
                "patrol": self._create_patrol,
                "defend": self._create_defend,
                "produce": self._create_production,
                "economy": self._create_economy,
                "delete": self._delete_selected_reference,
                "save": self._save_game,
                "load": self._load_game,
                "new": self._new_game,
            }[action]()
            return

    def _handle_settings_click(self, position: tuple[int, int]) -> bool:
        for rectangle, action in self._settings_buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "resolution_lower":
                self._request_resolution_step(-1)
                return True
            if action == "resolution_higher":
                self._request_resolution_step(1)
                return True
            {"save": self._save_game, "load": self._load_game, "new": self._new_game}[action]()
            self.settings_open = False
            return True
        return False

    def _handle_panel_click(self, position: tuple[int, int]) -> None:
        for rectangle, kind in self._type_buttons:
            if rectangle.collidepoint(position):
                self._filter_selection_to_kind(kind)
                return
        self._handle_command_click(position)
        for rectangle, action, automation_id in self._automation_buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "inspect":
                self.selected_automation_id = automation_id
                self.notice = f"Inspecting {automation_id}."
                return
            if action == "pause":
                result = self.simulation.execute(PauseAutomationCommand(automation_id))
            elif action == "resume":
                result = self.simulation.execute(ResumeAutomationCommand(automation_id))
            else:
                result = self.simulation.execute(CancelAutomationCommand(automation_id))
            self.notice = "Automation updated." if result.accepted else result.reason
            if result.accepted:
                self._clamp_automation_scroll(
                    visible_rows=self._automation_visible_rows,
                    total_rows=len(self._automation_panel_rows()),
                )
            return

    def _draw(self, screen: pygame.Surface) -> None:
        draw_key: tuple[object, ...] = (
            id(screen),
            screen.get_size(),
            self.simulation.tick,
            self._opengl_overlay_key(),
        )
        if self._software_cache_pending:
            self._software_frame_surface = screen.copy()
            self._software_cache_pending = False
            if draw_key == self._software_draw_key:
                return
        if draw_key == self._software_draw_key:
            assert self._software_frame_surface is not None
            screen.blit(self._software_frame_surface, (0, 0))
            return
        same_target = (
            self._software_draw_key is not None and self._software_draw_key[:2] == draw_key[:2]
        )
        if (
            same_target
            and self._software_draw_tick != self.simulation.tick
            and self._software_deferred_tick is None
        ):
            self._software_deferred_tick = self.simulation.tick
            assert self._software_frame_surface is not None
            screen.blit(self._software_frame_surface, (0, 0))
            return
        tile_size = min(
            self.canvas_rect.width / self.simulation.game_map.width,
            self.canvas_rect.height / self.simulation.game_map.height,
        )
        map_pixel_size = (
            round(self.simulation.game_map.width * tile_size),
            round(self.simulation.game_map.height * tile_size),
        )
        self._frame_tile_size = tile_size
        self._frame_map_pixel_size = map_pixel_size
        self._frame_map_origin = (
            self.canvas_rect.centerx - map_pixel_size[0] // 2 + round(self.camera_offset.x),
            self.canvas_rect.centery - map_pixel_size[1] // 2 + round(self.camera_offset.y),
        )
        try:
            self._prune_removed_entities()
            self._frame_selected_entities = tuple(
                self.simulation.entities[entity_id] for entity_id in self.selected_entities
            )
            pygame.draw.rect(screen, self.BACKGROUND, self.canvas_rect)
            previous_clip = screen.get_clip()
            screen.set_clip(self.canvas_rect)
            self._draw_map(screen)
            self._draw_spatial_input(screen)
            self._draw_construction(screen)
            self._draw_assembly_glows(screen)
            self._draw_entities(screen)
            self._draw_projectiles(screen)
            screen.set_clip(previous_clip)
            self._draw_interface(screen)
        finally:
            self._frame_tile_size = None
            self._frame_map_pixel_size = None
            self._frame_map_origin = None
            self._frame_selected_entities = None
        self._software_draw_key = draw_key
        self._software_draw_tick = self.simulation.tick
        self._software_deferred_tick = None
        if self._software_frame_surface is None:
            self._software_frame_surface = screen.copy()
        else:
            self._software_cache_pending = True

    def _draw_interface(self, screen: pygame.Surface) -> None:
        self._draw_command_bar(screen)
        self._draw_panel(screen)
        self._draw_context_panel(screen)
        if self.settings_open:
            self._draw_settings_menu(screen)
        if self.help_open:
            self._draw_help(screen)

    def _draw_opengl_overlay(self, screen: pygame.Surface) -> None:
        """Draw non-base scene feedback and the UI into a GPU-composited texture."""

        self._prune_removed_entities()
        self._frame_selected_entities = tuple(
            self.simulation.entities[entity_id] for entity_id in self.selected_entities
        )
        try:
            previous_clip = screen.get_clip()
            screen.set_clip(self.canvas_rect)
            self._draw_spatial_input(screen)
            self._draw_construction(screen)
            screen.set_clip(previous_clip)
            self._draw_interface(screen)
        finally:
            self._frame_selected_entities = None

    def _draw_opengl_partial_overlay(
        self,
        screen: pygame.Surface,
        regions: tuple[pygame.Rect, ...],
    ) -> None:
        """Refresh selected overlay rectangles without rebuilding the native canvas."""

        refresh_canvas = any(region.colliderect(self.canvas_rect) for region in regions)
        refresh_left = any(region.colliderect(self.left_panel_rect) for region in regions)
        refresh_right = any(region.colliderect(self.right_panel_rect) for region in regions)
        refresh_commands = any(region.colliderect(self.command_bar_rect) for region in regions)
        if refresh_canvas:
            for region in regions:
                canvas_region = region.clip(self.canvas_rect)
                if canvas_region.width and canvas_region.height:
                    screen.fill((0, 0, 0, 0), canvas_region)
        self._prune_removed_entities()
        self._frame_selected_entities = tuple(
            self.simulation.entities[entity_id] for entity_id in self.selected_entities
        )
        try:
            if refresh_canvas:
                previous_clip = screen.get_clip()
                screen.set_clip(self.canvas_rect)
                if any(
                    automation.kind is AutomationKind.CONSTRUCTION
                    for automation in self.simulation.live_automations
                ):
                    self._draw_construction(screen)
                screen.set_clip(previous_clip)
            if refresh_right or refresh_commands:
                self._draw_interface(screen)
            elif refresh_left:
                self._command_buttons[:] = [
                    button for button in self._command_buttons if button[1] != "help"
                ]
                self._draw_panel(
                    screen,
                    background_regions=tuple(
                        region.clip(self.left_panel_rect)
                        for region in regions
                        if region.colliderect(self.left_panel_rect)
                    ),
                )
        finally:
            self._frame_selected_entities = None

    def _opengl_overlay_key(self) -> tuple[object, ...]:
        """Identify every state change that requires rebuilding the native UI texture."""

        pointer = (
            pygame.mouse.get_pos()
            if self.placement_kind is not None
            or self.drag_start is not None
            or self.mode is not InputMode.SELECT
            else None
        )
        return (
            id(self.simulation),
            self.simulation.tick // self.OPENGL_OVERLAY_REFRESH_TICKS,
            self.simulation.command_count,
            self.mode,
            self.paused,
            self.notice,
            frozenset(self.selected_entities),
            frozenset(self.selected_points),
            frozenset(self.selected_routes),
            frozenset(self.selected_regions),
            repr(self.active_target),
            self.active_reference_id,
            self.editing_reference_id,
            self.selected_automation_id,
            self.inspected_entity_id,
            self.inspected_kind,
            self.naming_reference_id,
            self.naming_buffer,
            tuple(self.line_points),
            tuple(self.freehand_points),
            self.drag_start,
            self.camera_offset,
            self.automation_scroll,
            self.settings_open,
            self.help_open,
            self.placement_kind,
            tuple(self.production_sequence),
            tuple(self.left_panel_rect),
            tuple(self.right_panel_rect),
            tuple(self.canvas_rect),
            tuple(self.command_bar_rect),
            pointer,
            self._opengl_canvas_overlay_key(),
        )

    def _opengl_partial_overlay_regions(
        self,
        previous: tuple[object, ...],
        current: tuple[object, ...],
    ) -> tuple[pygame.Rect, ...] | None:
        """Return the exact UI regions affected by inexpensive state-only changes."""

        if len(previous) != len(current):
            return None
        changed = {
            index
            for index, (old_value, new_value) in enumerate(zip(previous, current, strict=True))
            if old_value != new_value
        }
        left_panel_changes = {1, 2, 4, 5, 6, 13, 14, 22}
        right_panel_changes = {15}
        canvas_changes = {32}
        if not changed or not changed <= (
            left_panel_changes | right_panel_changes | canvas_changes
        ):
            return None
        regions: list[pygame.Rect] = []
        if changed & left_panel_changes:
            if changed & {5, 22}:
                regions.append(self.left_panel_rect.copy())
            else:
                regions.extend(self._opengl_dynamic_overlay_regions())
        if changed & (right_panel_changes | {6}):
            context_height = min(self.right_panel_rect.height, 420)
            regions.append(
                pygame.Rect(
                    self.right_panel_rect.x,
                    self.right_panel_rect.y,
                    self.right_panel_rect.width,
                    context_height,
                )
            )
        if 6 in changed:
            bar = self.command_bar_rect
            regions.append(pygame.Rect(bar.x, bar.y, bar.width, min(78, bar.height)))
        if changed & canvas_changes:
            regions.extend(self._opengl_canvas_regions_from_key(previous[32]))
            regions.extend(self._opengl_canvas_regions_from_key(current[32]))
        return tuple(regions)

    def _opengl_dynamic_overlay_regions(self) -> tuple[pygame.Rect, ...]:
        """Return the populated left-panel bands whose values change with ticks."""

        panel = self.left_panel_rect
        y = 14 + 28 + 25 + len(self._wrap(self.notice, 46)) * 18 + 7
        y += 38
        y += 20 + 24
        if self.simulation.entities.get(self.inspected_entity_id or "") is not None:
            selection_lines = 4
        elif self.selected_entities:
            selection_lines = 2
        else:
            selection_lines = 1
        selection_bottom = y + selection_lines * 17 + 3
        y += selection_lines * 17 + 5 + 25
        list_top = y
        list_bottom = max(
            list_top + self.AUTOMATION_ROW_HEIGHT,
            panel.bottom - round(180 * self.ui_scale),
        )
        visible_rows = max(1, (list_bottom - list_top) // self.AUTOMATION_ROW_HEIGHT)
        live_automations = self.simulation.live_automations
        live_rows = min(len(live_automations), visible_rows)
        automation_height = live_rows * self.AUTOMATION_ROW_HEIGHT
        if self.selected_automation_id in self.simulation.automations:
            automation_height += 60
        top_bottom = max(selection_bottom, list_top + automation_height + 3)
        return (
            pygame.Rect(panel.x, 38, panel.width, top_bottom - 38),
            pygame.Rect(
                panel.x,
                list_bottom - 3,
                panel.width,
                panel.bottom - list_bottom + 3,
            ),
        )

    def _opengl_canvas_overlay_key(self) -> tuple[tuple[object, ...], ...]:
        """Describe transient canvas pixels independently from ordinary tick updates."""

        items: list[tuple[object, ...]] = []
        for automation in self.simulation.live_automations:
            if automation.kind is AutomationKind.CONSTRUCTION:
                parameters = automation.parameters
                assert isinstance(parameters, ConstructionParameters)
                items.append(
                    (
                        "construction",
                        automation.automation_id,
                        parameters.construction_value,
                        tuple(self.canvas_rect),
                    )
                )
                continue
        return tuple(items)

    def _opengl_canvas_regions_from_key(self, key: object) -> tuple[pygame.Rect, ...]:
        if not isinstance(key, tuple):
            return ()
        regions: list[pygame.Rect] = []
        for item in key:
            if not isinstance(item, tuple) or not item:
                continue
            rectangle = item[3]
            if isinstance(rectangle, tuple) and len(rectangle) == 4:
                regions.append(pygame.Rect(rectangle))
        return tuple(regions)

    def _prune_removed_entities(self) -> None:
        existing = self.simulation.entities.keys()
        self.selected_entities.intersection_update(existing)
        if self.inspected_entity_id not in self.simulation.entities:
            self.inspected_entity_id = None

    def _draw_map(self, screen: pygame.Surface) -> None:
        if self._map_surface is not None:
            if self._scaled_map_surface is None or self._scaled_map_size != self.map_pixel_size:
                self._scaled_map_surface = pygame.transform.scale(
                    self._map_surface,
                    self.map_pixel_size,
                )
                self._scaled_map_size = self.map_pixel_size
            screen.blit(self._scaled_map_surface, self.map_origin)
            return
        surface = pygame.Surface((self.MAP_PIXELS, self.MAP_PIXELS))
        terrain_colors = {
            Terrain.GRASS: (64, 102, 60),
            Terrain.ROAD: (119, 106, 77),
            Terrain.FOREST: (43, 78, 48),
            Terrain.WATER: (42, 91, 132),
            Terrain.ROCK: (66, 69, 72),
            Terrain.BRIDGE: (148, 126, 82),
        }
        tile = self.MAP_PIXELS / self.simulation.game_map.width
        for y, row in enumerate(self.simulation.game_map.terrain):
            for x, terrain in enumerate(row):
                rectangle = pygame.Rect(
                    round(x * tile), round(y * tile), round(tile + 1), round(tile + 1)
                )
                pygame.draw.rect(surface, terrain_colors[terrain], rectangle)
        for cell in range(0, self.simulation.game_map.width + 1, 8):
            pixel = round(cell * tile)
            pygame.draw.line(surface, (44, 65, 49), (pixel, 0), (pixel, self.MAP_PIXELS))
            pygame.draw.line(surface, (44, 65, 49), (0, pixel), (self.MAP_PIXELS, pixel))
        self._map_surface = surface
        self._scaled_map_surface = pygame.transform.scale(surface, self.map_pixel_size)
        self._scaled_map_size = self.map_pixel_size
        screen.blit(self._scaled_map_surface, self.map_origin)

    def _draw_entities(self, screen: pygame.Surface) -> None:
        colors = {
            EntityKind.SCOUT: (82, 211, 237),
            EntityKind.LIGHT_TANK: (235, 221, 93),
            EntityKind.HEAVY_TANK: (230, 139, 75),
            EntityKind.BUILDER: (99, 220, 176),
            EntityKind.FACTORY: (112, 142, 181),
            EntityKind.REPAIR_HUB: (110, 178, 151),
            EntityKind.COMMAND_CENTER: (155, 129, 190),
            EntityKind.RESOURCE_GENERATOR: (198, 168, 88),
        }
        for points in self._representative_path_points():
            pygame.draw.lines(screen, (225, 225, 225), False, points, 1)
        tile_size = self.tile_size
        origin_x, origin_y = self.map_origin
        selected_entities = self.selected_entities
        large_selection = len(selected_entities) > 128
        if large_selection and self._draw_cached_large_unit_entities(
            screen,
            colors,
            tile_size,
            (origin_x, origin_y),
        ):
            return
        show_full_health_bars = not large_selection
        unit_radius = max(5, round(tile_size * 0.42))
        bar_width = max(12, round(tile_size * 1.4))
        selected_min_x: int | None = None
        selected_min_y = 0
        selected_max_x = 0
        selected_max_y = 0
        for entity_id, entity in self.simulation.entities.items():
            center = (
                origin_x + round(entity.selection_position.x * tile_size),
                origin_y + round(entity.selection_position.y * tile_size),
            )
            if entity.category is EntityCategory.BUILDING:
                width, height = entity.kind.profile.footprint
                rectangle = pygame.Rect(
                    origin_x + round(entity.position.x * tile_size),
                    origin_y + round(entity.position.y * tile_size),
                    round(width * tile_size),
                    round(height * tile_size),
                )
                pygame.draw.rect(screen, colors[entity.kind], rectangle, border_radius=3)
                pygame.draw.rect(screen, (35, 42, 49), rectangle, 2, border_radius=3)
                if entity_id in selected_entities:
                    pygame.draw.rect(screen, (255, 255, 255), rectangle.inflate(6, 6), 2)
            else:
                radius = unit_radius
                color = colors[entity.kind] if entity.owner_id == "player" else (218, 78, 78)
                if large_selection and entity_id in selected_entities:
                    color = (
                        min(255, color[0] + 45),
                        min(255, color[1] + 45),
                        min(255, color[2] + 45),
                    )
                pygame.draw.circle(screen, color, center, radius)
                if entity_id in selected_entities and not large_selection:
                    pygame.draw.circle(screen, (255, 255, 255), center, radius + 3, 2)
                elif entity_id in selected_entities:
                    left = center[0] - radius
                    top = center[1] - radius
                    right = center[0] + radius
                    bottom = center[1] + radius
                    if selected_min_x is None:
                        selected_min_x = left
                        selected_min_y = top
                        selected_max_x = right
                        selected_max_y = bottom
                    else:
                        selected_min_x = min(selected_min_x, left)
                        selected_min_y = min(selected_min_y, top)
                        selected_max_x = max(selected_max_x, right)
                        selected_max_y = max(selected_max_y, bottom)
                if entity_id == self.inspected_entity_id and entity_id not in selected_entities:
                    pygame.draw.circle(screen, (255, 210, 90), center, radius + 3, 2)
            if (
                show_full_health_bars
                or entity.health < entity.kind.profile.max_health
                or entity_id == self.inspected_entity_id
            ):
                bar = pygame.Rect(center[0] - bar_width // 2, center[1] - 12, bar_width, 3)
                pygame.draw.rect(screen, (70, 35, 35), bar)
                health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
                pygame.draw.rect(
                    screen,
                    (74, 218, 111),
                    pygame.Rect(bar.x, bar.y, health_width, 3),
                )
        if selected_min_x is not None:
            pygame.draw.rect(
                screen,
                (245, 245, 245),
                pygame.Rect(
                    selected_min_x - 3,
                    selected_min_y - 3,
                    selected_max_x - selected_min_x + 6,
                    selected_max_y - selected_min_y + 6,
                ),
                1,
            )
        inspected = self.simulation.entities.get(self.inspected_entity_id or "")
        interaction_range = (
            inspected.kind.profile.build_range
            if inspected is not None and inspected.kind is EntityKind.BUILDER
            else inspected.kind.profile.attack_range
            if inspected is not None
            else 0.0
        )
        if inspected is not None and interaction_range > 0 and len(self.selected_entities) <= 1:
            pygame.draw.circle(
                screen,
                (105, 232, 172) if inspected.kind is EntityKind.BUILDER else (255, 218, 100),
                self._screen_point(inspected.selection_position),
                round(interaction_range * self.tile_size),
                1,
            )

    def _draw_cached_large_unit_entities(
        self,
        screen: pygame.Surface,
        colors: dict[EntityKind, tuple[int, int, int]],
        tile_size: float,
        origin: tuple[int, int],
    ) -> bool:
        """Draw a large scene without rebuilding unchanged transforms each frame."""

        selected_entities = frozenset(self.selected_entities)
        key: tuple[object, ...] = (
            id(self.simulation),
            self.simulation.tick,
            origin,
            tile_size,
            selected_entities,
            self.inspected_entity_id,
            len(self.simulation.entities),
        )
        if key != self._large_unit_render_key:
            self._large_unit_render_key = key
            self._large_unit_renderable = True
            if self._large_unit_renderable:
                radius = max(5, round(tile_size * 0.42))
                bar_width = max(12, round(tile_size * 1.4))
                origin_x, origin_y = origin
                blits: list[tuple[pygame.Surface, tuple[int, int]]] = []
                building_draws: list[tuple[pygame.Rect, tuple[int, int, int], bool]] = []
                health_bars: list[tuple[pygame.Rect, int]] = []
                selected_min_x: int | None = None
                selected_min_y = 0
                selected_max_x = 0
                selected_max_y = 0
                inspected_ring: tuple[tuple[int, int], int] | None = None
                for entity_id, entity in self.simulation.entities.items():
                    color = colors[entity.kind] if entity.owner_id == "player" else (218, 78, 78)
                    selected = entity_id in selected_entities
                    if entity.category is EntityCategory.BUILDING:
                        width, height = entity.kind.profile.footprint
                        rectangle = pygame.Rect(
                            origin_x + round(entity.position.x * tile_size),
                            origin_y + round(entity.position.y * tile_size),
                            round(width * tile_size),
                            round(height * tile_size),
                        )
                        building_draws.append((rectangle, color, selected))
                        center = (
                            origin_x + round(entity.selection_position.x * tile_size),
                            origin_y + round(entity.selection_position.y * tile_size),
                        )
                    else:
                        center = (
                            origin_x + round(entity.position.x * tile_size),
                            origin_y + round(entity.position.y * tile_size),
                        )
                        if selected:
                            color = (
                                min(255, color[0] + 45),
                                min(255, color[1] + 45),
                                min(255, color[2] + 45),
                            )
                            left = center[0] - radius
                            top = center[1] - radius
                            right = center[0] + radius
                            bottom = center[1] + radius
                            if selected_min_x is None:
                                selected_min_x = left
                                selected_min_y = top
                                selected_max_x = right
                                selected_max_y = bottom
                            else:
                                selected_min_x = min(selected_min_x, left)
                                selected_min_y = min(selected_min_y, top)
                                selected_max_x = max(selected_max_x, right)
                                selected_max_y = max(selected_max_y, bottom)
                        elif entity_id == self.inspected_entity_id:
                            inspected_ring = (center, radius)
                        sprite = self._unit_sprite(radius, color)
                        blits.append((sprite, (center[0] - radius, center[1] - radius)))
                    if (
                        entity.health < entity.kind.profile.max_health
                        or entity_id == self.inspected_entity_id
                    ):
                        bar = pygame.Rect(
                            center[0] - bar_width // 2,
                            center[1] - 12,
                            bar_width,
                            3,
                        )
                        health_width = round(
                            bar_width * entity.health / entity.kind.profile.max_health
                        )
                        health_bars.append((bar, health_width))
                self._large_unit_blits = tuple(blits)
                self._large_building_draws = tuple(building_draws)
                self._large_unit_health_bars = tuple(health_bars)
                self._large_unit_inspected_ring = inspected_ring
                self._large_unit_selected_bounds = (
                    None
                    if selected_min_x is None
                    else pygame.Rect(
                        selected_min_x - 3,
                        selected_min_y - 3,
                        selected_max_x - selected_min_x + 6,
                        selected_max_y - selected_min_y + 6,
                    )
                )
        if not self._large_unit_renderable:
            return False
        screen.fblits(self._large_unit_blits)
        for rectangle, color, selected in self._large_building_draws:
            pygame.draw.rect(screen, color, rectangle, border_radius=3)
            pygame.draw.rect(screen, (35, 42, 49), rectangle, 2, border_radius=3)
            if selected:
                pygame.draw.rect(screen, (255, 255, 255), rectangle.inflate(6, 6), 2)
        if self._large_unit_inspected_ring is not None:
            center, radius = self._large_unit_inspected_ring
            pygame.draw.circle(screen, (255, 210, 90), center, radius + 3, 2)
        for bar, health_width in self._large_unit_health_bars:
            pygame.draw.rect(screen, (70, 35, 35), bar)
            pygame.draw.rect(
                screen,
                (74, 218, 111),
                pygame.Rect(bar.x, bar.y, health_width, 3),
            )
        if self._large_unit_selected_bounds is not None:
            pygame.draw.rect(
                screen,
                (245, 245, 245),
                self._large_unit_selected_bounds,
                1,
            )
        return True

    def _unit_sprite(
        self,
        radius: int,
        color: tuple[int, int, int],
    ) -> pygame.Surface:
        key = (radius, color)
        sprite = self._unit_sprite_cache.get(key)
        if sprite is None:
            diameter = radius * 2 + 1
            sprite = pygame.Surface((diameter, diameter), pygame.SRCALPHA)
            pygame.draw.circle(sprite, color, (radius, radius), radius)
            self._unit_sprite_cache[key] = sprite
        return sprite

    def _draw_construction(self, screen: pygame.Surface) -> None:
        construction_automations = tuple(
            automation
            for automation in self.simulation.live_automations
            if automation.kind is AutomationKind.CONSTRUCTION
        )
        if not construction_automations and self.placement_kind is None:
            return
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        for automation in construction_automations:
            parameters = automation.parameters
            assert isinstance(parameters, ConstructionParameters)
            width, height = parameters.building_kind.profile.footprint
            origin = self._screen_point(parameters.position)
            rectangle = pygame.Rect(
                origin,
                (round(width * self.tile_size), round(height * self.tile_size)),
            )
            pygame.draw.rect(overlay, (94, 176, 220, 80), rectangle)
            pygame.draw.rect(overlay, (164, 222, 250, 230), rectangle, 2)
            progress = parameters.construction_value / parameters.required_value
            bar = pygame.Rect(rectangle.x, rectangle.bottom + 3, rectangle.width, 5)
            pygame.draw.rect(overlay, (30, 38, 46, 230), bar)
            pygame.draw.rect(
                overlay,
                (102, 222, 156, 245),
                pygame.Rect(bar.x, bar.y, round(bar.width * progress), bar.height),
            )
            if self._small_font is not None:
                label = self._small_font.render(f"{round(progress * 100)}%", True, (245, 250, 252))
                overlay.blit(label, (rectangle.x, rectangle.y - label.get_height()))

        mouse = pygame.mouse.get_pos() if pygame.display.get_init() else (-1, -1)
        if self.placement_kind is not None and self.canvas_rect.collidepoint(mouse):
            preview = self._construction_preview_at(self._map_point(mouse))
            if preview is not None:
                position, valid = preview
                width, height = self.placement_kind.profile.footprint
                origin = self._screen_point(position)
                rectangle = pygame.Rect(
                    origin,
                    (round(width * self.tile_size), round(height * self.tile_size)),
                )
                color = (95, 224, 145) if valid else (235, 88, 88)
                pygame.draw.rect(overlay, (*color, 72), rectangle)
                pygame.draw.rect(overlay, (*color, 245), rectangle, 3)
        screen.blit(overlay, (0, 0))

    def _construction_preview_at(self, point: Point) -> tuple[Point, bool] | None:
        if self.placement_kind is None:
            return None
        position = Point(float(int(point.x)), float(int(point.y)))
        valid = self.simulation._validate_building_placement(self.placement_kind, position) is None
        return position, valid

    def _draw_assembly_glows(self, screen: pygame.Surface) -> None:
        gathering_automations = tuple(
            automation
            for automation in self.simulation.live_automations
            if automation.kind is AutomationKind.DEFEND
            and isinstance(automation.parameters, DefendParameters)
            and automation.parameters.gathering_point
            and automation.entity_ids
        )
        if not gathering_automations:
            return
        glow = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        drawn = False
        for automation in gathering_automations:
            parameters = automation.parameters
            assert isinstance(parameters, DefendParameters)
            center = self._screen_point(target_center(parameters.target))
            radius = max(8, round((parameters.assembly_radius + 0.8) * self.tile_size))
            pygame.draw.circle(glow, (92, 177, 255, 20), center, radius)
            pygame.draw.circle(glow, (135, 205, 255, 72), center, radius, 2)
            pygame.draw.circle(glow, (190, 230, 255, 35), center, max(1, radius - 5), 2)
            drawn = True
        if drawn:
            screen.blit(glow, (0, 0))

    def _representative_path_entity_ids(self) -> tuple[str, ...]:
        selected = sorted(
            entity_id
            for entity_id in self.selected_entities
            if entity_id in self.simulation.entities and self.simulation.entities[entity_id].path
        )
        if len(selected) <= self.MAX_SELECTED_PATHS:
            representatives = selected
        else:
            step = (len(selected) - 1) / (self.MAX_SELECTED_PATHS - 1)
            representatives = [
                selected[round(index * step)] for index in range(self.MAX_SELECTED_PATHS)
            ]
        inspected = self.inspected_entity_id
        if (
            inspected is not None
            and inspected in self.simulation.entities
            and self.simulation.entities[inspected].path
            and inspected not in representatives
        ):
            if len(representatives) == self.MAX_SELECTED_PATHS:
                representatives[-1] = inspected
            else:
                representatives.append(inspected)
        return tuple(representatives)

    def _representative_path_points(self) -> tuple[tuple[tuple[int, int], ...], ...]:
        key: tuple[object, ...] = (
            id(self.simulation),
            self.simulation.tick,
            self.simulation.command_count,
            self.map_origin,
            self.tile_size,
            frozenset(self.selected_entities),
            self.inspected_entity_id,
        )
        if key != self._path_render_key:
            origin_x, origin_y = self.map_origin
            tile_size = self.tile_size
            paths: list[tuple[tuple[int, int], ...]] = []
            for entity_id in self._representative_path_entity_ids():
                entity = self.simulation.entities[entity_id]
                points = tuple(
                    (
                        origin_x + round(point.x * tile_size),
                        origin_y + round(point.y * tile_size),
                    )
                    for point in self._simplified_path(entity)
                )
                if len(points) >= 2:
                    paths.append(points)
            self._path_render_key = key
            self._path_render_points = tuple(paths)
        return self._path_render_points

    @staticmethod
    def _simplified_path(entity: Entity) -> tuple[Point, ...]:
        points = (entity.position, *entity.path)
        if len(points) <= 2:
            return points
        simplified = [points[0]]
        previous_direction: tuple[int, int] | None = None
        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            offset_x = current.x - previous.x
            offset_y = current.y - previous.y
            direction = (
                0 if abs(offset_x) <= 1e-9 else 1 if offset_x > 0 else -1,
                0 if abs(offset_y) <= 1e-9 else 1 if offset_y > 0 else -1,
            )
            if previous_direction is not None and direction != previous_direction:
                simplified.append(previous)
            previous_direction = direction
        simplified.append(points[-1])
        return tuple(simplified)

    def _draw_spatial_input(self, screen: pygame.Surface) -> None:
        for reference in self.simulation.spatial.references.values():
            self._draw_target(
                screen,
                reference.geometry,
                (92, 184, 222),
                reference.reference_id
                in self.selected_points | self.selected_routes | self.selected_regions,
            )
        target = self.active_target
        if target is not None:
            self._draw_target(screen, target, (229, 96, 155), True)
        draft_points = self.line_points or self.freehand_points
        if draft_points:
            pixels = [self._screen_point(point) for point in draft_points]
            mouse = pygame.mouse.get_pos()
            if (
                self.mode is InputMode.LINE
                and self.line_points
                and self.canvas_rect.collidepoint(mouse)
            ):
                pixels.append(mouse)
            if len(pixels) > 1:
                pygame.draw.lines(screen, (255, 170, 210), False, pixels, 2)
            for pixel in pixels[: len(draft_points)]:
                pygame.draw.circle(screen, (255, 170, 210), pixel, 3)
        if self.mode in {InputMode.SELECT, InputMode.RECTANGLE} and self.drag_start is not None:
            mouse = pygame.mouse.get_pos()
            if self.canvas_rect.collidepoint(mouse):
                start = self._screen_point(self.drag_start)
                rectangle = pygame.Rect(start, (mouse[0] - start[0], mouse[1] - start[1]))
                rectangle.normalize()
                pygame.draw.rect(screen, (245, 245, 245), rectangle, 1)
        if self._small_font is not None:
            for reference in self.simulation.spatial.references.values():
                if reference.kind is not SpatialKind.REGION or not reference.name:
                    continue
                assert isinstance(reference.geometry, PolygonRegion)
                center = Point(
                    sum(point.x for point in reference.geometry.points)
                    / len(reference.geometry.points),
                    sum(point.y for point in reference.geometry.points)
                    / len(reference.geometry.points),
                )
                label = self._small_font.render(reference.name, True, (240, 244, 250))
                label.set_alpha(220)
                label_rect = label.get_rect(center=self._screen_point(center))
                screen.blit(label, label_rect)

    def _draw_projectiles(self, screen: pygame.Surface) -> None:
        colors = {
            EntityKind.SCOUT: (120, 225, 255),
            EntityKind.LIGHT_TANK: (255, 232, 105),
            EntityKind.HEAVY_TANK: (255, 135, 70),
        }
        for trace in self.simulation.projectile_traces:
            points = [self._screen_point(point) for point in trace.points]
            if len(points) > 1:
                pygame.draw.lines(screen, colors[trace.weapon_kind], False, points, 1)
        for projectile in self.simulation.projectiles.values():
            color = colors[projectile.weapon_kind]
            points = [self._screen_point(point) for point in projectile.trajectory]
            if len(points) > 1:
                pygame.draw.lines(screen, color, False, points, 1)
            pygame.draw.line(
                screen,
                tuple(channel // 2 for channel in color),
                self._screen_point(projectile.position),
                self._screen_point(projectile.destination),
                1,
            )
            center = self._screen_point(projectile.position)
            pygame.draw.circle(screen, (255, 255, 255), center, 3)
            pygame.draw.circle(screen, color, center, 2)

    def _draw_target(
        self,
        screen: pygame.Surface,
        target: SpatialTarget,
        color: tuple[int, int, int],
        selected: bool,
    ) -> None:
        width = 4 if selected else 2
        if isinstance(target, PointTarget):
            center = self._screen_point(target.point)
            pygame.draw.circle(screen, color, center, round(target.radius * self.tile_size), width)
            pygame.draw.circle(screen, color, center, 4)
        elif isinstance(target, PolylineTarget):
            pygame.draw.lines(
                screen, color, False, [self._screen_point(p) for p in target.points], width
            )
        elif isinstance(target, PolygonRegion):
            surface = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            points = [self._screen_point(point) for point in target.points]
            pygame.draw.polygon(surface, (*color, 55), points)
            pygame.draw.polygon(surface, (*color, 230), points, width)
            screen.blit(surface, (0, 0))

    def _selected_entities_for_draw(self) -> tuple[Entity, ...]:
        if self._frame_selected_entities is not None:
            return self._frame_selected_entities
        return tuple(self.simulation.entities[entity_id] for entity_id in self.selected_entities)

    def _draw_command_bar(self, screen: pygame.Surface) -> None:
        if self._small_font is None:
            return
        bar = self.command_bar_rect
        pygame.draw.rect(screen, (24, 29, 36), bar)
        self._small_text(
            screen,
            "Commands — unavailable actions are omitted",
            (bar.x + 14, bar.y + 9),
            (190, 205, 220),
        )
        actions: list[tuple[str, str]] = []
        selected = self._selected_entities_for_draw()
        if selected and all(
            entity.owner_id == "player" and entity.is_movable for entity in selected
        ):
            actions.extend([("stop", "Stop"), ("hold", "Hold")])
            if self.active_target is not None:
                actions.extend([("patrol", "Patrol"), ("defend", "Defend")])
        factories = [
            entity
            for entity in selected
            if entity.owner_id == "player" and entity.kind is EntityKind.FACTORY
        ]
        if factories:
            loops = [self.simulation.continuous_production(item.entity_id) for item in factories]
            if all(loop is not None for loop in loops) and isinstance(
                self.active_target, PolygonRegion | PolylineTarget
            ):
                actions.append(("produce", "Produce + Defend"))
        if len(self.selected_routes | self.selected_regions) == 1:
            actions.append(("delete", "Delete route/region"))
        actions.append(("settings", "Settings"))
        self._command_buttons.clear()
        x = bar.x + 14
        y = bar.y + 38
        for action, label in actions:
            width = max(78, len(label) * 9 + 18)
            rectangle = pygame.Rect(x, y, width, 30)
            self._button(screen, rectangle, label)
            self._command_buttons.append((rectangle, action))
            x += width + 9

    def _draw_panel(
        self,
        screen: pygame.Surface,
        *,
        background_regions: tuple[pygame.Rect, ...] | None = None,
    ) -> None:
        if self._font is None or self._small_font is None:
            raise RuntimeError("fonts not initialized")
        panel = self.left_panel_rect
        for region in (panel,) if background_regions is None else background_regions:
            pygame.draw.rect(screen, self.PANEL_BACKGROUND, region)
        x = 16
        y = 14
        self._text(screen, "AIRTS — Phase 5", (x, y), (245, 245, 245))
        y += 28
        self._small_text(
            screen,
            f"Tick {self.simulation.tick} | Real FPS {self.real_fps:4.0f} | "
            f"{self.mode.value} | {'PAUSED' if self.paused else 'RUNNING'}",
            (x, y),
            (166, 191, 215),
        )
        y += 25
        for line in self._wrap(self.notice, 46):
            self._small_text(screen, line, (x, y), (244, 216, 118))
            y += 18
        y += 7
        help_button = pygame.Rect(x, y, 72, 26)
        self._button(screen, help_button, "Help")
        self._command_buttons.append((help_button, "help"))
        y += 38
        self._small_text(
            screen,
            f"Resources: {self.simulation.resources.get('player', 0)}",
            (x, y),
            (111, 221, 151),
        )
        y += 20
        self._text(screen, "Selection", (x, y), (245, 245, 245))
        y += 24
        inspected = self.simulation.entities.get(self.inspected_entity_id or "")
        if inspected is not None:
            profile = inspected.kind.profile
            owner = "friendly" if inspected.owner_id == "player" else "enemy / inspect only"
            lines = [
                f"{inspected.entity_id} | {inspected.kind.value} | {owner}",
                f"HP {inspected.health}/{profile.max_health} | state {inspected.state.value}",
                f"damage {profile.attack_damage} | range {profile.attack_range}",
                f"speed {profile.movement_speed or 0} | target {inspected.attack_target_id or '-'}",
            ]
        elif selected_entities := self._selected_entities_for_draw():
            kinds: dict[str, int] = {}
            health = 0
            maximum = 0
            for entity in selected_entities:
                kinds[entity.kind.value] = kinds.get(entity.kind.value, 0) + 1
                health += entity.health
                maximum += entity.kind.profile.max_health
            distribution = ", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items()))
            lines = [
                f"{len(selected_entities)} selected | HP {health}/{maximum}",
                distribution,
            ]
        else:
            lines = ["Nothing selected"]
        for line in lines:
            self._small_text(screen, line[:48], (x, y), (180, 198, 214))
            y += 17
        y += 5
        self._text(screen, "Automations", (x, y), (245, 245, 245))
        y += 25
        self._automation_buttons.clear()
        live_automations = self._automation_panel_rows()
        live_ids = {automation.automation_id for automation in live_automations}
        if self.selected_automation_id not in live_ids:
            self.selected_automation_id = (
                live_automations[0].automation_id if live_automations else None
            )
        list_top = y
        list_bottom = max(
            list_top + self.AUTOMATION_ROW_HEIGHT, panel.bottom - round(180 * self.ui_scale)
        )
        self._automation_visible_rows = max(
            1, (list_bottom - list_top) // self.AUTOMATION_ROW_HEIGHT
        )
        self._clamp_automation_scroll(
            visible_rows=self._automation_visible_rows,
            total_rows=len(live_automations),
        )
        visible_automations = live_automations[
            self.automation_scroll : self.automation_scroll + self._automation_visible_rows
        ]
        track = pygame.Rect(
            panel.right - self.AUTOMATION_SCROLLBAR_WIDTH - 6,
            list_top,
            self.AUTOMATION_SCROLLBAR_WIDTH,
            self._automation_visible_rows * self.AUTOMATION_ROW_HEIGHT,
        )
        self._automation_scrollbar_track = track
        pygame.draw.rect(screen, (49, 61, 75), track, border_radius=6)
        total_rows = len(live_automations)
        thumb_height = (
            track.height
            if total_rows <= self._automation_visible_rows
            else max(
                self.AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT,
                round(track.height * self._automation_visible_rows / total_rows),
            )
        )
        maximum = max(0, total_rows - self._automation_visible_rows)
        thumb_y = track.top + (
            0
            if maximum == 0
            else round((track.height - thumb_height) * self.automation_scroll / maximum)
        )
        thumb = pygame.Rect(track.x, thumb_y, track.width, thumb_height)
        self._automation_scrollbar_thumb = thumb
        pygame.draw.rect(screen, (112, 174, 224), thumb, border_radius=6)
        pygame.draw.rect(screen, (190, 225, 250), thumb, 2, border_radius=6)
        for automation in visible_automations:
            available_width = track.left - x - 8
            self._small_text(
                screen,
                self._fit_small_text(automation.title, available_width),
                (x, y),
                (232, 232, 232),
            )
            self._automation_buttons.append(
                (
                    pygame.Rect(x, y - 2, panel.width - 32, 38),
                    "inspect",
                    automation.automation_id,
                )
            )
            y += 17
            summary = (
                f"{automation.automation_id} | {automation.kind.value} | "
                f"{automation.status.value} | {len(automation.entity_ids)} entities"
            )
            if automation.kind is AutomationKind.PRODUCTION:
                parameters = automation.parameters
                assert isinstance(parameters, ProductionParameters)
                queue = self.simulation.production_queue(parameters.factory_id)
                queue_ids = [item.automation_id for item in queue]
                summary += f" | queue {queue_ids.index(automation.automation_id) + 1}/{len(queue)}"
                if parameters.continuous:
                    summary += f" | nonstop {parameters.produced_count}"
                linked_id = parameters.patrol_automation_id or parameters.defend_automation_id
                if linked_id is not None:
                    summary += (
                        " | area defense"
                        if parameters.defend_automation_id is not None
                        else f" | -> {linked_id}"
                    )
            elif automation.kind is AutomationKind.CONSTRUCTION:
                parameters = automation.parameters
                assert isinstance(parameters, ConstructionParameters)
                summary += (
                    f" | {parameters.building_kind.value} "
                    f"{parameters.construction_value:.0f}/{parameters.required_value:.0f}"
                )
            self._small_text(
                screen,
                self._fit_small_text(summary, available_width),
                (x, y),
                (153, 178, 198),
            )
            y += 21
            if automation.status in {
                AutomationStatus.ACTIVE,
                AutomationStatus.WAITING,
                AutomationStatus.BLOCKED,
                AutomationStatus.PAUSED,
            }:
                action = "resume" if automation.status is AutomationStatus.PAUSED else "pause"
                toggle = pygame.Rect(x, y, 75, 24)
                cancel = pygame.Rect(x + 84, y, 75, 24)
                self._button(screen, toggle, action.title())
                self._button(screen, cancel, "Cancel")
                self._automation_buttons.extend(
                    [
                        (toggle, action, automation.automation_id),
                        (cancel, "cancel", automation.automation_id),
                    ]
                )
                y += 32
            else:
                y += 7
        selected = self.simulation.automations.get(self.selected_automation_id or "")
        if selected is not None:
            y += 4
            self._small_text(
                screen,
                f"Inspector: {selected.automation_id} p={selected.priority}",
                (x, y),
                (244, 216, 118),
            )
            y += 17
            self._small_text(
                screen,
                f"{selected.creation_source} | {selected.reason_code} | owner {selected.owner_id}",
                (x, y),
                (158, 178, 194),
            )
            y += 17
            self._small_text(
                screen,
                f"created {selected.created_tick} modified {selected.modified_tick}",
                (x, y),
                (158, 178, 194),
            )
        y = max(y + 8, list_bottom)
        self._text(screen, "Recent events", (x, y), (245, 245, 245))
        y += 24
        for event in self.simulation.events.query(limit=7):
            subject = f" {event.subject_id}" if event.subject_id else ""
            line = f"{event.tick}: {event.event_type.value}{subject}"
            self._small_text(screen, line[:47], (x, y), (158, 178, 194))
            y += 18
            detail = str(event.details.get("reason") or event.details.get("code") or "")
            if detail:
                self._small_text(screen, f"  {detail}"[:47], (x, y), (124, 147, 166))
                y += 16

    def _draw_context_panel(self, screen: pygame.Surface) -> None:
        panel = self.right_panel_rect
        x = panel.x
        pygame.draw.rect(screen, (22, 27, 34), panel)
        x += 16
        y = 16
        self._text(screen, "Units and buildings", (x, y), (245, 245, 245))
        y += 34
        kinds: dict[EntityKind, int] = {}
        for entity in self._selected_entities_for_draw():
            kinds[entity.kind] = kinds.get(entity.kind, 0) + 1
        self._type_buttons.clear()
        if not kinds:
            self._small_text(screen, "Select a friendly unit or building.", (x, y), (164, 184, 202))
            return
        if len(kinds) > 1:
            for kind, count in sorted(kinds.items(), key=lambda item: item[0].value):
                rectangle = pygame.Rect(x, y, panel.width - 32, 30)
                self._button(screen, rectangle, f"{kind.value.replace('_', ' ').title()}  x{count}")
                self._type_buttons.append((rectangle, kind))
                y += 38
        else:
            self.inspected_kind = next(iter(kinds))
        detail_kind = self.inspected_kind if self.inspected_kind in kinds else None
        if detail_kind is None:
            self._small_text(
                screen, "Choose a type to show its controls.", (x, y + 4), (164, 184, 202)
            )
            return
        y += 12
        profile = detail_kind.profile
        count = kinds[detail_kind]
        detail_title = detail_kind.value.replace("_", " ").title()
        if count > 1:
            detail_title += f" x{count}"
        self._text(screen, detail_title, (x, y), (244, 216, 118))
        y += 28
        self._small_text(
            screen,
            f"HP {profile.max_health}  Speed {profile.movement_speed or 0}  Cost {profile.production_cost}",
            (x, y),
            (180, 198, 214),
        )
        y += 34
        if detail_kind is EntityKind.BUILDER:
            self._small_text(
                screen,
                f"Build range {profile.build_range}  Work {profile.build_speed}/tick",
                (x, y - 10),
                (105, 222, 172),
            )
            y += 22
            for kind in (EntityKind.FACTORY, EntityKind.REPAIR_HUB, EntityKind.RESOURCE_GENERATOR):
                rectangle = pygame.Rect(x, y, panel.width - 32, 30)
                self._button(screen, rectangle, f"Build {kind.value.replace('_', ' ').title()}")
                self._command_buttons.append((rectangle, f"build:{kind.value}"))
                y += 38
        elif detail_kind is EntityKind.FACTORY:
            for kind in (
                EntityKind.SCOUT,
                EntityKind.LIGHT_TANK,
                EntityKind.HEAVY_TANK,
                EntityKind.BUILDER,
            ):
                queue = pygame.Rect(x, y, panel.width - 126, 30)
                loop = pygame.Rect(queue.right + 8, y, 86, 30)
                self._button(screen, queue, f"Add {kind.value.replace('_', ' ').title()}")
                self._button(screen, loop, f"Loop x{count}" if count > 1 else "Loop")
                self._command_buttons.append((queue, f"queue:{kind.value}"))
                self._command_buttons.append((loop, f"loop:{kind.value}"))
                y += 38
            summary = ", ".join(
                f"{quantity} {kind.value}" for kind, quantity in self.production_sequence
            )
            self._small_text(screen, summary[:42] or "Queue is empty", (x, y), (164, 184, 202))
            y += 28
            rectangle = pygame.Rect(x, y, panel.width - 32, 30)
            self._button(
                screen,
                rectangle,
                f"Start queue on {count} factories" if count > 1 else "Start ordered queue",
            )
            self._command_buttons.append((rectangle, "start_queue"))
        elif detail_kind is EntityKind.RESOURCE_GENERATOR:
            rectangle = pygame.Rect(x, y, panel.width - 32, 30)
            self._button(
                screen,
                rectangle,
                f"Develop economy x{count}" if count > 1 else "Develop economy",
            )
            self._command_buttons.append((rectangle, "economy"))

    def _draw_settings_menu(self, screen: pygame.Surface) -> None:
        x = self.command_bar_rect.x + 14
        y = self.command_bar_rect.y - 208
        self._settings_buttons.clear()
        pygame.draw.rect(screen, (34, 41, 51), pygame.Rect(x, y, 268, 202), border_radius=4)
        self._small_text(
            screen,
            f"Resolution {self.window_size[0]} x {self.window_size[1]}",
            (x + 10, y + 8),
            (225, 232, 238),
        )
        lower = pygame.Rect(x + 8, y + 30, 120, 24)
        higher = pygame.Rect(x + 140, y + 30, 120, 24)
        self._button(screen, lower, "Lower")
        self._button(screen, higher, "Higher")
        self._settings_buttons.extend(((lower, "resolution_lower"), (higher, "resolution_higher")))
        metrics = self.presentation_metrics
        self._small_text(
            screen,
            f"Frame p95 {metrics.frame_p95_ms:5.1f} ms  Present {metrics.present_p95_ms:5.1f} ms",
            (x + 10, y + 62),
            (166, 191, 215),
        )
        self._small_text(
            screen,
            f"Render {metrics.render_p95_ms:5.1f} ms  Sim {metrics.simulation_p95_ms:5.1f} ms",
            (x + 10, y + 82),
            (166, 191, 215),
        )
        self._small_text(
            screen,
            f"Real FPS {metrics.one_percent_low_fps:4.0f}  Submit FPS {metrics.submit_fps:4.0f}",
            (x + 10, y + 102),
            (111, 221, 151),
        )
        y += 122
        for action, label in (("save", "Save"), ("load", "Load"), ("new", "New game")):
            rectangle = pygame.Rect(x + 8, y, 252, 24)
            self._button(screen, rectangle, label)
            self._settings_buttons.append((rectangle, action))
            y += 26

    def _draw_help(self, screen: pygame.Surface) -> None:
        rectangle = pygame.Rect(
            self.canvas_rect.x + 40,
            80,
            max(320, self.canvas_rect.width - 80),
            230,
        )
        pygame.draw.rect(screen, (28, 34, 42), rectangle, border_radius=4)
        pygame.draw.rect(screen, (104, 126, 149), rectangle, 1, border_radius=4)
        self._text(screen, "Controls", (rectangle.x + 18, rectangle.y + 16), (245, 245, 245))
        lines = (
            "1 Select   2 Line   3 Rectangle   4 Freehand",
            "Right-click Move/Attack or finish a line   Middle-drag Pan",
            "A Patrol   D Defend   R Repair   G Economy   Space Pause",
            "Shift multi-select   N Name   E Edit   U Retarget   [ ] Priority",
            "Choose a selected type in the right rail to show its actions.",
        )
        for index, line in enumerate(lines):
            self._small_text(
                screen,
                line,
                (rectangle.x + 18, rectangle.y + 55 + index * 28),
                (190, 205, 220),
            )

    def _button(self, screen: pygame.Surface, rectangle: pygame.Rect, label: str) -> None:
        pygame.draw.rect(screen, (59, 72, 88), rectangle, border_radius=3)
        pygame.draw.rect(screen, (104, 126, 149), rectangle, 1, border_radius=3)
        self._small_text(screen, label, (rectangle.x + 10, rectangle.y + 4), (240, 240, 240))

    def _text(
        self,
        screen: pygame.Surface,
        text: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        if self._font is None:
            raise RuntimeError("font not initialized")
        screen.blit(self._font.render(text, True, color), position)

    def _small_text(
        self,
        screen: pygame.Surface,
        text: str,
        position: tuple[int, int],
        color: tuple[int, int, int],
    ) -> None:
        if self._small_font is None:
            raise RuntimeError("font not initialized")
        screen.blit(self._small_font.render(text, True, color), position)

    def _map_point(self, position: tuple[int, int]) -> Point:
        origin_x, origin_y = self.map_origin
        if self._font is None and position[0] < self.canvas_rect.left:
            origin_x -= self.canvas_rect.left
        return Point(
            (position[0] - origin_x) / self.tile_size,
            (position[1] - origin_y) / self.tile_size,
        )

    def _screen_point(self, point: Point) -> tuple[int, int]:
        origin_x, origin_y = self.map_origin
        return (
            origin_x + round(point.x * self.tile_size),
            origin_y + round(point.y * self.tile_size),
        )

    def _clear_draft(self) -> None:
        self.line_points.clear()
        self.freehand_points.clear()
        self.drag_start = None

    def _fit_small_text(self, text: str, width: int) -> str:
        if self._small_font is None or self._small_font.size(text)[0] <= width:
            return text
        suffix = "..."
        available = max(0, width - self._small_font.size(suffix)[0])
        fitted = text
        while fitted and self._small_font.size(fitted)[0] > available:
            fitted = fitted[:-1]
        return fitted.rstrip() + suffix

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _entity_hit_distance(entity: Entity, point: Point) -> float:
    if entity.category is not EntityCategory.BUILDING:
        return entity.selection_position.distance_to(point)
    width, height = entity.kind.profile.footprint
    nearest_x = min(max(point.x, entity.position.x), entity.position.x + width)
    nearest_y = min(max(point.y, entity.position.y), entity.position.y + height)
    return point.distance_to(Point(nearest_x, nearest_y))


def _distance_to_polyline(point: Point, vertices: tuple[Point, ...]) -> float:
    distances: list[float] = []
    for start, end in zip(vertices, vertices[1:], strict=False):
        dx = end.x - start.x
        dy = end.y - start.y
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            distances.append(point.distance_to(start))
            continue
        fraction = max(
            0.0,
            min(
                1.0,
                ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_squared,
            ),
        )
        distances.append(
            hypot(
                point.x - (start.x + fraction * dx),
                point.y - (start.y + fraction * dy),
            )
        )
    return min(distances)
