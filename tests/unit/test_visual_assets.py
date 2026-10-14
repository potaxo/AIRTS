"""Art coverage, transparent pixels, bounded transforms and angle conventions."""

from math import pi, radians

import pygame
import pytest

from airts.contracts.spatial import Point
from airts.presentation.visuals.assets import SpriteCache, atlas_pixels, source_sprite
from airts.presentation.visuals.catalog import (
    APPEARANCES,
    ATLAS_COLUMNS,
    ATLAS_PATHS,
    COMPOSITE_PATHS,
    SPRITE_INDICES,
    SPRITE_PATHS,
    TILE_PIXELS,
)
from airts.presentation.visuals.facing import heading, turn_towards
from airts.world.units.catalog import EntityKind


def test_every_kind_has_distinct_packaged_art_and_atlas_coordinates() -> None:
    assert set(APPEARANCES) == set(EntityKind)
    fingerprints = set()
    size, pixels = atlas_pixels()
    atlas = pygame.image.frombytes(pixels, size, "RGBA")
    for kind, appearance in APPEARANCES.items():
        source = source_sprite(appearance.body)
        assert source.get_size() == (TILE_PIXELS, TILE_PIXELS)
        assert source.get_at((0, 0)).a == 0
        assert source.get_at((32, 32)).a == 255
        fingerprints.add(pygame.image.tobytes(source, "RGBA"))
        if appearance.weapon is not None:
            composite = source_sprite(COMPOSITE_PATHS[kind])
            assert composite.get_at((32, 32)).a == 255
    assert len(fingerprints) == len(EntityKind)
    assert len(SPRITE_PATHS) == len(set(SPRITE_PATHS))
    for path in ATLAS_PATHS:
        index = SPRITE_INDICES[path]
        tile = pygame.Rect(
            index % ATLAS_COLUMNS * TILE_PIXELS,
            index // ATLAS_COLUMNS * TILE_PIXELS,
            TILE_PIXELS,
            TILE_PIXELS,
        )
        assert pygame.image.tobytes(atlas.subsurface(tile), "RGBA") == pygame.image.tobytes(
            source_sprite(path), "RGBA"
        )


def test_missing_art_fails_with_a_useful_diagnostic() -> None:
    with pytest.raises(ValueError, match="cannot load packaged sprite missing.png"):
        source_sprite("missing.png")


def test_transform_cache_reuses_variants_is_bounded_and_keeps_transparency() -> None:
    cache = SpriteCache()
    cache.CAPACITY = 4
    cache.prepare(pygame.Surface((100, 100), depth=32))
    path = APPEARANCES[EntityKind.SCOUT].body
    first = cache.get(path, (82, 211, 237), (64, 64), 0)
    assert cache.get(path, (82, 211, 237), (64, 64), 0) is first
    assert first.get_colorkey() is not None
    target = pygame.Surface((64, 64))
    target.fill((50, 60, 70))
    target.blit(first, (0, 0))
    assert target.get_at((0, 0))[:3] == (50, 60, 70)
    assert target.get_at((32, 32))[:3] == (82, 211, 237)
    for index in range(5):
        cache.get(path, (82, 211, 237), (64, 64), index * pi / 4)
    assert len(cache._transforms) == cache.CAPACITY
    cache.prepare(pygame.Surface((100, 100), depth=16))
    assert not cache._transforms


@pytest.mark.parametrize(
    ("point", "expected"),
    [(Point(0, -1), 0), (Point(1, 0), pi / 2), (Point(0, 1), pi), (Point(-1, 0), -pi / 2)],
)
def test_map_heading_is_clockwise_from_north(point: Point, expected: float) -> None:
    assert heading(Point(0, 0), point) == expected


def test_turning_uses_the_short_arc_and_respects_the_angular_budget() -> None:
    assert turn_towards(radians(179), radians(-179), radians(10)) == pytest.approx(radians(181))
    assert turn_towards(0, pi / 2, pi / 6) == pytest.approx(pi / 6)
