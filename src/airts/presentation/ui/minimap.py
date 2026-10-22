"""Cached tactical overview and camera projection, shared by both renderers."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor
from typing import Protocol

import numpy as np
import pygame
from numpy.typing import NDArray

from airts.contracts.spatial import Point
from airts.presentation.palette import TEAM_COLORS, TERRAIN_COLORS
from airts.presentation.ui import theme
from airts.simulation import Simulation
from airts.world.entities import Entity
from airts.world.map import GameMap
from airts.world.units.schema import EntityCategory


class MinimapView(Protocol):
    @property
    def simulation(self) -> Simulation: ...

    @property
    def canvas_rect(self) -> pygame.Rect: ...

    @property
    def selected_entities(self) -> set[str]: ...

    @property
    def map_origin(self) -> tuple[int, int]: ...

    @property
    def tile_size(self) -> float: ...


@dataclass(slots=True)
class _MarkerGroup:
    entities: tuple[Entity, ...]
    colors: NDArray[np.uint32]
    selected: NDArray[np.bool_]
    side: int


class Minimap:
    """Keep static terrain and tick-driven contacts out of per-frame camera work."""

    def __init__(self) -> None:
        self.panel = pygame.Rect(0, 0, 1, 1)
        self.map_rect = pygame.Rect(0, 0, 1, 1)
        self._game_map: GameMap | None = None
        self._terrain: pygame.Surface | None = None
        self._scaled_terrain: pygame.Surface | None = None
        self._contacts: pygame.Surface | None = None
        self._contacts_key: tuple[object, ...] | None = None
        self._membership_key: tuple[object, ...] | None = None
        self._groups: tuple[_MarkerGroup, ...] = ()

    def layout(self, viewport: pygame.Rect, game_map: GameMap, scale: float) -> None:
        side = min(round(176 * scale), viewport.width - 32, viewport.height - 32)
        fit = min(side / game_map.width, side / game_map.height)
        self.map_rect = pygame.Rect(
            0, 0, max(1, round(game_map.width * fit)), max(1, round(game_map.height * fit))
        )
        self.map_rect.midtop = (viewport.centerx, viewport.top + 13)
        self.panel = self.map_rect.inflate(2, 2)

    def world_point(self, position: tuple[int, int], game_map: GameMap) -> Point:
        """Clamp captured drags to map edges, including rectangular-map letterboxing."""
        return Point(
            max(0.0, min(1.0, (position[0] - self.map_rect.x) / self.map_rect.width))
            * game_map.width,
            max(0.0, min(1.0, (position[1] - self.map_rect.y) / self.map_rect.height))
            * game_map.height,
        )

    def screen_point(self, point: Point, game_map: GameMap) -> tuple[int, int]:
        return (
            self.map_rect.x
            + min(
                self.map_rect.width - 1, max(0, int(point.x / game_map.width * self.map_rect.width))
            ),
            self.map_rect.y
            + min(
                self.map_rect.height - 1,
                max(0, int(point.y / game_map.height * self.map_rect.height)),
            ),
        )

    def viewport_rect(self, view: MinimapView) -> pygame.Rect:
        game_map = view.simulation.game_map
        origin_x, origin_y = view.map_origin
        tile = view.tile_size
        left = max(0.0, (view.canvas_rect.left - origin_x) / tile)
        top = max(0.0, (view.canvas_rect.top - origin_y) / tile)
        right = min(float(game_map.width), (view.canvas_rect.right - origin_x) / tile)
        bottom = min(float(game_map.height), (view.canvas_rect.bottom - origin_y) / tile)
        sx = self.map_rect.width / game_map.width
        sy = self.map_rect.height / game_map.height
        return pygame.Rect(
            self.map_rect.x + floor(left * sx),
            self.map_rect.y + floor(top * sy),
            max(1, ceil(right * sx) - floor(left * sx)),
            max(1, ceil(bottom * sy) - floor(top * sy)),
        ).clip(self.map_rect)

    def draw(self, view: MinimapView, screen: pygame.Surface, *, contacts: bool = True) -> None:
        game_map = view.simulation.game_map
        if self._game_map is not game_map:
            self._game_map = game_map
            self._terrain = pygame.Surface((game_map.width, game_map.height))
            for y, row in enumerate(game_map.terrain):
                for x, terrain in enumerate(row):
                    self._terrain.set_at((x, y), TERRAIN_COLORS[terrain])
            self._scaled_terrain = None
            self._contacts_key = None
        if self._scaled_terrain is None or self._scaled_terrain.get_size() != self.map_rect.size:
            assert self._terrain is not None
            self._scaled_terrain = pygame.transform.scale(self._terrain, self.map_rect.size)
            self._contacts_key = None
        key = (
            id(view.simulation),
            view.simulation.tick if contacts else None,
            view.simulation.command_count if contacts else None,
            len(view.simulation.entities) if contacts else None,
            frozenset(view.selected_entities) if contacts else None,
            tuple(self.map_rect),
            contacts,
        )
        if key != self._contacts_key:
            self._contacts = self._scaled_terrain.copy()
            self._contacts_key = key
            for site in game_map.resource_sites or ():
                point = self.screen_point(Point(site.x + 1, site.y + 1), game_map)
                pygame.draw.circle(
                    self._contacts,
                    theme.ACCENT,
                    (point[0] - self.map_rect.x, point[1] - self.map_rect.y),
                    2,
                    1,
                )
            if contacts:
                self._draw_contacts(view)
        assert self._contacts is not None
        screen.blit(self._contacts, self.map_rect)
        pygame.draw.rect(screen, theme.EDGE, self.map_rect.inflate(2, 2), 1)
        if contacts:
            pygame.draw.rect(screen, theme.TEXT, self.viewport_rect(view), 1)

    def _draw_contacts(self, view: MinimapView) -> None:
        """Stamp contacts in bulk; cache membership/colors separately from moving centers."""
        assert self._contacts is not None
        simulation = view.simulation
        game_map = simulation.game_map
        key = (simulation, tuple(simulation.entities), frozenset(view.selected_entities))
        if key != self._membership_key:
            self._membership_key = key
            owner_colors = {slot.owner_id: TEAM_COLORS[slot.team] for slot in game_map.match_slots}
            groups: list[_MarkerGroup] = []
            # Buildings first so mobile armies remain readable over their bases.
            for buildings in (True, False):
                entities = tuple(
                    entity
                    for entity in simulation.entities.values()
                    if (entity.category is EntityCategory.BUILDING) == buildings
                )
                if entities:
                    groups.append(
                        _MarkerGroup(
                            entities,
                            np.asarray(
                                [
                                    self._contacts.map_rgb(
                                        owner_colors[entity.owner_id]
                                        if game_map.match_slots
                                        else TEAM_COLORS[1 if entity.owner_id == "player" else 2]
                                    )
                                    for entity in entities
                                ],
                                dtype=np.uint32,
                            ),
                            np.fromiter(
                                (entity.entity_id in view.selected_entities for entity in entities),
                                dtype=np.bool_,
                                count=len(entities),
                            ),
                            5 if buildings else 3,
                        )
                    )
            self._groups = tuple(groups)
        pixels = pygame.surfarray.pixels2d(self._contacts)
        size = np.asarray(self.map_rect.size)
        scale = size / (game_map.width, game_map.height)
        for group in self._groups:
            positions = np.asarray(
                [
                    (position.x, position.y)
                    for entity in group.entities
                    for position in (
                        entity.position if group.side == 3 else entity.selection_position,
                    )
                ]
            )
            centers = np.clip((positions * scale).astype(np.intp), 0, size - 1)
            if group.selected.any():
                self._stamp(
                    pixels,
                    centers[group.selected],
                    group.side + 2,
                    np.uint32(self._contacts.map_rgb(theme.TEXT)),
                )
            self._stamp(pixels, centers, group.side, group.colors[:, None, None])
        del pixels  # Release the surface lock before compositing it in either renderer.

    def _stamp(
        self,
        pixels: NDArray[np.uint32],
        centers: NDArray[np.intp],
        side: int,
        colors: NDArray[np.uint32] | np.uint32,
    ) -> None:
        offsets = np.arange(-(side // 2), side // 2 + 1)
        x = np.clip(centers[:, 0, None, None] + offsets[None, :, None], 0, self.map_rect.width - 1)
        y = np.clip(centers[:, 1, None, None] + offsets[None, None, :], 0, self.map_rect.height - 1)
        pixels[x, y] = colors
