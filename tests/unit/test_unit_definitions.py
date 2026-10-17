"""Definition diagnostics and complete production bindings for static entity kinds."""

from dataclasses import replace

import pytest

from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory


@pytest.mark.parametrize(
    "kind,base_income,tiers",
    [
        (EntityKind.RESOURCE_GENERATOR, 4, ((120, 300, 8), (360, 600, 16))),
        (EntityKind.MATTER_SYNTHESIZER, 2, ((200, 450, 4), (600, 900, 8))),
    ],
)
def test_income_building_balance_is_defined_by_profiles(
    kind: EntityKind, base_income: int, tiers: tuple[tuple[int, int, int], ...]
) -> None:
    assert kind.profile.income_per_second == base_income
    assert (
        tuple(
            (upgrade.cost, upgrade.duration_ticks, upgrade.profile.income_per_second)
            for upgrade in kind.upgrades
        )
        == tiers
    )


@pytest.mark.parametrize("ticks", [-1, True, 2.5])
def test_production_time_requires_nonnegative_integer_ticks(ticks: int) -> None:
    with pytest.raises(ValueError, match="production_ticks must be a nonnegative integer"):
        replace(EntityKind.SCOUT.profile, production_ticks=ticks)


def test_mobile_profiles_require_production_time_and_costlier_units_take_longer() -> None:
    with pytest.raises(
        ValueError, match="mobile entity profile requires positive production_ticks"
    ):
        replace(EntityKind.SCOUT.profile, production_ticks=0)
    units = sorted(
        (kind for kind in EntityKind if kind.profile.movable),
        key=lambda kind: kind.profile.production_cost,
    )
    assert all(
        first.profile.production_ticks < second.profile.production_ticks
        for first, second in zip(units, units[1:], strict=False)
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_health", 0, "max_health must be a positive integer"),
        ("vision_range", -1.0, "vision_range must be finite and nonnegative"),
        ("footprint", (0, 1), "footprint must contain two positive integers"),
        ("movement_speed", 0.0, "movement_speed must be finite and positive"),
        ("attack_damage", -1, "attack_damage must be a nonnegative integer"),
        ("attack_range", -1.0, "attack_range must be finite and nonnegative"),
        ("production_cost", -1, "production_cost must be a nonnegative integer"),
        ("construction_cost", -1, "construction_cost must be a nonnegative integer"),
        ("income_per_second", -1, "income_per_second must be a nonnegative integer"),
        ("build_speed", float("nan"), "build_speed must be finite and nonnegative"),
        ("build_range", -1.0, "build_range must be finite and nonnegative"),
        ("repair_range", float("inf"), "repair_range must be finite and nonnegative"),
        ("repair_rate", True, "repair_rate must be a nonnegative integer"),
        ("reclaim_rate", -1, "reclaim_rate must be a nonnegative integer"),
    ],
)
def test_invalid_profile_fields_fail_at_definition_time(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(EntityKind.FACTORY.profile, **{field: value})


def test_profile_capabilities_are_derived_from_validated_rates() -> None:
    builder = EntityKind.BUILDER.profile
    hub = EntityKind.REPAIR_HUB.profile

    assert builder.can_build and builder.can_repair and builder.can_reclaim
    assert hub.can_repair and not hub.can_build and not hub.can_reclaim
    construction_only = replace(builder, repair_rate=0, reclaim_rate=0)
    assert construction_only.can_build
    assert not construction_only.can_repair and not construction_only.can_reclaim


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("build_speed", "requires positive build_range"),
        ("reclaim_rate", "requires positive build_range"),
        ("repair_rate", "repair capability requires positive repair_range"),
    ],
)
def test_work_capabilities_require_a_positive_range(field: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replace(EntityKind.FACTORY.profile, **{field: 1})


@pytest.mark.parametrize("mass", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_mass_fails_at_definition_time(mass: float) -> None:
    with pytest.raises(ValueError, match="mass must be finite and positive"):
        replace(EntityKind.SCOUT.profile, mass=mass)


@pytest.mark.parametrize("radius", [0.0, -0.1, float("nan"), float("inf")])
def test_invalid_collision_radius_fails_at_definition_time(radius: float) -> None:
    with pytest.raises(ValueError, match="collision_radius must be finite and positive"):
        replace(EntityKind.SCOUT.profile, collision_radius=radius)


@pytest.mark.parametrize("speed", [-1.0, float("nan"), float("inf")])
def test_invalid_projectile_speed_fails_at_definition_time(speed: float) -> None:
    with pytest.raises(ValueError, match="projectile_speed must be finite and nonnegative"):
        replace(EntityKind.SCOUT.profile, projectile_speed=speed)


def test_armed_definition_cannot_silently_disable_projectiles() -> None:
    with pytest.raises(
        ValueError, match="armed entity profile requires a positive projectile_speed"
    ):
        replace(EntityKind.SCOUT.profile, projectile_speed=0.0)
    assert EntityKind.BUILDER.profile.projectile_speed == 0.0


@pytest.mark.parametrize("radius", [-0.1, float("nan"), float("inf")])
def test_invalid_splash_radius_fails_at_definition_time(radius: float) -> None:
    with pytest.raises(ValueError, match="splash_radius must be finite and nonnegative"):
        replace(EntityKind.ARTILLERY.profile, splash_radius=radius)


def test_splash_requires_a_weapon() -> None:
    with pytest.raises(ValueError, match="splash damage requires an armed entity profile"):
        replace(EntityKind.BUILDER.profile, splash_radius=3)


def test_automatic_support_is_an_explicit_combat_capability() -> None:
    for kind in (
        EntityKind.SCOUT,
        EntityKind.LIGHT_TANK,
        EntityKind.HEAVY_TANK,
        EntityKind.ARTILLERY,
        EntityKind.LIGHT_ARTILLERY,
        EntityKind.TANK_DESTROYER,
    ):
        assert kind.profile.combat_support
    assert not EntityKind.BUILDER.profile.combat_support
    # An armed auxiliary profile still opts out unless it explicitly enables support.
    assert not replace(
        EntityKind.BUILDER.profile, attack_damage=1, attack_range=1, projectile_speed=1
    ).combat_support
    with pytest.raises(ValueError, match="combat support requires an armed mobile unit"):
        replace(EntityKind.BUILDER.profile, combat_support=True)


@pytest.mark.parametrize("interval", [0, -1, True, 2.5])
def test_attack_interval_requires_positive_integer_ticks(interval: int) -> None:
    with pytest.raises(ValueError, match="attack_interval_ticks must be a positive integer"):
        replace(EntityKind.ARTILLERY.profile, attack_interval_ticks=interval)


@pytest.mark.parametrize("kind", list(EntityKind), ids=lambda kind: kind.value)
def test_every_kind_has_a_profile_and_producers_offer_buildable_units(kind: EntityKind) -> None:
    profile = kind.profile
    assert profile.max_health > 0
    if kind.production_kinds:
        assert profile.category is EntityCategory.BUILDING
    for produced in kind.production_kinds:
        assert produced.profile.category is EntityCategory.UNIT
        assert produced.profile.movable
        assert produced.profile.production_cost > 0
