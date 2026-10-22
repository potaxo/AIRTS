"""Explicit art bindings; world profiles never depend on image files or visual size."""

from dataclasses import dataclass

from airts.world.units.catalog import EntityKind

TILE_PIXELS = 64
ATLAS_COLUMNS = 4


@dataclass(frozen=True, slots=True)
class Appearance:
    body: str
    size: tuple[float, float]
    weapon: str | None = None
    work_port: tuple[float, float] | None = None


APPEARANCES = {
    EntityKind.SCOUT: Appearance("units/scout/body.png", (1.25, 1.25), "units/scout/weapon.png"),
    EntityKind.LIGHT_TANK: Appearance(
        "units/light_tank/body.png", (1.4, 1.4), "units/light_tank/weapon.png"
    ),
    EntityKind.HEAVY_TANK: Appearance(
        "units/heavy_tank/body.png", (1.4, 1.4), "units/heavy_tank/weapon.png"
    ),
    # Center of the open claw at source pixel (40, 8), relative to the 64px pivot.
    EntityKind.BUILDER: Appearance(
        "units/builder/body.png", (1.35, 1.35), work_port=(8 / 64, -24 / 64)
    ),
    EntityKind.ARTILLERY: Appearance(
        "units/artillery/body.png", (1.6, 1.6), "units/artillery/weapon.png"
    ),
    EntityKind.LIGHT_ARTILLERY: Appearance(
        "units/light_artillery/body.png", (1.4, 1.4), "units/light_artillery/weapon.png"
    ),
    EntityKind.TANK_DESTROYER: Appearance(
        "units/tank_destroyer/body.png", (1.45, 1.45), "units/tank_destroyer/weapon.png"
    ),
    EntityKind.TURRET: Appearance(
        "buildings/turret/body.png", (1, 1), "buildings/turret/weapon.png"
    ),
    EntityKind.FACTORY: Appearance("buildings/factory/body.png", (4, 4)),
    EntityKind.REPAIR_HUB: Appearance("buildings/repair_hub/body.png", (3, 3)),
    EntityKind.COMMAND_CENTER: Appearance("buildings/command_center/body.png", (5, 5)),
    EntityKind.RESOURCE_GENERATOR: Appearance("buildings/resource_generator/body.png", (2, 2)),
    EntityKind.MATTER_SYNTHESIZER: Appearance("buildings/matter_synthesizer/body.png", (3, 3)),
}

SPRITE_PATHS = tuple(
    path
    for appearance in APPEARANCES.values()
    for path in (appearance.body, appearance.weapon)
    if path is not None
)
COMPOSITE_PATHS = {
    kind: f"combined:{kind.value}" for kind, art in APPEARANCES.items() if art.weapon is not None
}
COMPOSITES = {
    COMPOSITE_PATHS[kind]: (art.body, art.weapon)
    for kind, art in APPEARANCES.items()
    if art.weapon is not None
}
ATLAS_PATHS = (*SPRITE_PATHS, *COMPOSITES)
SPRITE_INDICES = {path: index for index, path in enumerate(ATLAS_PATHS)}
