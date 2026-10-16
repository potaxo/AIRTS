"""Deterministic pixel terrain, shared by software tiles and the GPU shader.

The authoritative map remains one RGB texel per cell. Fine detail is display-only,
static in world coordinates, and hidden below one screen pixel per detail pixel.
"""

from functools import lru_cache

import numpy as np
import pygame

from airts.presentation.palette import TERRAIN_COLORS
from airts.world.map import GameMap, Terrain

DETAIL_SIDE = 8
DETAIL_MIN_TILE = 8
VARIANTS = 17
GRID_COLOR = (55, 67, 49)
SHORE_COLOR = (109, 116, 81)

# These expressions use only nonnegative integer arithmetic. The software author
# and GLSL use the same expressions, including the unmodified central reference.
_PATTERNS = {
    Terrain.GRASS: "((x*3+y*5+v)%17 == 0 ? 7 : ((x*3+y*5+v)%17 == 1 ? -5 : 0))",
    Terrain.ROAD: "((x*7+y*3+v)%19 == 0 ? 5 : ((x*7+y*3+v)%19 == 1 ? -4 : 0))",
    Terrain.FOREST: "((x+2*(y/2)+v)%7 < 2 ? -9 : ((x+y+v)%11 == 0 ? 5 : 0))",
    Terrain.WATER: "(y%4 == 0 && (x+v)%8 < 4 ? 9 : (y%4 == 1 && (x+v)%8 < 4 ? -5 : 0))",
    Terrain.ROCK: "((x*7+y*3+v)%13 < 2 ? -9 : ((x*7+y*3+v)%13 == 2 ? 7 : 0))",
    Terrain.BRIDGE: "(y%4 == 0 ? -15 : (y%4 == 1 ? 6 : 0))",
}


def detail_offset(terrain: Terrain, x: int, y: int, variant: int) -> int:
    """Small material-specific marks; central pixels preserve the palette reference."""
    if 3 <= x <= 4 and 3 <= y <= 4:
        return 0
    match terrain:
        case Terrain.GRASS:
            grain = (x * 3 + y * 5 + variant) % 17
            return 7 if grain == 0 else -5 if grain == 1 else 0
        case Terrain.ROAD:
            grain = (x * 7 + y * 3 + variant) % 19
            return 5 if grain == 0 else -4 if grain == 1 else 0
        case Terrain.FOREST:
            return (
                -9
                if (x + 2 * (y // 2) + variant) % 7 < 2
                else 5
                if (x + y + variant) % 11 == 0
                else 0
            )
        case Terrain.WATER:
            return (
                9
                if y % 4 == 0 and (x + variant) % 8 < 4
                else -5
                if y % 4 == 1 and (x + variant) % 8 < 4
                else 0
            )
        case Terrain.ROCK:
            grain = (x * 7 + y * 3 + variant) % 13
            return -9 if grain < 2 else 7 if grain == 2 else 0
        case Terrain.BRIDGE:
            return -15 if y % 4 == 0 else 6 if y % 4 == 1 else 0


@lru_cache(maxsize=1)
def _tiles() -> np.ndarray:
    tiles = np.empty((len(Terrain), VARIANTS, DETAIL_SIDE, DETAIL_SIDE, 3), dtype=np.uint8)
    for index, terrain in enumerate(Terrain):
        for variant in range(VARIANTS):
            for y in range(DETAIL_SIDE):
                for x in range(DETAIL_SIDE):
                    offset = detail_offset(terrain, x, y, variant)
                    tiles[index, variant, y, x] = [
                        channel + offset for channel in TERRAIN_COLORS[terrain]
                    ]
    return tiles


def detailed_surface(game_map: GameMap) -> pygame.Surface:
    """Build a fixed eight-pixels-per-cell source once, independently of camera zoom."""
    indices = {terrain: index for index, terrain in enumerate(Terrain)}
    cells = np.array([[indices[terrain] for terrain in row] for row in game_map.terrain])
    y, x = np.indices(cells.shape)
    variants = (x * 3 + y * 7) % VARIANTS
    tiles = _tiles()[cells, variants]
    water = cells == indices[Terrain.WATER]
    # A one-detail-pixel bank belongs inside water, so gameplay cells do not change.
    top = water.copy()
    top[0] = False
    top[1:] &= ~water[:-1]
    bottom = water.copy()
    bottom[-1] = False
    bottom[:-1] &= ~water[1:]
    left = water.copy()
    left[:, 0] = False
    left[:, 1:] &= ~water[:, :-1]
    right = water.copy()
    right[:, -1] = False
    right[:, :-1] &= ~water[:, 1:]
    tiles[:, :, 0, :, :][top] = SHORE_COLOR
    tiles[:, :, -1, :, :][bottom] = SHORE_COLOR
    tiles[:, :, :, 0, :][left] = SHORE_COLOR
    tiles[:, :, :, -1, :][right] = SHORE_COLOR
    pixels = tiles.transpose(0, 2, 1, 3, 4).reshape(
        game_map.height * DETAIL_SIDE, game_map.width * DETAIL_SIDE, 3
    )
    return pygame.image.frombytes(
        pixels.tobytes(), (game_map.width * DETAIL_SIDE, game_map.height * DETAIL_SIDE), "RGB"
    )


def fragment_shader() -> str:
    cases = []
    for terrain, expression in _PATTERNS.items():
        color = ", ".join(str(channel) for channel in TERRAIN_COLORS[terrain])
        cases.append(f"if (all(equal(base, ivec3({color})))) return {expression};")
    water = ", ".join(str(channel) for channel in TERRAIN_COLORS[Terrain.WATER])
    shore = ", ".join(str(channel) for channel in SHORE_COLOR)
    return (
        """
int terrainDetail(ivec3 base, ivec2 cell, vec2 position) {
    ivec2 p = clamp(ivec2(floor(fract(position) * 8.0)), ivec2(0), ivec2(7));
    int x = p.x; int y = p.y;
    if (x >= 3 && x <= 4 && y >= 3 && y <= 4) return 0;
    int v = (cell.x*3 + cell.y*7)%17;
    CASES
    return 0;
}
vec3 terrainPixel(vec3 base, ivec2 cell, vec2 position) {
    ivec3 material = ivec3(round(base * 255.0));
    if (all(equal(material, ivec3(WATER_COLOR)))) {
        ivec2 p = clamp(ivec2(floor(fract(position) * 8.0)), ivec2(0), ivec2(7));
        bool shore = false;
        if (p.x == 0 && cell.x > 0) shore = any(notEqual(ivec3(round(texelFetch(terrain_texture, cell-ivec2(1,0), 0).rgb*255.0)), material));
        if (p.x == 7 && cell.x < terrain_size.x-1) shore = shore || any(notEqual(ivec3(round(texelFetch(terrain_texture, cell+ivec2(1,0), 0).rgb*255.0)), material));
        if (p.y == 0 && cell.y > 0) shore = shore || any(notEqual(ivec3(round(texelFetch(terrain_texture, cell-ivec2(0,1), 0).rgb*255.0)), material));
        if (p.y == 7 && cell.y < terrain_size.y-1) shore = shore || any(notEqual(ivec3(round(texelFetch(terrain_texture, cell+ivec2(0,1), 0).rgb*255.0)), material));
        if (shore) return vec3(SHORE_COLOR)/255.0;
    }
    return base + vec3(float(terrainDetail(material, cell, position))/255.0);
}
""".replace("CASES", "\n    ".join(cases))
        .replace("WATER_COLOR", water)
        .replace("SHORE_COLOR", shore)
    )
