"""Shared terrain, entity, and projectile colors for both rendering backends."""

from airts.world.map import EntityKind, Terrain

type Color = tuple[int, int, int]

TERRAIN_COLORS: dict[Terrain, Color] = {
    Terrain.GRASS: (64, 102, 60),
    Terrain.ROAD: (119, 106, 77),
    Terrain.FOREST: (43, 78, 48),
    Terrain.WATER: (42, 91, 132),
    Terrain.ROCK: (66, 69, 72),
    Terrain.BRIDGE: (148, 126, 82),
}

ENTITY_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (82, 211, 237),
    EntityKind.LIGHT_TANK: (235, 221, 93),
    EntityKind.HEAVY_TANK: (230, 139, 75),
    EntityKind.BUILDER: (99, 220, 176),
    EntityKind.FACTORY: (112, 142, 181),
    EntityKind.REPAIR_HUB: (110, 178, 151),
    EntityKind.COMMAND_CENTER: (155, 129, 190),
    EntityKind.RESOURCE_GENERATOR: (198, 168, 88),
}

PROJECTILE_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (120, 225, 255),
    EntityKind.LIGHT_TANK: (255, 232, 105),
    EntityKind.HEAVY_TANK: (255, 135, 70),
}
