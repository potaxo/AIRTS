"""Shared fields for immutable unit and building definitions."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class EntityCategory(StrEnum):
    UNIT = "unit"
    BUILDING = "building"


@dataclass(frozen=True, slots=True, kw_only=True)
class EntityProfile:
    category: EntityCategory
    max_health: int
    vision_range: float
    footprint: tuple[int, int]
    collision_radius: float
    mass: float = 1.0
    movement_speed: float | None = None
    attack_damage: int = 0
    attack_range: float = 0.0
    projectile_speed: float = 0.0
    production_cost: int = 0
    construction_cost: int = 0
    build_speed: float = 0.0
    build_range: float = 0.0
    repair_range: float = 0.0
    repair_rate: int = 0
    income_per_second: int = 0

    def __post_init__(self) -> None:
        if not isfinite(self.mass) or self.mass <= 0:
            raise ValueError("entity profile mass must be finite and positive")
        if not isfinite(self.collision_radius) or self.collision_radius <= 0:
            raise ValueError("entity profile collision_radius must be finite and positive")
        if not isfinite(self.projectile_speed) or self.projectile_speed < 0:
            raise ValueError("entity profile projectile_speed must be finite and nonnegative")
        if self.attack_damage > 0 and self.projectile_speed == 0:
            raise ValueError("armed entity profile requires a positive projectile_speed")

    @property
    def movable(self) -> bool:
        return self.movement_speed is not None
