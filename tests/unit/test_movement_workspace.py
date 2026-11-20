"""Derived movement storage follows live membership without retaining old profile rows."""

import numpy as np

from airts.contracts.spatial import Point
from airts.systems.movement import MovementWorkspace
from airts.world.entities import Entity
from airts.world.units.catalog import EntityKind


def test_profile_rows_follow_growth_reordering_kind_changes_and_shrinkage() -> None:
    scout = Entity("scout", EntityKind.SCOUT, "player", Point(2.5, 2.5), 1)
    heavy = Entity("heavy", EntityKind.HEAVY_TANK, "player", Point(5.5, 5.5), 1)
    workspace = MovementWorkspace({"scout": scout})
    rows = [scout]
    workspace.refresh_profiles(rows, 0.1)
    records = workspace.records
    workspace.refresh_profiles(rows, 0.1)
    workspace.ensure(2)
    assert workspace.records is records
    variants = [
        [heavy, scout],
        [scout, heavy],
        [heavy] * (workspace.capacity + 1),
        [scout],
    ]
    for rows in variants:
        workspace.ensure(len(rows))
        workspace.refresh_profiles(rows, 0.1)
        for name, values in (
            ("radii", [e.kind.profile.collision_radius for e in rows]),
            ("base_speeds", [e.kind.movement_speed * 0.1 for e in rows]),
            ("masses", [e.kind.profile.mass for e in rows]),
        ):
            np.testing.assert_array_equal(workspace.floats[name][: len(rows)], values)
    scout.kind = EntityKind.BUILDER
    workspace.refresh_profiles([scout], 0.1)
    assert workspace.floats["base_speeds"][0] == EntityKind.BUILDER.movement_speed * 0.1
    workspace.refresh_profiles([], 0.1)
    assert workspace.profile_keys == ()
