"""Fragile ranged tank hunter; shared combat executes slow-firing direct shots."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=85,
    vision_range=6.0,
    footprint=(1, 1),
    collision_radius=0.38,
    mass=4.5,
    movement_speed=3.0,
    attack_damage=45,
    combat_support=True,
    attack_range=9.0,
    projectile_speed=26.0,
    attack_interval_ticks=25,
    production_cost=160,
    production_ticks=60,
)
