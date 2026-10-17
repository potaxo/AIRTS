"""World camera scale and bounded pan, independent of input and application state."""

from dataclasses import dataclass, field

import pygame

from airts.contracts.spatial import Point


@dataclass(slots=True)
class Camera:
    zoom: float = 1.0
    offset: Point = field(default_factory=lambda: Point(0, 0))

    def tile_size(self, viewport: pygame.Rect, map_size: tuple[int, int]) -> float:
        return self.zoom * min(viewport.width / map_size[0], viewport.height / map_size[1])

    def pixel_size(self, viewport: pygame.Rect, map_size: tuple[int, int]) -> tuple[int, int]:
        tile = self.tile_size(viewport, map_size)
        return round(map_size[0] * tile), round(map_size[1] * tile)

    def origin(self, viewport: pygame.Rect, pixel_size: tuple[int, int]) -> tuple[int, int]:
        return (
            viewport.centerx - pixel_size[0] // 2 + round(self.offset.x),
            viewport.centery - pixel_size[1] // 2 + round(self.offset.y),
        )

    def reset_view(self, viewport: pygame.Rect, map_size: tuple[int, int]) -> None:
        fit = min(viewport.width / map_size[0], viewport.height / map_size[1])
        self.zoom = max(1.0, min(self.zoom, max(1.0, 32.0 / fit)))
        self.offset = Point(0, 0)

    def change_zoom(self, steps: int, viewport: pygame.Rect, map_size: tuple[int, int]) -> None:
        fit = min(viewport.width / map_size[0], viewport.height / map_size[1])
        maximum = max(1.0, 32.0 / fit)
        self.zoom = max(1.0, min(maximum, self.zoom * 1.2 ** max(-100, min(100, steps))))

    def pan(self, dx: float, dy: float, viewport: pygame.Rect, pixel_size: tuple[int, int]) -> None:
        width, height = pixel_size
        base_x = viewport.centerx - width // 2
        base_y = viewport.centery - height // 2
        minimum_x = min(0, viewport.right - base_x - width)
        maximum_x = max(0, viewport.left - base_x)
        minimum_y = min(0, viewport.bottom - base_y - height)
        maximum_y = max(0, viewport.top - base_y)
        self.offset = Point(
            max(minimum_x, min(maximum_x, self.offset.x + dx)),
            max(minimum_y, min(maximum_y, self.offset.y + dy)),
        )

    def center_on(self, point: Point, viewport: pygame.Rect, map_size: tuple[int, int]) -> None:
        """Center on a world point using the same map bounds as ordinary panning."""
        origin_x, origin_y = self.origin(viewport, self.pixel_size(viewport, map_size))
        tile = self.tile_size(viewport, map_size)
        self.pan(
            viewport.centerx - origin_x - point.x * tile,
            viewport.centery - origin_y - point.y * tile,
            viewport,
            self.pixel_size(viewport, map_size),
        )
