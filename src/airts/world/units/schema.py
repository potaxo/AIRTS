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
    splash_radius: float = 0.0
    attack_interval_ticks: int = 10
    combat_support: bool = False
    production_cost: int = 0
    production_ticks: int = 0
    construction_cost: int = 0
    build_speed: float = 0.0
    build_range: float = 0.0
    repair_range: float = 0.0
    repair_rate: int = 0
    reclaim_rate: int = 0
    income_per_second: int = 0

    def __post_init__(self) -> None:
        if type(self.production_ticks) is not int or self.production_ticks < 0:
            raise ValueError("entity profile production_ticks must be a nonnegative integer")
        if self.movable and self.production_ticks == 0:
            raise ValueError("mobile entity profile requires positive production_ticks")
        if not isfinite(self.mass) or self.mass <= 0:
            raise ValueError("entity profile mass must be finite and positive")
        if not isfinite(self.collision_radius) or self.collision_radius <= 0:
            raise ValueError("entity profile collision_radius must be finite and positive")
        if not isfinite(self.projectile_speed) or self.projectile_speed < 0:
            raise ValueError("entity profile projectile_speed must be finite and nonnegative")
        if type(self.attack_interval_ticks) is not int or self.attack_interval_ticks <= 0:
            raise ValueError("entity profile attack_interval_ticks must be a positive integer")
        if self.attack_damage > 0 and self.projectile_speed == 0:
            raise ValueError("armed entity profile requires a positive projectile_speed")
        if not isfinite(self.splash_radius) or self.splash_radius < 0:
            raise ValueError("entity profile splash_radius must be finite and nonnegative")
        if self.splash_radius > 0 and self.attack_damage <= 0:
            raise ValueError("splash damage requires an armed entity profile")
        if self.combat_support and (not self.movable or self.attack_damage <= 0):
            raise ValueError("combat support requires an armed mobile unit")

    @property
    def movable(self) -> bool:
        return self.movement_speed is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpgradeDefinition:
    """One paid transition to an absolute target profile, using the single resource."""

    cost: int
    duration_ticks: int
    profile: EntityProfile

    def __post_init__(self) -> None:
        if type(self.cost) is not int or self.cost <= 0:
            raise ValueError("upgrade cost must be a positive integer")
        if type(self.duration_ticks) is not int or self.duration_ticks <= 0:
            raise ValueError("upgrade duration_ticks must be a positive integer")
