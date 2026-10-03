"""Scene view contract and cached software rendering, independent of application input."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

import pygame

from airts.contracts.spatial import Point
from airts.presentation.palette import ENTITY_COLORS, PROJECTILE_COLORS, TERRAIN_COLORS, Color
from airts.simulation import Simulation
from airts.world.entities import Entity
from airts.world.map import EntityCategory, EntityKind


class RendererBackend(StrEnum):
    OPENGL = "opengl"
    SOFTWARE = "software"


class SceneView(Protocol):
    """Read-only scene inputs shared by software and GPU drawing."""

    MAX_SELECTED_PATHS: int
    MAP_PIXELS: int

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


class SoftwareRenderer:
    """Draw domain state while owning all terrain, sprite and path caches."""

    def __init__(self) -> None:
        self._map_surface: pygame.Surface | None = None
        self._scaled_map_surface: pygame.Surface | None = None
        self._scaled_map_size: tuple[int, int] | None = None
        self._unit_sprite_cache: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}
        self._large_unit_render_key: tuple[object, ...] | None = None
        self._large_unit_blits: tuple[tuple[pygame.Surface, tuple[int, int]], ...] = ()
        self._large_building_draws: tuple[tuple[pygame.Rect, tuple[int, int, int], bool], ...] = ()
        self._large_unit_selected_bounds: pygame.Rect | None = None
        self._large_unit_health_bars: tuple[tuple[pygame.Rect, int], ...] = ()
        self._large_unit_inspected_ring: tuple[tuple[int, int], int] | None = None
        self._path_render_key: tuple[object, ...] | None = None
        self._path_render_points: tuple[tuple[tuple[int, int], ...], ...] = ()

    def reset_map(self) -> None:
        """Discard terrain surfaces after the application loads a different world."""
        self._map_surface = None
        self._scaled_map_surface = None
        self._scaled_map_size = None

    def draw_map(self, view: SceneView, screen: pygame.Surface) -> None:
        if self._map_surface is not None:
            if self._scaled_map_surface is None or self._scaled_map_size != view.map_pixel_size:
                self._scaled_map_surface = pygame.transform.scale(
                    self._map_surface,
                    view.map_pixel_size,
                )
                self._scaled_map_size = view.map_pixel_size
            screen.blit(self._scaled_map_surface, view.map_origin)
            return
        surface = pygame.Surface((view.MAP_PIXELS, view.MAP_PIXELS))
        terrain_colors = TERRAIN_COLORS
        tile = view.MAP_PIXELS / view.simulation.game_map.width
        for y, row in enumerate(view.simulation.game_map.terrain):
            for x, terrain in enumerate(row):
                rectangle = pygame.Rect(
                    round(x * tile), round(y * tile), round(tile + 1), round(tile + 1)
                )
                pygame.draw.rect(surface, terrain_colors[terrain], rectangle)
        for cell in range(0, view.simulation.game_map.width + 1, 8):
            pixel = round(cell * tile)
            pygame.draw.line(surface, (44, 65, 49), (pixel, 0), (pixel, view.MAP_PIXELS))
            pygame.draw.line(surface, (44, 65, 49), (0, pixel), (view.MAP_PIXELS, pixel))
        self._map_surface = surface
        self._scaled_map_surface = pygame.transform.scale(surface, view.map_pixel_size)
        self._scaled_map_size = view.map_pixel_size
        screen.blit(self._scaled_map_surface, view.map_origin)

    def draw_entities(self, view: SceneView, screen: pygame.Surface) -> None:
        colors = ENTITY_COLORS
        for points in self._representative_path_points(view):
            pygame.draw.lines(screen, (225, 225, 225), False, points, 1)
        tile_size = view.tile_size
        origin_x, origin_y = view.map_origin
        selected_entities = view.selected_entities
        if len(selected_entities) > 128:
            self._draw_cached_large_unit_entities(
                view, screen, colors, tile_size, (origin_x, origin_y)
            )
            return
        unit_radius = max(5, round(tile_size * 0.42))
        bar_width = max(12, round(tile_size * 1.4))
        for entity_id, entity in view.simulation.entities.items():
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
                pygame.draw.circle(screen, color, center, radius)
                if entity_id in selected_entities:
                    pygame.draw.circle(screen, (255, 255, 255), center, radius + 3, 2)
                if entity_id == view.inspected_entity_id and entity_id not in selected_entities:
                    pygame.draw.circle(screen, (255, 210, 90), center, radius + 3, 2)
            bar = pygame.Rect(center[0] - bar_width // 2, center[1] - 12, bar_width, 3)
            pygame.draw.rect(screen, (70, 35, 35), bar)
            health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
            pygame.draw.rect(
                screen,
                (74, 218, 111),
                pygame.Rect(bar.x, bar.y, health_width, 3),
            )
        inspected = view.simulation.entities.get(view.inspected_entity_id or "")
        interaction_range = (
            inspected.kind.profile.build_range
            if inspected is not None and inspected.kind is EntityKind.BUILDER
            else inspected.kind.profile.attack_range
            if inspected is not None
            else 0.0
        )
        if inspected is not None and interaction_range > 0 and len(view.selected_entities) <= 1:
            pygame.draw.circle(
                screen,
                (105, 232, 172) if inspected.kind is EntityKind.BUILDER else (255, 218, 100),
                view.screen_point(inspected.selection_position),
                round(interaction_range * view.tile_size),
                1,
            )

    def _draw_cached_large_unit_entities(
        self,
        view: SceneView,
        screen: pygame.Surface,
        colors: dict[EntityKind, Color],
        tile_size: float,
        origin: tuple[int, int],
    ) -> None:
        """Draw a large scene without rebuilding unchanged transforms each frame."""

        selected_entities = frozenset(view.selected_entities)
        key: tuple[object, ...] = (
            id(view.simulation),
            view.simulation.tick,
            origin,
            tile_size,
            selected_entities,
            view.inspected_entity_id,
            len(view.simulation.entities),
        )
        if key != self._large_unit_render_key:
            self._large_unit_render_key = key
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
            for entity_id, entity in view.simulation.entities.items():
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
                    elif entity_id == view.inspected_entity_id:
                        inspected_ring = (center, radius)
                    sprite = self._unit_sprite(radius, color)
                    blits.append((sprite, (center[0] - radius, center[1] - radius)))
                if (
                    entity.health < entity.kind.profile.max_health
                    or entity_id == view.inspected_entity_id
                ):
                    bar = pygame.Rect(
                        center[0] - bar_width // 2,
                        center[1] - 12,
                        bar_width,
                        3,
                    )
                    health_width = round(bar_width * entity.health / entity.kind.profile.max_health)
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

    def _representative_path_points(
        self, view: SceneView
    ) -> tuple[tuple[tuple[int, int], ...], ...]:
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
            paths: list[tuple[tuple[int, int], ...]] = []
            for entity_id in view.representative_path_entity_ids():
                entity = view.simulation.entities[entity_id]
                points = tuple(
                    (
                        origin_x + round(point.x * tile_size),
                        origin_y + round(point.y * tile_size),
                    )
                    for point in view.simplified_path(entity)
                )
                if len(points) >= 2:
                    paths.append(points)
            self._path_render_key = key
            self._path_render_points = tuple(paths)
        return self._path_render_points

    def draw_projectiles(self, view: SceneView, screen: pygame.Surface) -> None:
        colors = PROJECTILE_COLORS
        for trace in view.simulation.projectile_traces:
            points = [view.screen_point(point) for point in trace.points]
            if len(points) > 1:
                pygame.draw.lines(screen, colors[trace.weapon_kind], False, points, 1)
        for projectile in view.simulation.projectiles.values():
            color = colors[projectile.weapon_kind]
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
