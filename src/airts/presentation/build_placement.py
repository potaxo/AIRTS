"""Grid-aligned building-line drafts; no authoritative state is changed here."""

from dataclasses import dataclass
from math import floor

from airts.contracts.spatial import Point
from airts.simulation import Simulation
from airts.systems.construction import validate_building_placement
from airts.world.units.catalog import EntityKind


@dataclass(slots=True)
class BuildingLineDraft:
    kind: EntityKind
    builder_ids: tuple[str, ...]
    start: Point
    end: Point

    def positions(self) -> tuple[Point, ...]:
        return building_line(self.kind, self.start, self.end)


def building_line_previews(
    simulation: Simulation, draft: BuildingLineDraft
) -> tuple[tuple[Point, bool], ...]:
    return tuple(
        (
            point,
            validate_building_placement(simulation, draft.kind, point, owner_id="player") is None,
        )
        for point in draft.positions()
    )


def building_line(kind: EntityKind, start: Point, end: Point) -> tuple[Point, ...]:
    """Space anchors by the footprint on the dominant axis, including reverse drags."""
    x, y = floor(start.x), floor(start.y)
    dx, dy = floor(end.x) - x, floor(end.y) - y
    width, height = kind.profile.footprint
    horizontal = abs(dx) / width >= abs(dy) / height
    extent, step = (abs(dx), width) if horizontal else (abs(dy), height)
    if extent == 0:
        return (Point(float(x), float(y)),)
    return tuple(
        Point(float(x + round(dx * i * step / extent)), float(y + round(dy * i * step / extent)))
        for i in range(extent // step + 1)
    )
