"""Pygame traditional RTS interaction and inspection interface for Phase 4."""

from __future__ import annotations

from enum import StrEnum
from math import hypot
from pathlib import Path

import pygame

from airts.automations import (
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
from airts.entities import Entity
from airts.geometry import (
    Point,
    PointTarget,
    PolygonRegion,
    PolylineTarget,
    SpatialTarget,
    rectangle_region,
    simplify_freehand,
)
from airts.map_model import EntityCategory, EntityKind, Terrain
from airts.persistence import PersistenceError, load_simulation, save_simulation
from airts.simulation import Simulation
from airts.spatial import SpatialKind


class InputMode(StrEnum):
    SELECT = "select"
    LINE = "line"
    RECTANGLE = "rectangle"
    FREEHAND = "freehand"


class AirtsApp:
    MAP_PIXELS = 768
    LEFT_PANEL_WIDTH = 280
    RIGHT_PANEL_WIDTH = 380
    PANEL_WIDTH = RIGHT_PANEL_WIDTH
    COMMAND_BAR_HEIGHT = 104
    WINDOW_SIZE = (
        LEFT_PANEL_WIDTH + MAP_PIXELS + RIGHT_PANEL_WIDTH,
        MAP_PIXELS + COMMAND_BAR_HEIGHT,
    )
    BACKGROUND = (18, 22, 28)
    PANEL_BACKGROUND = (27, 32, 40)
    ENTITY_CLICK_RADIUS = 1.5
    ENEMY_CLICK_RADIUS = 2.5

    def __init__(self, simulation: Simulation) -> None:
        self.simulation = simulation
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
        self.settings_open = False
        self.help_open = False
        self.placement_kind: EntityKind | None = None
        self.production_sequence: list[tuple[EntityKind, int]] = []
        self.paused = False
        self.fps = 0.0
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
        self.resize_layout(self.WINDOW_SIZE)

    @property
    def tile_size(self) -> float:
        return min(
            self.canvas_rect.width / self.simulation.game_map.width,
            self.canvas_rect.height / self.simulation.game_map.height,
        )

    @property
    def map_pixel_size(self) -> tuple[int, int]:
        return (
            round(self.simulation.game_map.width * self.tile_size),
            round(self.simulation.game_map.height * self.tile_size),
        )

    @property
    def map_origin(self) -> tuple[int, int]:
        width, height = self.map_pixel_size
        return (
            self.canvas_rect.centerx - width // 2 + round(self.camera_offset.x),
            self.canvas_rect.centery - height // 2 + round(self.camera_offset.y),
        )

    def resize_layout(self, size: tuple[int, int]) -> None:
        width = max(760, size[0])
        height = max(520, size[1])
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

    def pan_camera(self, dx: float, dy: float) -> None:
        limit = min(self.map_pixel_size) * 0.45
        self.camera_offset = Point(
            max(-limit, min(limit, self.camera_offset.x + dx)),
            max(-limit, min(limit, self.camera_offset.y + dy)),
        )

    def scroll_automations(self, delta: int, *, visible_rows: int, total_rows: int) -> None:
        maximum = max(0, total_rows - visible_rows)
        self.automation_scroll = max(0, min(maximum, self.automation_scroll + delta))

    def _in_canvas(self, position: tuple[int, int]) -> bool:
        return self.canvas_rect.collidepoint(position)

    def run(self, max_frames: int | None = None) -> None:
        display_initialized = False
        font_initialized = False
        try:
            pygame.display.init()
            display_initialized = True
            pygame.font.init()
            font_initialized = True
            screen = pygame.display.set_mode(self.WINDOW_SIZE, pygame.RESIZABLE)
            self.resize_layout(self.WINDOW_SIZE)
            pygame.display.set_caption("AIRTS — Phase 5")
            self._font = pygame.font.Font(None, 24)
            self._small_font = pygame.font.Font(None, 19)
            clock = pygame.time.Clock()
            accumulator = 0.0
            running = True
            frames = 0
            while running and (max_frames is None or frames < max_frames):
                elapsed = min(clock.tick(60) / 1000.0, 0.25)
                self.fps = clock.get_fps()
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                        break
                    else:
                        self._handle_event(event)
                if not running:
                    break
                if not self.paused:
                    accumulator += elapsed
                    while accumulator >= Simulation.TICK_SECONDS:
                        self.simulation.advance()
                        accumulator -= Simulation.TICK_SECONDS
                self._draw(screen)
                pygame.display.flip()
                frames += 1
        finally:
            self._shutdown_pygame(display_initialized, font_initialized)

    def _shutdown_pygame(self, display_initialized: bool, font_initialized: bool) -> None:
        """Release UI resources in dependency order, including exceptional exits."""

        self._font = None
        self._small_font = None
        self._map_surface = None
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
                self.scroll_automations(
                    -event.y, visible_rows=7, total_rows=len(self.simulation.live_automations)
                )
        elif event.type in {pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED}:
            size = event.size if hasattr(event, "size") else (event.x, event.y)
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
            return

    def _draw(self, screen: pygame.Surface) -> None:
        self._prune_removed_entities()
        screen.fill(self.BACKGROUND)
        previous_clip = screen.get_clip()
        screen.set_clip(self.canvas_rect)
        self._draw_map(screen)
        self._draw_spatial_input(screen)
        self._draw_construction(screen)
        self._draw_assembly_glows(screen)
        self._draw_entities(screen)
        self._draw_projectiles(screen)
        screen.set_clip(previous_clip)
        self._draw_command_bar(screen)
        self._draw_panel(screen)
        self._draw_context_panel(screen)
        if self.settings_open:
            self._draw_settings_menu(screen)
        if self.help_open:
            self._draw_help(screen)

    def _prune_removed_entities(self) -> None:
        existing = self.simulation.entities.keys()
        self.selected_entities.intersection_update(existing)
        if self.inspected_entity_id not in self.simulation.entities:
            self.inspected_entity_id = None

    def _draw_map(self, screen: pygame.Surface) -> None:
        if self._map_surface is not None:
            scaled = pygame.transform.scale(self._map_surface, self.map_pixel_size)
            screen.blit(scaled, self.map_origin)
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
        scaled = pygame.transform.scale(surface, self.map_pixel_size)
        screen.blit(scaled, self.map_origin)

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
        path_entity_ids = set(self.selected_entities)
        if self.inspected_entity_id is not None:
            path_entity_ids.add(self.inspected_entity_id)
        for entity_id in sorted(path_entity_ids):
            entity = self.simulation.entities.get(entity_id)
            if entity is None:
                continue
            if entity.path:
                points = [self._screen_point(entity.position)] + [
                    self._screen_point(point) for point in entity.path
                ]
                pygame.draw.lines(screen, (225, 225, 225), False, points, 1)
        for entity_id, entity in self.simulation.entities.items():
            center = self._screen_point(entity.selection_position)
            if entity.category is EntityCategory.BUILDING:
                width, height = entity.kind.profile.footprint
                rectangle = pygame.Rect(
                    self._screen_point(entity.position)[0],
                    self._screen_point(entity.position)[1],
                    round(width * self.tile_size),
                    round(height * self.tile_size),
                )
                pygame.draw.rect(screen, colors[entity.kind], rectangle, border_radius=3)
                pygame.draw.rect(screen, (35, 42, 49), rectangle, 2, border_radius=3)
                if entity_id in self.selected_entities:
                    pygame.draw.rect(screen, (255, 255, 255), rectangle.inflate(6, 6), 2)
            else:
                radius = max(5, round(self.tile_size * 0.42))
                color = colors[entity.kind] if entity.owner_id == "player" else (218, 78, 78)
                pygame.draw.circle(screen, color, center, radius)
                if entity_id in self.selected_entities:
                    pygame.draw.circle(screen, (255, 255, 255), center, radius + 3, 2)
                if (
                    entity_id == self.inspected_entity_id
                    and entity_id not in self.selected_entities
                ):
                    pygame.draw.circle(screen, (255, 210, 90), center, radius + 3, 2)
            bar_width = max(12, round(self.tile_size * 1.4))
            bar = pygame.Rect(center[0] - bar_width // 2, center[1] - 12, bar_width, 3)
            pygame.draw.rect(screen, (70, 35, 35), bar)
            health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
            pygame.draw.rect(screen, (74, 218, 111), pygame.Rect(bar.x, bar.y, health_width, 3))
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

    def _draw_construction(self, screen: pygame.Surface) -> None:
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        for automation in self.simulation.live_automations:
            if automation.kind is not AutomationKind.CONSTRUCTION:
                continue
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
        glow = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        drawn = False
        for automation in self.simulation.live_automations:
            if automation.kind is not AutomationKind.DEFEND:
                continue
            parameters = automation.parameters
            assert isinstance(parameters, DefendParameters)
            if not parameters.gathering_point or not automation.entity_ids:
                continue
            center = self._screen_point(target_center(parameters.target))
            radius = max(8, round((parameters.assembly_radius + 0.8) * self.tile_size))
            pygame.draw.circle(glow, (92, 177, 255, 20), center, radius)
            pygame.draw.circle(glow, (135, 205, 255, 72), center, radius, 2)
            pygame.draw.circle(glow, (190, 230, 255, 35), center, max(1, radius - 5), 2)
            drawn = True
        if drawn:
            screen.blit(glow, (0, 0))

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
        selected = [self.simulation.entities[item] for item in sorted(self.selected_entities)]
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

    def _draw_panel(self, screen: pygame.Surface) -> None:
        if self._font is None or self._small_font is None:
            raise RuntimeError("fonts not initialized")
        panel = self.left_panel_rect
        pygame.draw.rect(screen, self.PANEL_BACKGROUND, panel)
        x = 16
        y = 14
        self._text(screen, "AIRTS — Phase 5", (x, y), (245, 245, 245))
        y += 28
        self._small_text(
            screen,
            f"Tick {self.simulation.tick} | FPS {self.fps:4.0f} | "
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
        elif self.selected_entities:
            kinds: dict[str, int] = {}
            health = 0
            maximum = 0
            for entity_id in self.selected_entities:
                entity = self.simulation.entities[entity_id]
                kinds[entity.kind.value] = kinds.get(entity.kind.value, 0) + 1
                health += entity.health
                maximum += entity.kind.profile.max_health
            distribution = ", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items()))
            lines = [
                f"{len(self.selected_entities)} selected | HP {health}/{maximum}",
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
        live_automations = self.simulation.live_automations
        live_ids = {automation.automation_id for automation in live_automations}
        if self.selected_automation_id not in live_ids:
            self.selected_automation_id = (
                live_automations[0].automation_id if live_automations else None
            )
        visible_automations = live_automations[self.automation_scroll : self.automation_scroll + 7]
        for automation in visible_automations:
            available_width = panel.right - x - 12
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
                    summary += f" | -> {linked_id}"
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
            if y > 600:
                break
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
        y = max(y + 8, panel.bottom - round(180 * self.ui_scale))
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
        for entity_id in self.selected_entities:
            entity = self.simulation.entities[entity_id]
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
        y = self.command_bar_rect.y - 106
        self._settings_buttons.clear()
        pygame.draw.rect(screen, (34, 41, 51), pygame.Rect(x, y, 160, 100), border_radius=4)
        for action, label in (("save", "Save"), ("load", "Load"), ("new", "New game")):
            rectangle = pygame.Rect(x + 8, y + 7, 144, 24)
            self._button(screen, rectangle, label)
            self._settings_buttons.append((rectangle, action))
            y += 30

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
