"""Slow, long-range artillery; shared combat owns projectile execution."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=90,
    vision_range=7,
    footprint=(1, 1),
    collision_radius=0.42,
    mass=5,
    movement_speed=2,
    attack_damage=40,
    combat_support=True,
    attack_range=12,
    projectile_speed=10,
    splash_radius=3,
    attack_interval_ticks=25,
    production_cost=225,
    production_ticks=85,
)
