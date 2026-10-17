"""Scene view contract and cached software rendering, independent of application input."""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from math import ceil, floor
from typing import Protocol

import pygame

from airts.contracts.automations import (
    AutomationStatus,
    ConstructionParameters,
    RepairParameters,
    RepairPhase,
)
from airts.contracts.spatial import Point
from airts.presentation.palette import (
    ENTITY_COLORS,
    PROGRESS_COLOR,
    PROGRESS_TRACK,
    PROJECTILE_COLORS,
    RECLAIM_COLOR,
    REPAIR_COLOR,
    TERRAIN_COLORS,
    WORK_BEAM_WIDTH,
    Color,
    match_color,
)
from airts.presentation.progress import building_progress
from airts.presentation.projectile_effects import (
    SHELL_CORE_RADIUS,
    SHELL_RIM_RADIUS,
    blast_circles,
    shell_visual_position,
)
from airts.presentation.visuals.assets import SpriteCache
from airts.presentation.visuals.catalog import APPEARANCES, COMPOSITE_PATHS
from airts.presentation.visuals.facing import FacingHistory
from airts.presentation.visuals.terrain import (
    DETAIL_MIN_TILE,
    DETAIL_SIDE,
    GRID_COLOR,
    detailed_surface,
)
from airts.simulation import Simulation
from airts.systems.construction import construction_distance
from airts.systems.repair import building_distance
from airts.systems.repair_positions import in_service_range
from airts.world.entities import Entity, UnitState
from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory


class RendererBackend(StrEnum):
    OPENGL = "opengl"
    SOFTWARE = "software"


class SceneView(Protocol):
    """Read-only scene inputs shared by software and GPU drawing."""

    MAX_SELECTED_PATHS: int
    MAP_PIXELS: int
    facing: FacingHistory

    @property
    def simulation(self) -> Simulation: ...

    @property
    def selected_entities(self) -> set[str]: ...

    @property
    def inspected_entity_id(self) -> str | None: ...

    @property
    def tile_size(self) -> float: ...

    @property
    def map_origin(self) -> tuple[int, int]: ...

    @property
    def map_pixel_size(self) -> tuple[int, int]: ...

    def representative_path_entity_ids(self) -> tuple[str, ...]: ...

    @staticmethod
    def simplified_path(entity: Entity) -> tuple[Point, ...]: ...

    def screen_point(self, point: Point) -> tuple[int, int]: ...


def order_feedback(view: SceneView, entity: Entity) -> tuple[tuple[Point, ...], Color]:
    """Use current authoritative endpoints; no targeting or routing work in rendering."""
    target = view.simulation.entities.get(entity.attack_target_id or "")
    if entity.pursue_target and target is not None:
        return (entity.selection_position, target.selection_position), (155, 92, 92)
    return view.simplified_path(entity), (225, 225, 225)


def interaction_range(entity: Entity) -> tuple[float, bool]:
    """Return the inspected entity's primary range and whether it is a work range."""

    profile = entity.profile
    if profile.can_build or profile.can_reclaim:
        return max(profile.build_range, profile.repair_range), True
    if profile.can_repair:
        return profile.repair_range, True
    return profile.attack_range, False


def work_links(simulation: Simulation) -> Iterator[tuple[Point, Point]]:
    """Read active construction/repair endpoints without changing domain state."""
    source: Entity | None
    for worker in simulation.entities.values():
        if not worker.profile.can_repair or not worker.is_complete:
            continue
        target = simulation.entities.get(worker.repair_target_id or "")
        if (
            target is not None
            and target.owner_id == worker.owner_id
            and target.health < target.kind.profile.max_health
            and (
                not worker.is_movable
                and target is not worker
                and target.is_complete
                and worker.selection_position.distance_to(target.selection_position)
                <= worker.profile.repair_range
                or worker.is_movable
                and target.category is EntityCategory.BUILDING
                and target.is_complete
                and worker.state is UnitState.REPAIRING
                and not worker.path
                and building_distance(worker.position, target) <= worker.profile.repair_range
            )
        ):
            yield worker.selection_position, target.selection_position
    for automation in simulation.live_automations:
        parameters = automation.parameters
        if (
            isinstance(parameters, ConstructionParameters)
            and automation.status is AutomationStatus.ACTIVE
        ):
            target = simulation.entities.get(parameters.site_entity_id or "")
            if target is None or target.is_complete:
                continue
            for key in automation.entity_ids:
                source = simulation.entities.get(key)
                if (
                    source is not None
                    and simulation.assignments.get(key) == automation.automation_id
                    and source.state is UnitState.BUILDING
                    and construction_distance(source.position, parameters)
                    <= source.profile.build_range
                ):
                    yield source.selection_position, target.selection_position
        elif isinstance(parameters, RepairParameters):
            for key, phase in parameters.phases.items():
                target = simulation.entities.get(key)
                source = simulation.entities.get(parameters.destinations.get(key, ""))
                if (
                    phase is RepairPhase.REPAIRING
                    and simulation.assignments.get(key) == automation.automation_id
                    and source is not None
                    and target is not None
                    and not source.profile.can_repair
                    and source.is_complete
                    and source.owner_id == target.owner_id
                    and target.state is UnitState.REPAIRING
                    and target.health < target.kind.profile.max_health
                    and not target.path
                    and in_service_range(simulation, target, source)
                ):
                    yield source.selection_position, target.selection_position


def reclaim_links(simulation: Simulation) -> Iterator[tuple[Point, Point]]:
    """Read the red beam endpoints of builders currently reclaiming in range."""
    for builder in simulation.entities.values():
        if builder.reclaim_target_id is None:
            continue
        target = simulation.entities.get(builder.reclaim_target_id)
        if (
            target is not None
            and builder.profile.can_reclaim
            and builder.state is UnitState.RECLAIMING
            and not builder.path
            and target.owner_id == builder.owner_id
            and target.category is EntityCategory.BUILDING
            and building_distance(builder.position, target) <= builder.profile.build_range
        ):
            yield builder.selection_position, target.selection_position


class SoftwareRenderer:
    """Draw domain state while owning all terrain, sprite and path caches."""

    def __init__(self) -> None:
        self._map_surface: pygame.Surface | None = None
        self._map_detail_surface: pygame.Surface | None = None
        self._scaled_map_surface: pygame.Surface | None = None
        self._viewport_terrain_key: tuple[object, ...] | None = None
        self._sprites = SpriteCache()
        self._entity_render_key: tuple[object, ...] | None = None
        self._entity_blits: tuple[tuple[pygame.Surface, tuple[int, int]], ...] = ()
        self._building_selection_rects: tuple[pygame.Rect, ...] = ()
        self._selected_bounds: pygame.Rect | None = None
        self._health_bars: tuple[tuple[pygame.Rect, int], ...] = ()
        self._progress_bars: tuple[tuple[pygame.Rect, int], ...] = ()
        self._inspected_ring: tuple[tuple[int, int], int] | None = None
        self._path_render_key: tuple[object, ...] | None = None
        self._path_render_points: tuple[tuple[Color, tuple[tuple[int, int], ...]], ...] = ()

    def reset_map(self) -> None:
        """Discard terrain surfaces after the application loads a different world."""
        self._map_surface = None
        self._map_detail_surface = None
        self._scaled_map_surface = None
        self._viewport_terrain_key = None

    def draw_map(self, view: SceneView, screen: pygame.Surface) -> None:
        # Fixed-resolution map sources, then a viewport-sized crop. Zoom never
        # allocates a full zoomed world, and static frames reuse the same crop.
        origin_x, origin_y = view.map_origin
        tile = view.tile_size
        clip = screen.get_clip()
        game_map = view.simulation.game_map
        if self._map_surface is None:
            self._map_surface = pygame.Surface((game_map.width, game_map.height))
            for y, row in enumerate(game_map.terrain):
                for x, terrain in enumerate(row):
                    self._map_surface.set_at((x, y), TERRAIN_COLORS[terrain])
        detailed = tile >= DETAIL_MIN_TILE
        if detailed and self._map_detail_surface is None:
            self._map_detail_surface = detailed_surface(game_map)
        source = self._map_detail_surface if detailed else self._map_surface
        assert source is not None
        source_scale = DETAIL_SIDE if detailed else 1
        left = max(0, floor((clip.left - origin_x) / tile))
        top = max(0, floor((clip.top - origin_y) / tile))
        right = min(game_map.width, ceil((clip.right - origin_x) / tile))
        bottom = min(game_map.height, ceil((clip.bottom - origin_y) / tile))
        if right <= left or bottom <= top:
            return
        crop = pygame.Rect(
            left * source_scale,
            top * source_scale,
            (right - left) * source_scale,
            (bottom - top) * source_scale,
        )
        start = (round(origin_x + left * tile), round(origin_y + top * tile))
        size = (
            max(1, round(origin_x + right * tile) - start[0]),
            max(1, round(origin_y + bottom * tile) - start[1]),
        )
        key = (tuple(crop), size, detailed)
        if key != self._viewport_terrain_key:
            self._scaled_map_surface = pygame.transform.scale(source.subsurface(crop), size)
            self._viewport_terrain_key = key
        assert self._scaled_map_surface is not None
        screen.blit(self._scaled_map_surface, start)
        for x in range(((left + 7) // 8) * 8, right + 1, 8):
            pixel = round(origin_x + x * tile)
            pygame.draw.line(screen, GRID_COLOR, (pixel, start[1]), (pixel, start[1] + size[1]))
        for y in range(((top + 7) // 8) * 8, bottom + 1, 8):
            pixel = round(origin_y + y * tile)
            pygame.draw.line(screen, GRID_COLOR, (start[0], pixel), (start[0] + size[0], pixel))

    def draw_entities(self, view: SceneView, screen: pygame.Surface) -> None:
        colors = ENTITY_COLORS
        for color, points in self._representative_path_points(view):
            pygame.draw.lines(
                screen,
                color,
                False,
                points,
                WORK_BEAM_WIDTH if color in (REPAIR_COLOR, RECLAIM_COLOR) else 1,
            )
        tile_size = view.tile_size
        origin_x, origin_y = view.map_origin
        view.facing.sync(view.simulation)
        self._draw_cached_entities(view, screen, colors, tile_size, (origin_x, origin_y))
        inspected = view.simulation.entities.get(view.inspected_entity_id or "")
        radius, work_range = interaction_range(inspected) if inspected is not None else (0.0, False)
        if inspected is not None and radius > 0 and len(view.selected_entities) <= 1:
            pygame.draw.circle(
                screen,
                (105, 232, 172) if work_range else (255, 218, 100),
                view.screen_point(inspected.selection_position),
                round(radius * view.tile_size),
                1,
            )

    def _draw_cached_entities(
        self,
        view: SceneView,
        screen: pygame.Surface,
        colors: dict[EntityKind, Color],
        tile_size: float,
        origin: tuple[int, int],
    ) -> None:
        """Cache all entity layers and retain small/large-selection feedback policies."""

        selected_entities = frozenset(view.selected_entities)
        key: tuple[object, ...] = (
            id(view.simulation),
            view.simulation.tick,
            view.simulation.command_count,
            origin,
            tile_size,
            selected_entities,
            view.inspected_entity_id,
            len(view.simulation.entities),
        )
        if key != self._entity_render_key:
            self._entity_render_key = key
            self._sprites.prepare(screen)
            radius = max(5, round(tile_size * 0.42))
            large_selection = len(selected_entities) > 128
            bar_width = max(12, round(tile_size * 1.4))
            origin_x, origin_y = origin
            blits: list[tuple[pygame.Surface, tuple[int, int]]] = []
            building_draws: list[pygame.Rect] = []
            health_bars: list[tuple[pygame.Rect, int]] = []
            progress_bars: list[tuple[pygame.Rect, int]] = []
            selected_min_x: int | None = None
            selected_min_y = 0
            selected_max_x = 0
            selected_max_y = 0
            inspected_ring: tuple[tuple[int, int], int] | None = None
            for entity_id, entity in view.simulation.entities.items():
                color = colors[entity.kind] if entity.owner_id == "player" else (218, 78, 78)
                if view.simulation.game_map.match_slots:
                    color = match_color(view.simulation.game_map, entity.owner_id)
                selected = entity_id in selected_entities
                if entity.category is EntityCategory.BUILDING:
                    width, height = entity.kind.profile.footprint
                    rectangle = pygame.Rect(
                        origin_x + round(entity.position.x * tile_size),
                        origin_y + round(entity.position.y * tile_size),
                        round(width * tile_size),
                        round(height * tile_size),
                    )
                    if selected:
                        building_draws.append(rectangle)
                    center = (
                        origin_x + round(entity.selection_position.x * tile_size),
                        origin_y + round(entity.selection_position.y * tile_size),
                    )
                else:
                    center = (
                        origin_x + round(entity.position.x * tile_size),
                        origin_y + round(entity.position.y * tile_size),
                    )
                    if selected and large_selection:
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
                    elif entity_id == view.inspected_entity_id:
                        inspected_ring = (center, radius)
                appearance = APPEARANCES[entity.kind]
                size = (
                    max(14 if entity.is_movable else 1, round(appearance.size[0] * tile_size)),
                    max(14 if entity.is_movable else 1, round(appearance.size[1] * tile_size)),
                )
                pose = view.facing.pose(entity_id)
                layers: tuple[tuple[str | None, float], ...] = (
                    (appearance.body, pose.body),
                    (appearance.weapon, pose.weapon),
                )
                if appearance.weapon is not None and pose.body == pose.weapon:
                    layers = ((COMPOSITE_PATHS[entity.kind], pose.body),)
                for path, angle in layers:
                    if path is not None:
                        sprite = self._sprites.get(path, color, size, angle)
                        blits.append(
                            (
                                sprite,
                                (
                                    center[0] - sprite.get_width() // 2,
                                    center[1] - sprite.get_height() // 2,
                                ),
                            )
                        )
                if (
                    not large_selection
                    or entity.health < entity.kind.profile.max_health
                    or entity_id == view.inspected_entity_id
                ):
                    bar = pygame.Rect(
                        center[0] - bar_width // 2,
                        center[1] - max(7, round(appearance.size[1] * tile_size / 2)) - 6,
                        bar_width,
                        3,
                    )
                    health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
                    health_bars.append((bar, health_width))
                if entity.category is EntityCategory.BUILDING:
                    for index, progress in enumerate(building_progress(view.simulation, entity)):
                        bar = pygame.Rect(
                            center[0] - bar_width // 2,
                            center[1]
                            + max(7, round(appearance.size[1] * tile_size / 2))
                            + 4
                            + index * 6,
                            bar_width,
                            3,
                        )
                        progress_bars.append((bar, round(bar_width * progress)))
            self._entity_blits = tuple(blits)
            self._building_selection_rects = tuple(building_draws)
            self._health_bars = tuple(health_bars)
            self._progress_bars = tuple(progress_bars)
            self._inspected_ring = inspected_ring
            self._selected_bounds = (
                None
                if selected_min_x is None
                else pygame.Rect(
                    selected_min_x - 3,
                    selected_min_y - 3,
                    selected_max_x - selected_min_x + 6,
                    selected_max_y - selected_min_y + 6,
                )
            )
        screen.fblits(self._entity_blits)
        for rectangle in self._building_selection_rects:
            pygame.draw.rect(screen, (255, 255, 255), rectangle.inflate(6, 6), 2)
        if self._inspected_ring is not None:
            center, radius = self._inspected_ring
            pygame.draw.circle(screen, (255, 210, 90), center, radius + 3, 2)
        for bar, health_width in self._health_bars:
            pygame.draw.rect(screen, (70, 35, 35), bar)
            pygame.draw.rect(
                screen,
                (74, 218, 111),
                pygame.Rect(bar.x, bar.y, health_width, 3),
            )
        for bar, progress_width in self._progress_bars:
            pygame.draw.rect(screen, PROGRESS_TRACK, bar)
            pygame.draw.rect(
                screen, PROGRESS_COLOR, pygame.Rect(bar.x, bar.y, progress_width, bar.height)
            )
        if len(selected_entities) <= 128:
            for entity_id in selected_entities:
                entity = view.simulation.entities[entity_id]
                if entity.is_movable:
                    pygame.draw.circle(
                        screen,
                        (255, 255, 255),
                        view.screen_point(entity.position),
                        max(5, round(tile_size * 0.42)) + 3,
                        2,
                    )
        elif self._selected_bounds is not None:
            pygame.draw.rect(
                screen,
                (245, 245, 245),
                self._selected_bounds,
                1,
            )

    def _representative_path_points(
        self, view: SceneView
    ) -> tuple[tuple[Color, tuple[tuple[int, int], ...]], ...]:
        key: tuple[object, ...] = (
            id(view.simulation),
            view.simulation.tick,
            view.simulation.command_count,
            view.map_origin,
            view.tile_size,
            frozenset(view.selected_entities),
            view.inspected_entity_id,
        )
        if key != self._path_render_key:
            origin_x, origin_y = view.map_origin
            tile_size = view.tile_size
            paths: list[tuple[Color, tuple[tuple[int, int], ...]]] = []
            for entity_id in view.representative_path_entity_ids():
                entity = view.simulation.entities[entity_id]
                world_points, color = order_feedback(view, entity)
                points = tuple(
                    (
                        origin_x + round(point.x * tile_size),
                        origin_y + round(point.y * tile_size),
                    )
                    for point in world_points
                )
                if len(points) >= 2:
                    paths.append((color, points))
            paths.extend(
                (REPAIR_COLOR, (view.screen_point(source), view.screen_point(target)))
                for source, target in work_links(view.simulation)
            )
            paths.extend(
                (RECLAIM_COLOR, (view.screen_point(source), view.screen_point(target)))
                for source, target in reclaim_links(view.simulation)
            )
            self._path_render_key = key
            self._path_render_points = tuple(paths)
        return self._path_render_points

    def draw_projectiles(self, view: SceneView, screen: pygame.Surface) -> None:
        colors = PROJECTILE_COLORS
        for trace in view.simulation.projectile_traces:
            if trace.weapon_kind.profile.splash_radius > 0:
                for point, radius, color, width in blast_circles(trace, view.simulation.tick):
                    pygame.draw.circle(
                        screen,
                        color,
                        view.screen_point(point),
                        max(1, round(radius * view.tile_size)),
                        width,
                    )
                continue
            points = [view.screen_point(point) for point in trace.points]
            if len(points) > 1:
                pygame.draw.lines(screen, colors[trace.weapon_kind], False, points, 1)
        for projectile in view.simulation.projectiles.values():
            color = colors[projectile.weapon_kind]
            if projectile.weapon_kind.profile.splash_radius > 0:
                pygame.draw.circle(screen, (45, 39, 32), view.screen_point(projectile.position), 3)
                center = view.screen_point(shell_visual_position(projectile, projectile.position))
                pygame.draw.circle(screen, (255, 226, 150), center, SHELL_RIM_RADIUS)
                pygame.draw.circle(screen, color, center, SHELL_CORE_RADIUS)
                continue
            points = [view.screen_point(point) for point in projectile.trajectory]
            if len(points) > 1:
                pygame.draw.lines(screen, color, False, points, 1)
            pygame.draw.line(
                screen,
                tuple(channel // 2 for channel in color),
                view.screen_point(projectile.position),
                view.screen_point(projectile.destination),
                1,
            )
            center = view.screen_point(projectile.position)
            pygame.draw.circle(screen, (255, 255, 255), center, 3)
            pygame.draw.circle(screen, color, center, 2)
