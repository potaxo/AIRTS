"""Stable entity tags, explicit profile bindings, and production relationships."""

from __future__ import annotations

from enum import StrEnum

from airts.world.units import builder, buildings, heavy_tank, light_tank, scout
from airts.world.units.schema import EntityProfile


class EntityKind(StrEnum):
    SCOUT = "scout"
    LIGHT_TANK = "light_tank"
    HEAVY_TANK = "heavy_tank"
    BUILDER = "builder"
    FACTORY = "factory"
    REPAIR_HUB = "repair_hub"
    COMMAND_CENTER = "command_center"
    RESOURCE_GENERATOR = "resource_generator"
    RESOURCE_EXTRACTOR = "resource_generator"  # Preserve existing map/save/command tags.
    MATTER_SYNTHESIZER = "matter_synthesizer"

    @property
    def profile(self) -> EntityProfile:
        return _ENTITY_PROFILES[self]

    @property
    def production_kinds(self) -> tuple[EntityKind, ...]:
        if self is EntityKind.FACTORY:
            return (
                EntityKind.SCOUT,
                EntityKind.LIGHT_TANK,
                EntityKind.HEAVY_TANK,
                EntityKind.BUILDER,
            )
        if self is EntityKind.COMMAND_CENTER:
            return (EntityKind.BUILDER,)
        return ()

    @property
    def movement_speed(self) -> float:
        speed = self.profile.movement_speed
        if speed is None:
            raise ValueError(f"{self.value} is not movable")
        return speed


_ENTITY_PROFILES: dict[EntityKind, EntityProfile] = {
    EntityKind.SCOUT: scout.PROFILE,
    EntityKind.LIGHT_TANK: light_tank.PROFILE,
    EntityKind.HEAVY_TANK: heavy_tank.PROFILE,
    EntityKind.BUILDER: builder.PROFILE,
    EntityKind.FACTORY: buildings.FACTORY,
    EntityKind.REPAIR_HUB: buildings.REPAIR_HUB,
    EntityKind.COMMAND_CENTER: buildings.COMMAND_CENTER,
    EntityKind.RESOURCE_GENERATOR: buildings.RESOURCE_GENERATOR,
    EntityKind.MATTER_SYNTHESIZER: buildings.MATTER_SYNTHESIZER,
}
