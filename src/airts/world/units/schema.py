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
        if type(self.max_health) is not int or self.max_health <= 0:
            raise ValueError("entity profile max_health must be a positive integer")
        if not isfinite(self.vision_range) or self.vision_range < 0:
            raise ValueError("entity profile vision_range must be finite and nonnegative")
        if len(self.footprint) != 2 or any(
            type(value) is not int or value <= 0 for value in self.footprint
        ):
            raise ValueError("entity profile footprint must contain two positive integers")
        if type(self.production_ticks) is not int or self.production_ticks < 0:
            raise ValueError("entity profile production_ticks must be a nonnegative integer")
        if self.movement_speed is not None and (
            not isfinite(self.movement_speed) or self.movement_speed <= 0
        ):
            raise ValueError("entity profile movement_speed must be finite and positive")
        if self.movable and self.production_ticks == 0:
            raise ValueError("mobile entity profile requires positive production_ticks")
        if self.movable and self.production_cost <= 0:
            raise ValueError("mobile entity profile requires positive production_cost")
        if not isfinite(self.mass) or self.mass <= 0:
            raise ValueError("entity profile mass must be finite and positive")
        if not isfinite(self.collision_radius) or self.collision_radius <= 0:
            raise ValueError("entity profile collision_radius must be finite and positive")
        if type(self.attack_damage) is not int or self.attack_damage < 0:
            raise ValueError("entity profile attack_damage must be a nonnegative integer")
        if not isfinite(self.attack_range) or self.attack_range < 0:
            raise ValueError("entity profile attack_range must be finite and nonnegative")
        if not isfinite(self.projectile_speed) or self.projectile_speed < 0:
            raise ValueError("entity profile projectile_speed must be finite and nonnegative")
        if type(self.attack_interval_ticks) is not int or self.attack_interval_ticks <= 0:
            raise ValueError("entity profile attack_interval_ticks must be a positive integer")
        if self.attack_damage > 0 and self.projectile_speed == 0:
            raise ValueError("armed entity profile requires a positive projectile_speed")
        if self.attack_damage > 0 and self.attack_range <= 0:
            raise ValueError("armed entity profile requires a positive attack_range")
        if not isfinite(self.splash_radius) or self.splash_radius < 0:
            raise ValueError("entity profile splash_radius must be finite and nonnegative")
        if self.splash_radius > 0 and self.attack_damage <= 0:
            raise ValueError("splash damage requires an armed entity profile")
        if self.combat_support and (not self.movable or self.attack_damage <= 0):
            raise ValueError("combat support requires an armed mobile unit")
        for field, value in (
            ("production_cost", self.production_cost),
            ("construction_cost", self.construction_cost),
            ("repair_rate", self.repair_rate),
            ("reclaim_rate", self.reclaim_rate),
            ("income_per_second", self.income_per_second),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"entity profile {field} must be a nonnegative integer")
        for field, rate in (
            ("build_speed", self.build_speed),
            ("build_range", self.build_range),
            ("repair_range", self.repair_range),
        ):
            if not isfinite(rate) or rate < 0:
                raise ValueError(f"entity profile {field} must be finite and nonnegative")
        if (self.build_speed > 0 or self.reclaim_rate > 0) and self.build_range <= 0:
            raise ValueError("construction or reclamation capability requires positive build_range")
        if self.repair_rate > 0 and self.repair_range <= 0:
            raise ValueError("repair capability requires positive repair_range")

    @property
    def movable(self) -> bool:
        return self.movement_speed is not None

    @property
    def can_build(self) -> bool:
        return self.movable and self.build_speed > 0 and self.build_range > 0

    @property
    def can_repair(self) -> bool:
        return self.repair_rate > 0 and self.repair_range > 0

    @property
    def can_reclaim(self) -> bool:
        return self.movable and self.reclaim_rate > 0 and self.build_range > 0


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
