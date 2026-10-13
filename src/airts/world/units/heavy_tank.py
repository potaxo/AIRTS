"""Heavy tank definition."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=160,
    vision_range=4.0,
    footprint=(1, 1),
    collision_radius=0.45,
    mass=9.0,
    movement_speed=2.5,
    attack_damage=20,
    combat_support=True,
    attack_range=7.0,
    projectile_speed=7.0,
    production_cost=175,
)
