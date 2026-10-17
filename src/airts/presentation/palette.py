"""Shared terrain, entity, and projectile colors for both rendering backends."""

from airts.world.map import GameMap, Terrain
from airts.world.units.catalog import EntityKind

type Color = tuple[int, int, int]

REPAIR_COLOR: Color = (105, 232, 172)
RECLAIM_COLOR: Color = (240, 82, 82)
WORK_BEAM_WIDTH = 3
PROGRESS_COLOR: Color = (76, 156, 240)
PROGRESS_TRACK: Color = (28, 43, 65)

TEAM_COLORS: dict[int, Color] = {
    1: (92, 220, 145),
    2: (232, 91, 88),
    3: (234, 203, 85),
    4: (153, 135, 235),
}


def match_color(game_map: GameMap, owner_id: str) -> Color:
    return TEAM_COLORS[
        next(slot.team for slot in game_map.match_slots if slot.owner_id == owner_id)
    ]


TERRAIN_COLORS: dict[Terrain, Color] = {
    Terrain.GRASS: (68, 82, 51),
    Terrain.ROAD: (115, 106, 81),
    Terrain.FOREST: (41, 61, 43),
    Terrain.WATER: (44, 76, 94),
    Terrain.ROCK: (86, 89, 82),
    Terrain.BRIDGE: (151, 129, 83),
}

ENTITY_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (82, 211, 237),
    EntityKind.LIGHT_TANK: (235, 221, 93),
    EntityKind.HEAVY_TANK: (230, 139, 75),
    EntityKind.BUILDER: (99, 220, 176),
    EntityKind.ARTILLERY: (204, 115, 214),
    EntityKind.LIGHT_ARTILLERY: (174, 151, 224),
    EntityKind.TANK_DESTROYER: (202, 188, 130),
    EntityKind.TURRET: (186, 195, 111),
    EntityKind.FACTORY: (112, 142, 181),
    EntityKind.REPAIR_HUB: (110, 178, 151),
    EntityKind.COMMAND_CENTER: (155, 129, 190),
    EntityKind.RESOURCE_GENERATOR: (198, 168, 88),
    EntityKind.MATTER_SYNTHESIZER: (160, 125, 210),
}

PROJECTILE_COLORS: dict[EntityKind, Color] = {
    EntityKind.SCOUT: (120, 225, 255),
    EntityKind.LIGHT_TANK: (255, 232, 105),
    EntityKind.HEAVY_TANK: (255, 135, 70),
    EntityKind.ARTILLERY: (255, 147, 57),
    EntityKind.LIGHT_ARTILLERY: (255, 183, 89),
    EntityKind.TANK_DESTROYER: (255, 241, 173),
    EntityKind.TURRET: (238, 255, 136),
}
