"""Building-line geometry must respect footprints in every drag direction."""

import pytest

from airts.contracts.spatial import Point
from airts.presentation.build_placement import building_line
from airts.world.map import footprint_cells
from airts.world.units.catalog import BUILDABLE_KINDS, EntityKind


@pytest.mark.parametrize("kind", BUILDABLE_KINDS)
@pytest.mark.parametrize(
    "end", [Point(29, 8), Point(8, 29), Point(29, 24), Point(1, 2), Point(8, 8)]
)
def test_building_line_is_aligned_inside_drag_bounds_and_nonoverlapping(
    kind: EntityKind, end: Point
) -> None:
    start = Point(8, 8)
    positions = building_line(kind, start, end)
    assert positions[0] == start
    occupied: set[tuple[int, int]] = set()
    for position in positions:
        assert position.x.is_integer() and position.y.is_integer()
        assert min(start.x, end.x) <= position.x <= max(start.x, end.x)
        assert min(start.y, end.y) <= position.y <= max(start.y, end.y)
        cells = footprint_cells(kind, position)
        assert not occupied.intersection(cells)
        occupied.update(cells)
