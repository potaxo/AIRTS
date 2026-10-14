"""Validated packaged PNGs, a shared atlas and bounded software transforms."""

from collections import OrderedDict
from functools import lru_cache
from importlib.resources import files
from math import ceil, degrees, tau

import pygame

from airts.presentation.palette import Color
from airts.presentation.visuals.catalog import ATLAS_COLUMNS, ATLAS_PATHS, COMPOSITES, TILE_PIXELS

SOFTWARE_DIRECTIONS = 32


@lru_cache(maxsize=len(ATLAS_PATHS))
def source_sprite(path: str) -> pygame.Surface:
    if path in COMPOSITES:
        body, weapon = COMPOSITES[path]
        assert weapon is not None
        composed = source_sprite(body).copy()
        composed.blit(source_sprite(weapon), (0, 0))
        return composed
    resource = files("airts").joinpath("data", "sprites", path)
    try:
        with resource.open("rb") as stream:
            surface = pygame.image.load(stream, path)
    except (OSError, pygame.error) as error:
        raise ValueError(f"cannot load packaged sprite {path}: {error}") from error
    if (
        surface.get_size() != (TILE_PIXELS, TILE_PIXELS)
        or not surface.get_flags() & pygame.SRCALPHA
    ):
        raise ValueError(f"sprite {path} must be a {TILE_PIXELS}x{TILE_PIXELS} RGBA PNG")
    if any(alpha not in (0, 255) for alpha in pygame.image.tobytes(surface, "RGBA")[3::4]):
        raise ValueError(f"pixel sprite {path} must use hard alpha edges (0 or 255)")
    return surface


@lru_cache(maxsize=1)
def atlas_pixels() -> tuple[tuple[int, int], bytes]:
    size = (ATLAS_COLUMNS * TILE_PIXELS, ceil(len(ATLAS_PATHS) / ATLAS_COLUMNS) * TILE_PIXELS)
    atlas = pygame.Surface(size, pygame.SRCALPHA, 32)
    for index, path in enumerate(ATLAS_PATHS):
        atlas.blit(
            source_sprite(path),
            (index % ATLAS_COLUMNS * TILE_PIXELS, index // ATLAS_COLUMNS * TILE_PIXELS),
        )
    return size, pygame.image.tobytes(atlas, "RGBA")


class SpriteCache:
    """Rotation/scale/tint work runs on cache misses, never for every unit/frame."""

    CAPACITY = 512

    def __init__(self) -> None:
        self._transforms: OrderedDict[tuple[str, Color, tuple[int, int], int], pygame.Surface] = (
            OrderedDict()
        )
        self._format = pygame.Surface((1, 1), depth=32)

    def prepare(self, target: pygame.Surface) -> None:
        if (target.get_bitsize(), target.get_masks()) != (
            self._format.get_bitsize(),
            self._format.get_masks(),
        ):
            self._format = pygame.Surface((1, 1)).convert(target)
            self._transforms.clear()

    def get(self, path: str, color: Color, size: tuple[int, int], angle: float) -> pygame.Surface:
        direction = round(angle / tau * SOFTWARE_DIRECTIONS) % SOFTWARE_DIRECTIONS
        key = (path, color, size, direction)
        surface = self._transforms.get(key)
        if surface is None:
            tinted = source_sprite(path).copy()
            tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            scaled = pygame.transform.scale(tinted, size)
            rotated = pygame.transform.rotate(
                scaled, -degrees(direction * tau / SOFTWARE_DIRECTIONS)
            )
            # Binary pixel alpha uses fast RGB/RLE blits in the target's format.
            surface = pygame.Surface(rotated.get_size()).convert(self._format)
            surface.fill((0, 0, 0))
            surface.blit(rotated, (0, 0))
            surface.set_colorkey((0, 0, 0), pygame.RLEACCEL)
            self._transforms[key] = surface
            if len(self._transforms) > self.CAPACITY:
                self._transforms.popitem(last=False)
        self._transforms.move_to_end(key)
        return surface
