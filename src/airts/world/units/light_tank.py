"""Light tank definition."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=100,
    vision_range=5.0,
    footprint=(1, 1),
    collision_radius=0.38,
    mass=3.0,
    movement_speed=3.5,
    attack_damage=12,
    combat_support=True,
    attack_range=6.0,
    projectile_speed=10.0,
    production_cost=100,
)
