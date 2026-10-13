"""Scout definition."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=60,
    vision_range=7.0,
    footprint=(1, 1),
    collision_radius=0.30,
    mass=1.0,
    movement_speed=5.0,
    attack_damage=5,
    attack_range=5.0,
    projectile_speed=12.0,
    production_cost=50,
)
