"""Buildable fixed defense turret."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=400,
    vision_range=9,
    footprint=(2, 2),
    collision_radius=1,
    attack_damage=16,
    attack_range=9,
    projectile_speed=20,
    construction_cost=250,
)
