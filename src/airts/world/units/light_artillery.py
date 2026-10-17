"""Mobile, short-range artillery; shared combat executes fixed-impact splash shells."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=65,
    vision_range=7.0,
    footprint=(1, 1),
    collision_radius=0.36,
    mass=3.5,
    movement_speed=3.0,
    attack_damage=20,
    combat_support=True,
    attack_range=9.0,
    projectile_speed=14.0,
    splash_radius=1.5,
    attack_interval_ticks=16,
    production_cost=140,
    production_ticks=55,
)
