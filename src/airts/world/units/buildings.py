"""Static building definitions sharing the entity profile schema."""

from airts.world.units.schema import EntityCategory, EntityProfile

FACTORY = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=500,
    vision_range=5.0,
    footprint=(4, 4),
    collision_radius=0.5,
    construction_cost=400,
)

REPAIR_HUB = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=350,
    vision_range=4.0,
    footprint=(3, 3),
    collision_radius=0.5,
    construction_cost=250,
    repair_range=6.0,
    repair_rate=5,
)

COMMAND_CENTER = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=700,
    vision_range=7.0,
    footprint=(5, 5),
    collision_radius=0.5,
)

RESOURCE_GENERATOR = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=250,
    vision_range=3.0,
    footprint=(2, 2),
    collision_radius=0.5,
    construction_cost=200,
    income_per_second=25,
)

MATTER_SYNTHESIZER = EntityProfile(
    category=EntityCategory.BUILDING,
    max_health=400,
    vision_range=4.0,
    footprint=(3, 3),
    collision_radius=0.5,
    construction_cost=1200,
    income_per_second=5,
)
