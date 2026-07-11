"""Pygame traditional RTS interaction and inspection interface for Phase 4."""

from __future__ import annotations

from enum import StrEnum
from math import hypot

import pygame

from airts.automations import AutomationStatus
from airts.commands import (
    AttackCommand,
    CancelAutomationCommand,
    CreateDefendCommand,
    CreateEconomyCommand,
    CreatePatrolCommand,
    CreateProductionCommand,
    CreateRepairAndReturnCommand,
    CreateSpatialReferenceCommand,
    EditSpatialReferenceCommand,
    ModifyAutomationCommand,
    MoveCommand,
    PauseAutomationCommand,
    RenameRegionCommand,
    ResumeAutomationCommand,
    SetSelectionCommand,
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
from airts.map_model import EntityCategory, EntityKind, Terrain
from airts.simulation import Simulation
from airts.spatial import SpatialKind


class InputMode(StrEnum):
    SELECT = "select"
    POINT = "point"
    LINE = "line"
    RECTANGLE = "rectangle"
    FREEHAND = "freehand"


class AirtsApp:
    MAP_PIXELS = 768
    PANEL_WIDTH = 400
    WINDOW_SIZE = (MAP_PIXELS + PANEL_WIDTH, MAP_PIXELS)
    BACKGROUND = (18, 22, 28)
    PANEL_BACKGROUND = (27, 32, 40)

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
        self.naming_reference_id: str | None = None
        self.naming_buffer = ""
        self.line_points: list[Point] = []
        self.freehand_points: list[Point] = []
        self.drag_start: Point | None = None
        self.paused = False
        self.notice = "Select units, draw a target, then press A to patrol."
        self._automation_buttons: list[tuple[pygame.Rect, str, str]] = []
        self._font: pygame.font.Font | None = None
        self._small_font: pygame.font.Font | None = None

    @property
    def tile_size(self) -> float:
        return self.MAP_PIXELS / self.simulation.game_map.width

    def run(self, max_frames: int | None = None) -> None:
        pygame.init()
        try:
            screen = pygame.display.set_mode(self.WINDOW_SIZE)
            pygame.display.set_caption("AIRTS — Phase 5")
            self._font = pygame.font.Font(None, 24)
            self._small_font = pygame.font.Font(None, 19)
            clock = pygame.time.Clock()
            accumulator = 0.0
            running = True
            frames = 0
            while running and (max_frames is None or frames < max_frames):
                elapsed = min(clock.tick(60) / 1000.0, 0.25)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    else:
                        self._handle_event(event)
                if not self.paused:
                    accumulator += elapsed
                    while accumulator >= Simulation.TICK_SECONDS:
                        self.simulation.advance()
                        accumulator -= Simulation.TICK_SECONDS
                self._draw(screen)
                pygame.display.flip()
                frames += 1
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

    def _handle_key(self, key: int) -> None:
        mode_keys = {
            pygame.K_1: InputMode.SELECT,
            pygame.K_2: InputMode.POINT,
            pygame.K_3: InputMode.LINE,
            pygame.K_4: InputMode.RECTANGLE,
            pygame.K_5: InputMode.FREEHAND,
        }
        if key in mode_keys:
            self.mode = mode_keys[key]
            self._clear_draft()
            self.notice = f"Input mode: {self.mode.value}"
        elif key in {pygame.K_RETURN, pygame.K_KP_ENTER} and self.mode is InputMode.LINE:
            if len(self.line_points) < 2:
                self.notice = "A line needs at least two points."
                return
            self._finish_target(PolylineTarget(tuple(self.line_points)))
            self.line_points.clear()
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
            self.active_target = None
            self.notice = "Spatial target cleared."

    def _handle_mouse_down(self, button: int, position: tuple[int, int]) -> None:
        if position[0] >= self.MAP_PIXELS:
            if button == 1:
                self._handle_panel_click(position)
            return
        point = self._map_point(position)
        if button == 3:
            enemies = sorted(
                (entity.selection_position.distance_to(point), entity.entity_id)
                for entity in self.simulation.entities.values()
                if entity.owner_id != "player"
                and entity.selection_position.distance_to(point) <= 1.5
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
        if self.mode is InputMode.POINT:
            self._finish_target(PointTarget(point))
        elif self.mode is InputMode.LINE:
            self.line_points.append(point)
            self.notice = "Add another line point or press Enter to finish."
        elif self.mode is InputMode.FREEHAND:
            self.freehand_points = [point]
        else:
            self.drag_start = point

    def _handle_mouse_motion(
        self, position: tuple[int, int], buttons: tuple[bool, bool, bool]
    ) -> None:
        if (
            self.mode is InputMode.FREEHAND
            and buttons[0]
            and position[0] < self.MAP_PIXELS
            and self.freehand_points
        ):
            point = self._map_point(position)
            if point.distance_to(self.freehand_points[-1]) >= 0.2:
                self.freehand_points.append(point)

    def _handle_mouse_up(self, button: int, position: tuple[int, int]) -> None:
        if button != 1 or position[0] >= self.MAP_PIXELS:
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
                    (entity.selection_position.distance_to(end), entity_id)
                    for entity_id, entity in self.simulation.entities.items()
                    if entity.owner_id == "player"
                    if entity.selection_position.distance_to(end) <= 1.5
                )
            )
            found = {candidates[0][1]} if candidates else set()
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
                    self.selected_entities.clear()
                self._commit_selection()
                return
        else:
            left, right = sorted((start.x, end.x))
            top, bottom = sorted((start.y, end.y))
            found = {
                entity_id
                for entity_id, entity in self.simulation.entities.items()
                if entity.owner_id == "player"
                if left <= entity.selection_position.x <= right
                and top <= entity.selection_position.y <= bottom
            }
        if additive:
            self.selected_entities.symmetric_difference_update(found)
        else:
            self.selected_entities = found
            self.selected_points.clear()
            self.selected_routes.clear()
            self.selected_regions.clear()
        self._commit_selection()
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

    def _select_reference(self, reference_id: str | None) -> None:
        if reference_id is None:
            return
        reference = self.simulation.spatial.references[reference_id]
        self.selected_points = {reference_id} if reference.kind is SpatialKind.POINT else set()
        self.selected_routes = {reference_id} if reference.kind is SpatialKind.ROUTE else set()
        self.selected_regions = {reference_id} if reference.kind is SpatialKind.REGION else set()
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
        factories = [
            entity_id
            for entity_id in sorted(self.selected_entities)
            if self.simulation.entities[entity_id].kind is EntityKind.FACTORY
        ]
        if len(factories) != 1:
            self.notice = "Select exactly one factory for production."
            return
        rally_point = (
            self.active_target.point if isinstance(self.active_target, PointTarget) else None
        )
        result = self.simulation.execute(
            CreateProductionCommand(factories[0], EntityKind.LIGHT_TANK, 3, rally_point)
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.selected_automation_id = result.automation_id

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
            if self.simulation.entities[entity_id].kind is EntityKind.RESOURCE_GENERATOR
        )
        result = self.simulation.execute(
            CreateEconomyCommand(generators, self.simulation.resources.get("player", 0) + 100)
        )
        self.notice = f"Created {result.automation_id}." if result.accepted else result.reason
        if result.accepted:
            self.selected_automation_id = result.automation_id

    def _handle_panel_click(self, position: tuple[int, int]) -> None:
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
        screen.fill(self.BACKGROUND)
        self._draw_map(screen)
        self._draw_spatial_input(screen)
        self._draw_entities(screen)
        self._draw_panel(screen)

    def _draw_map(self, screen: pygame.Surface) -> None:
        terrain_colors = {
            Terrain.GRASS: (64, 102, 60),
            Terrain.ROAD: (119, 106, 77),
            Terrain.FOREST: (43, 78, 48),
            Terrain.WATER: (42, 91, 132),
            Terrain.ROCK: (66, 69, 72),
            Terrain.BRIDGE: (148, 126, 82),
        }
        tile = self.tile_size
        for y, row in enumerate(self.simulation.game_map.terrain):
            for x, terrain in enumerate(row):
                rectangle = pygame.Rect(
                    round(x * tile), round(y * tile), round(tile + 1), round(tile + 1)
                )
                pygame.draw.rect(screen, terrain_colors[terrain], rectangle)
        for cell in range(0, self.simulation.game_map.width + 1, 8):
            pixel = round(cell * tile)
            pygame.draw.line(screen, (44, 65, 49), (pixel, 0), (pixel, self.MAP_PIXELS))
            pygame.draw.line(screen, (44, 65, 49), (0, pixel), (self.MAP_PIXELS, pixel))

    def _draw_entities(self, screen: pygame.Surface) -> None:
        colors = {
            EntityKind.SCOUT: (82, 211, 237),
            EntityKind.LIGHT_TANK: (235, 221, 93),
            EntityKind.HEAVY_TANK: (230, 139, 75),
            EntityKind.FACTORY: (112, 142, 181),
            EntityKind.REPAIR_HUB: (110, 178, 151),
            EntityKind.COMMAND_CENTER: (155, 129, 190),
            EntityKind.RESOURCE_GENERATOR: (198, 168, 88),
        }
        for entity in self.simulation.entities.values():
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
                    round(entity.position.x * self.tile_size),
                    round(entity.position.y * self.tile_size),
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
            bar_width = max(12, round(self.tile_size * 1.4))
            bar = pygame.Rect(center[0] - bar_width // 2, center[1] - 12, bar_width, 3)
            pygame.draw.rect(screen, (70, 35, 35), bar)
            health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
            pygame.draw.rect(screen, (74, 218, 111), pygame.Rect(bar.x, bar.y, health_width, 3))

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
            if len(pixels) > 1:
                pygame.draw.lines(screen, (255, 170, 210), False, pixels, 2)
            for pixel in pixels:
                pygame.draw.circle(screen, (255, 170, 210), pixel, 3)

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
            surface = pygame.Surface((self.MAP_PIXELS, self.MAP_PIXELS), pygame.SRCALPHA)
            points = [self._screen_point(point) for point in target.points]
            pygame.draw.polygon(surface, (*color, 55), points)
            pygame.draw.polygon(surface, (*color, 230), points, width)
            screen.blit(surface, (0, 0))

    def _draw_panel(self, screen: pygame.Surface) -> None:
        if self._font is None or self._small_font is None:
            raise RuntimeError("fonts not initialized")
        panel = pygame.Rect(self.MAP_PIXELS, 0, self.PANEL_WIDTH, self.MAP_PIXELS)
        pygame.draw.rect(screen, self.PANEL_BACKGROUND, panel)
        x = self.MAP_PIXELS + 16
        y = 14
        self._text(screen, "AIRTS — Phase 5", (x, y), (245, 245, 245))
        y += 28
        self._small_text(
            screen,
            f"Tick {self.simulation.tick} | {self.mode.value} | {'PAUSED' if self.paused else 'RUNNING'}",
            (x, y),
            (166, 191, 215),
        )
        y += 25
        for line in self._wrap(self.notice, 46):
            self._small_text(screen, line, (x, y), (244, 216, 118))
            y += 18
        y += 7
        self._small_text(screen, "1 Select  2 Point  3 Line", (x, y), (205, 210, 218))
        y += 18
        self._small_text(screen, "4 Rectangle  5 Freehand", (x, y), (205, 210, 218))
        y += 18
        self._small_text(
            screen, "A Patrol D Defend P Produce R Repair G Economy", (x, y), (205, 210, 218)
        )
        y += 18
        self._small_text(screen, "Right-click Move/Attack  Space Pause", (x, y), (205, 210, 218))
        y += 28
        self._small_text(screen, "Shift multi-select  N Name  E Edit", (x, y), (205, 210, 218))
        y += 18
        self._small_text(screen, "U Retarget  [ ] Priority", (x, y), (205, 210, 218))
        y += 22
        self._small_text(
            screen,
            f"Resources: {self.simulation.resources.get('player', 0)}",
            (x, y),
            (111, 221, 151),
        )
        y += 20
        self._text(screen, "Automations", (x, y), (245, 245, 245))
        y += 25
        self._automation_buttons.clear()
        for automation in self.simulation.automations.values():
            if self.selected_automation_id is None:
                self.selected_automation_id = automation.automation_id
            self._small_text(screen, automation.title, (x, y), (232, 232, 232))
            self._automation_buttons.append(
                (
                    pygame.Rect(x, y - 2, self.PANEL_WIDTH - 32, 38),
                    "inspect",
                    automation.automation_id,
                )
            )
            y += 17
            summary = (
                f"{automation.automation_id} | {automation.kind.value} | "
                f"{automation.status.value} | {len(automation.entity_ids)} entities"
            )
            self._small_text(screen, summary, (x, y), (153, 178, 198))
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
            if y > 485:
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
        y = max(y + 8, 570)
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
        return Point(position[0] / self.tile_size, position[1] / self.tile_size)

    def _screen_point(self, point: Point) -> tuple[int, int]:
        return round(point.x * self.tile_size), round(point.y * self.tile_size)

    def _clear_draft(self) -> None:
        self.line_points.clear()
        self.freehand_points.clear()
        self.drag_start = None

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
