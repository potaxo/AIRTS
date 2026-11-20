"""Runtime geometry caches remain derived through movement and kind changes."""

from dataclasses import replace

import pytest

from airts.contracts.spatial import Point
from airts.world.entities import Entity
from airts.world.units.catalog import EntityKind


@pytest.mark.parametrize("kind", (EntityKind.SCOUT, EntityKind.FACTORY))
def test_selection_center_reuses_immutable_geometry_and_refreshes_without_saved_state(
    kind: EntityKind,
) -> None:
    unit = Entity("test", kind, "player", Point(3, 4), kind.profile.max_health)
    saved = unit.to_dict()
    center = unit.selection_position
    assert unit.selection_position is center
    assert unit.to_dict() == saved
    assert unit == replace(unit)
    unit.position = Point(8, 9)
    fresh = replace(unit)
    assert unit.selection_position == fresh.selection_position
    assert unit.selection_position != center
    # Same origin, different footprint/category must invalidate the cache.
    unit.kind = EntityKind.RESOURCE_GENERATOR
    assert unit.selection_position == replace(unit).selection_position
    unit.kind = EntityKind.SCOUT
    assert unit.selection_position is unit.position
