"""Application window, interaction state, interface drawing and frame composition."""

from __future__ import annotations

import os
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from enum import StrEnum
from logging import getLogger
from math import floor, hypot
from pathlib import Path
from time import perf_counter

import pygame

from airts.adapters.persistence import PersistenceError, capture_save, load_simulation, write_save
from airts.contracts.automations import (
    AUTO_REPAIR_HEALTH_THRESHOLD,
    AutomationKind,
    ConstructionParameters,
    DefendParameters,
    PatrolParameters,
    ProductionParameters,
    target_center,
)
from airts.contracts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateConstructionCommand,
    CreateDefendCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    DeleteSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    HoldPositionCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    ReclaimBuildingCommand,
    ReduceProductionCommand,
    RenameRegionCommand,
    RepairBuildingCommand,
    ResumeAutomationCommand,
    SetDeveloperModeCommand,
    SetSelectionCommand,
    StopCommand,
)
from airts.contracts.spatial import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialKind,
    SpatialTarget,
    distance_to_segment,
    rectangle_region,
    simplify_freehand,
)
from airts.presentation.build_placement import BuildingLineDraft, building_line_previews
from airts.presentation.camera import Camera
from airts.presentation.lobby import Lobby
from airts.presentation.metrics import PresentationMetrics, PresentationProfiler
from airts.presentation.opengl_renderer import OpenGLRenderer, OpenGLRendererError
from airts.presentation.rendering import RendererBackend, SoftwareRenderer
from airts.presentation.ui import automations, menus, saves, selection
from airts.presentation.ui.drawing import UiPainter
from airts.simulation import Simulation
from airts.systems import construction
from airts.systems.skirmish import owner_status
from airts.world.entities import Entity
from airts.world.skirmish import create_skirmish_map
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory


class InputMode(StrEnum):
    SELECT = "select"
    LINE = "line"
    RECTANGLE = "rectangle"
    FREEHAND = "freehand"


class AirtsApp:
    FRAME_RATE_LIMIT = 1_000
    OPENGL_OVERLAY_REFRESH_TICKS = 3
    MAX_SELECTED_PATHS = 32
    AUTOMATION_ROW_HEIGHT = automations.AUTOMATION_ROW_HEIGHT
    AUTOMATION_SCROLLBAR_WIDTH = automations.AUTOMATION_SCROLLBAR_WIDTH
    AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT = automations.AUTOMATION_SCROLLBAR_MIN_THUMB_HEIGHT
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
    PANEL_BACKGROUND = automations.PANEL_BACKGROUND
    ENTITY_CLICK_RADIUS = 1.5
    ENEMY_CLICK_RADIUS = 2.5
    DOUBLE_CLICK_MS = 400
    DOUBLE_CLICK_PIXELS = 6

    def __init__(
        self,
        simulation: Simulation,
        renderer_backend: RendererBackend = RendererBackend.OPENGL,
        *,
        show_lobby: bool = False,
    ) -> None:
        self.simulation = simulation
        self.renderer_backend = renderer_backend
        self.lobby: Lobby | None = Lobby() if show_lobby else None
        self.camera = Camera()
        self.mode = InputMode.SELECT
        self.selected_entities: set[str] = set()
        self.selected_points: set[str] = set()
        self.selected_routes: set[str] = set()
        self.selected_regions: set[str] = set()
        self.active_target: SpatialTarget | None = None
        self.active_reference_id: str | None = None
        self.editing_reference_id: str | None = None
        self.inspected_entity_id: str | None = None
        self.naming_reference_id: str | None = None
        self.naming_buffer = ""
        self.line_points: list[Point] = []
        self.freehand_points: list[Point] = []
        self.drag_start: Point | None = None
        self._camera_keys: set[int] = set()
        self._camera_drag_position: tuple[int, int] | None = None
        self.production_detail_kind: EntityKind | None = None
        self.settings_open = False
        self.help_open = False
        self.placement_kind: EntityKind | None = None
        self.building_line_draft: BuildingLineDraft | None = None
        self.reclaim_mode = False
        self.paused = False
        self.real_fps = 0.0
        self.presentation_metrics = PresentationMetrics()
        self.render_alpha = 1.0
        self._previous_entity_positions: dict[str, Point] = {}
        self._previous_projectile_positions: dict[str, Point] = {}
        self.window_size = self.WINDOW_SIZE
        self._pending_window_size: tuple[int, int] | None = None
        self.notice = "Select units, draw a target, then press T to patrol."
        self._command_buttons: list[tuple[pygame.Rect, str]] = []
        self._settings_buttons: list[tuple[pygame.Rect, str]] = []
        self._type_buttons: list[tuple[pygame.Rect, EntityKind]] = []
        self.inspected_kind: EntityKind | None = None
        self._last_entity_click: tuple[str, int] | None = None
        self._last_move_click: tuple[tuple[str, ...], Point, int] | None = None
        self._initial_map = simulation.game_map
        self._initial_seed = simulation.random_seed
        self._initial_ambient_enemy_spawns = simulation.ambient_enemy_spawns
        self._initial_enemy_spawn_interval_ticks = simulation.enemy_spawn_interval_ticks
        self._initial_enemy_spawn_cap = simulation.enemy_spawn_cap
        self.quick_save_path = Path("airts-quicksave.json")
        self.save_directory = Path("saves")
        self.save_panel = saves.SavePanelState()
        self._save_executor: ThreadPoolExecutor | None = None
        self._save_future: Future[None] | None = None
        self._saving_path: Path | None = None
        self.ui = UiPainter()
        self.automation_panel = automations.AutomationPanelState()
        self._frame_selected_entities: tuple[Entity, ...] | None = None
        self._software_renderer = SoftwareRenderer()
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
        return self.camera.tile_size(
            self.canvas_rect, (self.simulation.game_map.width, self.simulation.game_map.height)
        )

    @property
    def map_pixel_size(self) -> tuple[int, int]:
        if self._frame_map_pixel_size is not None:
            return self._frame_map_pixel_size
        return self.camera.pixel_size(
            self.canvas_rect, (self.simulation.game_map.width, self.simulation.game_map.height)
        )

    @property
    def map_origin(self) -> tuple[int, int]:
        if self._frame_map_origin is not None:
            return self._frame_map_origin
        return self.camera.origin(self.canvas_rect, self.map_pixel_size)

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
        self.camera.reset_view(
            self.canvas_rect, (self.simulation.game_map.width, self.simulation.game_map.height)
        )
        if self.ui.font is not None and pygame.font.get_init():
            self.ui.font = pygame.font.Font(None, round(24 * self.ui_scale))
            self.ui.small_font = pygame.font.Font(None, round(19 * self.ui_scale))

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
        self.camera.pan(dx, dy, self.canvas_rect, self.map_pixel_size)

    def _move_camera_from_keys(self, seconds: float) -> None:
        if (
            self.lobby is not None
            or self.naming_reference_id is not None
            or self.settings_open
            or self.save_panel.open
            or self.help_open
        ):
            return
        dx = int(pygame.K_a in self._camera_keys) - int(pygame.K_d in self._camera_keys)
        dy = int(pygame.K_w in self._camera_keys) - int(pygame.K_s in self._camera_keys)
        length = hypot(dx, dy)
        if length:
            step = 700 * seconds / length
            self.pan_camera(dx * step, dy * step)

    def zoom_camera(self, steps: int, position: tuple[int, int]) -> None:
        point = self._map_point(position)
        self.camera.change_zoom(
            steps,
            self.canvas_rect,
            (self.simulation.game_map.width, self.simulation.game_map.height),
        )
        after = self.screen_point(point)
        self.pan_camera(position[0] - after[0], position[1] - after[1])

    def start_match(self, setup: tuple[tuple[str, int], ...]) -> None:
        try:
            game_map = create_skirmish_map(setup)
        except ValueError as error:
            self.notice = str(error)
            return
        self.simulation = Simulation(game_map, self._initial_seed)
        self._initial_map = game_map
        self._initial_ambient_enemy_spawns = False
        self.lobby = None
        self.paused = False
        self.camera.zoom = 1.0
        self.camera.offset = Point(0, 0)
        self._software_renderer.reset_map()
        self._reset_presentation_history()
        self._clear_selection_state()
        self.notice = "Match started. Build extractors on the red resource sites."

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
            self.ui.font = pygame.font.Font(None, 24)
            self.ui.small_font = pygame.font.Font(None, 19)
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
                self._poll_save()
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
                self._move_camera_from_keys(elapsed)
                if (
                    not self.paused
                    and not self.save_panel.open
                    and self.lobby is None
                    and self.simulation.match_result is None
                ):
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
                    accumulator = 0.0
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
            self._finish_save()
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

        self.ui.font = None
        self.ui.small_font = None
        self._software_renderer = SoftwareRenderer()
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
        if event.type == pygame.KEYUP:
            self._camera_keys.discard(event.key)
            return
        if event.type == pygame.WINDOWFOCUSLOST:
            self._camera_keys.clear()
            self.building_line_draft = None
            return
        if self.save_panel.open and event.type not in {
            pygame.VIDEORESIZE,
            pygame.WINDOWSIZECHANGED,
        }:
            if event.type == pygame.MOUSEWHEEL:
                self.save_panel.scroll_by(-event.y)
            elif event.type == pygame.KEYDOWN:
                self._handle_key(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN:
                self._handle_mouse_down(event.button, event.pos)
            return
        if self.lobby is not None and event.type not in {
            pygame.VIDEORESIZE,
            pygame.WINDOWSIZECHANGED,
        }:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.lobby.click(self, event.pos)
            return
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
            details = automations.details_rect(self)
            if details is not None and details.collidepoint(mouse):
                return
            if self.left_panel_rect.collidepoint(mouse):
                rows = automations.panel_rows(self)
                automations.scroll(
                    self,
                    -event.y,
                    visible_rows=self.automation_panel.visible_rows,
                    total_rows=len(rows),
                )
            elif self._in_canvas(mouse) and not self.settings_open and not self.help_open:
                self.zoom_camera(event.y, mouse)
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
        if self.save_panel.open:
            if key == pygame.K_ESCAPE:
                if self.save_panel.confirmation:
                    self.save_panel.confirmation = None
                else:
                    self.save_panel.open = False
            elif key in {pygame.K_UP, pygame.K_DOWN}:
                self.save_panel.scroll_by(-1 if key == pygame.K_UP else 1)
            return
        if key in {pygame.K_t, pygame.K_y, pygame.K_p, pygame.K_r, pygame.K_x, pygame.K_h} and any(
            self.simulation.entities[entity_id].owner_id != "player"
            for entity_id in self.selected_entities
        ):
            self.notice = "Enemy units cannot receive orders."
            return
        if key in {pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d}:
            self._camera_keys.add(key)
            return
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
        elif key == pygame.K_t:
            self._create_patrol()
        elif key == pygame.K_y:
            self._create_defend()
        elif key == pygame.K_p:
            self._create_production()
        elif key == pygame.K_r:
            self._create_repair()
        elif key == pygame.K_x:
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
            self._toggle_pause()
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
        if self.save_panel.open:
            if button == 1:
                self._handle_save_click(position)
            return
        last_move_click = self._last_move_click
        self._last_move_click = None
        if button == 3 and (self.building_line_draft is not None or self.reclaim_mode):
            self.building_line_draft = None
            self.reclaim_mode = False
            self.placement_kind = None
            self.notice = "Draft canceled. Queued construction is unchanged."
            return
        # Direct headless interaction tests use historical canvas-local pixels.
        if self.ui.font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if button == 1 and self.settings_open:
            if self._handle_settings_click(position):
                return
            self.settings_open = False
            return
        details = automations.details_rect(self)
        if details is not None and details.collidepoint(position):
            if button == 1:
                self._handle_command_click(position)
            return
        if button == 2 and self._in_canvas(position):
            self._camera_drag_position = position
            return
        if (
            button == 1
            and self.automation_panel.scrollbar_track is not None
            and self.automation_panel.scrollbar_track.collidepoint(position)
        ):
            thumb = self.automation_panel.scrollbar_thumb
            self.automation_panel.scroll_drag_offset = (
                position[1] - thumb.top
                if thumb is not None and thumb.collidepoint(position)
                else None
            )
            automations.scroll_from_pointer(self, position[1])
            return
        if self.left_panel_rect.collidepoint(position) or self.right_panel_rect.collidepoint(
            position
        ):
            if button in {1, 3}:
                self._handle_panel_click(position, button=button)
            return
        if self.command_bar_rect.collidepoint(position):
            if button == 1:
                self._handle_command_click(position)
            return
        if not self._in_canvas(position):
            return
        point = self._map_point(position)
        try:
            shift_down = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        except pygame.error:
            shift_down = False
        if button == 1 and self.reclaim_mode:
            targets = sorted(
                (distance, entity.entity_id)
                for entity in self.simulation.entities.values()
                if entity.owner_id == "player"
                and entity.category is EntityCategory.BUILDING
                and (distance := _entity_hit_distance(entity, point)) <= self.ENTITY_CLICK_RADIUS
            )
            if not targets:
                self.notice = "Choose a friendly building to reclaim."
                return
            reclaim_builders = tuple(
                key
                for key in sorted(self.selected_entities)
                if self.simulation.entities[key].kind is EntityKind.BUILDER
            )
            result = self.simulation.execute(
                ReclaimBuildingCommand(reclaim_builders, targets[0][1])
            )
            self.notice = (
                "Builders will reclaim the building for 70%." if result.accepted else result.reason
            )
            if result.accepted:
                self.reclaim_mode = False
            return
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
            preview = self._construction_preview_at(point)
            assert preview is not None
            snapped = preview[0]
            try:
                queued = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
            except pygame.error:
                queued = False
            if queued:
                self.building_line_draft = BuildingLineDraft(
                    self.placement_kind, tuple(builders), snapped, snapped
                )
                self.notice = (
                    "Drag to preview a building line; release to queue, right-click to cancel."
                )
                return
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
                self.automation_panel.group = AutomationKind.CONSTRUCTION
                self.automation_panel.selected_id = result.automation_id
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
            if any(
                self.simulation.entities[entity_id].owner_id != "player"
                for entity_id in self.selected_entities
            ):
                self.notice = "Enemy units cannot receive orders."
                return
            entity_ids = tuple(sorted(self.selected_entities))
            if shift_down:
                result = self.simulation.execute(MoveCommand(entity_ids, point, queued=True))
                self.notice = "Movement waypoint queued." if result.accepted else result.reason
                return
            hits = sorted(
                (distance, entity.entity_id)
                for entity in self.simulation.entities.values()
                if (distance := _entity_hit_distance(entity, point))
                <= (
                    self.ENEMY_CLICK_RADIUS
                    if self.simulation.are_hostile(entity.owner_id, "player")
                    else self.ENTITY_CLICK_RADIUS
                )
            )
            enemy_id = (
                hits[0][1]
                if hits
                and self.simulation.are_hostile(
                    self.simulation.entities[hits[0][1]].owner_id, "player"
                )
                else None
            )
            click_tick = pygame.time.get_ticks()
            gathering = (
                last_move_click is not None
                and last_move_click[0] == entity_ids
                and 0 <= click_tick - last_move_click[2] <= self.DOUBLE_CLICK_MS
                and last_move_click[1].distance_to(point) * self.tile_size
                <= self.DOUBLE_CLICK_PIXELS
            )
            hit = self.simulation.entities[hits[0][1]] if hits else None
            repair_builders = tuple(
                key
                for key in entity_ids
                if self.simulation.entities[key].kind is EntityKind.BUILDER
            )
            command: AttackCommand | MoveCommand | RepairBuildingCommand
            if gathering:
                assert last_move_click is not None
                point = last_move_click[1]
                command = MoveCommand(entity_ids, point, gathering=True)
            elif enemy_id is not None:
                armed = tuple(
                    key
                    for key in entity_ids
                    if self.simulation.entities[key].kind.profile.attack_damage > 0
                )
                command = AttackCommand(armed or entity_ids, enemy_id)
            elif (
                repair_builders
                and hit is not None
                and hit.owner_id == "player"
                and hit.category is EntityCategory.BUILDING
                and hit.is_complete
                and hit.health < hit.kind.profile.max_health
            ):
                command = RepairBuildingCommand(repair_builders, hit.entity_id)
            else:
                command = MoveCommand(entity_ids, point)
            result = self.simulation.execute(command)
            if result.accepted and not gathering:
                self._last_move_click = (entity_ids, point, click_tick)
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
        if self.ui.font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if self.building_line_draft is not None:
            if buttons[2]:
                self.building_line_draft = None
                self.placement_kind = None
                self.notice = "Building line canceled."
            elif buttons[0] and self._in_canvas(position):
                preview = self._construction_preview_at(self._map_point(position))
                assert preview is not None
                self.building_line_draft.end = preview[0]
            return
        if self.automation_panel.scroll_drag_offset is not None and buttons[0]:
            automations.scroll_from_pointer(self, position[1])
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
        if self.ui.font is None and position[0] < self.canvas_rect.width:
            origin_x, origin_y = self.map_origin
            position = (position[0] + origin_x, position[1] + origin_y)
        if button == 1 and self.building_line_draft is not None:
            draft = self.building_line_draft
            self.building_line_draft = None
            if self._in_canvas(position):
                preview = self._construction_preview_at(self._map_point(position))
                assert preview is not None
                draft.end = preview[0]
            self._queue_building_line(draft)
            return
        if button == 2:
            self._camera_drag_position = None
            return
        if button == 1 and self.automation_panel.scroll_drag_offset is not None:
            self.automation_panel.scroll_drag_offset = None
            return
        if button != 1:
            return
        if not self._in_canvas(position) and not (
            self.mode is InputMode.SELECT and self.drag_start is not None
        ):
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

    def _queue_building_line(self, draft: BuildingLineDraft) -> None:
        accepted = 0
        rejected = 0
        for position, _ in building_line_previews(self.simulation, draft):
            result = self.simulation.execute(
                CreateConstructionCommand(
                    draft.builder_ids[0],
                    draft.kind,
                    position,
                    builder_ids=draft.builder_ids,
                    queued=True,
                )
            )
            if result.accepted:
                accepted += 1
                self.automation_panel.group = AutomationKind.CONSTRUCTION
                self.automation_panel.selected_id = result.automation_id
            else:
                rejected += 1
        self.notice = f"Queued {accepted} buildings." + (
            f" Skipped {rejected} invalid sites." if rejected else ""
        )

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
            if (
                clicked is not None
                and self.simulation.entities[clicked].owner_id != "player"
                and not self.simulation.are_hostile(
                    self.simulation.entities[clicked].owner_id, "player"
                )
            ):
                self.inspected_entity_id = clicked
                if not additive:
                    self.selected_entities.clear()
                self._commit_selection()
                self._last_entity_click = None
                self.notice = f"Inspecting ally {clicked}; commands disabled."
                return
            found = {clicked} if clicked is not None else set()
            if clicked is not None:
                click_tick = pygame.time.get_ticks()
                if (
                    self._last_entity_click is not None
                    and self._last_entity_click[0] == clicked
                    and click_tick - self._last_entity_click[1] <= self.DOUBLE_CLICK_MS
                ):
                    entity = self.simulation.entities[clicked]
                    self._select_all_visible_kind(entity.kind, owner_id=entity.owner_id)
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
                or self.simulation.are_hostile(entity.owner_id, "player")
                if entity.category is EntityCategory.UNIT
                if left <= entity.selection_position.x <= right
                and top <= entity.selection_position.y <= bottom
                if self.canvas_rect.collidepoint(self.screen_point(entity.selection_position))
            }
            player_units = {
                entity_id
                for entity_id in found
                if self.simulation.entities[entity_id].owner_id == "player"
            }
            if player_units:
                found = player_units
        if additive:
            self.selected_entities.symmetric_difference_update(found)
        else:
            self.selected_entities = found
        self.inspected_entity_id = next(iter(found)) if len(found) == 1 else None
        self._selection_changed()
        self.notice = (
            f"Selected {len(self.selected_entities)} enemy unit(s)."
            if self.selected_entities
            and all(
                self.simulation.entities[key].owner_id != "player" for key in self.selected_entities
            )
            else f"Selected {len(self.selected_entities)} unit(s)."
        )

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
                tuple(
                    sorted(
                        entity_id
                        for entity_id in self.selected_entities
                        if self.simulation.entities[entity_id].owner_id == "player"
                    )
                ),
                tuple(sorted(self.selected_points)),
                tuple(sorted(self.selected_routes)),
                tuple(sorted(self.selected_regions)),
            )
        )

    def _selection_changed(self) -> None:
        self._last_move_click = None
        self.production_detail_kind = None
        player_entities = {
            entity_id
            for entity_id in self.selected_entities
            if entity_id in self.simulation.entities
            and self.simulation.entities[entity_id].owner_id == "player"
        }
        if player_entities:
            self.selected_entities = player_entities
        self.inspected_entity_id = (
            next(iter(self.selected_entities)) if len(self.selected_entities) == 1 else None
        )
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

    def _select_all_visible_kind(self, kind: EntityKind, *, owner_id: str = "player") -> None:
        self.selected_entities = {
            entity_id
            for entity_id, entity in self.simulation.entities.items()
            if entity.owner_id == owner_id
            and entity.kind is kind
            and self.canvas_rect.collidepoint(self.screen_point(entity.selection_position))
        }
        self.inspected_entity_id = (
            next(iter(self.selected_entities)) if len(self.selected_entities) == 1 else None
        )
        self._selection_changed()
        self.notice = (
            f"Selected all visible {'enemy ' if owner_id != 'player' else ''}{kind.value} units."
        )

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
        if self.automation_panel.selected_id is None or self.active_target is None:
            self.notice = "Select an automation and spatial target first."
            return
        result = self.simulation.execute(
            ModifyAutomationCommand(self.automation_panel.selected_id, target=self.active_target)
        )
        self.notice = "Automation target updated." if result.accepted else result.reason

    def _change_automation_priority(self, delta: int) -> None:
        if self.automation_panel.selected_id is None:
            self.notice = "Select an automation first."
            return
        automation = self.simulation.automations[self.automation_panel.selected_id]
        result = self.simulation.execute(
            ModifyAutomationCommand(automation.automation_id, priority=automation.priority + delta)
        )
        self.notice = "Automation priority updated." if result.accepted else result.reason

    def _create_patrol(self) -> None:
        if self.active_target is None:
            self.notice = "Draw a point, line, rectangle, or freehand area first."
            return
        result = self.simulation.execute(
            CreatePatrolCommand(
                tuple(sorted(self.selected_entities)),
                self.active_target,
                auto_repair=self.automation_panel.new_area_auto_repair,
            )
        )
        if result.accepted:
            self.automation_panel.selected_id = result.automation_id
            self.automation_panel.group = None
            self.notice = f"Created {result.automation_id}."
        else:
            self.notice = result.reason

    def _create_defend(self) -> None:
        if self.active_target is None:
            self.notice = "Draw a spatial target before creating a defense automation."
            return
        result = self.simulation.execute(
            CreateDefendCommand(
                tuple(sorted(self.selected_entities)),
                self.active_target,
                auto_repair=self.automation_panel.new_area_auto_repair,
            )
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.automation_panel.selected_id = result.automation_id
            self.automation_panel.group = None

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
                ModifyAutomationCommand(
                    loop.automation_id,
                    target=self.active_target,
                    auto_repair=self.automation_panel.new_area_auto_repair,
                )
            )
            for loop in loops
            if loop is not None
        ]
        accepted = sum(result.accepted for result in results)
        if accepted == len(factories):
            self.notice = f"Production defense assigned to {accepted} factories."
            self.automation_panel.selected_id = next(
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

    def _selected_producer_ids(self) -> tuple[str, ...]:
        return tuple(
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].owner_id == "player"
            and self.simulation.entities[entity_id].kind.production_kinds
            and self.simulation.entities[entity_id].kind is self.inspected_kind
        )

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.notice = "Game paused." if self.paused else "Game resumed."

    def _create_repair(self) -> None:
        units = tuple(
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].is_movable
        )
        result = self.simulation.execute(
            CreateRepairAndReturnCommand(units, health_threshold=AUTO_REPAIR_HEALTH_THRESHOLD)
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.automation_panel.selected_id = result.automation_id
            self.automation_panel.group = None

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

    @property
    def save_busy(self) -> bool:
        return self._save_future is not None

    def _save_game(self, path: Path | None = None) -> None:
        self._poll_save()
        if self.save_busy:
            self.notice = "A save is already in progress."
            return
        destination = path if path is not None else self.quick_save_path
        try:
            if destination.parent == self.save_directory:
                self.save_directory.mkdir(parents=True, exist_ok=True)
            snapshot = capture_save(self.simulation)
        except OSError as error:
            self.notice = f"Save failed: {error}"
            return
        if self._save_executor is None:
            self._save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="airts-save")
        self._saving_path = destination
        self._save_future = self._save_executor.submit(write_save, snapshot, destination)
        self.notice = f"Saving {destination.name}..."

    def _poll_save(self) -> None:
        future = self._save_future
        if future is None or not future.done():
            return
        try:
            future.result()
        except (OSError, ValueError, TypeError) as error:
            self.notice = f"Save failed: {error}"
            getLogger(__name__).error(self.notice)
        else:
            self.notice = f"Saved {self._saving_path}."
            self.save_panel.selected = self._saving_path
        finally:
            self._save_future = None
            self._saving_path = None
        if self.save_panel.open:
            self.save_panel.refresh(self.save_directory, self.quick_save_path)

    def _finish_save(self) -> None:
        """Normal window exit waits for the pending atomic write, including its errors."""
        if self._save_executor is not None:
            self._save_executor.shutdown(wait=True)
            self._save_executor = None
        self._poll_save()

    def _open_save_manager(self) -> None:
        self.settings_open = False
        self.help_open = False
        self._camera_keys.clear()
        self._camera_drag_position = None
        self._clear_draft()
        self.save_panel.open = True
        self.save_panel.confirmation = None
        self.save_panel.refresh(self.save_directory, self.quick_save_path)
        if not self.save_busy:
            self.notice = "Choose New save, or select an existing save."

    def _handle_save_click(self, position: tuple[int, int]) -> None:
        for rectangle, action in self.save_panel.buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "close":
                self.save_panel.open = False
                self.save_panel.confirmation = None
            elif action == "cancel":
                self.save_panel.confirmation = None
            elif action.startswith("select:"):
                self.save_panel.selected = self.save_panel.entries[int(action.split(":")[1])].path
                self.save_panel.confirmation = None
            elif action in {"previous", "next"}:
                self.save_panel.scroll_by(-1 if action == "previous" else 1)
            elif self.save_busy:
                self.notice = "Wait for the current save to finish."
            elif action == "refresh":
                self.save_panel.confirmation = None
                self.save_panel.refresh(self.save_directory, self.quick_save_path)
            elif action == "new_save":
                self.save_panel.confirmation = None
                name = datetime.now().strftime("Save %Y-%m-%d %H.%M.%S.%f.json")
                self._save_game(self.save_directory / name)
            elif action in {"overwrite", "delete", "load"}:
                self.save_panel.confirmation = action
            elif action == "confirm":
                operation = self.save_panel.confirmation
                selected = self.save_panel.selected
                self.save_panel.confirmation = None
                if selected is None:
                    return
                if operation == "overwrite":
                    self._save_game(selected)
                elif operation == "load":
                    self._load_game(selected)
                elif operation == "delete":
                    try:
                        selected.unlink()
                    except OSError as error:
                        self.notice = f"Delete failed: {error}"
                    else:
                        self.notice = f"Deleted {selected.name}."
                    self.save_panel.refresh(self.save_directory, self.quick_save_path)
            return

    def _load_game(self, path: Path | None = None) -> None:
        self._poll_save()
        if self.save_busy:
            self.notice = "Wait for the current save to finish before loading."
            return
        source = path if path is not None else self.quick_save_path
        try:
            simulation = load_simulation(source)
        except (OSError, PersistenceError) as error:
            self.notice = f"Load failed: {error}"
            return
        self.simulation = simulation
        self.lobby = None
        self.save_panel.open = False
        self._software_renderer.reset_map()
        self.camera.zoom = 1.0
        self.camera.offset = Point(0, 0)
        self.paused = False
        self._reset_presentation_history()
        self._clear_selection_state()
        self.notice = f"Loaded {source}."

    def _new_game(self) -> None:
        if self.simulation.game_map.match_slots:
            self.lobby = Lobby()
            self.notice = "Configure players and teams before starting."
            self._clear_selection_state()
            return
        self.simulation = Simulation(
            self._initial_map,
            self._initial_seed,
            ambient_enemy_spawns=self._initial_ambient_enemy_spawns,
            enemy_spawn_interval_ticks=self._initial_enemy_spawn_interval_ticks,
            enemy_spawn_cap=self._initial_enemy_spawn_cap,
        )
        self._software_renderer.reset_map()
        self._reset_presentation_history()
        self._clear_selection_state()
        self.notice = "New game started."

    def _clear_selection_state(self) -> None:
        self._camera_keys.clear()
        self._last_move_click = None
        self.selected_entities.clear()
        self.selected_points.clear()
        self.selected_routes.clear()
        self.selected_regions.clear()
        self.inspected_entity_id = None
        self.active_reference_id = None
        self.active_target = None
        self.automation_panel.selected_id = None
        self.inspected_kind = None
        self.placement_kind = None
        self.production_detail_kind = None
        self.automation_panel.details_open = False

    def _handle_command_click(self, position: tuple[int, int]) -> None:
        for rectangle, action in self._command_buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "settings":
                self.settings_open = not self.settings_open
                return
            if action == "saves":
                self._open_save_manager()
                return
            if action == "help":
                self.help_open = not self.help_open
                return
            if action == "pause_game":
                self._toggle_pause()
                return
            if action == "close_automation_details":
                self.automation_panel.details_open = False
                return
            if action == "toggle_new_area_auto_repair":
                self.automation_panel.new_area_auto_repair = (
                    not self.automation_panel.new_area_auto_repair
                )
                enabled = self.automation_panel.new_area_auto_repair
                self.notice = f"New areas: auto repair {'ON' if enabled else 'OFF'} below 50% HP."
                return
            if action.startswith("toggle_auto_repair:"):
                automation_id = action.partition(":")[2]
                automation = self.simulation.automations.get(automation_id)
                if automation is None or not isinstance(
                    automation.parameters,
                    PatrolParameters | DefendParameters | ProductionParameters,
                ):
                    return
                result = self.simulation.execute(
                    ModifyAutomationCommand(
                        automation_id, auto_repair=not automation.parameters.auto_repair
                    )
                )
                self.notice = "Auto repair updated." if result.accepted else result.reason
                return
            if action.startswith("automation_group:"):
                group = action.partition(":")[2]
                self.automation_panel.group = None if group == "units" else AutomationKind(group)
                self.automation_panel.scroll = 0
                return
            if action.startswith("unit_details:"):
                self.production_detail_kind = EntityKind(action.partition(":")[2])
                self.automation_panel.details_open = False
                return
            if action == "reclaim":
                self._clear_draft()
                self.placement_kind = None
                self.reclaim_mode = True
                self.notice = "Click a friendly building to reclaim (70%); right-click cancels."
                return
            if action.startswith("build:"):
                self._clear_draft()
                self.placement_kind = EntityKind(action.partition(":")[2])
                self.notice = "Click a clear grid location to place the building."
                return
            if action.startswith(("queue:", "reduce:", "loop:")):
                factories = self._selected_producer_ids()
                if not factories:
                    self.notice = "Select a factory or command center."
                    return
                operation, _, unit_name = action.partition(":")
                kind = EntityKind(unit_name)
                results = [
                    self.simulation.execute(
                        ReduceProductionCommand(factory_id, kind)
                        if operation == "reduce"
                        else CreateProductionCommand(
                            factory_id,
                            kind,
                            1,
                            title=f"{'Loop' if operation == 'loop' else 'Produce'} {kind.value.replace('_', ' ').title()}",
                            continuous=operation == "loop",
                        )
                    )
                    for factory_id in factories
                ]
                accepted = sum(result.accepted for result in results)
                source_label = (
                    "factories" if self.inspected_kind is EntityKind.FACTORY else "command centers"
                )
                if accepted == len(factories):
                    self.notice = (
                        f"{'Continuous production started' if operation == 'loop' else 'Production reduced' if operation == 'reduce' else 'Production added'} "
                        f"for {accepted} {source_label}."
                    )
                else:
                    failure = next(result.reason for result in results if not result.accepted)
                    self.notice = f"Updated {accepted}/{len(factories)} {source_label}: {failure}"
                if accepted:
                    self.automation_panel.selected_id = next(
                        result.automation_id for result in reversed(results) if result.accepted
                    )
                    self.automation_panel.group = AutomationKind.PRODUCTION
                return
            {
                "stop": self._stop_selected,
                "hold": self._hold_selected,
                "patrol": self._create_patrol,
                "defend": self._create_defend,
                "produce": self._create_production,
                "delete": self._delete_selected_reference,
                "save": self._save_game,
                "load": self._load_game,
                "new": self._new_game,
            }[action]()
            return

    @property
    def developer_mode(self) -> bool:
        return self.simulation.developer_mode

    def _handle_settings_click(self, position: tuple[int, int]) -> bool:
        for rectangle, action in self._settings_buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "developer_mode":
                result = self.simulation.execute(SetDeveloperModeCommand(not self.developer_mode))
                if result.accepted:
                    self.notice = (
                        "Developer mode ON: +2000 resources per game second."
                        if self.developer_mode
                        else "Developer mode OFF."
                    )
                else:
                    self.notice = f"Developer mode change failed: {result.reason}"
                return True
            if action == "resolution_lower":
                self._request_resolution_step(-1)
                return True
            if action == "resolution_higher":
                self._request_resolution_step(1)
                return True
            {"saves": self._open_save_manager, "new": self._new_game}[action]()
            self.settings_open = False
            return True
        return False

    def _handle_panel_click(self, position: tuple[int, int], *, button: int = 1) -> None:
        for rectangle, kind in self._type_buttons:
            if rectangle.collidepoint(position):
                if button == 1:
                    self._filter_selection_to_kind(kind)
                elif (
                    button == 3
                    and self.selected_entities
                    and all(
                        self.simulation.entities[key].owner_id == "player"
                        for key in self.selected_entities
                    )
                ):
                    self.selected_entities = {
                        key
                        for key in self.selected_entities
                        if self.simulation.entities[key].kind is not kind
                    }
                    self._selection_changed()
                    self.notice = f"Selected {len(self.selected_entities)} unit(s)."
                return
        if button != 1:
            return
        self._handle_command_click(position)
        for rectangle, action, automation_id in self.automation_panel.buttons:
            if not rectangle.collidepoint(position):
                continue
            if action == "inspect":
                was_open = (
                    self.automation_panel.details_open
                    and self.automation_panel.selected_id == automation_id
                )
                self.automation_panel.selected_id = automation_id
                self.automation_panel.details_open = not was_open
                self.production_detail_kind = None
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
                automations.clamp_scroll(
                    self,
                    visible_rows=self.automation_panel.visible_rows,
                    total_rows=len(automations.panel_rows(self)),
                )
            return

    def _draw(self, screen: pygame.Surface) -> None:
        draw_key: tuple[object, ...] = (
            id(screen),
            screen.get_size(),
            self.simulation.tick,
            self._opengl_overlay_key(),
            None
            if self.building_line_draft is None
            else (
                self.building_line_draft.kind,
                self.building_line_draft.start,
                self.building_line_draft.end,
            ),
            self.drag_start,
            pygame.mouse.get_pos() if self.drag_start is not None else None,
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
        tile_size = self.camera.tile_size(
            self.canvas_rect, (self.simulation.game_map.width, self.simulation.game_map.height)
        )
        map_pixel_size = (
            round(self.simulation.game_map.width * tile_size),
            round(self.simulation.game_map.height * tile_size),
        )
        self._frame_tile_size = tile_size
        self._frame_map_pixel_size = map_pixel_size
        self._frame_map_origin = self.camera.origin(self.canvas_rect, map_pixel_size)
        try:
            self._prune_removed_entities()
            self._frame_selected_entities = tuple(
                self.simulation.entities[entity_id] for entity_id in self.selected_entities
            )
            pygame.draw.rect(screen, self.BACKGROUND, self.canvas_rect)
            previous_clip = screen.get_clip()
            screen.set_clip(self.canvas_rect)
            self._software_renderer.draw_map(self, screen)
            self._draw_resource_sites(screen)
            self._draw_spatial_input(screen)
            self._draw_assembly_glows(screen)
            self._software_renderer.draw_entities(self, screen)
            self._software_renderer.draw_projectiles(self, screen)
            self._draw_construction(screen)
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
        automations.draw_panel(self, screen)
        selection.draw_selection(self, screen)
        if self.simulation.game_map.match_slots:
            self._draw_match_status(screen)
        automations.draw_details(self, screen)
        if self.settings_open:
            menus.draw_settings(self, screen)
        if self.help_open:
            menus.draw_help(self, screen)
        if self.lobby is not None:
            self.lobby.draw(self, screen)
        if self.save_panel.open:
            saves.draw(self, screen)

    def _draw_opengl_overlay(self, screen: pygame.Surface) -> None:
        """Draw non-base scene feedback and the UI into a GPU-composited texture."""

        self._prune_removed_entities()
        self._frame_selected_entities = tuple(
            self.simulation.entities[entity_id] for entity_id in self.selected_entities
        )
        try:
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
                for region in regions:
                    canvas_region = region.clip(self.canvas_rect)
                    if not canvas_region.width or not canvas_region.height:
                        continue
                    screen.set_clip(canvas_region)
                    if self.simulation.game_map.match_slots and not (
                        refresh_right or refresh_commands
                    ):
                        self._draw_match_status(screen)
                screen.set_clip(previous_clip)
            if refresh_right or refresh_commands:
                self._draw_interface(screen)
            elif refresh_left:
                self._command_buttons[:] = [
                    button
                    for button in self._command_buttons
                    if not self.left_panel_rect.collidepoint(button[0].center)
                ]
                automations.draw_panel(
                    self,
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
            self.automation_panel.selected_id,
            self.inspected_entity_id,
            self.inspected_kind,
            self.naming_reference_id,
            self.naming_buffer,
            (),
            (),
            None,
            None,
            self.automation_panel.scroll,
            (self.settings_open, self.developer_mode),
            self.help_open,
            (self.placement_kind, self.reclaim_mode),
            (
                self.automation_panel.group,
                self.automation_panel.details_open,
                self.production_detail_kind,
                self.automation_panel.new_area_auto_repair,
            ),
            tuple(self.left_panel_rect),
            tuple(self.right_panel_rect),
            tuple(self.canvas_rect),
            tuple(self.command_bar_rect),
            None,
            (),
            None,
            None if self.lobby is None else (self.lobby.page, tuple(self.lobby.setup)),
            self.simulation.match_result,
            self.save_panel.cache_key(),
            self.save_busy,
        )

    def _opengl_partial_overlay_regions(
        self,
        previous: tuple[object, ...],
        current: tuple[object, ...],
    ) -> tuple[pygame.Rect, ...] | None:
        """Return the exact UI regions affected by inexpensive state-only changes."""

        if len(previous) != len(current):
            return None
        if self.lobby is not None or self.save_panel.open or previous[-2] != current[-2]:
            return None
        # A flyout can move with its list row or disappear after completion.
        # Recompose it together with the underlying canvas to erase old pixels.
        if self.automation_panel.details_open or previous[26] != current[26]:
            return None
        changed = {
            index
            for index, (old_value, new_value) in enumerate(zip(previous, current, strict=True))
            if old_value != new_value
        }
        left_panel_changes = {1, 2, 4, 5, 6, 13, 22, 26}
        right_panel_changes = {1, 2, 6, 13, 14, 15, 26}
        if not changed or not changed <= (left_panel_changes | right_panel_changes):
            return None
        regions: list[pygame.Rect] = []
        if changed & left_panel_changes:
            if changed & {4, 5, 22, 26}:
                regions.append(self.left_panel_rect.copy())
            else:
                regions.extend(self._opengl_dynamic_overlay_regions())
        if changed & (right_panel_changes | {6}):
            panel = self.right_panel_rect
            context_height = (
                panel.height
                if 26 in changed
                else min(panel.height, max(420, round(420 * self.ui_scale)))
            )
            regions.append(pygame.Rect(panel.x, panel.y, panel.width, context_height))
        if 6 in changed:
            bar = self.command_bar_rect
            regions.append(pygame.Rect(bar.x, bar.y, bar.width, min(78, bar.height)))
        if 1 in changed and self.simulation.game_map.match_slots:
            regions.append(
                pygame.Rect(self.canvas_rect.x, self.canvas_rect.y, self.canvas_rect.width, 96)
            )
        return tuple(regions)

    def _opengl_dynamic_overlay_regions(self) -> tuple[pygame.Rect, ...]:
        """Refresh populated status and rows, including the previous rows to erase."""

        panel = self.left_panel_rect
        count = len(automations.panel_rows(self))
        row_height = round(self.AUTOMATION_ROW_HEIGHT * self.ui_scale)
        bottom = max(
            self.automation_panel.content_bottom,
            self.automation_panel.list_top
            + min(count, self.automation_panel.visible_rows) * row_height
            + 8,
        )
        if (
            self.automation_panel.scrollbar_track is not None
            or count > self.automation_panel.visible_rows
        ):
            bottom = panel.bottom
        return (pygame.Rect(panel.x, 38, panel.width, max(1, bottom - 38)),)

    def _prune_removed_entities(self) -> None:
        existing = self.simulation.entities.keys()
        self.selected_entities.intersection_update(existing)
        if self.inspected_entity_id not in self.simulation.entities:
            self.inspected_entity_id = None
        automation = self.simulation.automations.get(self.automation_panel.selected_id or "")
        if automation is not None and automation.owner_id != "player":
            self.automation_panel.selected_id = None

    def _draw_construction(self, screen: pygame.Surface) -> None:
        kind: EntityKind | None
        construction_automations = tuple(
            automation
            for automation in self.simulation.live_automations
            if automation.kind is AutomationKind.CONSTRUCTION and automation.owner_id == "player"
        )
        sites = tuple(
            entity for entity in self.simulation.entities.values() if not entity.is_complete
        )
        if not construction_automations and not sites and self.placement_kind is None:
            return
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        drafts: list[tuple[EntityKind, Point, float, bool]] = []
        for automation in construction_automations:
            parameters = automation.parameters
            assert isinstance(parameters, ConstructionParameters)
            if parameters.site_entity_id is None:
                drafts.append((parameters.building_kind, parameters.position, 0.0, True))
        for entity in sites:
            assert entity.construction_progress is not None
            drafts.append((entity.kind, entity.position, entity.construction_progress, False))
        for kind, position, progress, blueprint in drafts:
            width, height = kind.profile.footprint
            origin = self.screen_point(position)
            rectangle = pygame.Rect(
                origin,
                (round(width * self.tile_size), round(height * self.tile_size)),
            )
            if blueprint:
                pygame.draw.rect(overlay, (94, 176, 220, 80), rectangle)
                pygame.draw.rect(overlay, (164, 222, 250, 230), rectangle, 2)
                continue
            pygame.draw.rect(overlay, (240, 186, 92, 245), rectangle, 2)
            pygame.draw.line(
                overlay, (240, 186, 92, 180), rectangle.topleft, rectangle.bottomright, 2
            )
            pygame.draw.line(
                overlay, (240, 186, 92, 180), rectangle.topright, rectangle.bottomleft, 2
            )
            bar = pygame.Rect(rectangle.x, rectangle.bottom + 3, rectangle.width, 5)
            pygame.draw.rect(overlay, (30, 38, 46, 230), bar)
            pygame.draw.rect(
                overlay,
                (102, 222, 156, 245),
                pygame.Rect(bar.x, bar.y, round(bar.width * progress), bar.height),
            )
            if self.ui.small_font is not None:
                label = self.ui.small_font.render(
                    f"{round(progress * 100)}%", True, (245, 250, 252)
                )
                overlay.blit(label, (rectangle.x, rectangle.y - label.get_height()))

        mouse = pygame.mouse.get_pos() if pygame.display.get_init() else (-1, -1)
        pointer = self._map_point(mouse) if self.canvas_rect.collidepoint(mouse) else None
        previews = self._construction_previews(pointer)
        preview_kind = (
            self.building_line_draft.kind
            if self.building_line_draft is not None
            else self.placement_kind
        )
        if preview_kind is not None:
            width, height = preview_kind.profile.footprint
            for position, valid in previews:
                rectangle = pygame.Rect(
                    self.screen_point(position),
                    (round(width * self.tile_size), round(height * self.tile_size)),
                )
                color = (95, 224, 145) if valid else (235, 88, 88)
                pygame.draw.rect(overlay, (*color, 72), rectangle)
                pygame.draw.rect(overlay, (*color, 245), rectangle, 3)
        screen.blit(overlay, (0, 0))

    def _construction_previews(self, pointer: Point | None) -> tuple[tuple[Point, bool], ...]:
        if self.building_line_draft is not None:
            return building_line_previews(self.simulation, self.building_line_draft)
        preview = self._construction_preview_at(pointer) if pointer is not None else None
        return (preview,) if preview is not None else ()

    def _construction_preview_at(self, point: Point) -> tuple[Point, bool] | None:
        if self.placement_kind is None:
            return None
        position = Point(float(floor(point.x)), float(floor(point.y)))
        sites = self.simulation.game_map.resource_sites
        if self.placement_kind is EntityKind.RESOURCE_GENERATOR and sites:
            nearest = min(sites, key=lambda site: point.distance_to(Point(site.x + 1, site.y + 1)))
            if point.distance_to(Point(nearest.x + 1, nearest.y + 1)) <= 3:
                position = nearest
        valid = (
            construction.validate_building_placement(
                self.simulation, self.placement_kind, position, owner_id="player"
            )
            is None
        )
        return position, valid

    def _draw_resource_sites(self, screen: pygame.Surface) -> None:
        for site in self.simulation.game_map.resource_sites or ():
            center = self.screen_point(Point(site.x + 1, site.y + 1))
            pygame.draw.circle(screen, (244, 63, 66), center, max(3, round(self.tile_size)), 2)

    def _draw_match_status(self, screen: pygame.Surface) -> None:
        x, y = self.canvas_rect.x + 12, 12
        for slot in self.simulation.game_map.match_slots:
            text = f"{slot.owner_id} | Team {slot.team} | {owner_status(self.simulation, slot.owner_id)}"
            self.ui.small_text(screen, text, (x, y), (245, 220, 150))
            y += 20
        result = self.simulation.match_result
        if result is not None:
            label = "Draw" if result == 0 else f"Team {result} wins!"
            rectangle = pygame.Rect(
                self.canvas_rect.centerx - 130, self.canvas_rect.centery - 40, 260, 80
            )
            pygame.draw.rect(screen, (25, 34, 44), rectangle)
            self.ui.text(screen, label, (rectangle.x + 20, rectangle.y + 16), (130, 245, 170))
            self.ui.small_text(
                screen,
                "F2: return to opening setup",
                (rectangle.x + 20, rectangle.y + 48),
                (225, 232, 238),
            )

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
            center = self.screen_point(target_center(parameters.target))
            radius = max(8, round((parameters.assembly_radius + 0.8) * self.tile_size))
            pygame.draw.circle(glow, (92, 177, 255, 20), center, radius)
            pygame.draw.circle(glow, (135, 205, 255, 72), center, radius, 2)
            pygame.draw.circle(glow, (190, 230, 255, 35), center, max(1, radius - 5), 2)
            drawn = True
        if drawn:
            screen.blit(glow, (0, 0))

    def representative_path_entity_ids(self) -> tuple[str, ...]:
        selected = sorted(
            entity_id
            for entity_id in self.selected_entities
            if entity_id in self.simulation.entities
            and (
                self.simulation.entities[entity_id].path
                or self.simulation.entities[entity_id].move_queue
                or self.simulation.entities[entity_id].pursue_target
            )
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
            and (
                self.simulation.entities[inspected].path
                or self.simulation.entities[inspected].move_queue
                or self.simulation.entities[inspected].pursue_target
            )
            and inspected not in representatives
        ):
            if len(representatives) == self.MAX_SELECTED_PATHS:
                representatives[-1] = inspected
            else:
                representatives.append(inspected)
        return tuple(representatives)

    @staticmethod
    def simplified_path(entity: Entity) -> tuple[Point, ...]:
        points = (entity.position, *entity.path, *entity.move_queue)
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
            pixels = [self.screen_point(point) for point in draft_points]
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
            if self.mode is InputMode.SELECT or self.canvas_rect.collidepoint(mouse):
                start = self.screen_point(self.drag_start)
                rectangle = pygame.Rect(start, (mouse[0] - start[0], mouse[1] - start[1]))
                rectangle.normalize()
                pygame.draw.rect(screen, (245, 245, 245), rectangle, 1)
        if self.ui.small_font is not None:
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
                label = self.ui.small_font.render(reference.name, True, (240, 244, 250))
                label.set_alpha(220)
                label_rect = label.get_rect(center=self.screen_point(center))
                screen.blit(label, label_rect)

    def _draw_target(
        self,
        screen: pygame.Surface,
        target: SpatialTarget,
        color: tuple[int, int, int],
        selected: bool,
    ) -> None:
        width = 4 if selected else 2
        if isinstance(target, PointTarget):
            center = self.screen_point(target.point)
            pygame.draw.circle(screen, color, center, round(target.radius * self.tile_size), width)
            pygame.draw.circle(screen, color, center, 4)
        elif isinstance(target, PolylineTarget):
            pygame.draw.lines(
                screen, color, False, [self.screen_point(p) for p in target.points], width
            )
        elif isinstance(target, PolygonRegion):
            surface = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
            points = [self.screen_point(point) for point in target.points]
            pygame.draw.polygon(surface, (*color, 55), points)
            pygame.draw.polygon(surface, (*color, 230), points, width)
            screen.blit(surface, (0, 0))

    def _selected_entities_for_draw(self) -> tuple[Entity, ...]:
        if self._frame_selected_entities is not None:
            return self._frame_selected_entities
        return tuple(self.simulation.entities[entity_id] for entity_id in self.selected_entities)

    def _draw_command_bar(self, screen: pygame.Surface) -> None:
        if self.ui.small_font is None:
            return
        bar = self.command_bar_rect
        pygame.draw.rect(screen, (24, 29, 36), bar)
        self.ui.small_text(
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
            self.ui.button(screen, rectangle, label)
            self._command_buttons.append((rectangle, action))
            x += width + 9

    def _map_point(self, position: tuple[int, int]) -> Point:
        origin_x, origin_y = self.map_origin
        if self.ui.font is None and position[0] < self.canvas_rect.left:
            origin_x -= self.canvas_rect.left
        return Point(
            (position[0] - origin_x) / self.tile_size,
            (position[1] - origin_y) / self.tile_size,
        )

    def screen_point(self, point: Point) -> tuple[int, int]:
        origin_x, origin_y = self.map_origin
        return (
            origin_x + round(point.x * self.tile_size),
            origin_y + round(point.y * self.tile_size),
        )

    def _clear_draft(self) -> None:
        self.line_points.clear()
        self.freehand_points.clear()
        self.drag_start = None
        self.building_line_draft = None
        self.reclaim_mode = False


def _entity_hit_distance(entity: Entity, point: Point) -> float:
    if entity.category is not EntityCategory.BUILDING:
        return entity.selection_position.distance_to(point)
    width, height = entity.kind.profile.footprint
    nearest_x = min(max(point.x, entity.position.x), entity.position.x + width)
    nearest_y = min(max(point.y, entity.position.y), entity.position.y + height)
    return point.distance_to(Point(nearest_x, nearest_y))


def _distance_to_polyline(point: Point, vertices: tuple[Point, ...]) -> float:
    return min(
        distance_to_segment(point, start, end)
        for start, end in zip(vertices, vertices[1:], strict=False)
    )
