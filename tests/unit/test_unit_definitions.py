"""Definition diagnostics and complete production bindings for static entity kinds."""

from dataclasses import replace

import pytest

from airts.world.units.catalog import EntityKind
from airts.world.units.schema import EntityCategory


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
