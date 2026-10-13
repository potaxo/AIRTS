"""Builder definition, including construction and repair capabilities."""

from airts.world.units.schema import EntityCategory, EntityProfile

PROFILE = EntityProfile(
    category=EntityCategory.UNIT,
    max_health=80,
    vision_range=5.0,
    footprint=(1, 1),
    collision_radius=0.32,
    mass=2.0,
    movement_speed=3.0,
    production_cost=75,
    build_speed=5.0,
    build_range=4.0,
    repair_range=4.0,
    repair_rate=5,
)
